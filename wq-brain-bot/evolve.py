import os, sys, json, glob, shutil
from datetime import datetime

STRATEGIES_DIR = os.path.expanduser("~/trading-bot/strategies")
RESULTS_DIR = os.path.expanduser("~/trading-bot/results")
CHAMPION_FILE = os.path.expanduser("~/trading-bot/config/champion.json")

def get_all_backtest_results():
    results = []
    for f in glob.glob(f"{STRATEGIES_DIR}/**/*_backtest.json", recursive=True):
        with open(f) as fp:
            data = json.load(fp)
        data["path"] = f.replace("_backtest.json", ".py")
        results.append(data)
    return results

def promote_champion():
    results = get_all_backtest_results()
    if not results:
        print("[evolve] no backtest results found yet")
        return None

    results.sort(key=lambda x: x.get("score", -999), reverse=True)
    best = results[0]

    current_champion = None
    if os.path.exists(CHAMPION_FILE):
        with open(CHAMPION_FILE) as f:
            current_champion = json.load(f)

    if current_champion and current_champion.get("score", -999) >= best["score"]:
        print(f"[evolve] current champion still best: score={current_champion['score']:.4f}")
        return current_champion

    print(f"[evolve] new champion: {best['strategy']} score={best['score']:.4f}")
    with open(CHAMPION_FILE, "w") as f:
        json.dump({
            "strategy": best["strategy"],
            "path": best["path"],
            "score": best["score"],
            "promoted_at": datetime.utcnow().isoformat(),
            "symbol_results": best.get("symbol_results", [])
        }, f, indent=2)
    return best

def print_leaderboard(top_n=10):
    results = get_all_backtest_results()
    results.sort(key=lambda x: x.get("score", -999), reverse=True)
    print(f"\n{'Rank':<5} {'Strategy':<35} {'Score':>8} {'Tested At'}")
    print("-" * 70)
    for i, r in enumerate(results[:top_n], 1):
        tested = r.get("tested_at", "")[:19]
        print(f"  {i:<4} {r['strategy']:<35} {r.get('score', 0):>8.4f}  {tested}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "promote"
    if cmd == "promote":
        promote_champion()
    elif cmd == "leaderboard":
        print_leaderboard()
