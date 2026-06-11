# Trading Bot System

**Two parallel, self-evolving algorithmic trading bots powered by Alpaca, evolutionary optimization, and real-time sentiment analysis.**

This project contains two independent trading systems that run daily on an Oracle Cloud VM, using Alpaca Paper Trading API. Both bots autonomously evolve their strategies through genetic mutation, execute trades via WebSocket streams, monitor their own performance, and communicate results through a live dashboard and WhatsApp.

---

## System Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Oracle Cloud VM                       │
│                                                          │
│  ┌───────────────┐          ┌──────────────────┐        │
│  │  Hermes Bot    │          │   WQ Brain Bot   │        │
│  │ VWAP Mean Rev  │          │  ORB Momentum v11│        │
│  │ Champion: v21  │          │  Champion: v122  │        │
│  └───────┬───────┘          └────────┬─────────┘        │
│          │                           │                   │
│          └───────────┬───────────────┘                   │
│                      │                                   │
│              ┌───────▼────────┐                          │
│              │  Alpaca Paper  │                          │
│              │  Trading API   │                          │
│              └───────┬────────┘                          │
│                      │                                   │
│              ┌───────▼────────┐                          │
│              │   Dashboard    │                          │
│              │ Flask + SVG   │                          │
│              └───────┬────────┘                          │
│                      │                                   │
│              ┌───────▼────────┐                          │
│              │ Cloudflare     │                          │
│              │ Tunnel (HTTPS) │                          │
│              └────────────────┘                          │
└─────────────────────────────────────────────────────────┘
```

---

## The Two Bots

| Feature | Hermes Bot | WQ Brain Bot |
|---------|-----------|-------------|
| **Strategy** | VWAP Mean Reversion v21 | ORB Momentum v11 (score 122.87) |
| **Core Logic** | VWAP deviation + RSI mean reversion | Opening Range Breakout + momentum |
| **Symbols** | SOXL, LABU, HOOD, UPST, SNAP, FNGU, ARM, TECL, IONQ, CRWD, AAPL | SOXL, AMD, FNGU, UPST, TECL, QBTS, ARQQ, RGTI, QUBT, AFRM |
| **Trade Style** | Intraday scalping, 1-min bars | Intraday momentum, 1-min bars |
| **Evolve Cycle** | Daily at 05:00 UTC | Daily at 06:00 UTC |
| **Launch** | 12:00 UTC | 12:00 UTC |
| **Account** | $87.6k paper | $101.8k paper |

---

## Key Architecture Decisions

1. **Evolutionary Optimization** — Both bots evolve their strategy parameters (VWAP deviation, RSI thresholds, stop/target percentages, ORB windows) through genetic mutation. New candidate strategies are backtested daily; the best replaces the champion.

2. **WebSocket Streaming** — 1-minute Alpaca bars via WebSocket for real-time signal execution. No polling, no latency, instant fills.

3. **Sentiment Override** — A daily sentiment analysis scores each symbol (0-1) and adjusts position sizing. Event catalysts (earnings, WWDC, etc.) trigger multiplier boosts.

4. **Watchdog + Auto-restart** — Each bot runs inside a tmux session with a 5-minute watchdog. If the process dies, it's automatically restarted with catch-up logic for missed bars.

5. **Daily Loss Limit** — A hard stop triggered when the day's loss exceeds 5% of the start-of-day equity. All positions are closed and the bot shuts down for the day.

6. **Time-based EOD Exit** — All positions force-close at 3:50 PM ET to avoid overnight gap risk, with stale-position cleanup on next day's open.

---

## Dashboard

A self-hosted Flask dashboard with zero CDN dependencies (pure SVG charts):
- Live equity curve chart (inline SVG)
- Daily PnL bar chart
- Per-symbol position table with PnL
- Trade history log
- Sentiment analysis panel
- Premium insights section
- Exposed via Cloudflare Tunnel for HTTPS access from any device

---

## Quick Start

```bash
# Clone
git clone <repo-url>
cd trading-bots

# Setup Hermes Bot
cd hermes-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config/broker.env.example config/broker.env
# Edit config/broker.env with your Alpaca keys
python3 live_trader_ws.py

# Setup WQ Brain Bot (separate terminal)
cd ../wq-brain-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config/broker.env.example config/broker.env
# Edit config/broker.env with your Alpaca keys
python3 live_trader_ws.py
```

---

## Project Structure

```
trading-bots/
├── README.md                     ← this file
├── ARCHITECTURE.md               ← full system design
├── SETUP.md                      ← deployment guide
├── STRATEGIES.md                 ← strategy documentation
├── hermes-bot/                   ← VWAP Mean Reversion bot
│   ├── live_trader_ws.py         ← main live trader
│   ├── backtester.py             ← backtesting engine
│   ├── backtest_analyzer.py      ← performance analysis
│   ├── evolve.py                 ← evolutionary optimization
│   ├── mutator.py                ← strategy mutation
│   ├── news_fetcher.py           ← news/sentiment feed
│   ├── check_positions.py        ← position monitoring
│   ├── close_stale.py            ← stale position cleanup
│   ├── run_evolution.sh          ← evolution cron wrapper
│   ├── config/
│   ├── screener/
│   ├── scripts/
│   └── strategies/
├── wq-brain-bot/                 ← ORB Momentum bot
│   ├── live_trader_ws.py         ← main live trader
│   ├── live_trader.py            ← legacy trader
│   ├── backtester.py
│   ├── backtest_analyzer.py
│   ├── evolve.py
│   ├── mutator.py
│   ├── news_fetcher.py
│   ├── check_positions.py
│   ├── run_evolution.sh
│   ├── config/
│   ├── screener/
│   ├── scripts/
│   └── strategies/
├── dashboard/                    ← monitoring dashboard
│   ├── main.py                   ← Flask app (port 8765)
│   ├── analyze_sentiment.py      ← daily sentiment scoring
│   ├── post_market_analysis.py   ← EOD performance summary
│   ├── collect_equity.py         ← equity logging
│   ├── static/index.html         ← frontend (pure SVG)
│   └── changelog.md
└── docs/
    ├── architecture.md
    ├── strategies.md
    ├── evolution.md
    └── deployment.md
```

---

## Dependencies

- **Alpaca Trading API** — Paper trading, real-time data, order execution
- **Python 3.10+** — Core runtime
- **pandas, numpy** — Data processing
- **Alpaca-py** — Python SDK for Alpaca
- **Flask** — Dashboard web server
- **yfinance** — Fundamental data for sentiment analysis
- **pytz** — Timezone handling (ET)
- **cloudflared** — Cloudflare Tunnel (deployment)

---

## License

MIT — Use at your own risk. This is experimental trading software. Past performance does not guarantee future results.
