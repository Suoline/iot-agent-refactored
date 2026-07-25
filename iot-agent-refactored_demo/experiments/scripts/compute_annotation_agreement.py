from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ANNOTATION_ROOT = REPO_ROOT / "experiments" / "annotations" / "inter_annotator"
OUTPUT_PATH = REPO_ROOT / "experiments" / "annotations" / "annotation_agreement.json"


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


def main() -> None:
    complete = []
    pending = []
    labels_a = []
    labels_b = []
    disagreements = []
    for path in sorted(ANNOTATION_ROOT.glob("*.json")):
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
    report = {
        "status": "complete" if not pending else "pending_human_annotation",
        "scenario_count": len(complete) + len(pending),
        "completed_count": len(complete),
        "pending_count": len(pending),
        "pending_scenario_ids": pending,
        "disagreement_scenario_ids": disagreements,
        "cohen_kappa": _cohen_kappa(labels_a, labels_b),
        "unit": "scenario_full_label_exact_match",
        "warning": "kappa 仅在真实独立标注完成后计算；空占位不会生成数值。",
    }
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
