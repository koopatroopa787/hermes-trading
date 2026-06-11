"""
Strategy: orb_momentum_v27 (mutated from orb_momentum_v25.py)
Opening Range Breakout with volume confirmation.
Generated: baseline starter strategy.
"""
import pandas as pd
import numpy as np

PARAMS = {
    "orb_minutes": 21,
    "volume_multiplier": 2.29903,
    "stop_pct": 0.008579,
    "target_pct": 0.030286
}

def generate_signals(bars: pd.DataFrame):
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    volume = bars["volume"]

    entries = pd.Series(False, index=bars.index)
    exits = pd.Series(False, index=bars.index)

    if len(bars) < PARAMS["orb_minutes"] + 5:
        return entries, exits

    orb_high = high.iloc[:PARAMS["orb_minutes"]].max()
    orb_low = low.iloc[:PARAMS["orb_minutes"]].min()
    avg_vol = volume.iloc[:PARAMS["orb_minutes"]].mean()

    in_trade = False
    entry_price = 0.0

    for i in range(PARAMS["orb_minutes"], len(bars)):
        c = close.iloc[i]
        v = volume.iloc[i]

        if not in_trade:
            long_signal = (c > orb_high) and (v > avg_vol * PARAMS["volume_multiplier"])
            if long_signal:
                entries.iloc[i] = True
                in_trade = True
                entry_price = c
        else:
            stop_hit = c < entry_price * (1 - PARAMS["stop_pct"])
            target_hit = c > entry_price * (1 + PARAMS["target_pct"])
            end_of_day = i >= len(bars) - 30
            if stop_hit or target_hit or end_of_day:
                exits.iloc[i] = True
                in_trade = False
                entry_price = 0.0

    return entries, exits
