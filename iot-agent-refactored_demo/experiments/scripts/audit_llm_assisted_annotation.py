from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_FIELDS = [
    "expected_actions",
    "expected_action",
    "expected_final_state",
    "final_state_applicable",
    "expected_clarify",
    "expected_no_action",
]
RESOLUTION_TYPES = {"agreement", "schema_normalization", "semantic_adjudication"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_label(row: dict) -> str:
    payload = {field: row.get(field) for field in CORE_FIELDS}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


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


def audit(
    *,
    annotator_a_path: Path,
    annotator_b_path: Path,
    adjudicator_path: Path,
    researcher_accepted_provisional: bool,
) -> dict:
    annotator_a = _load(annotator_a_path)
    annotator_b = _load(annotator_b_path)
    adjudicator = _load(adjudicator_path)
    scenarios_a = annotator_a.get("scenarios", {})
    scenarios_b = annotator_b.get("scenarios", {})
    scenarios_c = adjudicator.get("scenarios", {})
    scenario_ids = sorted(set(scenarios_a) | set(scenarios_b) | set(scenarios_c))
    issues: list[str] = []

    if annotator_a.get("annotation_type") != "independent_llm_annotation":
        issues.append("annotator_a_type_invalid")
    if annotator_b.get("annotation_type") != "independent_llm_annotation":
        issues.append("annotator_b_type_invalid")
    if adjudicator.get("annotation_type") != "independent_llm_adjudication":
        issues.append("adjudicator_type_invalid")
    if not (set(scenarios_a) == set(scenarios_b) == set(scenarios_c)):
        issues.append("scenario_coverage_mismatch")
    if len(scenario_ids) != 13:
        issues.append(f"scenario_count_invalid:{len(scenario_ids)}")

    labels_a: list[str] = []
    labels_b: list[str] = []
    exact_agreement_count = 0
    semantic_agreement_count = 0
    resolution_counts: Counter[str] = Counter()
    final_labels: dict[str, dict] = {}
    for scenario_id in scenario_ids:
        row_a = scenarios_a.get(scenario_id, {})
        row_b = scenarios_b.get(scenario_id, {})
        row_c = scenarios_c.get(scenario_id, {})
        label_a = _canonical_label(row_a)
        label_b = _canonical_label(row_b)
        labels_a.append(label_a)
        labels_b.append(label_b)
        exact_agreement_count += int(label_a == label_b)
        resolution_type = row_c.get("resolution_type")
        resolution_counts[resolution_type] += 1
        if resolution_type not in RESOLUTION_TYPES:
            issues.append(f"resolution_type_invalid:{scenario_id}")
        if resolution_type in {"agreement", "schema_normalization"}:
            semantic_agreement_count += 1
        final_label = row_c.get("final_label")
        if not isinstance(final_label, dict) or any(field not in final_label for field in CORE_FIELDS):
            issues.append(f"final_label_incomplete:{scenario_id}")
        else:
            final_labels[scenario_id] = final_label
        if not row_c.get("rationale"):
            issues.append(f"adjudication_rationale_missing:{scenario_id}")

    scenario_count = len(scenario_ids)
    model_kappa = _cohen_kappa(labels_a, labels_b)
    status = "invalid"
    if not issues:
        status = "complete_llm_assisted" if researcher_accepted_provisional else "complete_pending_researcher_acceptance"
    return {
        "protocol": "v4",
        "status": status,
        "annotation_source": "two_independent_llm_annotators_plus_llm_adjudicator",
        "protocol_deviation": "replaces_preregistered_real_human_double_annotation_for_execution_only",
        "researcher_accepted_for_execution": bool(researcher_accepted_provisional and not issues),
        "human_annotation_complete": False,
        "human_cohen_kappa": None,
        "scenario_count": scenario_count,
        "scenario_ids": scenario_ids,
        "raw_exact_agreement_count": exact_agreement_count,
        "raw_exact_agreement_rate": exact_agreement_count / scenario_count if scenario_count else None,
        "semantic_agreement_count_after_schema_normalization": semantic_agreement_count,
        "semantic_agreement_rate_after_schema_normalization": semantic_agreement_count / scenario_count if scenario_count else None,
        "model_model_cohen_kappa": model_kappa,
        "resolution_counts": dict(sorted(resolution_counts.items())),
        "annotator_ids": [annotator_a.get("annotator_id"), annotator_b.get("annotator_id")],
        "adjudicator_id": adjudicator.get("adjudicator_id"),
        "final_labels": final_labels,
        "source_files": [
            _display_path(annotator_a_path),
            _display_path(annotator_b_path),
            _display_path(adjudicator_path),
        ],
        "claim_limit": "This report is model-model agreement, not human inter-annotator agreement. Human review remains required before claiming human Cohen's kappa.",
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit independent LLM annotations and adjudication without treating them as human labels.")
    base = REPO_ROOT / "experiments" / "annotations" / "protocol_v4" / "model_independent"
    parser.add_argument("--annotator-a", type=Path, default=base / "annotator_a.json")
    parser.add_argument("--annotator-b", type=Path, default=base / "annotator_b.json")
    parser.add_argument("--adjudicator", type=Path, default=base / "adjudicator_c.json")
    parser.add_argument("--researcher-accepted-provisional", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(
        annotator_a_path=args.annotator_a,
        annotator_b_path=args.annotator_b,
        adjudicator_path=args.adjudicator,
        researcher_accepted_provisional=args.researcher_accepted_provisional,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    if report["status"] == "invalid":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
