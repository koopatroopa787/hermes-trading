"""
Strategy: Reversal v1 — Price Reversal + Volume Gate
Ported from WQ alpha O0b3PJOp (Price Reversal + Volume Gate, IS Sharpe 2.36)
Concept: Buy when price drops on abnormal volume (expecting mean reversion).
Sell when price recovers or hits stop.

Adapted for intraday 1-min bars on single stocks.
"""
import pandas as pd
import numpy as np

PARAMS = {
    "vol_window": 20,          # rolling volume average window (bars)
    "volume_spike": 1.6,       # volume ÷ rolling avg threshold (top 20% ≈ 1.5x+)
    "price_lookback": 5,       # bars to measure price change
    "reversal_entry": 0.003,   # 0.3% price drop triggers reversal entry
    "stop_pct": 0.015,         # 1.5% stop loss
    "target_pct": 0.012,       # 1.2% mean reversion target
    "cooldown": 15,            # wait N bars after exit before re-entering
}

def generate_signals(bars: pd.DataFrame):
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    volume = bars["volume"]

    entries = pd.Series(False, index=bars.index)
    exits = pd.Series(False, index=bars.index)

    min_bars = max(PARAMS["vol_window"], PARAMS["price_lookback"]) + PARAMS["cooldown"] + 5
    if len(bars) < min_bars:
        return entries, exits

    # Rolling average volume
    avg_vol = volume.rolling(window=PARAMS["vol_window"], min_periods=PARAMS["vol_window"]).mean()

    # Price change over lookback
    price_change = close.diff(PARAMS["price_lookback"])

    in_trade = False
    entry_price = 0.0
    bars_since_exit = 999  # start with cooldown satisfied

    for i in range(PARAMS["vol_window"] + PARAMS["price_lookback"], len(bars)):
        c = close.iloc[i]
        v = volume.iloc[i]
        av = avg_vol.iloc[i]
        pc = price_change.iloc[i]

        bars_since_exit += 1

        if not in_trade:
            if bars_since_exit < PARAMS["cooldown"]:
                continue

            # Volume spike condition
            vol_spike = (v > av * PARAMS["volume_spike"]) if pd.notna(av) and av > 0 else False

            # Price dropped (reversal buy signal)
            price_dropped = pc < -c * PARAMS["reversal_entry"] if pd.notna(pc) else False

            if vol_spike and price_dropped:
                entries.iloc[i] = True
                in_trade = True
                entry_price = c
        else:
            # Exit conditions
            stop_hit = c < entry_price * (1 - PARAMS["stop_pct"])
            target_hit = c > entry_price * (1 + PARAMS["target_pct"])
            end_of_day = i >= len(bars) - 30  # last 30 min of trading day

            if stop_hit or target_hit or end_of_day:
                exits.iloc[i] = True
                in_trade = False
                bars_since_exit = 0

    return entries, exits
