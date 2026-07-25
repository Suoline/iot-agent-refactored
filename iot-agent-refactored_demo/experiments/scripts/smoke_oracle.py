from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.runner.batch_run import run_batch


def main():
    root = Path(__file__).resolve().parents[2]
    scenario = root / "experiments" / "scenarios" / "category_a" / "A1.yaml"
    result = run_batch([scenario], seed=1001, results_root=root / "experiments" / "results")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
