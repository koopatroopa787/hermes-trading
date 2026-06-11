import os, sys, json, glob, random, shutil
from datetime import datetime

CANDIDATES_DIR = os.path.expanduser("~/trading-bot-hermes/strategies/candidates")
ARCHIVE_DIR = os.path.expanduser("~/trading-bot-hermes/strategies/archive")

def load_champion():
    champion_file = os.path.expanduser("~/trading-bot-hermes/config/champion.json")
    if not os.path.exists(champion_file):
        print("[mutator] no champion found")
        return None
    with open(champion_file) as f:
        return json.load(f)

def mutate_params(params: dict, mutation_rate=0.2) -> dict:
    mutated = {}
    for k, v in params.items():
        if isinstance(v, float):
            delta = v * mutation_rate * random.uniform(-1, 1)
            mutated[k] = round(max(0.0001, v + delta), 6)
        elif isinstance(v, int):
            delta = max(1, int(v * mutation_rate))
            mutated[k] = max(1, v + random.randint(-delta, delta))
        else:
            mutated[k] = v
    return mutated

def extract_params(strategy_path: str) -> dict:
    params = {}
    with open(strategy_path) as f:
        content = f.read()
    import ast
    tree = ast.parse(content)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PARAMS":
                    params = ast.literal_eval(node.value)
    return params

def write_mutant(source_path: str, new_params: dict, version: int) -> str:
    with open(source_path) as f:
        content = f.read()

    base_name = os.path.basename(source_path).replace(".py", "")
    # Strip existing version suffix if present
    if "_v" in base_name:
        base_name = base_name.rsplit("_v", 1)[0]

    mutant_name = f"{base_name}_v{version}"
    mutant_path = os.path.join(CANDIDATES_DIR, f"{mutant_name}.py")

    # Replace PARAMS block
    import re
    params_str = "PARAMS = " + json.dumps(new_params, indent=4)
    content = re.sub(
        r'PARAMS\s*=\s*\{[^}]*\}',
        params_str,
        content,
        flags=re.DOTALL
    )

    # Update docstring strategy name
    content = re.sub(
        r'Strategy:.*',
        f'Strategy: {mutant_name} (mutated from {os.path.basename(source_path)})',
        content
    )

    with open(mutant_path, "w") as f:
        f.write(content)

    print(f"[mutator] wrote {mutant_path}")
    return mutant_path

def archive_losers(threshold=0):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    archived = []
    for bt_file in glob.glob(f"{CANDIDATES_DIR}/**/*_backtest.json", recursive=True):
        with open(bt_file) as f:
            data = json.load(f)
        if data.get("score", 0) < threshold:
            strategy_file = bt_file.replace("_backtest.json", ".py")
            for fpath in [strategy_file, bt_file]:
                if os.path.exists(fpath):
                    dest = os.path.join(ARCHIVE_DIR, os.path.basename(fpath))
                    shutil.move(fpath, dest)
                    archived.append(os.path.basename(fpath))
    if archived:
        print(f"[mutator] archived {len(archived)} files: {archived}")
    else:
        print("[mutator] no losers to archive")
    return archived

def get_next_version() -> int:
    all_strategies = glob.glob(f"{CANDIDATES_DIR}/*.py")
    versions = []
    for s in all_strategies:
        name = os.path.basename(s).replace(".py", "")
        if "_v" in name:
            try:
                versions.append(int(name.rsplit("_v", 1)[1]))
            except ValueError:
                pass
    return max(versions, default=1) + 1

def run_mutation(n_mutants=3):
    champion = load_champion()
    if not champion:
        return []

    source_path = champion["path"]
    if not os.path.exists(source_path):
        print(f"[mutator] champion path not found: {source_path}")
        return []

    params = extract_params(source_path)
    if not params:
        print(f"[mutator] could not extract PARAMS from {source_path}")
        return []

    print(f"[mutator] champion: {champion['strategy']} (score={champion['score']})")
    print(f"[mutator] base params: {params}")

    mutant_paths = []
    version = get_next_version()
    for i in range(n_mutants):
        rate = random.uniform(0.1, 0.35)
        new_params = mutate_params(params, mutation_rate=rate)
        print(f"[mutator] mutant {i+1} params (rate={rate:.2f}): {new_params}")
        path = write_mutant(source_path, new_params, version + i)
        mutant_paths.append(path)

    return mutant_paths

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "mutate"
    if cmd == "mutate":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        paths = run_mutation(n_mutants=n)
        print(f"[mutator] generated {len(paths)} mutants")
        for p in paths:
            print(f"  {p}")
    elif cmd == "archive":
        threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0
        archive_losers(threshold)
