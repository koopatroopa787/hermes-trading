#!/bin/bash
set -e
source ~/trading-bot-hermes/venv/bin/activate
cd ~/trading-bot-hermes

echo "=== Hermes Evolution Cycle $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

echo "--- Step 1: Screen for candidates ---"
python3 screener/screener.py

echo "--- Step 2: Generate mutants from champion ---"
python3 mutator.py mutate 3

echo "--- Step 3: Backtest all candidates ---"
for f in strategies/candidates/*.py; do
    bt="${f%.py}_backtest.json"
    if [ ! -f "$bt" ]; then
        echo "  backtesting: $f"
        python3 backtester.py "$f" || echo "  FAILED: $f"
    else
        echo "  skipping (already tested): $f"
    fi
done

echo "--- Step 4: Promote champion ---"
python3 evolve.py promote

echo "--- Step 5: Archive losers ---"
python3 mutator.py archive 0

echo "--- Step 6: Verify champion robustness ---"
python3 backtest_analyzer.py --champion 30 || echo "  analyzer skipped (needs >= 100 bars of history)"

echo "--- Step 7: Leaderboard ---"
python3 evolve.py leaderboard

echo "=== Hermes Cycle complete ==="
