from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _generated_at_or_mtime(manifest: Path, data: dict) -> str:
    generated_at = data.get("generated_at")
    if generated_at:
        return generated_at
    return datetime.fromtimestamp(manifest.stat().st_mtime, tz=timezone.utc).isoformat()


def main():
    reports_root = REPO_ROOT / "experiments" / "results" / "reports"
    rows = []
    for manifest in sorted(reports_root.rglob("manifest.json")):
        data = _load_json(manifest)
        failed_task_ids = data.get("failed_task_ids", []) or []
        rows.append(
            {
                "manifest_path": str(manifest.relative_to(REPO_ROOT)),
                "run_id": data.get("run_id"),
                "system_id": data.get("system_id"),
                "planner_mode": data.get("planner_mode"),
                "world_version": data.get("world_version"),
                "system_policy_version": data.get("system_policy_version"),
                "scenario_count": data.get("scenario_count"),
                "generated_at": _generated_at_or_mtime(manifest, data),
                "failed_task_count": len(failed_task_ids),
                "failed_task_ids": failed_task_ids,
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
