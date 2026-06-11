# Strategy Documentation

## Hermes Bot: VWAP Mean Reversion

### Current Champion: v21 (Score: 578.42)

A mean reversion strategy that enters positions when price deviates significantly from VWAP and RSI indicates oversold conditions, then exits on recovery.

### Strategy Logic

```python
PARAMS = {
    "vwap_deviation": 0.0001,    # Min deviation from VWAP to trigger entry
    "rsi_period": 24,            # RSI lookback period
    "rsi_oversold": 23,          # RSI entry threshold (below = buy)
    "rsi_overbought": 82,        # RSI exit threshold (above = sell)
    "stop_pct": 0.0382,          # Stop loss % from entry
    "target_pct": 0.0100,        # Take profit % from entry
    "min_volume_pct": 0.5289,    # Volume must be > X% of avg volume
    "warmup_bars": 26            # Bars needed before generating signals
}
```

### Entry Conditions (all must be true)
1. Price < VWAP × (1 - vwap_deviation)  → Trading below VWAP
2. RSI < rsi_oversold                        → Oversold condition
3. Volume > avg_volume × min_volume_pct      → Sufficient volume

### Exit Conditions (any one)
1. Price > VWAP × (1 + target_pct)    → Hit target (mean reversion achieved)
2. RSI > rsi_overbought                    → Overbought signal
3. Price < entry_price × (1 - stop_pct)    → Stop loss hit

### Evolution History

| Version | Score | Key Changes |
|---------|-------|-------------|
| v1 | N/A | Baseline: RSI(14) + VWAP deviation 0.5% |
| v3 | 35.2 | Added volume filter, tuned RSI to 20/80 |
| v8 | 89.1 | Added warmup_bars, tightened stop |
| v13 | 145.3 | Optimized RSI period to 16 |
| v18 | 312.7 | Switched to 1-min bars from 5-min |
| v21 | 578.4 | Current champion. Tight VWAP deviation (0.01%), RSI 24/23/82 |

### Key Insights from Evolution

- **Tighter VWAP deviation** (from 0.5% down to 0.01%) — The strategy profited from much smaller mean reversion moves than originally expected
- **RSI period shifted up** (from 14 to 24) — Longer lookback filtered out noise
- **Oversold threshold dropped** (from 30 to 23) — Only bought at genuinely oversold levels
- **Target tight** (0.996%) — Take profit fast, swing for small gains
- **Stop wide** (3.82%) — Let positions breathe, most losses were from early exits, not adverse moves

---

## WQ Brain Bot: ORB Momentum v11

### Current Champion: v11 (Score: 122.87)

An Opening Range Breakout (ORB) momentum strategy that identifies the initial price range of each trading day and enters when price breaks out of that range with momentum confirmation.

### Strategy Logic

```python
PARAMS = {
    "orb_window": 10,            # Minutes to establish opening range
    "breakout_pct": 0.002,       # % above/below range to confirm breakout
    "holding_period": 20,        # Max bars to hold
    "stop_pct": 0.025,           # Stop loss
    "target_pct": 0.040,         # Take profit
    "min_volume_ratio": 1.5,     # Volume must exceed avg by this ratio
    "rsi_filter": 55,            # Only take long if RSI > this
    "trailing_stop_pct": 0.015   # Trailing stop activation
}
```

### Entry Conditions (Long)

1. Current bar close > max(high within orb_window) × (1 + breakout_pct)
2. Volume in breakout bar > avg_volume × min_volume_ratio
3. RSI(14) > rsi_filter (momentum confirmation)
4. Entry at market on bar close

### Entry Conditions (Short)

1. Current bar close < min(low within orb_window) × (1 - breakout_pct)
2. Volume > avg_volume × min_volume_ratio
3. RSI(14) < (100 - rsi_filter)
4. Entry at market on bar close

### Exit Conditions

1. Holding period > holding_period → Time-based exit
2. Price hits stop_pct from entry
3. Price hits target_pct from entry
4. Price reverses < trailing_stop_pct from peak after 50% of target reached

### Evolution History (WR Brain)

| Version | Strategy | Score | Notes |
|---------|----------|-------|-------|
| v1 | Basic ORB | N/A | Baseline: 15-min range, no filters |
| v7 | ORB + RSI filter | 45.6 | Added RSI > 55 filter, big improvement |
| v11 | ORB + volume + RSI | 122.87 | Current champion |
| v25 | ORB + VWAP combo | 89.4 | Hybrid approach, didn't outperform v11 |
| v41 | Latest candidate | Tested | Various param explorations |

### Experimental Strategies

Beyond the champion, WQ Brain has explored:
- **orb_reversal_combo** — Hybrid ORB entry with mean reversion exit
- **orb_spring** — Early breakout detection (spring pattern)
- **momentum_volume** — Pure volume-based momentum (no ORB)
- **reversal** — Pure mean reversion variant

---

## Comparing the Two Bots

| Aspect | Hermes (VWAP Mean Rev) | WQ Brain (ORB Momentum) |
|--------|----------------------|----------------------|
| **Edge** | Mean reversion after intraday dips | Breakout momentum continuation |
| **Hold time** | 5-15 minutes | 10-30 minutes |
| **Win rate** | ~65% (many small wins) | ~58% (fewer, larger wins) |
| **Max DD** | Lower (smaller positions) | Higher (trend breaks hurt) |
| **Best market** | Ranging, low volatility | Trending, high volatility |
| **Worst market** | Strong trends (no reversion) | Choppy ranges (fake breakouts) |

---

## Strategy File Format

All strategies follow the same interface:

```python
PARAMS = {
    # Strategy-specific parameters (used by evolve.py for mutation)
}

def strategy(df: pd.DataFrame, params: dict) -> tuple[pd.Series, pd.Series]:
    """
    Generate entry and exit signals.
    
    Args:
        df: DataFrame with columns ['open', 'high', 'low', 'close', 'volume']
            (may also include 'vwap' for strategies that use it)
        params: Strategy PARAMS dict (may be mutated version)
    
    Returns:
        (entries, exits) — boolean pd.Series aligned to df.index
        True = generate signal at this bar's close
    """
    # ... indicator calculations ...
    entries = ...
    exits = ...
    return entries, exits
```

This interface allows the evolution system to:
1. Load any strategy module dynamically (`importlib`)
2. Override PARAMS with mutated values
3. Call `strategy(df, mutated_params)` for backtesting
4. The best strategy becomes the new champion
