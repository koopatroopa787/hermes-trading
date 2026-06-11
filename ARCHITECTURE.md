# Architecture Guide

## Table of Contents
1. [Overview](#overview)
2. [Bot Lifecycle](#bot-lifecycle)
3. [WebSocket Streaming Engine](#websocket-streaming-engine)
4. [Evolution System](#evolution-system)
5. [Sentiment Override System](#sentiment-override-system)
6. [Risk Management](#risk-management)
7. [Dashboard Architecture](#dashboard-architecture)

---

## Overview

Both trading bots follow the same architectural pattern with different strategy implementations:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         BOT LIFECYCLE (Daily)                          │
│                                                                         │
│  05:00/06:00 UTC                                                        │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌───────────────┐  │
│  │ EVOLVE   │────▶│ SELECT   │────▶│ BACKTEST │────▶│ UPDATE        │  │
│  │ (mutate) │     │ champion │     │ new strats│    │ champion.json  │  │
│  └──────────┘     └──────────┘     └──────────┘     └───────────────┘  │
│                                                                         │
│  12:00 UTC                                                              │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌───────────────┐  │
│  │ LAUNCH   │────▶│ CATCHUP  │────▶│ STREAM   │────▶│ EOD / SHUT    │  │
│  │ trader   │     │ bars     │     │ live     │     │ down          │  │
│  └──────────┘     └──────────┘     └──────────┘     └───────────────┘  │
│                                                                         │
│  20:30 UTC                                                              │
│  ┌──────────────────┐     ┌──────────────────┐                         │
│  │ POST-MARKET      │────▶│ SENTIMENT        │                         │
│  │ ANALYSIS         │     │ UPDATE           │                         │
│  └──────────────────┘     └──────────────────┘                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Bot Lifecycle

### 1. Evolution (Pre-market, 05:00 / 06:00 UTC)

Run by cron via `run_evolution.sh` -> `evolve.py`:

```
load_champion() -> apply_mutations() -> backtest_candidates() -> select_best() -> save_champion()
```

**Mutation process in `mutator.py`:**
- Takes the current champion PARAMS dict
- Applies Gaussian noise to each numeric parameter within bounded ranges
- Generates 20-30 candidate strategy files
- Each candidate gets a version number (e.g., `vwap_reversion_v22.py`)

**Backtesting in `backtester.py`:**
- Fetches 60 days of historical 1-minute bar data from Alpaca
- Runs each candidate over the full period
- Scores on: Sharpe ratio, total return, max drawdown, win rate, profit factor
- Combined score weights each metric

### 2. Pre-market Sentiment (12:30 UTC)

Run by cron via `analyze_sentiment.py`:

```
for each symbol:
    fetch_fundamentals(yfinance)  # P/E, revenue growth, debt/equity, beta
    check_nearby_catalysts()      # earnings, events, product launches
    score_symbol()                # 0.0 (bear) to 1.0 (bull)
    
save -> sentiment_override.json
```

Output: `sentiment_override.json` consumed by both bots at launch:
```json
{
  "market_regime": "bullish",
  "position_multiplier": 1.0,
  "reduced_symbols": ["AI", "LABU"],
  "skip_symbols": [],
  "per_symbol": {
    "AAPL": {
      "position_multiplier": 1.2,
      "catalysts": ["WWDC 2026"]
    }
  }
}
```

### 3. Live Trading Session (12:00 - ~20:00 UTC)

Launched by `scripts/start_live_trader_ws.sh`:

1. **Load champion** — Read `config/champion.json` to get the best strategy and symbols
2. **Load sentiment** — Read `config/sentiment_override.json`
3. **Catch-up bars** — Fetch all 1-min bars from market open to now, process for missed entries
4. **Subscribe to WebSocket** — Stream `trade_updates` and `1Min` bars for all symbols
5. **Process bars in real-time** — On each bar, run champion strategy logic
6. **Handle EOD** — At 3:50 PM ET, force-close all positions
7. **Daily loss check** — If day's loss exceeds 5%, liquidate and shut down

### 4. Post-market Analysis (20:30 UTC)

Run by cron via `analyze_day.py`:

- Read equity log from dashboard
- Aggregate today's trades
- Calculate PnL breakdown per symbol
- Root cause analysis (overnight hold, market condition, algo failure)
- Generate suggestions for next trading day
- Deliver summary to WhatsApp

---

## WebSocket Streaming Engine

The core loop in `live_trader_ws.py`:

```python
async def main_async():
    # 1. Setup
    champion = load_champion()
    strategy_module = load_strategy_module(champion["path"])
    trading_client = get_trading_client()
    
    # 2. Catch up historical bars
    bar_buffers = fetch_today_bars(symbols)
    
    # 3. Process missed entry signals
    process_historical_bars(bar_buffers)
    
    # 4. Connect WebSocket
    conn = WSSClient(api_key, secret_key, paper=True)
    await conn.subscribe_bars(on_bar, *symbols)
    await conn.subscribe_trade_updates(on_trade_update)
    
    # 5. Stream until market close
    await keep_alive()
```

**Key architectural decisions:**

- **Single-threaded async** — No thread contention, one event loop processes all bars sequentially
- **Global state** — `positions`, `daily_pnl`, `trade_log`, `bar_buffers`, `sentiment_overrides` are module-level globals shared across handlers
- **Self-terminating** — The bot calls `os._exit(1)` at EOD or when the daily loss limit is hit; the watchdog restarts it

### Signal Processing (`process_bar`)

The strategy module exports a strategy function that takes a DataFrame and returns entry/exit signals as boolean Series:

```python
# Called on every 1-min bar
process_bar(symbol, bar_data):
    bar_buffers[symbol] = bar_buffers[symbol].append(new_row)
    sym_bars = bar_buffers[symbol]
    
    entries, exits = strategy_module.strategy(sym_bars, strategy_module.PARAMS)
    
    if entries.iloc[-1] and symbol not in positions:
        # Entry signal
        qty = calc_qty(price, symbol, sentiment)
        submit_order(symbol, qty, "buy")
    elif exits.iloc[-1] and symbol in positions:
        # Exit signal
        qty = positions[symbol]["qty"]
        submit_order(symbol, qty, "sell")
```

---

## Evolution System

### Strategy Structure

Each strategy follows a fixed interface:

```python
# strategies/candidates/vwap_reversion_v21.py
PARAMS = {
    "vwap_deviation": 0.0001,
    "rsi_period": 24,
    "rsi_oversold": 23,
    "rsi_overbought": 82,
    "stop_pct": 0.038192,
    "target_pct": 0.009963,
    "min_volume_pct": 0.528871,
    "warmup_bars": 26
}

def strategy(df: pd.DataFrame, params: dict) -> (pd.Series, pd.Series):
    """
    Returns (entries, exits) boolean Series aligned to df index.
    """
    # Calculate indicators
    df["vwap"] = ...  # Alpaca VWAP
    df["rsi"] = ...   # RSI calculation
    df["volume_ratio"] = df["volume"] / df["volume"].rolling(...).mean()
    
    # Entry conditions
    entries = (
        (df["close"] < df["vwap"] * (1 - params["vwap_deviation"])) &
        (df["rsi"] < params["rsi_oversold"]) &
        (df["volume_ratio"] > params["min_volume_pct"])
    )
    
    # Exit conditions
    exits = (
        (df["close"] > df["vwap"] * (1 + params["target_pct"])) |
        (df["rsi"] > params["rsi_overbought"])
    )
    
    return entries, exits
```

### Mutator (`mutator.py`)

- Takes champion PARAMS dict
- Applies random mutation: for each param, `param *= (1 + random.gauss(0, 0.1))`
- Clamps values within strategy-specific bounds
- Creates new `.py` strategy file with mutated PARAMS

### Backtester (`backtester.py`)

- Fetches 60 days of 1-min bars per symbol via Alpaca `get_bars()`
- For each candidate:
  - Run `strategy()` on the full historical DataFrame
  - Track entry/exit timestamps, prices, PnL per trade
  - Calculate aggregate metrics
- Scoring formula:

```
score = (sharpe * 30) + (total_return_pct * 10) + (win_rate * 5) + (profit_factor * 10) - (max_dd_pct * 2)
```

---

## Risk Management

### Position Sizing (`calc_qty`)

```python
def calc_qty(price, symbol=None, sentiment=None):
    # Base position: 10-25% of equity per symbol
    base_pct = MAX_POSITION_PCT / len(symbols)
    base_qty = int((equity * base_pct) / price)
    
    # Sentiment multiplier (per-symbol override)
    mult = sentiment.get("position_multiplier", 1.0)
    
    # Per-symbol multiplier from sentiment override
    if symbol and sentiment:
        per_sym = sentiment.get("per_symbol", {}).get(symbol, {})
        sym_mult = per_sym.get("position_multiplier", None)
        if sym_mult:
            mult = sym_mult
    
    return max(1, int(base_qty * mult))
```

### Daily Loss Limit

- Hard limit: 5% of start-of-day equity
- Checked on EVERY bar after EOD check
- If triggered: liquidate ALL positions immediately and exit
- `os._exit(1)` — process dies; watchdog may or may not restart depending on market hours

### Time-based EOD Exit

- 3:50 PM ET: force-close every open position
- Handles both long and short positions correctly:
  - Longs: SELL qty
  - Shorts: BUY (to cover) abs(qty)

### Stale Overnight Cleanup

On restart (next trading day):
- Check for any positions still open (should be empty after EOD, but may persist)
- Sell all lingering positions at market open

---

## Dashboard Architecture

### Stack

- **Backend**: Flask (Python) on port 8765
- **Frontend**: Pure HTML/CSS/JS with inline SVG charts (zero CDN dependencies)
- **Data**: In-memory + equity_log.json
- **Exposure**: Cloudflare Tunnel (HTTPS)

### Endpoints

| Endpoint | Returns |
|----------|---------|
| `/` | Main dashboard HTML |
| `/api/hermes/account` | Hermes equity, positions, PnL |
| `/api/wq/account` | WQ Brain equity, positions, PnL |
| `/api/hermes/trades` | Hermes trade history |
| `/api/wq/trades` | WQ Brain trade history |
| `/api/equity` | Equity log for charting |
| `/api/sentiment` | Current sentiment override |
| `/changelog` | System changelog |

### SVG Chart Approach

Instead of bundling Chart.js (CDN dependency that breaks on remote VMs), charts are rendered as inline SVG:
- `<polyline>` for equity curves
- `<rect>` for bar charts
- `<text>` for axis labels
- CSS animations for auto-refresh

---

## Cron Schedule

| Time (UTC) | Job | Notes |
|------------|-----|-------|
| 05:00 | Hermes evolution | Daily strategy mutation |
| 06:00 | WQ Brain evolution | Daily strategy mutation |
| 12:00 | Launch Hermes trader | After pre-market bar catch-up |
| 12:00 | Launch WQ Brain trader | After pre-market bar catch-up |
| 12:30 | Pre-market sentiment | Runs sentiment analysis |
| 12:00-21:00 | Equity logging (every 5 min) | Dashboard data collection |
| */5 12-20 UTC | Watchdog (both) | 5-min health check |
| 20:30 Mon-Fri | Post-market analysis | EOD summary to WhatsApp |
