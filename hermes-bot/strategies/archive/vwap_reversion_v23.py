"""
Strategy: vwap_reversion_v23 (mutated from vwap_reversion_v21.py)

The opposite of ORB momentum. Buys dips below VWAP with oversold RSI,
sells when price reverts to fair value.

Completely different pattern from ORB:
  - ORB: buys breakouts above opening range (buy STRENGTH)
  - This: buys dips below VWAP (buy WEAKNESS, sell into strength)
  - ORB: most active in first hour
  - This: active throughout the entire day
  - ORB: trend-following
  - This: mean-reverting (works best in choppy / range-bound mkts)
"""
import pandas as pd
import numpy as np

PARAMS = {
    "vwap_deviation": 0.000111,
    "rsi_period": 29,
    "rsi_oversold": 16,
    "rsi_overbought": 62,
    "stop_pct": 0.040216,
    "target_pct": 0.008868,
    "min_volume_pct": 0.531042,
    "warmup_bars": 19
}


def calc_vwap(bars: pd.DataFrame) -> pd.Series:
    """Calculate cumulative VWAP from intraday bars."""
    tp = (bars["high"] + bars["low"] + bars["close"]) / 3
    cum_pv = (tp * bars["volume"]).cumsum()
    cum_vol = bars["volume"].cumsum()
    return cum_pv / cum_vol


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI calculation."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def generate_signals(bars: pd.DataFrame):
    """Generate entry/exit signals for the mean reversion strategy."""
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    volume = bars["volume"]

    entries = pd.Series(False, index=bars.index)
    exits = pd.Series(False, index=bars.index)

    if len(bars) < PARAMS["warmup_bars"] + 5:
        return entries, exits

    vwap = calc_vwap(bars)
    rsi = calc_rsi(close, PARAMS["rsi_period"])
    avg_vol = volume.rolling(window=PARAMS["warmup_bars"]).mean()

    in_trade = False
    entry_price = 0.0

    for i in range(PARAMS["warmup_bars"], len(bars)):
        c = close.iloc[i]
        v = volume.iloc[i]
        vwap_i = vwap.iloc[i]
        rsi_i = rsi.iloc[i]
        avg_vol_i = avg_vol.iloc[i]

        if np.isnan(rsi_i) or np.isnan(vwap_i) or avg_vol_i == 0:
            continue

        if not in_trade:
            # Entry: price below VWAP + RSI oversold + volume confirmation
            price_vs_vwap = (c - vwap_i) / vwap_i
            volume_ok = v > avg_vol_i * PARAMS["min_volume_pct"]
            oversold = rsi_i < PARAMS["rsi_oversold"]
            below_vwap = price_vs_vwap < PARAMS["vwap_deviation"]

            if below_vwap and oversold and volume_ok:
                entries.iloc[i] = True
                in_trade = True
                entry_price = c
        else:
            # Exit conditions:
            # 1. Price crosses back above VWAP (reversion complete)
            # 2. RSI overbought (bounce overextended)
            # 3. Stop loss hit
            # 4. Take profit hit
            # 5. End of day

            above_vwap = c > vwap_i
            overbought = rsi_i > PARAMS["rsi_overbought"]
            stop_hit = c < entry_price * (1 - PARAMS["stop_pct"])
            target_hit = c > entry_price * (1 + PARAMS["target_pct"])
            end_of_day = i >= len(bars) - 30

            if above_vwap or overbought or stop_hit or target_hit or end_of_day:
                exits.iloc[i] = True
                in_trade = False
                entry_price = 0.0

    return entries, exits
