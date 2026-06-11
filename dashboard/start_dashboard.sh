#!/bin/bash
# Start the trading dashboard server and Cloudflare tunnel
source /home/ubuntu/trading-dashboard/venv/bin/activate
cd /home/ubuntu/trading-dashboard
python3 main.py &
sleep 3
/usr/local/bin/cloudflared tunnel --url http://localhost:8765 &
wait
