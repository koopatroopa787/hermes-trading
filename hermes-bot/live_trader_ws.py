#!/usr/bin/env python3
"""
Live Paper Trader v2 — WebSocket-powered.

Subscribes to Alpaca's real-time 1-minute bar stream and trades
on bar close events. No polling, no 2-minute gaps.

Signals are processed the MOMENT a bar closes — instant entry/exit.

Usage:
  python3 live_trader_ws.py              # Start WebSocket daemon (default)
  python3 live_trader_ws.py --once       # Catch up only, no stream (for pre-open tests)
  python3 live_trader_ws.py --status     # Quick status check
"""

import os, sys, json, time, logging, signal, asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

import pandas as pd
import numpy as np

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# ── Configuration ──────────────────────────────────────────────────────
TRADING_BOT = os.path.expanduser("~/trading-bot-hermes")
ENV_PATH    = os.path.join(TRADING_BOT, "config", "broker.env")
CHAMPION    = os.path.join(TRADING_BOT, "config", "champion.json")
STATE_FILE  = os.path.join(TRADING_BOT, "config", "live_state.json")
SENTIMENT   = os.path.join(TRADING_BOT, "config", "sentiment_override.json")
LOG_DIR     = os.path.join(TRADING_BOT, "logs")
WS_LOG      = os.path.join(LOG_DIR, "live_trader_ws.log")

MAX_POSITION_PCT = 0.25
DAILY_LOSS_PCT   = -5.0
DAY_START_EQUITY = None  # set at startup, used for daily loss limit

ET = ZoneInfo("America/New_York")

# ── Global State ───────────────────────────────────────────────────────
running = True
logger = None  # set up in main()
trading_client: Optional[TradingClient] = None
strategy_module = None
symbols = []
sentiment_overrides = {}
# In-memory state
bar_buffers: dict[str, pd.DataFrame] = {}
positions: dict[str, dict] = {}       # {sym: {qty, avg_entry, etc}}
daily_pnl = 0.0
trade_log: list[dict] = []


# ── Logging ────────────────────────────────────────────────────────────
def setup_logger():
    os.makedirs(LOG_DIR, exist_ok=True)
    log = logging.getLogger("live_trader_ws")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(WS_LOG)
    fh.setFormatter(fmt)
    log.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    log.addHandler(ch)

    return log


# ── Helpers (ported from live_trader.py) ───────────────────────────────
def load_env():
    if not os.path.exists(ENV_PATH):
        raise FileNotFoundError(f"broker.env not found at {ENV_PATH}")
    for line in open(ENV_PATH):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()


def get_trading_client():
    load_env()
    return TradingClient(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
        paper=True,
    )


def get_data_client():
    load_env()
    from alpaca.data.historical import StockHistoricalDataClient
    return StockHistoricalDataClient(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
    )


def load_champion():
    if not os.path.exists(CHAMPION):
        logger.error("No champion.json — run the evolution cycle first!")
        return None
    with open(CHAMPION) as f:
        return json.load(f)


def load_strategy_module(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("strategy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"day": "", "trades": [], "daily_pnl": 0.0}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def submit_order(symbol, qty, side):
    try:
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        resp = trading_client.submit_order(order)
        order_id = str(resp.id)
        logger.info(f"ORDER: {side.upper()} {qty} {symbol} @ market (ID: {order_id})")

        # Track in trade log
        now = datetime.now(ET).isoformat()
        entry = {"time": now, "symbol": symbol, "side": side.upper(),
                 "qty": qty, "order_id": order_id}

        if side == "sell" and symbol in positions:
            entry_px = positions[symbol]["avg_entry"]
            exit_px = float(resp.filled_avg_price) if resp.filled_avg_price else 0.0
            trade_pnl = round((exit_px - entry_px) * qty, 2) if exit_px else 0.0
            entry["pnl"] = trade_pnl
            global daily_pnl
            daily_pnl = round(daily_pnl + trade_pnl, 2)

        trade_log.append(entry)

        # Save state after each trade
        state = {
            "day": datetime.now(ET).strftime("%Y-%m-%d"),
            "trades": trade_log[-500:],  # keep last 500
            "daily_pnl": daily_pnl,
        }
        save_state(state)
        return resp
    except Exception as e:
        logger.error(f"submit_order({symbol}, {side}): {e}")
        return None


def calc_qty(price, symbol=None, sentiment=None):
    """Calculate position quantity. Uses per-symbol multiplier if available."""
    account = trading_client.get_account()
    mult = sentiment.get("position_multiplier", 1.0) if sentiment else 1.0
    
    # Per-symbol override from sentiment
    if symbol and sentiment:
        per_sym = sentiment.get("per_symbol", {}).get(symbol, {})
        sym_mult = per_sym.get("position_multiplier", None)
        if sym_mult is not None:
            mult = sym_mult
    
    qty = int(float(account.equity) * MAX_POSITION_PCT * mult / price)
    return max(qty, 1)


def refresh_positions():
    """Sync in-memory positions with Alpaca."""
    global positions
    try:
        pos_map = {}
        for p in trading_client.get_all_positions():
            pos_map[p.symbol] = {
                "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "current_price": float(p.current_price),
            }
        positions = pos_map
        logger.info(f"Positions synced: {len(positions)} open")
        for sym, p in positions.items():
            logger.info(f"  {sym}: {p['qty']} @ ${p['avg_entry']:.2f} (PnL: ${p['unrealized_pl']:+.2f})")
    except Exception as e:
        logger.error(f"refresh_positions: {e}")


# ── Historical Catch-Up ────────────────────────────────────────────────
def fetch_today_bars(data_client):
    """Fetch all 1-min bars from today's market open via REST (catch-up on startup)."""
    now = datetime.now(ET)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now < market_open:
        logger.info("Market hasn't opened yet — no historical bars to fetch.")
        return {}

    try:
        bars = data_client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Minute,
            start=market_open,
            end=now,
            adjustment="all",
            feed="iex",
        ))
        if bars is None or bars.df is None or bars.df.empty:
            logger.info("No historical bars available yet.")
            return {}

        df = bars.df
        buffers = {}
        for sym in symbols:
            try:
                sym_bars = df.xs(sym, level="symbol").copy()
                buffers[sym] = sym_bars
                logger.info(f"  {sym}: {len(sym_bars)} bars caught up")
            except KeyError:
                logger.info(f"  {sym}: no bars today")
                buffers[sym] = pd.DataFrame()
        return buffers
    except Exception as e:
        logger.error(f"fetch_today_bars: {e}")
        return {}


# ── Signal Processing ──────────────────────────────────────────────────
def process_bar(symbol: str, bar_data: dict):
    """Process a single bar close event. Called on every 1-min bar."""
    global positions, daily_pnl, trade_log, bar_buffers, sentiment_overrides, DAY_START_EQUITY

    # Convert bar_data to a 1-row DataFrame
    ts = pd.Timestamp(bar_data["timestamp"]).tz_convert(ET)
    new_row = pd.DataFrame([{
        "open":   bar_data["open"],
        "high":   bar_data["high"],
        "low":    bar_data["low"],
        "close":  bar_data["close"],
        "volume": bar_data["volume"],
    }], index=[ts])

    # Append to buffer
    sym_bars = bar_buffers.get(symbol, pd.DataFrame())
    if sym_bars.empty:
        bar_buffers[symbol] = new_row
    else:
        bar_buffers[symbol] = pd.concat([sym_bars, new_row])

    sym_bars = bar_buffers[symbol]
    n_bars = len(sym_bars)
    min_bars = strategy_module.PARAMS.get("warmup_bars") or strategy_module.PARAMS.get("orb_minutes", 20)

    if n_bars < min_bars:
        logger.debug(f"  {symbol}: only {n_bars}/{min_bars} bars — not enough data")
        return

    # Apply sentiment-based multiplier adjustments to strategy params
    s = sentiment_overrides
    base_params = dict(strategy_module.PARAMS)
    if s.get("stop_multiplier", 1.0) != 1.0 or \
       s.get("target_multiplier", 1.0) != 1.0 or \
       s.get("volume_multiplier_adj", 0.0) != 0.0:
        strategy_module.PARAMS["stop_pct"] = base_params["stop_pct"] * s.get("stop_multiplier", 1.0)
        strategy_module.PARAMS["target_pct"] = base_params["target_pct"] * s.get("target_multiplier", 1.0)
        if "volume_multiplier" in base_params:
            strategy_module.PARAMS["volume_multiplier"] = base_params["volume_multiplier"] + s.get("volume_multiplier_adj", 0.0)
        vol_str = f", vol_mult={strategy_module.PARAMS['volume_multiplier']:.4f}" if "volume_multiplier" in strategy_module.PARAMS else ""
        logger.debug(f"  Adjusted params: stop={strategy_module.PARAMS['stop_pct']:.5f}, "
                     f"target={strategy_module.PARAMS['target_pct']:.5f}{vol_str}")

    # Run strategy on ALL bars (generates signals for each index)
    entries, exits = strategy_module.generate_signals(sym_bars)

    # Restore base params for next bar
    strategy_module.PARAMS.update(base_params)

    # Check if the LATEST bar has a signal
    if entries.iloc[-1] and symbol not in positions:
        # Entry signal
        s = sentiment_overrides

        # Check if sentiment says skip
        if symbol in s.get("skip_symbols", []):
            logger.info(f"  {symbol}: SKIPPED (negative sentiment)")
            return

        price = sym_bars["close"].iloc[-1]
        local_mult = min(s.get("position_multiplier", 1.0), 0.5) if symbol in s.get("reduced_symbols", []) else s.get("position_multiplier", 1.0)
        
        # Check for per-symbol multiplier override
        per_sym = s.get("per_symbol", {}).get(symbol, {})
        sym_mult = per_sym.get("position_multiplier", None)
        if sym_mult is not None:
            local_mult = sym_mult
        
        qty = calc_qty(price, symbol=symbol, sentiment=s)
        if qty < 1:
            return

        logger.info(f"  {symbol}: 🟢 BUY @ ${price:.2f} × {qty} "
                    f"[regime={s.get('market_regime','neutral')}, mult={local_mult:.2f}]"
                    f"{' [CATCHUP]' if per_sym.get('catalysts') else ''}")
        submit_order(symbol, qty, "buy")
        # Refresh positions after order
        refresh_positions()

    elif exits.iloc[-1] and symbol in positions:
        # Exit signal
        qty = int(positions[symbol]["qty"])
        entry_px = positions[symbol]["avg_entry"]
        price = sym_bars["close"].iloc[-1]
        trade_pnl = round((price - entry_px) * qty, 2)
        logger.info(f"  {symbol}: 🔴 SELL @ ${price:.2f} × {qty} (PnL: ${trade_pnl:+.2f})")
        submit_order(symbol, qty, "sell")
        # Refresh positions after order
        refresh_positions()

    elif symbol in positions:
        # Holding — log PnL update
        unrealized = positions[symbol].get("unrealized_pl", 0)
        close_price = sym_bars["close"].iloc[-1]
        logger.info(f"  {symbol}: HOLDING {positions[symbol]['qty']} @ ${close_price:.2f} "
                    f"(PnL: ${unrealized:+.2f})")

    # Time-based EOD exit: force-close all positions at 3:50 PM ET
    now_et = datetime.now(ET)
    if now_et.hour >= 15 and now_et.minute >= 50:
        for sym in list(positions.keys()):
            raw_qty = float(positions[sym]["qty"])
            qty = int(abs(raw_qty))
            entry_px = positions[sym]["avg_entry"]
            curr_price = positions[sym].get("current_price", entry_px)
            trade_pnl = round((curr_price - entry_px) * (-1 if raw_qty < 0 else 1) * qty, 2)
            side = "buy" if raw_qty < 0 else "sell"
            logger.info(f"  {sym}: 🕐 EOD {'BUY TO COVER' if raw_qty < 0 else 'FORCE SELL'} @ ${curr_price:.2f} × {qty} (PnL: ${trade_pnl:+.2f})")
            submit_order(sym, qty, side)
        refresh_positions()
        return

    # Check daily loss limit (against start-of-day equity, not a hardcoded value)
    account = trading_client.get_account()
    equity = float(account.equity)
    ref_equity = DAY_START_EQUITY if DAY_START_EQUITY else float(account.last_equity or 0)
    if ref_equity and equity < ref_equity * (1 + DAILY_LOSS_PCT / 100):
        logger.warning(f"🚨 DAILY LOSS LIMIT HIT! Equity: ${equity:.2f} (ref: ${ref_equity:.2f}, threshold: ${ref_equity * (1 + DAILY_LOSS_PCT / 100):.2f})")
        for sym in list(positions.keys()):
            qty = int(positions[sym]["qty"])
            submit_order(sym, qty, "sell")
        logger.warning("All positions closed. Shutting down.")
        os._exit(1)


# ── WebSocket Bar Handler ──────────────────────────────────────────────
async def on_bar(bar):
    """Alpaca WebSocket bar event handler."""
    if not running:
        return
    try:
        symbol = bar.symbol
        ts = bar.timestamp.isoformat() if hasattr(bar.timestamp, 'isoformat') else str(bar.timestamp)
        logger.info(f"\n📊 BAR: {symbol} @ {bar.timestamp} "
                    f"O={bar.open:.2f} H={bar.high:.2f} L={bar.low:.2f} "
                    f"C={bar.close:.2f} V={bar.volume:.0f}")
        process_bar(symbol, {
            "timestamp": bar.timestamp,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        })
    except Exception as e:
        logger.error(f"on_bar({symbol}): {e}", exc_info=True)


async def on_trade_update(trade):
    """Handle order fill/status updates via TradingStream."""
    try:
        symbol = trade.symbol if hasattr(trade, 'symbol') else '?'
        event = trade.event if hasattr(trade, 'event') else 'unknown'
        status = trade.order_status if hasattr(trade, 'order_status') else '?'
        qty = trade.qty if hasattr(trade, 'qty') else '?'
        filled_qty = trade.filled_qty if hasattr(trade, 'filled_qty') else '?'
        price = trade.filled_avg_price if hasattr(trade, 'filled_avg_price') else '?'

        if event == 'fill' or status == 'filled':
            logger.info(f"✅ FILL: {symbol} {qty} @ ${price}")

        if event == 'partial_fill':
            logger.info(f"⏳ PARTIAL: {symbol} {filled_qty}/{qty} @ ${price}")
    except Exception as e:
        logger.debug(f"on_trade_update: {e}")


# ── Main ───────────────────────────────────────────────────────────────
async def main_async():
    global trading_client, strategy_module, symbols, bar_buffers, sentiment_overrides
    global positions, daily_pnl, trade_log

    logger.info("=" * 60)
    logger.info("LIVE PAPER TRADER v2 — WEBSOCKET MODE")
    logger.info("=" * 60)

    # 1. Load champion
    champion = load_champion()
    if not champion or not os.path.exists(champion["path"]):
        logger.error("No champion strategy — can't trade.")
        return 1

    strategy_module = load_strategy_module(champion["path"])
    symbols = [r["symbol"] for r in champion.get("symbol_results", []) if "error" not in r]
    if not symbols:
        logger.info("No champion symbols — checking screener")
        sc = os.path.join(TRADING_BOT, "screener", "candidates.json")
        if os.path.exists(sc):
            with open(sc) as f:
                data = json.load(f)
            symbols = [c["symbol"] for c in data.get("candidates", [])[:10]]

    logger.info(f"Champion: {champion['strategy']} (score: {champion['score']})")
    logger.info(f"Symbols:  {symbols}")
    logger.info(f"Params:   {strategy_module.PARAMS}")

    # 2. Set up clients
    try:
        trading_client = get_trading_client()
    except Exception as e:
        logger.error(f"init trading client: {e}")
        return 1

    global DAY_START_EQUITY
    acc = trading_client.get_account()
    DAY_START_EQUITY = float(acc.equity)
    logger.info(f"Paper Account: ${DAY_START_EQUITY:,.2f} equity")

    # 3. Load sentiment
    sentiment_overrides = load_sentiment()

    # 4. Load state
    state = load_state()
    today = datetime.now(ET).strftime("%Y-%m-%d")
    if state.get("day") != today:
        trade_log = []
        daily_pnl = 0.0
        caught_up = set()
    else:
        trade_log = state.get("trades", [])[-500:]
        daily_pnl = state.get("daily_pnl", 0.0)
        caught_up = set(state.get("caught_up", []))

    # 5. Catch up historical bars
    logger.info("Catching up historical bars...")
    data_client = get_data_client()
    bar_buffers = fetch_today_bars(data_client)

    # 6. Sync existing positions
    refresh_positions()

    # 7. Clean up stale positions from previous days (held overnight)
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    state_day = state.get("day", "")
    if state_day != today_str and positions:
        logger.info(f"⚠️ Found {len(positions)} stale position(s) from previous day — selling at open")
        for sym, pos in list(positions.items()):
            raw_qty = float(pos["qty"])
            qty = int(abs(raw_qty))
            entry_px = pos["avg_entry"]
            side = "buy" if raw_qty < 0 else "sell"
            logger.info(f"  {sym}: {qty} shares @ ${entry_px:.2f} — {'buy to cover' if raw_qty < 0 else 'force selling'}")
            submit_order(sym, qty, side)
        refresh_positions()

    # 8. Run signals on historical bars (catch up missed trades)
    logger.info("Processing historical bars for missed signals...")
    for sym in symbols:
        sym_bars = bar_buffers.get(sym, pd.DataFrame())
        min_bars = strategy_module.PARAMS.get("warmup_bars") or strategy_module.PARAMS.get("orb_minutes", 20) + 5
        if sym_bars.empty or len(sym_bars) < min_bars:
            continue
        entries, exits = strategy_module.generate_signals(sym_bars)
        # Find any unfilled entry signals before the last bar
        if entries.any() and sym not in positions and sym not in caught_up:
            entry_idx = entries[entries].index[-1]
            if entry_idx != sym_bars.index[-1]:  # skip latest bar (handled by streaming)
                # This signal happened between restarts — still execute it
                price = sym_bars.loc[entry_idx, "close"]
                qty = calc_qty(price, sentiment=sentiment_overrides)
                if qty >= 1:
                    logger.info(f"  {sym}: 🟢 MISSED ENTRY @ ${price:.2f} × {qty} (catch-up)")
                    submit_order(sym, qty, "buy")
                    caught_up.add(sym)
                    state["caught_up"] = list(caught_up)
                    save_state(state)
                    refresh_positions()
                    break  # one entry per symbol

    # 9. Check if market is open
    clock = trading_client.get_clock()
    if not clock.is_open:
        next_open = clock.next_open
        wait_secs = (next_open - datetime.now(ET)).total_seconds()
        if wait_secs > 3600:
            logger.info(f"Market closed. Next open: {next_open.strftime('%H:%M ET')} "
                        f"(in {wait_secs/3600:.1f}h). Exiting.")
            return 0
        else:
            logger.info(f"Pre-market. Market opens in {wait_secs/60:.0f} min. Waiting...")
            await asyncio.sleep(wait_secs)

    # 9. Connect WebSocket
    load_env()
    stream = StockDataStream(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
    )

    # Subscribe to minute bars for all symbols
    stream.subscribe_bars(on_bar, *symbols)
    logger.info(f"📡 Subscribed to 1-min bars for: {', '.join(symbols)}")

    # 10. Also subscribe to trading updates (order fills)
    try:
        from alpaca.trading.stream import TradingStream
        trade_stream = TradingStream(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
        )
        trade_stream.subscribe_trade_updates(on_trade_update)
        logger.info("📡 Subscribed to order trade updates")
    except Exception as e:
        logger.warning(f"Could not subscribe to trade updates: {e}")
        trade_stream = None

    # 11. Run both streams
    logger.info("🚀 Streaming live...")
    try:
        if trade_stream:
            await asyncio.gather(
                stream._run_forever(),
                trade_stream._run_forever(),
            )
        else:
            await stream._run_forever()
    except asyncio.CancelledError:
        logger.info("Stream cancelled.")
    except Exception as e:
        logger.error(f"Stream error: {e}", exc_info=True)
    finally:
        logger.info("WebSocket disconnected.")

    # 12. EOD Summary
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


def cmd_status():
    """Quick status check — no trading."""
    global logger
    logger = setup_logger()
    try:
        tc = get_trading_client()
        acc = tc.get_account()
        clock = tc.get_clock()
        pos_list = tc.get_all_positions()
        state = load_state()

        print(f"Status:     {'🟢 LIVE' if clock.is_open else '🔴 CLOSED'}")
        print(f"Time:       {datetime.now(ET).strftime('%H:%M:%S ET')}")
        print(f"Equity:     ${float(acc.equity):,.2f}")
        print(f"Cash:       ${float(acc.cash):,.2f}")
        print(f"Positions:  {len(pos_list)}")
        for p in pos_list:
            print(f"  {p.symbol}: {p.qty} shares @ ${float(p.avg_entry_price):.2f}")
        print(f"Today PnL:  ${state.get('daily_pnl', 0):+.2f}")
        print(f"Trades:     {len(state.get('trades', []))}")
        if clock.is_open:
            print(f"Close:      {clock.next_close.strftime('%H:%M ET')}")
        else:
            print(f"Next Open:  {clock.next_open.strftime('%a %H:%M ET')}")
    except Exception as e:
        print(f"Error: {e}")


def signal_handler(sig, frame):
    global running
    logger.info("Shutdown signal received — stopping streams...")
    running = False


def main():
    global logger
    logger = setup_logger()

    if "--status" in sys.argv:
        cmd_status()
        return

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    asyncio.run(main_async())


if __name__ == "__main__":
    main()
