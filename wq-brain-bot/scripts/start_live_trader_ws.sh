#!/bin/bash
# start_live_trader_ws.sh — Starts the WebSocket live trader daemon in tmux.
# Called by cron at market open. Kills previous session first.
set -euo pipefail

TRADING_BOT="$HOME/trading-bot"
SESSION="live_trader_ws"
LOG="$TRADING_BOT/logs/ws_starter.log"

mkdir -p "$TRADING_BOT/logs"
echo "[$(date)] Starting WebSocket trader..." >> "$LOG"

# Kill existing session if any (exact match — prevents killing Hermes session)
SESSION_MARKER="$SESSION:"
if tmux ls 2>/dev/null | grep -q "^$SESSION_MARKER"; then
    tmux kill-session -t "$SESSION"
fi
sleep 1

# Start fresh session
cd "$TRADING_BOT"
tmux new-session -d -s "$SESSION" \
    "source venv/bin/activate && python3 live_trader_ws.py 2>&1 | tee -a logs/live_trader_ws_$(date +%Y%m%d).log"

# Verify it started (exact match)
sleep 2
if tmux ls 2>/dev/null | grep -q "^$SESSION_MARKER"; then
    echo "[$(date)] ✅ WebSocket trader started in tmux session: $SESSION" >> "$LOG"
else
    echo "[$(date)] ❌ Failed to start WebSocket trader" >> "$LOG"
fi
