#!/usr/bin/env python3
"""
Post-Market Analysis: Runs at market close to evaluate both bots' daily performance.
Analyzes PnL, trade quality, and root causes. Generates insights for the next evolution cycle.
"""

import os, json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path.home()
HERMES = BASE / "trading-bot-hermes"
WQ = BASE / "trading-bot"
DATA_DIR = BASE / "trading-dashboard"

# --- Helper: Load Alpaca client from bot's env ---
def load_env_file(env_path):
    env_vars = {}
    if not env_path.exists():
        return env_vars
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                k, _, v = line.partition('=')
                env_vars[k.strip()] = v.strip()
    return env_vars

def get_bot_data(bot_dir, name, strategy, start_capital):
    """Gather all performance data for a single bot."""
    env_path = bot_dir / "config" / "broker.env"
    env_vars = load_env_file(env_path)
    
    result = {
        "name": name,
        "strategy": strategy,
        "start_capital": start_capital,
        "equity": None,
        "cash": None,
        "buying_power": None,
        "positions": [],
        "today_trades": [],
        "today_pnl": 0,
        "stats": {},
        "day_open_equity": None,
        "day_close_equity": None,
        "errors": []
    }
    
    # 1. Fetch live account data from Alpaca
    if env_vars.get("ALPACA_API_KEY"):
        try:
            from alpaca.trading.client import TradingClient
            tc = TradingClient(
                env_vars["ALPACA_API_KEY"],
                env_vars["ALPACA_SECRET_KEY"],
                paper=True
            )
            acct = tc.get_account()
            result["equity"] = round(float(acct.equity), 2)
            result["cash"] = round(float(acct.cash), 2)
            result["buying_power"] = round(float(acct.buying_power), 2)
            
            # Get positions
            positions = tc.get_all_positions()
            for p in positions:
                qty = float(p.qty)
                entry = float(p.avg_entry_price)
                curr = float(p.current_price)
                pnl = float(p.unrealized_pl)
                result["positions"].append({
                    "symbol": p.symbol,
                    "qty": abs(qty),
                    "side": "SHORT" if qty < 0 else "LONG",
                    "entry": round(entry, 2),
                    "current": round(curr, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round((curr / entry - 1) * 100, 2) if qty > 0 else round((1 - curr / entry) * 100, 2)
                })
        except Exception as e:
            result["errors"].append(f"Alpaca API: {e}")
    
    # 2. Parse today's trades from logs
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_file = bot_dir / "logs" / f"live_trader_ws_{today_str}.log"
    
    today_trades = []
    all_time_trades = []
    
    if log_file.exists():
        content = log_file.read_text()
        lines = content.split("\n")
        
        # Track equity at start and end of day
        first_equity = None
        last_equity = None
        
        for line in lines:
            # Track equity snapshots
            eq_match = re.search(r"Paper Account:\s+\$?([\d,.]+)", line)
            if eq_match:
                eq = float(eq_match.group(1).replace(",", ""))
                if first_equity is None:
                    first_equity = eq
                last_equity = eq
            
            # Find completed trades with PnL
            m = re.search(r"(🔴 SELL|🟢 BUY|🕐 EOD FORCE SELL)\s+@\s+\$?([\d.]+)\s*×\s*(-?\d+)", line)
            if m:
                action = "BUY" if "BUY" in m.group(1) else "SELL"
                price = float(m.group(2))
                qty = int(m.group(3))
                
                pnl_match = re.search(r"PnL:\s*\$?([-+]?[\d.]+)", line)
                pnl = float(pnl_match.group(1)) if pnl_match else None
                
                ts_match = re.search(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
                timestamp = ts_match.group(1) if ts_match else ""
                
                sym_match = re.search(r"(?:^|\s)([A-Z]{1,5}):", line)
                symbol = sym_match.group(1) if sym_match else "?"
                
                # Find regime info
                regime_match = re.search(r"regime=(\w+)", line)
                regime = regime_match.group(1) if regime_match else None
                
                if abs(qty) > 0 and pnl is not None and abs(pnl) < 5000:
                    trade = {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "side": action,
                        "qty": abs(qty),
                        "price": price,
                        "pnl": pnl,
                        "regime": regime
                    }
                    today_trades.append(trade)
        
        result["day_open_equity"] = first_equity
        result["day_close_equity"] = last_equity
    
    # Also read previous days for all-time stats
    for lf in sorted(bot_dir.glob("live_trader_ws_*.log")):
        if lf.name == f"live_trader_ws_{today_str}.log":
            continue  # Already processed
        content = lf.read_text()
        for line in content.split("\n"):
            m = re.search(r"(🔴 SELL|🟢 BUY|🕐 EOD FORCE SELL)\s+@\s+\$?([\d.]+)\s*×\s*(-?\d+)", line)
            if m:
                pnl_match = re.search(r"PnL:\s*\$?([-+]?[\d.]+)", line)
                pnl = float(pnl_match.group(1)) if pnl_match else None
                if pnl is not None and abs(pnl) < 5000:
                    sym_match = re.search(r"(?:^|\s)([A-Z]{1,5}):", line)
                    symbol = sym_match.group(1) if sym_match else "?"
                    all_time_trades.append({"pnl": pnl, "symbol": symbol})
    
    result["today_trades"] = today_trades
    
    # Compute today's PnL: use equity_log.json for day-open snapshot, Alpaca API for close
    today_ymd = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    equity_log = DATA_DIR / "equity_log.json"
    if equity_log.exists():
        try:
            eq_data = json.loads(equity_log.read_text())
            # Find first entry of today for this bot
            field = "hermes" if name == "Hermes" else "wq"
            day_entries = [e for e in eq_data if e.get("ts","").startswith(today_ymd) and e.get(field) is not None]
            if day_entries:
                result["day_open_equity"] = day_entries[0][field]
                result["day_close_equity"] = day_entries[-1][field]
        except:
            pass
    
    # Use Alpaca current equity as the definitive close
    if result["equity"]:
        result["day_close_equity"] = result["equity"]
    
    if result["day_open_equity"] and result["day_close_equity"]:
        result["today_pnl"] = round(result["day_close_equity"] - result["day_open_equity"], 2)
    
    # Compute today's trade-based PnL
    today_trade_pnl = sum(t["pnl"] for t in today_trades if t.get("pnl") is not None)
    result["today_trade_pnl"] = round(today_trade_pnl, 2)
    
    # Stats for today
    closed_today = [t for t in today_trades if t.get("pnl") is not None]
    wins = [t for t in closed_today if t["pnl"] > 0]
    losses = [t for t in closed_today if t["pnl"] < 0]
    result["stats"] = {
        "today_trades": len(closed_today),
        "today_wins": len(wins),
        "today_losses": len(losses),
        "today_win_rate": round(len(wins) / len(closed_today) * 100, 1) if closed_today else 0,
        "today_avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "today_avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        "today_biggest_win": round(max(t["pnl"] for t in wins), 2) if wins else 0,
        "today_biggest_loss": round(min(t["pnl"] for t in losses), 2) if losses else 0,
    }
    
    # All-time stats
    all_closed = all_time_trades + [{"pnl": t["pnl"], "symbol": t["symbol"]} for t in today_trades if t.get("pnl") is not None]
    all_wins = [t for t in all_closed if t["pnl"] > 0]
    all_losses = [t for t in all_closed if t["pnl"] < 0]
    total_pnl = sum(t["pnl"] for t in all_closed)
    result["all_time"] = {
        "total_trades": len(all_closed),
        "total_wins": len(all_wins),
        "total_losses": len(all_losses),
        "win_rate": round(len(all_wins) / len(all_closed) * 100, 1) if all_closed else 0,
        "total_pnl": round(total_pnl, 2),
        "equity_change": round((result["equity"] or 0) - start_capital, 2),
    }
    
    return result

def analyze_day(bot_result):
    """Analyze the bot's day and determine why it performed the way it did."""
    analysis = {
        "verdict": "neutral",
        "reason": "",
        "insights": [],
        "issues": [],
        "wins": [],
        "suggestions": []
    }
    
    stats = bot_result["stats"]
    today_pnl = bot_result["today_pnl"]
    today_trade_pnl = bot_result.get("today_trade_pnl", 0)
    trades = bot_result["today_trades"]
    positions = bot_result["positions"]
    
    # Determine verdict based on EQUITY change (not just trade PnL, includes open positions)
    if today_pnl > 50:
        analysis["verdict"] = "profitable"
    elif today_pnl < -50:
        analysis["verdict"] = "loss"
    else:
        analysis["verdict"] = "breakeven"
    
    # Determine how much of the PnL is from closed trades vs open positions
    open_pnl = sum(p["pnl"] for p in positions)
    closed_pnl = today_trade_pnl
    
    # Analyze winners and losses from today's trades
    wins = [t for t in trades if t.get("pnl") and t["pnl"] > 0]
    losses = [t for t in trades if t.get("pnl") and t["pnl"] < 0]
    
    if wins:
        best = max(wins, key=lambda t: t["pnl"])
        analysis["wins"].append(f"Best trade: {best['symbol']} {best['side']} @ ${best['price']} for ${best['pnl']:.2f}")
        by_symbol = {}
        for t in wins:
            by_symbol.setdefault(t["symbol"], []).append(t)
        best_sym = max(by_symbol.items(), key=lambda x: sum(t["pnl"] for t in x[1]))
        analysis["wins"].append(f"Top symbol: {best_sym[0]} — {len(best_sym[1])} wins totalling ${sum(t['pnl'] for t in best_sym[1]):.2f}")
    
    if losses:
        worst = min(losses, key=lambda t: t["pnl"])
        analysis["issues"].append(f"Worst trade: {worst['symbol']} {worst['side']} @ ${worst['price']} for ${worst['pnl']:.2f}")
    
    # Show open positions if significant
    if positions:
        for p in positions:
            if abs(p["pnl"]) > 50:
                analysis["issues"].append(f"Open position: {p['symbol']} {p['side']} {p['qty']} @ ${p['entry']}, PnL: ${p['pnl']:.2f}")
    
    # --- Root cause analysis ---
    
    # 1. Check if problem is overnight positions (carry-over gap risk)
    if abs(open_pnl) > 200 and abs(open_pnl) > abs(closed_pnl):
        analysis["reason"] = "overnight_hold"
        analysis["insights"].append(f"Most loss is from open positions (${open_pnl:.2f}) carried overnight, not day-trading") 
        analysis["suggestions"].append("Enforce EOD flat — don't carry positions overnight")
        analysis["suggestions"].append("Add trailing stop-loss to open positions to limit gap damage")
    
    # 2. Check if technical errors occurred
    if bot_result.get("errors"):
        analysis["reason"] = "technical_glitch"
        analysis["issues"].extend(bot_result["errors"])
        analysis["suggestions"].append("Fix technical error before next session")
    
    # 3. Analyze trade performance (only if there were actual trades today)
    if stats["today_trades"] >= 2:
        if analysis["verdict"] == "loss":
            avg_loss = abs(sum(t["pnl"] for t in losses)) / len(losses) if losses else 0
            avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
            
            if avg_loss > 200:
                analysis["reason"] = analysis.get("reason") or "algo_failure"
                analysis["insights"].append(f"High avg loss per trade (${avg_loss:.2f}) — algo may be entering at bad prices")
                analysis["suggestions"].append("Reduce position sizes (losses too large)")
                analysis["suggestions"].append("Review entry logic — check if algo chases momentum")
            
            if stats["today_win_rate"] < 30 and stats["today_win_rate"] > 0:
                analysis["reason"] = analysis.get("reason") or "market_condition"
                analysis["insights"].append(f"Low win rate ({stats['today_win_rate']}%) — adverse market conditions")
                analysis["suggestions"].append("Add market regime filter — skip days with strong drift")
            
            if avg_win > 0 and avg_loss > 0 and avg_loss > avg_win * 2:
                analysis["reason"] = analysis.get("reason") or "asymmetric_risk"
                analysis["insights"].append(f"Risk/reward is bad: avg win ${avg_win:.2f} vs avg loss ${abs(avg_loss):.2f}")
                analysis["suggestions"].append("Tighten stop-losses to improve risk/reward ratio")
        
        elif analysis["verdict"] == "profitable":
            if stats["today_win_rate"] > 60:
                analysis["reason"] = analysis.get("reason") or "strong_algo"
                analysis["insights"].append(f"Strong win rate ({stats['today_win_rate']}%) — strategy fits current regime")
                analysis["suggestions"].append("Consider slightly increasing position sizes")
            elif stats["today_win_rate"] > 40:
                analysis["reason"] = analysis.get("reason") or "good_risk_mgmt"
                analysis["insights"].append(f"Wins outweigh losses despite {stats['today_win_rate']}% win rate — good risk management")
            else:
                analysis["reason"] = analysis.get("reason") or "few_big_winners"
                analysis["insights"].append(f"Profitable despite low win rate — a few big winners carrying the day")
    
    # For breakeven / low activity days
    elif stats["today_trades"] < 2:
        if analysis["verdict"] != "loss" and analysis["verdict"] != "profitable":
            if positions and abs(open_pnl) < 50:
                analysis["reason"] = "no_activity"
                analysis["insights"].append("Low trading activity — algo found few opportunities today")
                analysis["suggestions"].append("Check if algo missed signals — may need wider scanning")
    
    # Overnight holding note
    if positions:
        total_unrealized = sum(p["pnl"] for p in positions)
        analysis["issues"].append(f"Carrying {len(positions)} position(s) overnight with ${total_unrealized:.2f} unrealized PnL")
    
    return analysis

def format_report(hermes, wq, h_analysis, w_analysis):
    """Format the analysis as a WhatsApp message (no markdown)."""
    today_str = datetime.now(timezone.utc).strftime("%d %B %Y")
    
    lines = []
    lines.append(f"📊 Post-Market Analysis — {today_str}")
    lines.append("")
    
    # ─── HERMES ───
    h_em = "🟢" if hermes["today_pnl"] >= 0 else "🔴"
    lines.append(f"{h_em} Hermes — VWAP Mean Reversion v3")
    lines.append(f"   Equity: ${hermes['equity']:,.2f}  |  Today: ${hermes['today_pnl']:+,.2f}")
    lines.append(f"   Trades: {hermes['stats']['today_trades']}  |  Win: {hermes['stats']['today_win_rate']}%")
    if hermes["stats"]["today_trades"] > 0:
        lines.append(f"   Avg Win: ${hermes['stats']['today_avg_win']:.2f}  |  Avg Loss: ${hermes['stats']['today_avg_loss']:.2f}")
    
    v_em = "✅" if h_analysis["verdict"] == "profitable" else ("❌" if h_analysis["verdict"] == "loss" else "➖")
    lines.append(f"   Verdict: {v_em} {h_analysis['verdict'].upper()}")
    if h_analysis["reason"]:
        lines.append(f"   Why: {h_analysis['reason'].replace('_', ' ').title()}")
    for i in h_analysis["insights"]:
        lines.append(f"   → {i}")
    for w in h_analysis["wins"]:
        lines.append(f"   {w}")
    for issue in h_analysis["issues"]:
        lines.append(f"   ⚠ {issue}")
    
    lines.append("")
    
    # ─── WQ BRAIN ───
    w_em = "🟢" if wq["today_pnl"] >= 0 else "🔴"
    lines.append(f"{w_em} WQ Brain — ORB Momentum v25")
    lines.append(f"   Equity: ${wq['equity']:,.2f}  |  Today: ${wq['today_pnl']:+,.2f}")
    lines.append(f"   Trades: {wq['stats']['today_trades']}  |  Win: {wq['stats']['today_win_rate']}%")
    if wq["stats"]["today_trades"] > 0:
        lines.append(f"   Avg Win: ${wq['stats']['today_avg_win']:.2f}  |  Avg Loss: ${wq['stats']['today_avg_loss']:.2f}")
    
    v_em = "✅" if w_analysis["verdict"] == "profitable" else ("❌" if w_analysis["verdict"] == "loss" else "➖")
    lines.append(f"   Verdict: {v_em} {w_analysis['verdict'].upper()}")
    if w_analysis["reason"]:
        lines.append(f"   Why: {w_analysis['reason'].replace('_', ' ').title()}")
    for i in w_analysis["insights"]:
        lines.append(f"   → {i}")
    for w in w_analysis["wins"]:
        lines.append(f"   {w}")
    for issue in w_analysis["issues"]:
        lines.append(f"   ⚠ {issue}")
    
    lines.append("")
    
    # ─── ALL-TIME ───
    total_equity = (hermes["equity"] or 0) + (wq["equity"] or 0)
    total_start = hermes["start_capital"] + wq["start_capital"]
    total_pnl = total_equity - total_start
    lines.append(f"💰 Total Portfolio: ${total_equity:,.2f}")
    lines.append(f"   All-time PnL: ${total_pnl:+,.2f}  (${total_start:,.2f} start)")
    
    lines.append("")
    lines.append("")
    
    # ─── SUGGESTIONS FOR NEXT VERSION ───
    lines.append("💡 Suggestions for Next Version:")
    all_suggestions = []
    seen = set()
    for s in h_analysis["suggestions"] + w_analysis["suggestions"]:
        if s not in seen:
            all_suggestions.append(s)
            seen.add(s)
    for i, s in enumerate(all_suggestions[:5], 1):
        lines.append(f"  {i}. {s}")
    
    lines.append("")
    lines.append("━━━ End of Analysis ━━━")
    
    return "\n".join(lines)

def main():
    print("[post_market] Starting analysis...", file=sys.stderr)
    
    hermes = get_bot_data(HERMES, "Hermes", "VWAP Mean Reversion v3", 100000.0)
    wq = get_bot_data(WQ, "WQ Brain", "ORB Momentum v25", 100000.0)
    
    h_analysis = analyze_day(hermes)
    w_analysis = analyze_day(wq)
    
    report = format_report(hermes, wq, h_analysis, w_analysis)
    
    # Save report for reference
    report_path = DATA_DIR / f"post_market_{datetime.now(timezone.utc).strftime('%Y%m%d')}.txt"
    report_path.write_text(report)
    print(f"[post_market] Report saved to {report_path}", file=sys.stderr)
    
    # Print the report to stdout (cron job delivery)
    print(report)
    
    # Dump raw data as JSON for debugging
    debug_path = DATA_DIR / f"post_market_{datetime.now(timezone.utc).strftime('%Y%m%d')}_raw.json"
    debug_data = {"hermes": hermes, "wq": wq, "h_analysis": h_analysis, "w_analysis": w_analysis}
    # Convert non-serializable types
    debug_path.write_text(json.dumps(debug_data, indent=2, default=str))
    
    print(f"[post_market] Done.", file=sys.stderr)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
