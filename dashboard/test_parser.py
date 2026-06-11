#!/home/ubuntu/trading-dashboard/venv/bin/python3
"""Test trade parser"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path.home() / "trading-dashboard"))
from main import parse_trades_from_logs

trades = parse_trades_from_logs(Path.home() / "trading-bot-hermes")
print(f"Total trades: {len(trades)}")
with_pnl = [t for t in trades if t.get("pnl") is not None]
print(f"Trades with PnL: {len(with_pnl)}")
wins = [t for t in with_pnl if t["pnl"] > 0]
losses = [t for t in with_pnl if t["pnl"] < 0]
print(f"Wins: {len(wins)}, Losses: {len(losses)}")
for t in wins[:5]:
    print(f"  WIN: {t['date']} {t['symbol']} {t['side']} PnL: ${t['pnl']}")
for t in losses[:5]:
    print(f"  LOSS: {t['date']} {t['symbol']} {t['side']} PnL: ${t['pnl']}")
