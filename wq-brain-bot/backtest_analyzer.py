#!/usr/bin/env python3
"""
backtest_analyzer.py — 4-pillar robustness analysis for trading strategies.

Extends the basic backtester with:
  1. Parameter Sensitivity Analysis  — detect overfitting cliffs
  2. Walk-Forward Optimization        — test on unseen time windows
  3. Stress Testing                   — slippage, delays, fee spikes
  4. Monte Carlo Simulations          — worst-case drawdown range

Usage:
  python3 backtest_analyzer.py <strategy.py> [SYMBOL1,SYMBOL2] [days=60]
  python3 backtest_analyzer.py --champion   # auto-analyze the current champion
"""

import os, sys, json, importlib.util
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import warnings

# Suppress pandas FutureWarning about fillna downcasting
warnings.filterwarnings("ignore", category=FutureWarning, module="backtest_analyzer")

# Reuse data fetching from the existing backtester
from backtester import fetch_bars, run_backtest_on_series

TRADING_BOT = os.path.expanduser("~/trading-bot")


# ── Helpers ──────────────────────────────────────────────────────────────

def load_strategy(strategy_path):
    """Load a strategy module fresh (avoids import caching issues)."""
    spec = importlib.util.spec_from_file_location(
        "strategy_" + str(abs(hash(strategy_path))), strategy_path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_symbols(symbols_arg=None):
    """Resolve symbols from CLI arg or screener candidates."""
    if symbols_arg:
        return symbols_arg.split(",")
    candidates_path = os.path.join(TRADING_BOT, "screener", "candidates.json")
    with open(candidates_path) as f:
        data = json.load(f)
    return [c["symbol"] for c in data["candidates"][:10]]


# ── Pillar 1: Parameter Sensitivity Analysis ─────────────────────────────

def param_sensitivity_analysis(strategy_path, bars, fee=0.001):
    """
    Vary every numeric PARAM across ±20% and measure the performance surface.

    Returns:
      param_map: {param_name: {values: [...], smoothness_score, stable_region_pct}}
      avg_smoothness: 0-1 score (higher = smoother = better)

    A robust strategy shows gradual performance changes across the range.
    Sharp cliffs or single-spike peaks = overfitting to historical noise.
    """
    mod = load_strategy(strategy_path)
    close = bars["close"]
    params = dict(mod.PARAMS)
    results = {}

    for param_name, base_val in params.items():
        if not isinstance(base_val, (int, float)):
            continue

        # Build a range: 5 steps across ±20%
        if isinstance(base_val, int) and base_val >= 3:
            step = max(1, int(base_val * 0.05))
            low = max(1, int(base_val * 0.80))
            high = int(base_val * 1.20)
            values = list(range(low, high + 1, max(1, step)))
            if len(values) < 3:
                values = [low, base_val, high]
        elif isinstance(base_val, float):
            step = base_val * 0.05
            low = max(0.0001, base_val * 0.80)
            high = base_val * 1.20
            values = [round(low + i * (high - low) / 4, 6) for i in range(5)]
        else:
            continue

        param_results = []
        original = params[param_name]
        for val in values:
            mod.PARAMS[param_name] = val
            entries, exits = mod.generate_signals(bars)
            stats = run_backtest_on_series(close, entries, exits, fee=fee)
            param_results.append({
                "value": val,
                "return_pct": stats["total_return_pct"],
                "sharpe": stats["sharpe"],
                "drawdown_pct": stats["max_drawdown_pct"],
                "num_trades": stats["num_trades"]
            })
        mod.PARAMS[param_name] = original

        # Smoothness: low variance in deltas between adjacent param values
        returns = [r["return_pct"] for r in param_results]
        if len(returns) > 2:
            deltas = [abs(returns[i+1] - returns[i]) for i in range(len(returns)-1)]
            smoothness = 1.0 / (1.0 + float(np.std(deltas)))
        else:
            smoothness = 1.0

        stable_pct = (sum(1 for r in returns if r > 0) / len(returns) * 100) if returns else 0

        results[param_name] = {
            "values": param_results,
            "smoothness_score": round(smoothness, 4),
            "stable_region_pct": round(stable_pct, 1)
        }

    avg_smoothness = float(np.mean([v["smoothness_score"] for v in results.values()])) if results else 0.0
    return results, round(avg_smoothness, 4)


# ── Pillar 2: Walk-Forward Optimization ──────────────────────────────────

def walk_forward_analysis(strategy_path, bars, n_splits=5, fee=0.001):
    """
    Rolling time-window validation: test strategy's fixed PARAMS on
    each unseen forward window.

    Unlike traditional WFO, we do NOT re-optimize per window — we test
    the EXACT PARAMS the mutation produced. This answers: "would this
    strategy have worked if deployed N days ago?"

    Returns None if insufficient data.
    """
    mod = load_strategy(strategy_path)
    n = len(bars)
    if n < 100 or n_splits < 2:
        return None

    test_size = n // (n_splits + 1)
    oos_results = []

    for fold in range(1, n_splits + 1):
        split_idx = fold * test_size
        if split_idx >= n - test_size:
            break

        test_bars = bars.iloc[split_idx:split_idx + test_size]
        if len(test_bars) < 20:
            continue

        entries, exits = mod.generate_signals(test_bars)
        stats = run_backtest_on_series(test_bars["close"], entries, exits, fee=fee)
        stats["fold"] = fold
        oos_results.append(stats)

    if not oos_results:
        return None

    oos_returns = [r["total_return_pct"] for r in oos_results]
    avg_return = float(np.mean(oos_returns))
    avg_sharpe = float(np.mean([r["sharpe"] for r in oos_results]))
    avg_dd = float(np.mean([r["max_drawdown_pct"] for r in oos_results]))
    return_std = float(np.std(oos_returns)) if len(oos_returns) > 1 else 0.0
    # Consistency: high std = inconsistent across time = risky
    consistency = 1.0 / (1.0 + return_std) if return_std > 0 else 1.0
    positive_folds_pct = (sum(1 for r in oos_returns if r > 0) / len(oos_returns) * 100)

    return {
        "avg_oos_return_pct": round(avg_return, 2),
        "avg_oos_sharpe": round(avg_sharpe, 3),
        "avg_oos_drawdown_pct": round(avg_dd, 2),
        "oos_return_std": round(return_std, 4),
        "consistency_score": round(consistency, 4),
        "positive_folds_pct": round(positive_folds_pct, 1),
        "n_folds": len(oos_results),
        "fold_results": oos_results
    }


# ── Pillar 3: Stress Testing ─────────────────────────────────────────────

def _run_stress_scenario(mod, bars, close, fee_mult, delay_bars, slippage_pct):
    """Run one stress scenario: apply slippage multiplier to fees + delay."""
    adjusted_fee = 0.001 * fee_mult
    _ = slippage_pct  # slipped fee is approximated via adjusted_fee here

    entries, exits = mod.generate_signals(bars)

    if delay_bars > 0:
        # Delay entries: signal fires, but fill happens N bars later
        entries_delayed = entries.shift(delay_bars).infer_objects(copy=False).fillna(False)
        stats = run_backtest_on_series(close, entries_delayed, exits, fee=adjusted_fee)
    else:
        stats = run_backtest_on_series(close, entries, exits, fee=adjusted_fee)

    return stats


def stress_test_strategy(strategy_path, bars, fee=0.001):
    """
    Run the strategy through 5 stress scenarios:
      - normal:        baseline (1x fee, 0 delay, 1x slippage)
      - high_slippage: 5x fee (approximating extreme slippage)
      - execution_delay: fill trades 3 bars after signal
      - high_fees:     3x commission
      - worst_case:    3x fees + 5 bar delay

    Survival = positive total return under the scenario.
    Returns dict with per-scenario results + overall survival rate.
    """
    mod = load_strategy(strategy_path)
    close = bars["close"]

    scenarios = {
        "normal":          {"fee_mult": 1.0, "delay_bars": 0},
        "high_slippage":   {"fee_mult": 5.0, "delay_bars": 0},
        "execution_delay": {"fee_mult": 1.0, "delay_bars": 3},
        "high_fees":       {"fee_mult": 3.0, "delay_bars": 0},
        "worst_case":      {"fee_mult": 3.0, "delay_bars": 5},
    }

    results = {}
    for name, cfg in scenarios.items():
        stats = _run_stress_scenario(
            mod, bars, close,
            fee_mult=cfg["fee_mult"],
            delay_bars=cfg["delay_bars"],
            slippage_pct=fee * cfg.get("slippage_mult", 1.0)
        )
        results[name] = {
            "return_pct": stats["total_return_pct"],
            "sharpe": stats["sharpe"],
            "max_drawdown_pct": stats["max_drawdown_pct"],
            "num_trades": stats["num_trades"],
            "survived": bool(stats["total_return_pct"] > 0)
        }

    survival_rate = sum(1 for s in results.values() if s["survived"]) / len(results) * 100
    return {
        "scenarios": results,
        "survival_rate_pct": round(survival_rate, 1)
    }


# ── Pillar 4: Monte Carlo Simulations ────────────────────────────────────

def monte_carlo_analysis(strategy_path, bars, n_simulations=1000, fee=0.001):
    """
    Shuffle the sequence of historical trades 1000x to find the strategy's
    true risk envelope.

    Key outputs:
      - expected_return_pct:        mean of all simulations
      - worst_case_return_pct:      worst 1-in-1000 universe
      - 95th_percentile_return_pct: the bottom 5% outcome (reality check)
      - 90th_percentile_drawdown_pct: drawdown you should plan for
      - expected_cvar_pct:          average of worst 5% drawdowns

    Returns None if fewer than 5 trades found.
    """
    mod = load_strategy(strategy_path)
    close = bars["close"]
    entries, exits = mod.generate_signals(bars)

    # Extract individual trade returns
    cash = 10_000.0
    shares = 0.0
    entry_price = 0.0
    trades = []

    for i in range(len(close)):
        price = close.iloc[i]
        if entries.iloc[i] and shares == 0:
            shares = (cash * (1 - fee)) / price
            entry_price = price
            cash = 0.0
        elif exits.iloc[i] and shares > 0:
            proceeds = shares * price * (1 - fee)
            trades.append((price - entry_price) / entry_price)
            cash = proceeds
            shares = 0.0

    if shares > 0:  # Close last position
        price = close.iloc[-1]
        trades.append((price - entry_price) / entry_price)

    if len(trades) < 5:
        return None

    trades = np.array(trades)
    init_val = 10_000.0
    all_final_values = []
    all_drawdowns = []

    for _ in range(n_simulations):
        shuffled = np.random.choice(trades, size=len(trades), replace=True)
        equity = init_val
        peak = init_val
        curve_dds = []

        for t in shuffled:
            equity *= (1 + t)
            peak = max(peak, equity)
            curve_dds.append((equity - peak) / peak)

        all_final_values.append(equity)
        all_drawdowns.append(min(curve_dds) if curve_dds else 0.0)

    fv = np.array(all_final_values)
    dds = np.array(all_drawdowns)

    # Conditional VaR: avg of worst 5% drawdowns
    dd_threshold = np.percentile(dds, 5) if len(dds) > 0 else 0.0
    cvar = float(np.mean(dds[dds <= dd_threshold])) if np.any(dds <= dd_threshold) else 0.0

    return {
        "n_simulations": n_simulations,
        "n_actual_trades": len(trades),
        "expected_return_pct": round(float((np.mean(fv) - init_val) / init_val * 100), 2),
        "median_return_pct": round(float((np.median(fv) - init_val) / init_val * 100), 2),
        "best_case_return_pct": round(float((np.max(fv) - init_val) / init_val * 100), 2),
        "worst_case_return_pct": round(float((np.min(fv) - init_val) / init_val * 100), 2),
        "95th_percentile_return_pct": round(float((np.percentile(fv, 5) - init_val) / init_val * 100), 2),
        "expected_max_drawdown_pct": round(float(np.mean(dds) * 100), 2),
        "worst_drawdown_pct": round(float(np.min(dds) * 100), 2),
        "90th_percentile_drawdown_pct": round(float(np.percentile(dds, 10) * 100), 2),
        "expected_cvar_pct": round(float(cvar * 100), 2),
    }


# ── Composite Robustness Score ───────────────────────────────────────────

def _compute_robustness(baseline, smoothness, walk_forward, stress_test, monte_carlo):
    """
    Weighted composite: 0-100 score from all 4 pillars.

    Weights calibrated from live data (May 2026):
      - baseline:    15% (raw backtest overpromises — we proved this)
      - smoothness:  15% (weak correlation with real survival)
      - walk_forward:30% (best predictor — tests on unseen time)
      - stress_test: 20% (honest survival across 5 scenarios)
      - monte_carlo: 20% (critical for tail-risk awareness)
    """
    c = {}

    # Baseline performance (15%): sharpe-driven, capped to not dominate
    c["baseline"] = round(max(0, min(100, baseline["sharpe"] * 12 + baseline["total_return_pct"] * 0.25)), 2)

    # Parameter smoothness (15%): higher = smoother param surface
    c["smoothness"] = round(smoothness * 100, 2)

    # Walk-forward consistency (30%): the king metric
    if walk_forward:
        # % of folds with positive return = base survivability
        positivity = walk_forward["positive_folds_pct"] / 100.0
        # Consistency of returns across folds (low std = robust)
        consistency = walk_forward["consistency_score"]
        # Blend: positive folds ensure base score, consistency adds precision
        wf_score = positivity * 50 + consistency * 50
        # Bonus for strong positive OOS return
        if walk_forward["avg_oos_return_pct"] > 0:
            wf_score += min(15, walk_forward["avg_oos_return_pct"] * 1.5)
        c["walk_forward"] = round(max(0, min(100, wf_score)), 2)
    else:
        c["walk_forward"] = 0.0

    # Stress test survival (20%): raw survival % across 5 scenarios
    c["stress_test"] = stress_test["survival_rate_pct"]

    # Monte Carlo (20%): expected return vs worst-case drawdown
    if monte_carlo and monte_carlo["expected_return_pct"] > 0:
        return_score = min(40, monte_carlo["expected_return_pct"])
        # Penalize using 90th percentile drawdown (1-in-10 bad universe)
        dd_penalty = max(0, min(60, abs(monte_carlo.get("90th_percentile_drawdown_pct", 0)) * 0.4))
        c["monte_carlo"] = round(max(0, return_score + (60 - dd_penalty)), 2)
    elif monte_carlo:
        # Negative expected return: still score but harshly
        c["monte_carlo"] = round(max(0, 40 - abs(monte_carlo["expected_return_pct"])), 2)
    else:
        c["monte_carlo"] = 0.0

    total = (
        c["baseline"] * 0.15
        + c["smoothness"] * 0.15
        + c["walk_forward"] * 0.30
        + c["stress_test"] * 0.20
        + c["monte_carlo"] * 0.20
    )

    return {"total": round(total, 2), "components": c}


# ── Master Analysis Runner ───────────────────────────────────────────────

def analyze_strategy(strategy_path, symbols, days=60):
    """
    Run all 4 pillars on a strategy across multiple symbols.
    Returns a comprehensive robustness report.
    """
    strategy_name = os.path.basename(strategy_path)
    print(f"[analyzer] {strategy_name}: fetching {days}d data for {symbols}...")
    bars = fetch_bars(symbols, days)

    symbol_analyses = []

    for sym in symbols:
        try:
            if sym not in bars.index.get_level_values("symbol"):
                print(f"  {sym}: no data — skipping")
                continue
            sym_bars = bars.xs(sym, level="symbol")
            if len(sym_bars) < 100:
                print(f"  {sym}: only {len(sym_bars)} bars (< 100) — skipping")
                continue

            print(f"  {sym}: analyzing...", end=" ", flush=True)

            # 1. Baseline backtest
            mod = load_strategy(strategy_path)
            entries, exits = mod.generate_signals(sym_bars)
            baseline = run_backtest_on_series(sym_bars["close"], entries, exits)
            if baseline["num_trades"] < 3:
                print(f"too few trades ({baseline['num_trades']}) — skipping")
                continue

            # 2. Parameter sensitivity
            sens, smooth = param_sensitivity_analysis(strategy_path, sym_bars)

            # 3. Walk-forward
            wf = walk_forward_analysis(strategy_path, sym_bars)

            # 4. Stress test
            stress = stress_test_strategy(strategy_path, sym_bars)

            # 5. Monte Carlo
            mc = monte_carlo_analysis(strategy_path, sym_bars)

            robustness = _compute_robustness(baseline, smooth, wf, stress, mc)

            analysis = {
                "symbol": sym,
                "baseline": baseline,
                "parameter_sensitivity": {
                    "avg_smoothness": smooth,
                    "params": {
                        k: {"smoothness": v["smoothness_score"], "stable_pct": v["stable_region_pct"]}
                        for k, v in sens.items()
                    }
                },
                "walk_forward": wf,
                "stress_test": stress,
                "monte_carlo": mc,
                "robustness": robustness,
            }
            symbol_analyses.append(analysis)

            print(f"R={baseline['total_return_pct']:+.1f}%  "
                  f"S={smooth:.2f}  "
                  f"WF={'✓' if wf and wf['avg_oos_return_pct'] > 0 else '✗'}  "
                  f"ST={stress['survival_rate_pct']:.0f}%  "
                  f"MC={'✓' if mc and mc['expected_return_pct'] > 0 else '✗'}  "
                  f"RS={robustness['total']:.1f}")

        except Exception as e:
            print(f"  {sym}: ERROR — {e}")
            import traceback
            traceback.print_exc()

    if not symbol_analyses:
        return {"strategy": strategy_name, "error": "no valid symbol analyses"}

    # Aggregate across symbols
    rs_scores = [s["robustness"]["total"] for s in symbol_analyses]
    avg_rs = float(np.mean(rs_scores))
    avg_ret = float(np.mean([s["baseline"]["total_return_pct"] for s in symbol_analyses]))
    avg_sr = float(np.mean([s["baseline"]["sharpe"] for s in symbol_analyses]))
    avg_smooth = float(np.mean([
        s["parameter_sensitivity"]["avg_smoothness"] for s in symbol_analyses
    ]))
    avg_survival = float(np.mean([s["stress_test"]["survival_rate_pct"] for s in symbol_analyses]))

    return {
        "strategy": strategy_name,
        "robustness_score": round(avg_rs, 4),
        "avg_return_pct": round(avg_ret, 2),
        "avg_sharpe": round(avg_sr, 3),
        "avg_smoothness": round(avg_smooth, 4),
        "avg_survival_pct": round(avg_survival, 1),
        "n_symbols_analyzed": len(symbol_analyses),
        "symbol_analyses": symbol_analyses,
        "tested_at": datetime.utcnow().isoformat()
    }


# ── CLI ──────────────────────────────────────────────────────────────────

class NumpyEncoder(json.JSONEncoder):
    """Convert numpy types to native Python types for JSON serialization."""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def run_analyzer(strategy_path, symbols_arg=None, days=60):
    """CLI entrypoint: analyze a strategy and save to *_analysis.json."""
    symbols = get_symbols(symbols_arg)
    result = analyze_strategy(strategy_path, symbols, days)
    print(f"\n[analyzer] Results for {os.path.basename(strategy_path)}:")
    print(f"  Robustness Score:   {result.get('robustness_score', 'N/A')}")
    print(f"  Avg Return:         {result.get('avg_return_pct', 'N/A')}%")
    print(f"  Avg Sharpe:         {result.get('avg_sharpe', 'N/A')}")
    print(f"  Avg Smoothness:     {result.get('avg_smoothness', 'N/A')}")
    print(f"  Stress Survival:    {result.get('avg_survival_pct', 'N/A')}%")

    out_path = strategy_path.replace(".py", "_analysis.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, cls=NumpyEncoder)
    print(f"[analyzer] Full report saved to {out_path}")
    return result


def analyze_champion(days=60):
    """Auto-analyze the current champion strategy."""
    champion_path = os.path.join(TRADING_BOT, "config", "champion.json")
    if not os.path.exists(champion_path):
        print("[analyzer] No champion found.")
        return None
    with open(champion_path) as f:
        champion = json.load(f)
    strat_path = champion.get("path")
    if not strat_path or not os.path.exists(strat_path):
        print(f"[analyzer] Champion path not found: {strat_path}")
        return None
    print(f"[analyzer] Analyzing champion: {champion['strategy']} (score={champion['score']})")
    return run_analyzer(strat_path, days=days)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--champion":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        analyze_champion(days)
    elif len(sys.argv) > 1:
        strategy_path = sys.argv[1]
        symbols_arg = sys.argv[2] if len(sys.argv) > 2 else None
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 60
        run_analyzer(strategy_path, symbols_arg, days)
    else:
        print("Usage:")
        print("  python3 backtest_analyzer.py <strategy.py> [SYMBOL1,SYMBOL2] [days=60]")
        print("  python3 backtest_analyzer.py --champion [days=60]")
