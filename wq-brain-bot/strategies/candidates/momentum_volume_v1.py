"""
Strategy: Momentum + Volume v1 — Volume-Confirmed Momentum
Inspired by WQ alpha d5nQ8Z32 (Fundamental + News Composite, IS Sharpe 2.45)
and zq5WQmjO (OpInc + IV Call-Put Skew, IS Sharpe 2.03)

Concept: Enter when short-term momentum aligns with volume confirmation.
Uses multiple timeframes: fast (5-min) and slow (20-min) momentum,
with volume as a confidence gate. Exit on reversal or target/stop.

Adapted for intraday 1-min bars on single stocks.
"""
import pandas as pd
import numpy as np

PARAMS = {
    "fast_period": 5,           # fast momentum window (bars)
    "slow_period": 20,          # slow momentum window (bars)
    "volume_window": 20,        # rolling volume avg window
    "volume_threshold": 1.4,    # min volume ÷ rolling avg
    "momentum_threshold": 0.002,# 0.2% min momentum to enter
    "stop_pct": 0.015,          # 1.5% stop loss
    "target_pct": 0.025,        # 2.5% profit target
    "cooldown": 10,             # bars to wait after exit
}

def generate_signals(bars: pd.DataFrame):
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    volume = bars["volume"]

    entries = pd.Series(False, index=bars.index)
    exits = pd.Series(False, index=bars.index)

    min_bars = PARAMS["slow_period"] + PARAMS["volume_window"] + PARAMS["cooldown"] + 10
    if len(bars) < min_bars:
        return entries, exits

    # Rolling average volume
    avg_vol = volume.rolling(window=PARAMS["volume_window"], min_periods=PARAMS["volume_window"]).mean()

    # Fast momentum: close change over fast_period
    fast_mom = close.pct_change(PARAMS["fast_period"])
    # Slow momentum: close change over slow_period
    slow_mom = close.pct_change(PARAMS["slow_period"])

    # Volume ratio
    vol_ratio = volume / avg_vol

    in_trade = False
    entry_price = 0.0
    entry_idx = 0
    bars_since_exit = 999

    for i in range(PARAMS["slow_period"] + PARAMS["volume_window"], len(bars)):
        c = close.iloc[i]
        v = volume.iloc[i]
        av = avg_vol.iloc[i]
        fm = fast_mom.iloc[i]
        sm = slow_mom.iloc[i]
        vr = vol_ratio.iloc[i]

        bars_since_exit += 1

        if not in_trade:
            if bars_since_exit < PARAMS["cooldown"]:
                continue

            if pd.notna(av) and av > 0:
                has_volume = vr > PARAMS["volume_threshold"]
            else:
                has_volume = False

            # Buy signal: both fast AND slow momentum positive with volume confirmation
            momentum_buy = (pd.notna(fm) and pd.notna(sm) and
                           fm > PARAMS["momentum_threshold"] and
                           sm > 0)

            if has_volume and momentum_buy:
                entries.iloc[i] = True
                in_trade = True
                entry_price = c
                entry_idx = i
        else:
            # Exit: stop loss, target hit, momentum reversal, or end of day
            stop_hit = c < entry_price * (1 - PARAMS["stop_pct"])
            target_hit = c > entry_price * (1 + PARAMS["target_pct"])

            # Momentum reversal exit: fast mom turns negative while in trade
            momentum_reversal = (pd.notna(fm) and fm < -PARAMS["momentum_threshold"])

            end_of_day = i >= len(bars) - 30

            if stop_hit or target_hit or momentum_reversal or end_of_day:
                exits.iloc[i] = True
                in_trade = False
                bars_since_exit = 0

    return entries, exits
