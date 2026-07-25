from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    reports_root = REPO_ROOT / "experiments" / "results" / "reports"
    rows = []
    for manifest in sorted(reports_root.rglob("manifest.json")):
        data = _load_json(manifest)
        rows.append(
            {
                "manifest_path": str(manifest.relative_to(REPO_ROOT)),
                "run_id": data.get("run_id"),
                "system_id": data.get("system_id"),
                "planner_mode": data.get("planner_mode"),
                "world_version": data.get("world_version"),
                "system_policy_version": data.get("system_policy_version"),
                "scenario_count": data.get("scenario_count"),
                "metrics_file": data.get("metrics_file"),
                "per_scenario_file": data.get("per_scenario_file"),
            }
        )
    out_path = reports_root / "dev" / "run_index.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
