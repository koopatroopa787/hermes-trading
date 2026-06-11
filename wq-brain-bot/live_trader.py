#!/usr/bin/env python3
"""
Live Paper Trader — runs the champion strategy on Alpaca paper trading.
Mon-Fri during US market hours (9:30 AM - 4:00 PM ET).
Generates signals on 1-min bars, places market orders via Alpaca.

Usage:
  python3 live_trader.py          # Run once (for cron job)
  python3 live_trader.py --daemon  # Run persistently (for background process)
  python3 live_trader.py --status  # Quick status check
"""

import os, sys, json, time, logging, signal, importlib.util
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# ── Configuration ──────────────────────────────────────────────────────
TRADING_BOT = os.path.expanduser("~/trading-bot")
ENV_PATH    = os.path.join(TRADING_BOT, "config", "broker.env")
CHAMPION    = os.path.join(TRADING_BOT, "config", "champion.json")
STATE_FILE  = os.path.join(TRADING_BOT, "config", "live_state.json")
SENTIMENT   = os.path.join(TRADING_BOT, "config", "sentiment_override.json")
LOG_DIR     = os.path.join(TRADING_BOT, "logs")

POLL_SECS          = 120     # Check every 2 minutes
MAX_POSITION_PCT   = 0.25    # Base max % of portfolio per position
DAILY_LOSS_PCT     = -5.0    # Stop trading if portfolio dips 5% in a day
MAX_ERRORS         = 5       # Consecutive errors before pause
ERROR_PAUSE_SECS   = 600     # 10 min pause after too many errors

ET = ZoneInfo("America/New_York")
running = True

# ── Logging ────────────────────────────────────────────────────────────
def setup_logger():
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("live_trader")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(os.path.join(LOG_DIR, "live_trader.log"))
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger

logger = setup_logger()

# ── Helpers ────────────────────────────────────────────────────────────
def load_env():
    if not os.path.exists(ENV_PATH):
        raise FileNotFoundError(f"broker.env not found at {ENV_PATH}")
    for line in open(ENV_PATH):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def get_clients():
    load_env()
    trading = TradingClient(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
        paper=True,
    )
    data = StockHistoricalDataClient(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
    )
    return trading, data

def load_champion():
    if not os.path.exists(CHAMPION):
        logger.error("No champion.json — run the evolution cycle first!")
        return None
    with open(CHAMPION) as f:
        return json.load(f)

def load_strategy_module(path):
    spec = importlib.util.spec_from_file_location("strategy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def fetch_intraday_bars(data_client, symbols):
    """Fetch today's 1-min bars from market open until now."""
    now = datetime.now(ET)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now < market_open:
        return None
    try:
        bars = data_client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Minute,
            start=market_open,
            end=now,
            adjustment="all",
            feed="iex",
        ))
        return bars.df if bars is not None and not bars.df.empty else None
    except Exception as e:
        logger.error(f"fetch_bars: {e}")
        return None

def get_positions(trading_client, symbols):
    """Return {symbol: {qty, avg_entry, market_value, unrealized_pl}} for tracked symbols."""
    try:
        pos_map = {}
        for p in trading_client.get_all_positions():
            if p.symbol in symbols:
                pos_map[p.symbol] = {
                    "qty":           float(p.qty),
                    "avg_entry":     float(p.avg_entry_price),
                    "market_value":  float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                }
        return pos_map
    except Exception as e:
        logger.error(f"get_positions: {e}")
        return {}

def submit_order(trading_client, symbol, qty, side):
    try:
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        resp = trading_client.submit_order(order)
        logger.info(f"ORDER: {side.upper()} {qty} {symbol} @ market (ID: {resp.id})")
        return resp
    except Exception as e:
        logger.error(f"submit_order({symbol}, {side}): {e}")
        return None

def calc_qty(trading_client, price, sentiment=None):
    """Calculate position size, adjusted by sentiment multiplier."""
    account = trading_client.get_account()
    mult = sentiment.get("position_multiplier", 1.0) if sentiment else 1.0
    qty = int(float(account.equity) * MAX_POSITION_PCT * mult / price)
    return max(qty, 1)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"day": "", "trades": [], "daily_pnl": 0.0}


def load_sentiment():
    """Load sentiment overrides. Returns a dict with adjusted params and skip list."""
    default = {
        "market_regime": "neutral",
        "sentiment_score": 0.0,
        "position_multiplier": 1.0,
        "stop_multiplier": 1.0,
        "target_multiplier": 1.0,
        "volume_multiplier_adj": 0.0,
        "skip_symbols": [],
        "reduced_symbols": [],
        "notes": "No sentiment data loaded — trading with defaults",
        "analyzed_at": None,
    }
    if not os.path.exists(SENTIMENT):
        return default
    try:
        with open(SENTIMENT) as f:
            data = json.load(f)
        # Only use today's sentiment
        today = datetime.now(ET).strftime("%Y-%m-%d")
        analyzed = data.get("analyzed_at", "")
        if not analyzed.startswith(today):
            logger.info("Sentiment data is stale (not from today) — using defaults")
            return default

        merged = {**default, **{k: v for k, v in data.items() if k in default}}
        logger.info(f"Loaded sentiment: {merged['market_regime']} "
                    f"(score={merged['sentiment_score']:.2f}, "
                    f"pos_mult={merged['position_multiplier']:.2f})")
        if merged["skip_symbols"]:
            logger.info(f"  Skipping: {merged['skip_symbols']}")
        if merged["reduced_symbols"]:
            logger.info(f"  Reduced size: {merged['reduced_symbols']}")
        return merged
    except Exception as e:
        logger.warning(f"Failed to load sentiment: {e}")
        return default


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

def signal_handler(sig, frame):
    global running
    logger.info("Shutdown signal received — stopping...")
    running = False

# ── Main Trading Loop ─────────────────────────────────────────────────
def run_cycle(trading_client, data_client, strategy, symbols, state):
    """Single trading cycle: fetch data → load sentiment → generate signals → trade."""
    now = datetime.now(ET)
    clock = trading_client.get_clock()

    if not clock.is_open:
        next_open = clock.next_open
        if next_open and (next_open - now).total_seconds() < 600:
            logger.info(f"Pre-market warm-up. Market opens in {(next_open-now).total_seconds()/60:.0f} min")
        else:
            logger.debug("Market closed — skipping cycle")
        return False  # Not in trading hours

    # Load sentiment for today
    sentiment = load_sentiment()
    regime = sentiment.get("market_regime", "neutral")
    score = sentiment.get("sentiment_score", 0.0)

    if regime == "crash" or score <= -0.7:
        logger.warning(f"🚨 CRASH REGIME (score={score:.2f}) — skipping all trades today!")
        # Close any existing positions if crash
        for sym, pos in get_positions(trading_client, symbols).items():
            logger.warning(f"  Closing {sym} due to crash alert")
            submit_order(trading_client, sym, int(pos["qty"]), "sell")
        return True

    skip_list = set(sentiment.get("skip_symbols", []))
    reduced_list = set(sentiment.get("reduced_symbols", []))

    bars = fetch_intraday_bars(data_client, symbols)
    if bars is None or bars.empty:
        logger.warning("No bars available yet — market may have just opened")
        return True

    positions = get_positions(trading_client, symbols)

    for sym in symbols:
        try:
            # Check if sentiment says to skip this symbol
            if sym in skip_list:
                logger.info(f"  {sym}: SKIPPED (negative sentiment)")
                # Close position if held
                if sym in positions:
                    qty = int(positions[sym]["qty"])
                    logger.info(f"  {sym}: Closing position due to negative sentiment")
                    submit_order(trading_client, sym, qty, "sell")
                continue

            if sym not in bars.index.get_level_values("symbol"):
                continue
            sym_bars = bars.xs(sym, level="symbol")
            if len(sym_bars) < 20:
                continue  # not enough data

            entries, exits = strategy.generate_signals(sym_bars)
            price = sym_bars["close"].iloc[-1]
            in_pos = sym in positions

            if entries.iloc[-1] and not in_pos:
                # Adjust position size based on sentiment
                if sym in reduced_list:
                    local_mult = min(sentiment.get("position_multiplier", 1.0), 0.5)
                else:
                    local_mult = sentiment.get("position_multiplier", 1.0)

                qty = calc_qty(trading_client, price, sentiment)
                if qty < 1:
                    continue
                logger.info(f"  {sym}: BUY signal @ ${price:.2f} × {qty} "
                            f"[regime={regime}, mult={local_mult:.2f}]")
                order = submit_order(trading_client, sym, qty, "buy")
                if order:
                    state["trades"].append({
                        "time": datetime.now(ET).isoformat(),
                        "symbol": sym, "side": "BUY",
                        "qty": qty, "price": round(price, 2),
                        "order_id": str(order.id),
                        "sentiment_score": round(score, 2),
                    })
                    save_state(state)

            elif exits.iloc[-1] and in_pos:
                qty = int(positions[sym]["qty"])
                entry_p = positions[sym]["avg_entry"]
                trade_pnl = round((price - entry_p) * qty, 2)
                logger.info(f"  {sym}: SELL signal @ ${price:.2f} × {qty} (PnL: ${trade_pnl:.2f})")
                order = submit_order(trading_client, sym, qty, "sell")
                if order:
                    state["trades"].append({
                        "time": datetime.now(ET).isoformat(),
                        "symbol": sym, "side": "SELL",
                        "qty": qty, "price": round(price, 2),
                        "pnl": trade_pnl,
                        "order_id": str(order.id),
                    })
                    state["daily_pnl"] = round(state["daily_pnl"] + trade_pnl, 2)
                    save_state(state)

            elif in_pos:
                pl = positions[sym]["unrealized_pl"]
                logger.info(f"  {sym}: HOLDING | ${pl:+.2f}")

        except Exception as e:
            logger.error(f"  {sym}: {e}", exc_info=True)

    # Daily loss limit check
    account = trading_client.get_account()
    equity = float(account.equity)
    daily_pnl = state.get("daily_pnl", 0)
    if equity < 100000 * (1 + DAILY_LOSS_PCT / 100):
        logger.warning(f"DAILY LOSS LIMIT HIT! Equity: ${equity:.2f}, Daily PnL: ${daily_pnl:.2f}")
        for sym, pos in get_positions(trading_client, symbols).items():
            submit_order(trading_client, sym, int(pos["qty"]), "sell")
        return False

    return True

# ── Entry Points ───────────────────────────────────────────────────────
def cmd_status():
    """Quick status check — no trading."""
    try:
        trading_client, data_client = get_clients()
        acc = trading_client.get_account()
        clock = trading_client.get_clock()
        positions = trading_client.get_all_positions()
        state = load_state()

        print(f"Status:     {'🟢 LIVE' if clock.is_open else '🔴 CLOSED'}")
        print(f"Time:       {datetime.now(ET).strftime('%H:%M:%S ET')}")
        print(f"Equity:     ${float(acc.equity):,.2f}")
        print(f"Cash:       ${float(acc.cash):,.2f}")
        print(f"Positions:  {len(positions)}")
        for p in positions:
            print(f"  {p.symbol}: {p.qty} shares @ ${float(p.avg_entry_price):.2f}")
        print(f"Today PnL:  ${state.get('daily_pnl', 0):+.2f}")
        print(f"Trades:     {len(state.get('trades', []))}")
        if clock.is_open:
            print(f"Close:      {clock.next_close.strftime('%H:%M ET')}")
        else:
            print(f"Next Open:  {clock.next_open.strftime('%a %H:%M ET')}")
    except Exception as e:
        print(f"Error: {e}")

def cmd_once():
    """Run a single cycle (for cron jobs)."""
    champion = load_champion()
    if not champion or not os.path.exists(champion["path"]):
        logger.error("No champion strategy — can't trade.")
        return 1

    strategy = load_strategy_module(champion["path"])
    symbols = [r["symbol"] for r in champion.get("symbol_results", []) if "error" not in r]
    if not symbols:
        logger.info("No champion symbols — checking screener")
        sc = os.path.join(TRADING_BOT, "screener", "candidates.json")
        if os.path.exists(sc):
            with open(sc) as f:
                data = json.load(f)
            symbols = [c["symbol"] for c in data.get("candidates", [])[:10]]

    try:
        trading_client, data_client = get_clients()
    except Exception as e:
        logger.error(f"init: {e}")
        return 1

    state = load_state()
    today = datetime.now(ET).strftime("%Y-%m-%d")
    if state.get("day") != today:
        state = {"day": today, "trades": [], "daily_pnl": 0.0}
        save_state(state)

    run_cycle(trading_client, data_client, strategy, symbols, state)
    return 0

def cmd_daemon():
    """Run persistently — polls every POLL_SECS until market close or signal."""
    global running
    logger.info("=" * 60)
    logger.info("LIVE PAPER TRADER — DAEMON MODE")
    logger.info("=" * 60)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    champion = load_champion()
    if not champion or not os.path.exists(champion["path"]):
        logger.error("No champion strategy — can't trade.")
        return 1

    strategy = load_strategy_module(champion["path"])
    symbols = [r["symbol"] for r in champion.get("symbol_results", []) if "error" not in r]
    if not symbols:
        sc = os.path.join(TRADING_BOT, "screener", "candidates.json")
        if os.path.exists(sc):
            with open(sc) as f:
                data = json.load(f)
            symbols = [c["symbol"] for c in data.get("candidates", [])[:10]]

    logger.info(f"Champion: {champion['strategy']} (score: {champion['score']})")
    logger.info(f"Symbols:  {symbols}")
    logger.info(f"Params:   {strategy.PARAMS}")

    try:
        trading_client, data_client = get_clients()
    except Exception as e:
        logger.error(f"init: {e}")
        return 1

    acc = trading_client.get_account()
    logger.info(f"Paper Account: ${float(acc.equity):,.2f} equity")
    logger.info(f"Polling every {POLL_SECS}s")

    state = load_state()
    today = datetime.now(ET).strftime("%Y-%m-%d")
    if state.get("day") != today:
        state = {"day": today, "trades": [], "daily_pnl": 0.0}
        save_state(state)
        logger.info(f"New trading day: {today}")

    consecutive_errors = 0
    trading_active = True

    while running:
        try:
            trading_active = run_cycle(trading_client, data_client, strategy, symbols, state)

            if not trading_active:
                # Market closed or loss limit hit
                clock = trading_client.get_clock()
                if clock.is_open:
                    logger.info("Trading paused (loss limit). Waiting for next day...")
                    # Wait until market closes
                    while running and trading_client.get_clock().is_open:
                        time.sleep(60)
                else:
                    logger.info("Market closed. Daemon exiting.")
                    break

            consecutive_errors = 0
            now = datetime.now(ET)

            # Smart sleep: if close to market close, stay until end
            market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
            time_left = (market_close - now).total_seconds()

            if time_left <= 0:
                logger.info("Market closed for the day. Generating EOD summary...")
                break
            elif time_left < POLL_SECS:
                time.sleep(max(time_left, 5))
            else:
                time.sleep(POLL_SECS)

        except KeyboardInterrupt:
            logger.info("Interrupted — shutting down.")
            break
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Loop error #{consecutive_errors}: {e}", exc_info=True)
            if consecutive_errors >= MAX_ERRORS:
                logger.warning(f"Pausing for {ERROR_PAUSE_SECS}s after {consecutive_errors} errors")
                time.sleep(ERROR_PAUSE_SECS)
                consecutive_errors = 0
            else:
                time.sleep(POLL_SECS)

    # EOD Summary
    state = load_state()
    acc = trading_client.get_account()
    logger.info("")
    logger.info("=" * 60)
    logger.info("END OF DAY SUMMARY")
    logger.info(f"Date:   {datetime.now(ET).strftime('%Y-%m-%d')}")
    logger.info(f"Equity: ${float(acc.equity):,.2f}")
    logger.info(f"Trades: {len(state.get('trades', []))}")
    logger.info(f"PnL:    ${state.get('daily_pnl', 0):+.2f}")
    logger.info("=" * 60)

    return 0

# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--status" in sys.argv:
        cmd_status()
    elif "--daemon" in sys.argv or "--live" in sys.argv:
        sys.exit(cmd_daemon())
    else:
        sys.exit(cmd_once())
