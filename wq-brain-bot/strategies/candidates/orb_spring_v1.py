"""
Strategy: ORB Spring v1 — ORB Breakout after a Volume Pullback
Inspired by WQ alpha d5nQ8Z32 (Fundamental + News Composite, Sh=2.45, lowest DD=2.98%)
and O0b3PJOp (Price Reversal, Sh=3.49 in 2023 test).

Concept: Only take ORB breakouts when the stock has FIRST pulled back on volume
(the "spring" pattern). This filters out breakouts that are already extended
and catches the ones with the most momentum.

Flow:
1. Wait for a volume spike + price dip (reversal setup — smart money buying dips)
2. After the dip, wait for an ORB-style breakout with volume
3. Entry only if the breakout follows a recent dip
4. Exit at target, stop, or end of day

This gives HIGHER QUALITY entries instead of more entries.
"""
import pandas as pd
import numpy as np

PARAMS = {
    # Spring setup (reversal pre-condition)
    "spring_vol_window": 20,     # rolling volume avg window
    "spring_volume_spike": 1.5,  # volume spike threshold
    "spring_lookback": 10,       # look back this many bars for the dip
    "spring_dip_pct": 0.003,     # 0.3%+ dip triggers spring alert
    "spring_expiry": 40,         # spring alert expires after N bars

    # Breakout (ORB-style entry)
    "breakout_vol_mult": 1.3,    # breakout volume threshold (lower than pure ORB)
    "breakout_min_range": 0.003, # min range above recent high

    # Exit
    "stop_pct": 0.015,           # 1.5% stop loss
    "target_pct": 0.025,         # 2.5% profit target

    # General
    "cooldown": 5,
}

def generate_signals(bars: pd.DataFrame):
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    volume = bars["volume"]

    entries = pd.Series(False, index=bars.index)
    exits = pd.Series(False, index=bars.index)

    min_bars = PARAMS["spring_vol_window"] + PARAMS["spring_lookback"] + 20
    if len(bars) < min_bars:
        return entries, exits

    # Rolling avg volume
    avg_vol = volume.rolling(window=PARAMS["spring_vol_window"], min_periods=PARAMS["spring_vol_window"]).mean()

    # Recent high (for breakout level)
    recent_high = high.rolling(window=PARAMS["spring_lookback"], min_periods=PARAMS["spring_lookback"]).max()

    # Price change (for dip detection)
    price_change = close.diff(PARAMS["spring_lookback"])

    # Track spring alerts
    spring_active = pd.Series(False, index=bars.index)
    spring_age = 999  # bars since last spring alert

    # Pre-compute spring alerts
    for i in range(PARAMS["spring_vol_window"] + PARAMS["spring_lookback"], len(bars)):
        c = close.iloc[i]
        v = volume.iloc[i]
        av = avg_vol.iloc[i]
        pc = price_change.iloc[i]

        vol_spike = (v > av * PARAMS["spring_volume_spike"]) if pd.notna(av) and av > 0 else False
        price_dropped = (pc < -c * PARAMS["spring_dip_pct"]) if pd.notna(pc) else False

        if vol_spike and price_dropped:
            spring_active.iloc[i] = True

    in_trade = False
    entry_price = 0.0
    bars_since_exit = 999

    for i in range(PARAMS["spring_vol_window"] + PARAMS["spring_lookback"], len(bars)):
        c = close.iloc[i]
        v = volume.iloc[i]
        h = high.iloc[i]
        av = avg_vol.iloc[i]
        rh = recent_high.iloc[i]

        bars_since_exit += 1

        if not in_trade:
            if bars_since_exit < PARAMS["cooldown"]:
                continue

            # Check if there's a recent spring alert that hasn't expired
            recent_spring = False
            for j in range(max(i - PARAMS["spring_expiry"], PARAMS["spring_vol_window"] + PARAMS["spring_lookback"]), i + 1):
                if spring_active.iloc[j]:
                    recent_spring = True
                    break

            if not recent_spring:
                continue

            # ORB-style breakout after spring: price breaks above recent high with volume
            has_volume = (v > av * PARAMS["breakout_vol_mult"]) if pd.notna(av) and av > 0 else False
            breakout = (c > rh and h > rh * (1 + PARAMS["breakout_min_range"]))

            if breakout and has_volume:
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
                bars_since_exit = 0

    return entries, exits
