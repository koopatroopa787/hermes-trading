#!/bin/bash
# watchdog_live_trader_ws_hermes.sh — Checks if Hermes WS daemon is alive.
set -euo pipefail

TRADING_BOT="$HOME/trading-bot-hermes"
SESSION="live_trader_ws_hermes"
LOG="$TRADING_BOT/logs/ws_watchdog.log"

mkdir -p "$TRADING_BOT/logs"

HOUR=$(TZ="America/New_York" date +%H)
DAY=$(TZ="America/New_York" date +%u)

if [ "$DAY" -lt 1 ] || [ "$DAY" -gt 5 ]; then
    exit 0
fi
if [ "$HOUR" -lt 9 ] || [ "$HOUR" -ge 16 ]; then
    exit 0
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
    if tmux capture-pane -t "$SESSION" -p | tail -3 | grep -q "Streaming live\|BAR:\|HOLDING\|BUY\|SELL"; then
        exit 0
    fi
    if ! tmux list-panes -t "$SESSION" -F "#{pane_pid}" | xargs -I{} pgrep -P {} python3 2>/dev/null | grep -q .; then
        echo "[$(date)] ⚠️ Hermes tmux exists but Python dead. Restarting..." >> "$LOG"
        tmux kill-session -t "$SESSION" 2>/dev/null || true
    else
        exit 0
    fi
fi

echo "[$(date)] 🔄 Restarting Hermes WebSocket trader..." >> "$LOG"
"$TRADING_BOT/scripts/start_live_trader_ws.sh" >> "$LOG" 2>&1
