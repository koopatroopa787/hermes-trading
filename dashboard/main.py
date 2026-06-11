from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import os, json, subprocess, re, glob
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

app = FastAPI(title="Trading Dashboard")

BASE = Path.home()
HERMES = BASE / "trading-bot-hermes"
WQ = BASE / "trading-bot"
DATA = BASE / "trading-dashboard"
CHANGELOG_FILE = DATA / "changelog.md"
EQUITY_FILE = DATA / "equity_log.json"

ET_TZ = timedelta(hours=-4)  # Approx EDT, will use UTC offset

def alpaca_client(bot_dir):
    """Get Alpaca client for a bot directory."""
    env_path = bot_dir / "config" / "broker.env"
    if not env_path.exists():
        return None, None
    # Read env directly to avoid load_dotenv overrides
    env_vars = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                k, _, v = line.partition('=')
                env_vars[k.strip()] = v.strip()
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    tc = TradingClient(
        env_vars.get("ALPACA_API_KEY"),
        env_vars.get("ALPACA_SECRET_KEY"),
        paper=True
    )
    return tc, (OrderSide, TimeInForce)

def parse_trades_from_logs(bot_dir):
    """Parse all trades from log files."""
    trades = []
    log_dir = bot_dir / "logs"
    if not log_dir.exists():
        return trades
    for log_file in sorted(log_dir.glob("live_trader_ws_*.log")):
        date_str = re.search(r"(\d{8})", log_file.name)
        if not date_str:
            continue
        date = date_str.group(1)
        content = log_file.read_text()
        # Find BUY/SELL lines with PnL
        for line in content.split("\n"):
            # Pattern: SELL @ $X.XX × Y (PnL: $Z.ZZ) or BUY @ $X.XX × Y
            m = re.search(r"(🟢 BUY|🔴 SELL|🕐 EOD FORCE SELL)\s+@\s+\$?([\d.]+)\s*×\s*(-?\d+)", line)
            if m:
                action = "BUY" if "BUY" in m.group(1) else "SELL"
                price = float(m.group(2))
                qty = int(m.group(3))
                pnl_match = re.search(r"PnL:\s*\$?([-+]?[\d.]+)", line)
                pnl = float(pnl_match.group(1)) if pnl_match else None
                ts_match = re.search(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
                timestamp = ts_match.group(1) if ts_match else f"{date[:4]}-{date[4:6]}-{date[6:8]} 00:00:00"
                
                # Extract symbol
                sym_match = re.search(r"(?:^|\s)([A-Z]{1,5}):", line)
                symbol = sym_match.group(1) if sym_match else "?"
                
                if abs(qty) > 0 and 0.1 < price < 50000:
                    trades.append({
                        "date": timestamp[:10],
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "side": action,
                        "qty": abs(qty),
                        "price": price,
                        "pnl": pnl,
                        "log_file": log_file.name
                    })
        
        # Also find ORDER lines
        for line in content.split("\n"):
            m = re.search(r"ORDER:\s+(SELL|BUY)\s+(\d+)\s+([A-Z]{1,5})\s+@", line)
            if m:
                side = m.group(1)
                qty = int(m.group(2))
                symbol = m.group(3)
                ts_match = re.search(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
                timestamp = ts_match.group(1) if ts_match else ""
                if timestamp:
                    # Check if already have this trade
                    existing = [t for t in trades if t["timestamp"] == timestamp and t["symbol"] == symbol]
                    if not existing:
                        trades.append({
                            "date": timestamp[:10],
                            "timestamp": timestamp,
                            "symbol": symbol,
                            "side": side,
                            "qty": qty,
                            "price": None,
                            "pnl": None
                        })
    
    trades.sort(key=lambda x: x["timestamp"], reverse=True)
    return trades

def parse_equity_from_logs(bot_dir):
    """Parse equity snapshots from logs."""
    points = []
    log_dir = bot_dir / "logs"
    if not log_dir.exists():
        return points
    for log_file in sorted(log_dir.glob("live_trader_ws_*.log")):
        for line in log_file.read_text().split("\n"):
            m = re.search(r"Paper Account:\s+\$?([\d,.]+)", line)
            if m:
                equity = float(m.group(1).replace(",", ""))
                ts_match = re.search(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
                timestamp = ts_match.group(1) if ts_match else ""
                if timestamp:
                    points.append({"timestamp": timestamp, "equity": equity})
    
    # Also load from our equity_log
    if EQUITY_FILE.exists():
        try:
            extra = json.loads(EQUITY_FILE.read_text())
            for entry in extra:
                ts = entry.get("ts", "")
                eq = entry.get("hermes", 0)
                if ts and eq:
                    points.append({"timestamp": ts, "equity": eq})
        except:
            pass
    
    # Merge and deduplicate by timestamp
    seen = set()
    unique = []
    for p in sorted(points, key=lambda x: x["timestamp"]):
        key = p["timestamp"][:16]
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique

def parse_equity_wq():
    """Parse WQ Brain equity from logs."""
    points = []
    log_dir = WQ / "logs"
    if not log_dir.exists():
        return points
    for log_file in sorted(log_dir.glob("live_trader_ws_*.log")):
        for line in log_file.read_text().split("\n"):
            m = re.search(r"Paper Account:\s+\$?([\d,.]+)", line)
            if m:
                equity = float(m.group(1).replace(",", ""))
                ts_match = re.search(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
                timestamp = ts_match.group(1) if ts_match else ""
                if timestamp:
                    points.append({"timestamp": timestamp, "equity": equity})
    if EQUITY_FILE.exists():
        try:
            extra = json.loads(EQUITY_FILE.read_text())
            for entry in extra:
                ts = entry.get("ts", "")
                eq = entry.get("wq", 0)
                if ts and eq:
                    points.append({"timestamp": ts, "equity": eq})
        except:
            pass
    seen = set()
    unique = []
    for p in sorted(points, key=lambda x: x["timestamp"]):
        key = p["timestamp"][:16]
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique

def get_live_positions(tc):
    """Get current positions from Alpaca."""
    if not tc:
        return []
    try:
        positions = tc.get_all_positions()
        result = []
        for p in positions:
            qty = float(p.qty)
            entry = float(p.avg_entry_price)
            curr = float(p.current_price)
            pnl = float(p.unrealized_pl)
            pnl_pct = float(p.unrealized_plpc) * 100
            result.append({
                "symbol": p.symbol,
                "qty": abs(qty),
                "side": "SHORT" if qty < 0 else "LONG",
                "entry": round(entry, 2),
                "current": round(curr, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "market_value": round(abs(qty) * curr, 2)
            })
        return result
    except:
        return []

def get_live_account(tc):
    """Get account summary."""
    if not tc:
        return {"equity": 0, "cash": 0, "buying_power": 0}
    try:
        acct = tc.get_account()
        return {
            "equity": round(float(acct.equity), 2),
            "cash": round(float(acct.cash), 2),
            "buying_power": round(float(acct.buying_power), 2)
        }
    except:
        return {"equity": 0, "cash": 0, "buying_power": 0}

def get_changelog():
    if CHANGELOG_FILE.exists():
        return CHANGELOG_FILE.read_text()
    return "No changelog yet."

def get_news():
    """Read sentiment file."""
    sentiment_files = [
        (WQ / "config" / "sentiment_override.json", "WQ Brain"),
        (HERMES / "data" / "sentiment.json", "Hermes")
    ]
    results = []
    for f, name in sentiment_files:
        if f.exists():
            try:
                data = json.loads(f.read_text())
                results.append({"bot": name, "data": data})
            except:
                pass
    return results

def get_performance_stats(trades):
    """Calculate performance stats from trades."""
    if not trades:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0, "avg_win": 0, "avg_loss": 0, "largest_win": 0, "largest_loss": 0}
    # Filter out corrupt EOD force-sell retries (PnL > $5000 is impossible for these bots)
    closed = [t for t in trades if t.get("pnl") is not None and abs(t["pnl"]) < 5000]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] < 0]
    total_pnl = sum(t["pnl"] for t in closed)
    return {
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        "largest_win": round(max(t["pnl"] for t in wins), 2) if wins else 0,
        "largest_loss": round(min(t["pnl"] for t in losses), 2) if losses else 0,
    }

def daily_pnl_from_equity(equity_points):
    """Compute daily PnL from equity curve data.
    Takes the first and last equity value for each day.
    """
    if not equity_points:
        return {}
    
    # Get equity per day
    day_values = {}
    for p in equity_points:
        day = p["timestamp"][:10]
        eq = p["equity"]
        if day not in day_values:
            day_values[day] = {"first": eq, "last": eq}
        else:
            day_values[day]["last"] = eq
    
    # Not enough data yet
    return {}


@app.get("/api/hermes")
def hermes_status():
    tc, _ = alpaca_client(HERMES)
    account = get_live_account(tc)
    positions = get_live_positions(tc)
    trades = parse_trades_from_logs(HERMES)
    equity = parse_equity_from_logs(HERMES)
    
    # Get today's trades with PnL (filtered)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Compute today's PnL from equity change
    today_equity = [p for p in equity if p["timestamp"][:10] == today]
    today_pnl = 0
    if len(today_equity) >= 2:
        today_pnl = round(today_equity[-1]["equity"] - today_equity[0]["equity"], 2)
    
    stats = get_performance_stats(trades)
    
    return {
        "name": "Hermes",
        "strategy": "VWAP Mean Reversion v3",
        "account": account,
        "positions": positions,
        "positions_count": len(positions),
        "recent_trades": trades[:20],
        "today_pnl": today_pnl,
        "equity_curve": equity[-200:],
        "stats": stats,
    }

@app.get("/api/wq")
def wq_status():
    tc, _ = alpaca_client(WQ)
    account = get_live_account(tc)
    positions = get_live_positions(tc)
    trades = parse_trades_from_logs(WQ)
    equity = parse_equity_wq()
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Compute today's PnL from equity change
    today_equity = [p for p in equity if p["timestamp"][:10] == today]
    today_pnl = 0
    if len(today_equity) >= 2:
        today_pnl = round(today_equity[-1]["equity"] - today_equity[0]["equity"], 2)
    
    stats = get_performance_stats(trades)
    
    return {
        "name": "WQ Brain",
        "strategy": "ORB Momentum v25",
        "account": account,
        "positions": positions,
        "positions_count": len(positions),
        "recent_trades": trades[:20],
        "today_pnl": today_pnl,
        "equity_curve": equity[-200:],
        "stats": stats,
    }

@app.get("/api/summary")
def summary():
    tc_h, _ = alpaca_client(HERMES)
    tc_w, _ = alpaca_client(WQ)
    acct_h = get_live_account(tc_h)
    acct_w = get_live_account(tc_w)
    
    total_equity = round(acct_h["equity"] + acct_w["equity"], 2)
    
    # Starting capital
    hermes_start = 100000.0
    wq_start = 100000.0
    total_pnl = round(total_equity - hermes_start - wq_start, 2)
    
    return {
        "total_equity": total_equity,
        "total_cash": round(acct_h["cash"] + acct_w["cash"], 2),
        "total_pnl_since_inception": total_pnl,
        "hermes_equity": acct_h["equity"],
        "wq_equity": acct_w["equity"],
        "hermes_start": hermes_start,
        "wq_start": wq_start,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/api/changelog")
def changelog():
    return {"markdown": get_changelog()}

@app.get("/api/news")
def news():
    return get_news()

@app.get("/api/alltrades")
def all_trades():
    h_trades = parse_trades_from_logs(HERMES)
    w_trades = parse_trades_from_logs(WQ)
    for t in h_trades:
        t["bot"] = "Hermes"
    for t in w_trades:
        t["bot"] = "WQ Brain"
    combined = sorted(h_trades + w_trades, key=lambda x: x["timestamp"], reverse=True)
    return combined[:100]

@app.get("/api/hermes/trades")
def hermes_trades():
    return parse_trades_from_logs(HERMES)

@app.get("/api/wq/trades")
def wq_trades():
    return parse_trades_from_logs(WQ)

@app.get("/api/hermes/equity")
def hermes_equity():
    return parse_equity_from_logs(HERMES)

@app.get("/api/wq/equity")
def wq_equity():
    return parse_equity_wq()

# Serve the static frontend
app.mount("/", StaticFiles(directory=str(DATA / "static"), html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8765, reload=False)
