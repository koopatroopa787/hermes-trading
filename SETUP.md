# Setup Guide

## Prerequisites

- **Alpaca Paper Trading Account** (free at https://app.alpaca.markets/signup)
- **Python 3.10+** on Linux (tested on Ubuntu 22.04)
- **Oracle Cloud VM** (or any Linux VPS) — 2GB RAM minimum

---

## Step 1: Clone and Configure

```bash
git clone <repo-url>
cd trading-bots

# Hermes Bot
cd hermes-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config/broker.env.example config/broker.env
nano config/broker.env   # Add your Alpaca API keys
```

## Step 2: Alpaca API Setup

1. Create an Alpaca account at https://app.alpaca.markets
2. Navigate to API Keys
3. Generate Paper Trading API keys
4. Add them to `config/broker.env`:
```ini
APCA_API_KEY_ID=PKXXXXXX
APCA_API_SECRET_KEY=XXXXXXXXXXXXXXXXXXXXXXXX
APCA_BASE_URL=https://paper-api.alpaca.markets
```

## Step 3: Run the Bot (Manual)

```bash
source venv/bin/activate
python3 live_trader_ws.py
```

## Step 4: Run with Scripts (tmux + watchdog)

```bash
# Start the live trader in a tmux session
chmod +x scripts/start_live_trader_ws.sh
./scripts/start_live_trader_ws.sh

# Start the watchdog (restarts every 5 min if process dies)
chmod +x scripts/watchdog_live_trader_ws.sh
./scripts/watchdog_live_trader_ws.sh &
```

## Step 5: Evolution (Cron)

Add to crontab (`crontab -e`):

```cron
# Hermes evolution at 05:00 UTC daily
0 5 * * * cd /path/to/hermes-bot && venv/bin/python3 evolve.py 2>&1 | tee -a logs/evolve.log

# WQ Brain evolution at 06:00 UTC daily
0 6 * * * cd /path/to/wq-brain-bot && venv/bin/python3 evolve.py 2>&1 | tee -a logs/evolve.log

# Launch traders at 12:00 UTC
0 12 * * * cd /path/to/hermes-bot && bash scripts/start_live_trader_ws.sh
0 12 * * * cd /path/to/wq-brain-bot && bash scripts/start_live_trader_ws.sh

# Watchdogs every 5 min during trading hours
*/5 12-20 * * * cd /path/to/hermes-bot && bash scripts/watchdog_live_trader_ws.sh
*/5 12-20 * * * cd /path/to/wq-brain-bot && bash scripts/watchdog_live_trader_ws.sh
```

## Step 6: Dashboard

```bash
cd dashboard
python3 -m venv venv
source venv/bin/activate
pip install flask pandas yfinance alpaca-py
python3 main.py    # Runs on port 8765
```

## Step 7: Cloudflare Tunnel (Public Access)

```bash
# Install cloudflared
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared-linux-amd64
sudo mv cloudflared-linux-amd64 /usr/local/bin/cloudflared

# Run tunnel
cloudflared tunnel --url http://localhost:8765
```

For persistent tunnel:
```bash
# Install as systemd service
sudo cloudflared service install <token>
```

## Step 8: Sentiment Analysis (Cron)

```cron
# Pre-market sentiment at 12:30 UTC
30 12 * * * cd /path/to/dashboard && venv/bin/python3 analyze_sentiment.py --delay 120
```

## Step 9: Post-market Analysis (Cron)

```cron
# EOD analysis at 20:30 UTC Mon-Fri
30 20 * * 1-5 cd /path/to/dashboard && venv/bin/python3 post_market_analysis.py
```

---

## Troubleshooting

### "No champion strategy"
Run evolution first: `python3 evolve.py`

### "Daily loss limit hit" repeatedly
The limit is 5% of start-of-day equity. If the account has significant cumulative losses, the bot may trigger on every restart until the reference equity stabilizes.

### WebSocket won't connect
- Check your Alpaca API keys
- Check your internet connection
- Alpaca paper API requires the base URL to be `https://paper-api.alpaca.markets`

### Dashboard not loading
- The v2 dashboard uses zero CDN dependencies. If the page is blank, check Flask server logs.
- Ensure port 8765 is accessible (open `iptables` and cloud firewall rules).

### Bot keeps restarting (watchdog loop)
Check the log file for the specific error. Common causes:
- Alpaca API rate limits
- Strategy file errors (syntax, missing PARAMS)
- Network connectivity issues

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `APCA_API_KEY_ID` | Alpaca API key (paper or live) |
| `APCA_API_SECRET_KEY` | Alpaca secret key |
| `APCA_BASE_URL` | Alpaca API base URL |
| `ALPACA_CREDITS` | Alpaca data subscription credits |
