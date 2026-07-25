from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(relative: str):
    print(f"==> {relative}")
    subprocess.check_call([sys.executable, relative], cwd=REPO_ROOT)


def main():
    _run("experiments/scripts/sync_ground_truth.py")
    _run("experiments/scripts/run_configured_experiments.py")
    _run("experiments/scripts/run_configured_baselines.py")
    _run("experiments/scripts/run_configured_ablations.py")
    _run("experiments/scripts/generate_tables.py")
    _run("experiments/scripts/generate_figures.py")
    _run("experiments/scripts/generate_report.py")
    _run("experiments/scripts/generate_statistics.py")
    _run("experiments/scripts/generate_significance.py")
    _run("experiments/scripts/generate_run_index.py")
    _run("experiments/scripts/generate_run_index.py")


if __name__ == "__main__":
    main()
