from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def result_stage() -> str:
    return os.environ.get("RESULT_STAGE", "dev").strip() or "dev"


def results_root() -> Path:
    raw = os.environ.get("RESULTS_ROOT")
    if raw:
        return Path(raw).expanduser()
    return REPO_ROOT / "experiments" / "results"


def configured_run_id(kind: str) -> str:
    return f"configured_{kind}_{result_stage()}"


def preferred_run_ids() -> set[str]:
    stage = result_stage()
    return {
        f"configured_oracle_{stage}",
        f"configured_agent_{stage}",
        f"configured_baseline_{stage}",
        f"configured_ablation_{stage}",
    }


def reports_root() -> Path:
    return results_root() / "reports" / result_stage()


def tables_root() -> Path:
    return results_root() / "tables" / result_stage()


def figures_root() -> Path:
    return results_root() / "figures" / result_stage()
