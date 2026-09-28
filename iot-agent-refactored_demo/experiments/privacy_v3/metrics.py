"""指标计算（实验方案 §6）：EPR / TLR / TSR / 检测器 P-R-F1 / 配对 bootstrap CI。

从 e1_* 单元结果 JSON 聚合：
- EPR = 1 - ΣL_r / ΣG_r（配置级；分字段辅助报告）
- TLR = 至少一个云请求明文泄露的任务比例
- TSR = 任务级平均成功率（同一任务多 repeat 先平均，再对任务平均，§8.1）
- 配对 bootstrap（重采样单位=任务）报告 C0-C3 / C3-C2 的 TSR、EPR 差值 95% CI
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_units(results_dir: Path) -> list[dict]:
    units = []
    for f in sorted(results_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("finished_at"):
                units.append(data)
        except Exception:
            continue
    return units


def _task_level(units: list[dict]) -> dict[tuple[str, str], dict]:
    """(config, task_id) -> 聚合（多 repeat 平均）。

    带 missing 标记的合成单元（超时/崩溃，§4.3 计为失败）计入 TSR 分母，
    但不进 TLR / G/L / token 平均——其泄漏与开销数据不存在。
    """
    bucket: dict[tuple[str, str], dict] = {}
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for u in units:
        grouped[(u["config"], u["task_id"])].append(u)
    for key, runs in grouped.items():
        scored = [r for r in runs if not r.get("missing")]
        tsr = sum(1 for r in runs if r.get("tsr")) / len(runs)
        g = sum(r.get("leak", {}).get("G", 0) for r in scored)
        l = sum(r.get("leak", {}).get("L", 0) for r in scored)
        leaked = (sum(1 for r in scored if r.get("leak", {}).get("task_leaked")) / len(scored)
                  if scored else 0.0)
        tokens_in = (sum(r.get("tokens", {}).get("prompt_tokens", 0) for r in scored) / len(scored)
                     if scored else 0.0)
        tokens_out = (sum(r.get("tokens", {}).get("completion_tokens", 0) for r in scored) / len(scored)
                      if scored else 0.0)
        calls = (sum(r.get("tokens", {}).get("calls", 0) for r in scored) / len(scored)
                 if scored else 0.0)
        bucket[key] = {"tsr": tsr, "G": g, "L": l, "tlr": leaked,
                       "tokens_in": tokens_in, "tokens_out": tokens_out, "calls": calls,
                       "n_missing": len(runs) - len(scored)}
    return bucket


def config_metrics(bucket: dict[tuple[str, str], dict], config: str,
                   category_of: dict[str, str]) -> dict[str, Any]:
    rows = {k: v for k, v in bucket.items() if k[0] == config}
    if not rows:
        return {}
    total_g = sum(v["G"] for v in rows.values())
    total_l = sum(v["L"] for v in rows.values())
    epr = 1 - (total_l / total_g) if total_g > 0 else None
    by_cat: dict[str, list[float]] = defaultdict(list)
    for (cfg, task), v in rows.items():
        by_cat[category_of.get(task, "?")].append(v["tsr"])
    return {
        "config": config,
        "tasks": len(rows),
        "EPR": round(epr, 4) if epr is not None else None,
        "TLR": round(sum(v["tlr"] for v in rows.values()) / len(rows), 4),
        "TSR": round(sum(v["tsr"] for v in rows.values()) / len(rows), 4),
        "TSR_by_category": {c: round(sum(x) / len(x), 4) for c, x in sorted(by_cat.items())},
        "tokens_in_per_task": round(sum(v["tokens_in"] for v in rows.values()) / len(rows), 1),
        "tokens_out_per_task": round(sum(v["tokens_out"] for v in rows.values()) / len(rows), 1),
        "calls_per_task": round(sum(v["calls"] for v in rows.values()) / len(rows), 2),
    }


def paired_bootstrap(bucket: dict[tuple[str, str], dict], cfg_a: str, cfg_b: str,
                     metric: str = "tsr", n_boot: int = 10000, seed: int = 20260925) -> dict:
    """任务级配对 bootstrap：cfg_a - cfg_b 的差值与 95% CI（重采样单位=任务）。"""
    tasks = sorted({k[1] for k in bucket if k[0] == cfg_a} & {k[1] for k in bucket if k[0] == cfg_b})
    if not tasks:
        return {}
    va = [bucket[(cfg_a, t)][metric] for t in tasks]
    vb = [bucket[(cfg_b, t)][metric] for t in tasks]
    observed = sum(va) / len(va) - sum(vb) / len(vb)

    if metric in ("tsr", "tlr"):
        diffs = [a - b for a, b in zip(va, vb)]
        rng = random.Random(seed)
        boots = []
        n = len(diffs)
        for _ in range(n_boot):
            sample = [diffs[rng.randrange(n)] for _ in range(n)]
            boots.append(sum(sample) / n)
        boots.sort()
    else:  # EPR：按任务 G/L 加总后整体重采样
        ga = [bucket[(cfg_a, t)]["G"] for t in tasks]
        la = [bucket[(cfg_a, t)]["L"] for t in tasks]
        gb = [bucket[(cfg_b, t)]["G"] for t in tasks]
        lb = [bucket[(cfg_b, t)]["L"] for t in tasks]
        rng = random.Random(seed)
        boots = []
        n = len(tasks)
        for _ in range(n_boot):
            idx = [rng.randrange(n) for _ in range(n)]
            Ga = sum(ga[i] for i in idx); La = sum(la[i] for i in idx)
            Gb = sum(gb[i] for i in idx); Lb = sum(lb[i] for i in idx)
            epr_a = 1 - La / Ga if Ga else 1.0
            epr_b = 1 - Lb / Gb if Gb else 1.0
            boots.append(epr_a - epr_b)
        boots.sort()
    lo, hi = boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot)]
    return {"pair": f"{cfg_a}-{cfg_b}", "metric": metric,
            "diff": round(observed, 4), "ci95": [round(lo, 4), round(hi, 4)], "n_tasks": len(tasks)}


def detector_prf(units: list[dict]) -> dict[str, Any]:
    """E2：检测器命中 vs 金标准（span 级，跨任务去重聚合）。"""
    field_of: dict[str, str] = {}
    tp = fp = fn = 0
    fn_by_field: dict[str, int] = defaultdict(int)
    for u in units:
        if u["config"] == "C0":
            continue  # C0 不运行检测器
        detections = set(u.get("audit", {}).get("detector_detections", []))
        # 金标准中真实出现过的 span（G>0）= 应检出集合
        should: set[str] = set()
        for req in u.get("audit", {}).get("sensitive_hits_by_request", []):
            for hit in req["hits"]:
                if hit["G"] > 0:
                    should.add(hit["value"])
                    field_of[hit["value"]] = hit["field_type"]
        tp += len(detections & should)
        fp += len(detections - should)
        missed = should - detections
        fn += len(missed)
        for v in missed:
            fn_by_field[field_of.get(v, "unknown")] += 1
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    return {
        "span_tp": tp, "span_fp": fp, "span_fn": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "false_negatives_by_field": dict(sorted(fn_by_field.items())),
    }


def overhead(units: list[dict]) -> dict[str, Any]:
    """E3 本地开销：encode（sanitize）延迟分档 p50/p95 + 云侧 tokens/calls。"""
    latencies: list[float] = []
    for u in units:
        latencies.extend(u.get("encode_latencies", []))
    latencies.sort()
    def pct(p):
        return round(latencies[min(int(p * len(latencies)), len(latencies) - 1)], 4) if latencies else None
    by_config: dict[str, dict] = defaultdict(lambda: {"lats": [], "n": 0})
    for u in units:
        c = by_config[u["config"]]
        c["lats"].extend(u.get("encode_latencies", []))
        c["n"] += 1
    per_config = {}
    for cfg, c in by_config.items():
        ls = sorted(c["lats"])
        if ls:
            per_config[cfg] = {
                "p50": round(ls[len(ls)//2], 4),
                "p95": round(ls[min(int(0.95*len(ls)), len(ls)-1)], 4),
                "mean": round(sum(ls)/len(ls), 4),
                "n_calls": len(ls),
            }
    return {"sanitize_latency_all": {"p50": pct(0.5), "p95": pct(0.95), "n": len(latencies)},
            "sanitize_latency_by_config": per_config}
