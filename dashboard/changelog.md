# Trading Dashboard Change Log

## 2026-06-02 — Dashboard Launch
- **Built the Trading Dashboard** — real-time monitoring for both bots
- **Features:**
  - Live equity curve with historical data
  - Daily PnL breakdown per bot
  - Current positions with PnL tracking
  - Full trade history from all log files
  - Bot performance stats (win rate, avg win/loss, best/worst trades)
  - Position allocation chart
  - Dark theme, mobile responsive
  - Auto-refresh every 30 seconds
- **Fixed stale position cleanup bug** — short positions now correctly buy-to-cover instead of failing with "qty must be > 0"

## 2026-06-01 — Server Migration
- WQ Brain bot restarted on new server ($100,000 reset)
- Hermes bot continued running with stale positions from May 28

## 2026-05-28 — Major Fix: Stale Position & EOD Exit
- **Added time-based EOD exit** — force-sells all positions at 3:50 PM ET regardless of WebSocket state
- **Added stale overnight cleanup** — sells positions at open on new trading day
- Both fixes applied to Hermes (VWAP Mean Reversion) and WQ Brain (ORB Momentum)
