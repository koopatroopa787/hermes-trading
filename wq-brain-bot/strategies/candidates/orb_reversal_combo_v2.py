"""
Strategy: ORB + Reversal Combo v2 — ORB Priority
Same as v1 but ORB signals take priority over reversal.
Reversal only fires when there's been no ORB signal recently (not trending).

Key change: if an ORB signal is available in the current bar, use it
instead of reversal. Also: don't enter reversal if the stock has been
in a clear uptrend (recent ORB signals).
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
    "rev_stop_pct": 0.012,   # tighter stop for reversal
    "rev_target_pct": 0.01,   # smaller target (mean reversion)
    "rev_cooldown": 15,
    # Priority rules
    "orb_signal_memory": 60,  # bars to remember past ORB signals
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

    # ── Pre-compute ORB levels ──
    orb_high = high.iloc[:PARAMS["orb_minutes"]].max()
    orb_low = low.iloc[:PARAMS["orb_minutes"]].min()
    avg_vol_orb = volume.iloc[:PARAMS["orb_minutes"]].mean()

    # ── Pre-compute reversal levels ──
    avg_vol_rev = volume.rolling(window=PARAMS["rev_vol_window"], min_periods=PARAMS["rev_vol_window"]).mean()
    price_change = close.diff(PARAMS["rev_price_lookback"])

    # Track when ORB signals last fired (to prevent reversal in trending stocks)
    last_orb_bar = -999

    in_trade = False
    entry_price = 0.0
    entry_mode = ""
    bars_since_exit = 999

    for i in range(max(PARAMS["orb_minutes"], PARAMS["rev_vol_window"] + PARAMS["rev_price_lookback"]), len(bars)):
        c = close.iloc[i]
        v = volume.iloc[i]
        bars_since_exit += 1

        # Check for ORB signal
        orb_signal = (c > orb_high) and (v > avg_vol_orb * PARAMS["orb_volume_multiplier"])

        # Check for Reversal signal
        av = avg_vol_rev.iloc[i]
        pc = price_change.iloc[i]
        vol_spike = (v > av * PARAMS["rev_volume_spike"]) if pd.notna(av) and av > 0 else False
        price_dropped = (pc < -c * PARAMS["rev_reversal_entry"]) if pd.notna(pc) else False
        rev_signal = vol_spike and price_dropped

        if orb_signal:
            last_orb_bar = i

        if not in_trade:
            if bars_since_exit < PARAMS["cooldown_after_exit"]:
                continue

            # Priority: ORB first
            if orb_signal:
                entries.iloc[i] = True
                in_trade = True
                entry_price = c
                entry_mode = "orb"
            elif rev_signal:
                # Only enter reversal if no ORB signal in recent bars (not trending up)
                bars_since_orb = i - last_orb_bar
                if bars_since_orb > PARAMS["orb_signal_memory"]:
                    entries.iloc[i] = True
                    in_trade = True
                    entry_price = c
                    entry_mode = "rev"
        else:
            if entry_mode == "orb":
                stop_hit = c < entry_price * (1 - PARAMS["orb_stop_pct"])
                target_hit = c > entry_price * (1 + PARAMS["orb_target_pct"])
            else:
                stop_hit = c < entry_price * (1 - PARAMS["rev_stop_pct"])
                target_hit = c > entry_price * (1 + PARAMS["rev_target_pct"])

            end_of_day = i >= len(bars) - 30

            if stop_hit or target_hit or end_of_day:
                exits.iloc[i] = True
                in_trade = False
                entry_mode = ""
                bars_since_exit = 0

    return entries, exits
