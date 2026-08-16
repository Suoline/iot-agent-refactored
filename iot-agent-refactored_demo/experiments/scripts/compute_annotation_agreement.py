from __future__ import annotations

from collections import Counter
import json
import argparse
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _canonical_label(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cohen_kappa(labels_a: list[str], labels_b: list[str]) -> float | None:
    if not labels_a or len(labels_a) != len(labels_b):
        return None
    observed = sum(a == b for a, b in zip(labels_a, labels_b)) / len(labels_a)
    counts_a = Counter(labels_a)
    counts_b = Counter(labels_b)
    categories = set(counts_a) | set(counts_b)
    expected = sum(
        (counts_a[category] / len(labels_a)) * (counts_b[category] / len(labels_b))
        for category in categories
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else None
    return (observed - expected) / (1.0 - expected)


def _adjudication_complete(payload: dict) -> bool:
    adjudication = payload.get("adjudication") or {}
    return (
        adjudication.get("status") in {"complete", "adjudicated", "resolved"}
        and bool(adjudication.get("adjudicator_id"))
        and adjudication.get("final_label") is not None
        and bool(adjudication.get("rationale"))
    )


def compute(annotation_root: Path, output_path: Path) -> dict:
    complete = []
    pending = []
    labels_a = []
    labels_b = []
    disagreements = []
    adjudicated = []
    pending_adjudication = []
    for path in sorted(annotation_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        scenario_id = payload["scenario_id"]
        if payload.get("annotator_a") is None or payload.get("annotator_b") is None:
            pending.append(scenario_id)
            continue
        complete.append(scenario_id)
        label_a = _canonical_label(payload["annotator_a"])
        label_b = _canonical_label(payload["annotator_b"])
        labels_a.append(label_a)
        labels_b.append(label_b)
        if label_a != label_b:
            disagreements.append(scenario_id)
            if _adjudication_complete(payload):
                adjudicated.append(scenario_id)
            else:
                pending_adjudication.append(scenario_id)
    if pending:
        status = "pending_human_annotation"
    elif pending_adjudication:
        status = "pending_adjudication"
    else:
        status = "complete"
    report = {
        "status": status,
        "scenario_count": len(complete) + len(pending),
        "completed_count": len(complete),
        "pending_count": len(pending),
        "pending_scenario_ids": pending,
        "disagreement_scenario_ids": disagreements,
        "cohen_kappa": _cohen_kappa(labels_a, labels_b),
        "adjudication_required_count": len(pending_adjudication),
        "adjudicated_count": len(adjudicated),
        "adjudicated_scenario_ids": adjudicated,
        "pending_adjudication_scenario_ids": pending_adjudication,
        "missing_value_policy": "缺失独立标注不进入 kappa 分母，也不得用自动标签填补。",
        "unit": "scenario_full_label_exact_match",
        "warning": "kappa 仅由真实独立标注计算；分歧必须由第三方完成裁决后才允许状态为 complete。",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotation-root", type=Path, default=REPO_ROOT / "experiments" / "annotations" / "inter_annotator")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "experiments" / "annotations" / "annotation_agreement.json")
    args = parser.parse_args()
    compute(args.annotation_root, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
