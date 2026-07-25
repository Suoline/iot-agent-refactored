from __future__ import annotations

from datetime import datetime
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_rows() -> list[dict]:
    rows: list[dict] = []
    root = REPO_ROOT / "experiments" / "results" / "aggregated_metrics"
    for summary_path in sorted(root.rglob("metrics.summary.json")):
        parts = summary_path.parts
        run_id, system_id, planner_mode = parts[-4], parts[-3], parts[-2]
        rows.append(
            {
                "run_id": run_id,
                "system_id": system_id,
                "planner_mode": planner_mode,
                "metrics": _load_json(summary_path),
                "source": "summary",
            }
        )
    for metrics_path in sorted(root.rglob("metrics.json")):
        if metrics_path.with_name("metrics.summary.json").exists():
            continue
        parts = metrics_path.parts
        run_id, system_id, planner_mode = parts[-4], parts[-3], parts[-2]
        rows.append(
            {
                "run_id": run_id,
                "system_id": system_id,
                "planner_mode": planner_mode,
                "metrics": _load_json(metrics_path),
                "source": "single",
            }
        )
    return rows


def _metric_text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if value is None:
        return "-"
    if isinstance(value, dict):
        mean = value.get("mean")
        low = value.get("ci_low")
        high = value.get("ci_high")
        if mean is None:
            return "-"
        return f"{mean:.4f} [{low:.4f}, {high:.4f}]"
    if isinstance(value, (int, float)):
        return f"{value:.4f}"
    return str(value)


def _markdown_table(rows: list[dict], keys: list[str]) -> str:
    header = ["run_id", "system_id", "planner_mode", *keys]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in rows:
        metrics = row["metrics"]
        values = [row["run_id"], row["system_id"], row["planner_mode"]]
        values.extend(_metric_text(metrics, key) for key in keys)
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _significance_table(path: Path) -> str:
    if not path.exists():
        return "当前没有比较型统计摘要。"
    rows = _load_json(path)
    header = ["run_id", "system_id", "planner_mode", "TSR_delta_vs_ours", "WDR_delta_vs_ours", "CB_delta_vs_ours", "ECE_delta_vs_ours"]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in rows:
        metrics = row["metrics"]
        lines.append(
            "| "
            + " | ".join(
                [
                    row["run_id"],
                    row["system_id"],
                    row["planner_mode"],
                    f"{metrics.get('TSR', {}).get('delta_mean_vs_ours', 0.0):.4f}",
                    f"{metrics.get('WDR', {}).get('delta_mean_vs_ours', 0.0):.4f}",
                    f"{metrics.get('CB', {}).get('delta_mean_vs_ours', 0.0):.4f}",
                    f"{metrics.get('ECE', {}).get('delta_mean_vs_ours', 0.0):.4f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def main():
    rows = _collect_rows()
    report_path = REPO_ROOT / "docs" / "实验结果摘要.md"
    significance_path = REPO_ROOT / "experiments" / "results" / "reports" / "dev" / "significance_summary.json"
    generated_on = datetime.now().strftime("%A, %B %d, %Y")

    preferred_runs = {"configured_oracle_dev", "configured_agent_dev", "configured_baseline_dev", "configured_ablation_dev"}
    primary_rows = [row for row in rows if row["run_id"] in preferred_runs]
    appendix_rows = [row for row in rows if row["run_id"] not in preferred_runs]

    ours_rows = [row for row in primary_rows if row["system_id"] == "Ours"]
    baseline_rows = [row for row in primary_rows if row["run_id"] == "configured_baseline_dev"]
    ablation_rows = [row for row in primary_rows if row["run_id"] == "configured_ablation_dev"]
    appendix_rows = sorted(appendix_rows, key=lambda row: (row["run_id"], row["system_id"], row["planner_mode"]))

    content = [
        "# 实验结果摘要",
        "",
        f"> 生成日期：{generated_on}",
        "",
        "## 1. 说明",
        "",
        "本报告基于当前仓库 `experiments/results/aggregated_metrics/` 下已有结果自动汇总。",
        "本报告优先展示配置化主结果（`configured_*`），其余开发态/调试态运行放在附录。",
        "这些结果用于验证实验主线、baseline/ablation 配置和结果产物链是否可运行，**不是论文最终结果**。",
        "",
        "## 2. Ours 结果概览",
        "",
        _markdown_table(
            ours_rows,
            ["TSR", "WDR", "CB", "PM", "UAA", "UC", "MP", "DMR", "RRR", "Context Efficiency", "ECE"],
        ) if ours_rows else "暂无 Ours 结果。",
        "",
        "## 3. Baseline 结果概览",
        "",
        _markdown_table(
            baseline_rows,
            ["TSR", "WDR", "CB", "PM", "Context Efficiency", "ECE"],
        ) if baseline_rows else "暂无 Baseline 结果。",
        "",
        "## 4. Ablation 结果概览",
        "",
        _markdown_table(
            ablation_rows,
            ["TSR", "WDR", "CB", "UC", "MP", "DMR", "RRR", "ECE"],
        ) if ablation_rows else "暂无 Ablation 结果。",
        "",
        "## 5. 当前结论",
        "",
        f"- 截至 {generated_on}，实验主线已经可以运行 `Ours / baseline / ablation` 三类系统。",
        "- `PM`、`UAA`、`maintenance_latency_ms`、`RRR` 已开始出现非零信号。",
        "- 目前结果仍属于开发态结果，主要用于验证主线、场景和统计链路是否成立。",
        "",
        "## 6. 相对 Ours 的比较摘要",
        "",
        _significance_table(significance_path),
        "",
        "## 7. 结果目录",
        "",
        f"- 聚合指标：`{REPO_ROOT / 'experiments' / 'results' / 'aggregated_metrics'}`",
        f"- 表格：`{REPO_ROOT / 'experiments' / 'results' / 'tables' / 'dev'}`",
        f"- 图表：`{REPO_ROOT / 'experiments' / 'results' / 'figures' / 'dev'}`",
        f"- 报告：`{REPO_ROOT / 'experiments' / 'results' / 'reports'}`",
        "",
        "## 8. 附录：开发态/调试态运行",
        "",
        _markdown_table(
            appendix_rows,
            ["TSR", "WDR", "CB", "PM", "UAA", "Context Efficiency", "ECE"],
        ) if appendix_rows else "当前没有附录结果。",
        "",
    ]
    report_path.write_text("\n".join(content), encoding="utf-8")
    print(report_path)


if __name__ == "__main__":
    main()
