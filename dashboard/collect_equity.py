#!/usr/bin/env python3
"""Equity data collector — runs via cron during market hours.
Logs both bot's account equity to a JSON file every 5 minutes.
"""
import os, json, sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path.home()
DATA_FILE = BASE / "trading-dashboard" / "equity_log.json"

def load_env_file(env_path):
    """Load a .env file manually."""
    if not env_path.exists():
        return False
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                k, _, v = line.partition('=')
                os.environ[k.strip()] = v.strip()
    return True

def get_equity(env_path):
    """Fetch account equity from Alpaca."""
    try:
        if not load_env_file(env_path):
            return None
        from alpaca.trading.client import TradingClient
        tc = TradingClient(
            os.environ.get("ALPACA_API_KEY"),
            os.environ.get("ALPACA_SECRET_KEY"),
            paper=True
        )
        acct = tc.get_account()
        return round(float(acct.equity), 2)
    except Exception as e:
        print(f"  Error: {e}", file=sys.stderr)
        return None

def main():
    hermes_env = BASE / "trading-bot-hermes" / "config" / "broker.env"
    wq_env = BASE / "trading-bot" / "config" / "broker.env"
    
    hermes_eq = get_equity(hermes_env)
    wq_eq = get_equity(wq_env)
    
    if hermes_eq is None and wq_eq is None:
        print("Both accounts failed — skipping")
        sys.exit(1)
    
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "hermes": hermes_eq,
        "wq": wq_eq
    }
    
    # Append to log
    data = []
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text())
        except:
            pass
    
    data.append(entry)
    
    # Keep last 1000 entries
    if len(data) > 1000:
        data = data[-1000:]
    
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2))
    print(f"Logged: Hermes={hermes_eq}, WQ={wq_eq}")

if __name__ == "__main__":
    main()
