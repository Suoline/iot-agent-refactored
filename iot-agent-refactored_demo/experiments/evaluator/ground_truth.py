from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
V4_GROUND_TRUTH = REPO_ROOT / "experiments" / "annotations" / "protocol_v4" / "evaluator_ground_truth.json"


def evaluator_metadata_for_scenario(scenario_id: str) -> dict[str, Any]:
    """Load evaluator-only labels; these are never included in the planner prompt."""
    if not V4_GROUND_TRUTH.exists():
        return {}
    payload = json.loads(V4_GROUND_TRUTH.read_text(encoding="utf-8"))
    return dict(payload.get("scenarios", {}).get(scenario_id, {}))
