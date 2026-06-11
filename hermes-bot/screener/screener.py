import os, json, requests
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest

def load_env():
    with open(os.path.expanduser("~/trading-bot-hermes/config/broker.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k] = v

def get_candidates(top_n=20):
    load_env()
    client = StockHistoricalDataClient(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"]
    )
    # Initial universe: high-volume mid/large cap
    universe = [
        "NVDA","TSLA","AAPL","MSFT","META","GOOGL","AMZN","AMD","PLTR",
        "SMCI","ARM","MSTR","COIN","SQ","SHOP","CRWD","SOFI","RIVN",
        "LCID","RBLX","SNAP","UBER","LYFT","DKNG","HOOD","AFRM","UPST",
        "IONQ","RGTI","QBTS","QUBT","ARQQ","BBAI","AI","SOUN","DUOL",
        "TQQQ","SQQQ","SPXL","FNGU","LABU","SOXL","WEBL","TECL"
    ]
    req = StockSnapshotRequest(symbol_or_symbols=universe, feed="iex")
    snapshots = client.get_stock_snapshot(req)

    scored = []
    for sym, snap in snapshots.items():
        try:
            daily_bar = snap.daily_bar
            prev_bar = snap.previous_daily_bar
            if not daily_bar or not prev_bar:
                continue
            volume = daily_bar.volume or 0
            close = daily_bar.close or 0
            prev_close = prev_bar.close or 1
            change_pct = ((close - prev_close) / prev_close) * 100
            dollar_volume = volume * close
            atr_proxy = abs(daily_bar.high - daily_bar.low) / prev_close * 100
            score = (abs(change_pct) * 0.4) + (min(dollar_volume / 1e9, 10) * 0.4) + (atr_proxy * 0.2)
            scored.append({
                "symbol": sym,
                "close": round(close, 2),
                "change_pct": round(change_pct, 2),
                "dollar_volume_B": round(dollar_volume / 1e9, 2),
                "atr_proxy": round(atr_proxy, 2),
                "score": round(score, 4)
            })
        except Exception:
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    result = scored[:top_n]

    out = os.path.expanduser("~/trading-bot-hermes/screener/candidates.json")
    with open(out, "w") as f:
        json.dump({"updated_at": datetime.utcnow().isoformat(), "candidates": result}, f, indent=2)
    print(f"[screener] {len(result)} candidates written to {out}")
    return result

if __name__ == "__main__":
    candidates = get_candidates()
    for c in candidates:
        print(f"  {c['symbol']:8s}  score={c['score']:.3f}  chg={c['change_pct']:+.1f}%  dvol=${c['dollar_volume_B']:.1f}B")
