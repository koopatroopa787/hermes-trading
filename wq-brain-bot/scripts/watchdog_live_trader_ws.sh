#!/bin/bash
# watchdog_live_trader_ws.sh — Checks if the WebSocket daemon is alive
# during market hours, restarts if dead.
set -euo pipefail

TRADING_BOT="$HOME/trading-bot"
SESSION="live_trader_ws"
LOG="$TRADING_BOT/logs/ws_watchdog.log"

mkdir -p "$TRADING_BOT/logs"

# Only check during trading hours (Mon-Fri, 9:00-16:30 ET)
HOUR=$(TZ="America/New_York" date +%H)
DAY=$(TZ="America/New_York" date +%u)  # 1=Mon, 5=Fri

if [ "$DAY" -lt 1 ] || [ "$DAY" -gt 5 ]; then
    exit 0  # weekend
fi

if [ "$HOUR" -lt 9 ] || [ "$HOUR" -ge 16 ]; then
    exit 0  # outside trading hours
fi

# Exact session check (tmux has-session uses prefix matching — would match
# "live_trader_ws_hermes" when looking for "live_trader_ws")
SESSION_MARKER="$SESSION:"
if tmux ls 2>/dev/null | grep -q "^$SESSION_MARKER"; then
    # Session exists — check if Python process is actually running inside
    if tmux capture-pane -t "$SESSION" -p | tail -3 | grep -q "Streaming live\|BAR:\|HOLDING\|BUY\|SELL"; then
        exit 0  # alive and active
    fi
    # Could be stuck — check if process is alive
    if ! tmux list-panes -t "$SESSION" -F "#{pane_pid}" | xargs -I{} pgrep -P {} python3 2>/dev/null | grep -q .; then
        echo "[$(date)] ⚠️ tmux session exists but Python process dead. Restarting..." >> "$LOG"
        tmux kill-session -t "$SESSION" 2>/dev/null || true
    else
        exit 0  # process is running, just no output recently
    fi
fi

# Restart
echo "[$(date)] 🔄 Restarting WebSocket trader..." >> "$LOG"
"$TRADING_BOT/scripts/start_live_trader_ws.sh" >> "$LOG" 2>&1
