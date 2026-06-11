#!/bin/bash
# start_live_trader_ws_hermes.sh — Starts Hermes WebSocket daemon in tmux.
set -euo pipefail

TRADING_BOT="$HOME/trading-bot-hermes"
SESSION="live_trader_ws_hermes"
LOG="$TRADING_BOT/logs/ws_starter.log"

mkdir -p "$TRADING_BOT/logs"
echo "[$(date)] Starting Hermes WebSocket trader..." >> "$LOG"

# Kill existing session if any
tmux kill-session -t "$SESSION" 2>/dev/null || true
sleep 1

# Start fresh session
cd "$TRADING_BOT"
tmux new-session -d -s "$SESSION" \
    "source ~/trading-bot-hermes/venv/bin/activate && python3 live_trader_ws.py 2>&1 | tee -a logs/live_trader_ws_$(date +%Y%m%d).log"

sleep 2
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[$(date)] ✅ Hermes WebSocket trader started in tmux session: $SESSION" >> "$LOG"
else
    echo "[$(date)] ❌ Failed to start Hermes WebSocket trader" >> "$LOG"
fi
