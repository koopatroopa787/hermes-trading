import os, sys, json, importlib.util
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

def load_env():
    env_path = os.path.expanduser("~/trading-bot/config/broker.env")
    if not os.path.exists(env_path):
        raise FileNotFoundError(f"broker.env not found at {env_path}")
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k] = v

def fetch_bars(symbols, days=60):
    load_env()
    client = StockHistoricalDataClient(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"]
    )
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        adjustment="all", feed="iex"
    )
    return client.get_stock_bars(req).df

def run_backtest_on_series(close, entries, exits, init_cash=10000, fee=0.001):
    cash = init_cash
    shares = 0.0
    entry_price = 0.0
    trades = []
    equity = []

    for i in range(len(close)):
        price = close.iloc[i]
        if entries.iloc[i] and shares == 0:
            shares = (cash * (1 - fee)) / price
            entry_price = price
            cash = 0.0
        elif exits.iloc[i] and shares > 0:
            proceeds = shares * price * (1 - fee)
            trades.append((price - entry_price) / entry_price * 100)
            cash = proceeds
            shares = 0.0
        equity.append(cash + shares * price)

    if shares > 0:
        price = close.iloc[-1]
        trades.append((price - entry_price) / entry_price * 100)
        cash = shares * price * (1 - fee)
        equity[-1] = cash

    eq = pd.Series(equity, index=close.index)
    total_return = (eq.iloc[-1] - init_cash) / init_cash * 100
    returns = pd.Series(trades)
    sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if len(returns) > 1 and returns.std() > 0 else 0
    win_rate = (len(returns[returns > 0]) / len(returns) * 100) if len(returns) > 0 else 0
    max_dd = ((eq - eq.cummax()) / eq.cummax() * 100).min()

    return {
        "total_return_pct": round(total_return, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "win_rate_pct": round(win_rate, 2),
        "num_trades": len(trades),
    }

def run_backtest(strategy_path, symbols, days=60):
    spec = importlib.util.spec_from_file_location("strategy", strategy_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    print(f"[backtester] fetching bars for {symbols}...")
    bars = fetch_bars(symbols, days)
    results = []

    for sym in symbols:
        try:
            if sym not in bars.index.get_level_values("symbol"):
                print(f"  {sym}: no data")
                continue
            sym_bars = bars.xs(sym, level="symbol")
            if len(sym_bars) < 50:
                print(f"  {sym}: insufficient data ({len(sym_bars)} bars)")
                continue
            entries, exits = mod.generate_signals(sym_bars)
            stats = run_backtest_on_series(sym_bars["close"], entries, exits)
            stats["symbol"] = sym
            results.append(stats)
            print(f"  {sym}: return={stats['total_return_pct']:+.1f}%  sharpe={stats['sharpe']:.2f}  dd={stats['max_drawdown_pct']:.1f}%  trades={stats['num_trades']}")
        except Exception as e:
            print(f"  {sym}: ERROR - {e}")
            results.append({"symbol": sym, "error": str(e)})

    valid = [r for r in results if "error" not in r and r["num_trades"] >= 3]
    score = 0
    if valid:
        avg_sharpe = sum(r["sharpe"] for r in valid) / len(valid)
        avg_return = sum(r["total_return_pct"] for r in valid) / len(valid)
        avg_dd = sum(r["max_drawdown_pct"] for r in valid) / len(valid)
        score = (avg_sharpe * 40) + (avg_return * 0.3) - (avg_dd * 0.3)

    return {
        "strategy": os.path.basename(strategy_path),
        "score": round(score, 4),
        "symbol_results": results,
        "tested_at": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    strategy_path = sys.argv[1]
    symbols_arg = sys.argv[2] if len(sys.argv) > 2 else None
    if symbols_arg:
        symbols = symbols_arg.split(",")
    else:
        with open(os.path.expanduser("~/trading-bot/screener/candidates.json")) as f:
            data = json.load(f)
        symbols = [c["symbol"] for c in data["candidates"][:10]]

    print(f"[backtester] strategy: {os.path.basename(strategy_path)}")
    print(f"[backtester] symbols:  {symbols}")
    result = run_backtest(strategy_path, symbols)
    print(f"\n[backtester] score: {result['score']:.4f}")

    out_path = strategy_path.replace(".py", "_backtest.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"[backtester] saved to {out_path}")
