"""
Strategy: ORB + Reversal Combo v1
Combines ORB Momentum (trend breakout) with Price Reversal (mean reversion).
Uses whichever signal fires first. Only one position at a time.

ORB: Breakout above opening range (first 15 min) with volume confirmation.
Reversal: Price drop with volume spike, expecting bounce.

This gives the strategy two weapons depending on market conditions.
"""
import pandas as pd
import numpy as np

PARAMS = {
    # ORB params
    "orb_minutes": 15,
    "orb_volume_multiplier": 1.5,
    "orb_stop_pct": 0.015,
    "orb_target_pct": 0.03,
    # Reversal params
    "rev_vol_window": 20,
    "rev_volume_spike": 1.6,
    "rev_price_lookback": 5,
    "rev_reversal_entry": 0.003,
    "rev_stop_pct": 0.015,
    "rev_target_pct": 0.012,
    "rev_cooldown": 15,
    # General
    "cooldown_after_exit": 5,
}

def generate_signals(bars: pd.DataFrame):
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    volume = bars["volume"]

    entries = pd.Series(False, index=bars.index)
    exits = pd.Series(False, index=bars.index)

    if len(bars) < max(PARAMS["orb_minutes"], PARAMS["rev_vol_window"]) + 20:
        return entries, exits

    # ── ORB signals ──
    orb_high = high.iloc[:PARAMS["orb_minutes"]].max()
    orb_low = low.iloc[:PARAMS["orb_minutes"]].min()
    avg_vol_orb = volume.iloc[:PARAMS["orb_minutes"]].mean()

    # ── Reversal signals ──
    avg_vol_rev = volume.rolling(window=PARAMS["rev_vol_window"], min_periods=PARAMS["rev_vol_window"]).mean()
    price_change = close.diff(PARAMS["rev_price_lookback"])

    in_trade = False
    entry_price = 0.0
    entry_mode = ""  # "orb" or "rev"
    bars_since_exit = 999

    for i in range(max(PARAMS["orb_minutes"], PARAMS["rev_vol_window"] + PARAMS["rev_price_lookback"]), len(bars)):
        c = close.iloc[i]
        v = volume.iloc[i]

        bars_since_exit += 1

        if not in_trade:
            if bars_since_exit < PARAMS["cooldown_after_exit"]:
                continue

            # ORB entry: breakout above range with volume
            orb_entry = (c > orb_high) and (v > avg_vol_orb * PARAMS["orb_volume_multiplier"])

            # Reversal entry: volume spike + price drop
            av = avg_vol_rev.iloc[i]
            pc = price_change.iloc[i]
            vol_spike = (v > av * PARAMS["rev_volume_spike"]) if pd.notna(av) and av > 0 else False
            price_dropped = (pc < -c * PARAMS["rev_reversal_entry"]) if pd.notna(pc) else False
            rev_entry = vol_spike and price_dropped

            if orb_entry:
                entries.iloc[i] = True
                in_trade = True
                entry_price = c
                entry_mode = "orb"
            elif rev_entry:
                entries.iloc[i] = True
                in_trade = True
                entry_price = c
                entry_mode = "rev"
        else:
            # Determine exit based on entry mode
            if entry_mode == "orb":
                stop_hit = c < entry_price * (1 - PARAMS["orb_stop_pct"])
                target_hit = c > entry_price * (1 + PARAMS["orb_target_pct"])
            else:  # reversal
                stop_hit = c < entry_price * (1 - PARAMS["rev_stop_pct"])
                target_hit = c > entry_price * (1 + PARAMS["rev_target_pct"])

            end_of_day = i >= len(bars) - 30

            if stop_hit or target_hit or end_of_day:
                exits.iloc[i] = True
                in_trade = False
                entry_mode = ""
                bars_since_exit = 0

    return entries, exits
