"""E1 实验主调度：config × task × repeat 单元矩阵，随机交错，断点续跑。

- 每单元独立子进程（C2/C3 映射表随进程销毁 = session 生命周期）
- 随机交错避免 API 时间漂移只影响单一配置（§7.E1 流程 2）
- 已完成单元（输出含 finished_at）自动跳过
用法：
  python -m experiments.privacy_v3.runner --split dev --configs C0,C1,C2,C3 --repeats 1
  python -m experiments.privacy_v3.runner --split test --configs C0,C1,C2,C3 --repeats 3
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNIT_TIMEOUT_S = 600


def build_units(dataset: dict, split: str, configs: list[str], repeats: int) -> list[dict]:
    tasks = dataset[f"{split}_tasks"]
    units = [
        {"config": cfg, "task_id": t["task_id"], "repeat": rep}
        for rep in range(1, repeats + 1)
        for cfg in configs
        for t in tasks
    ]
    random.Random(20260925).shuffle(units)  # 固定 seed：交错顺序可复现
    return units


def unit_path(out_dir: Path, unit: dict) -> Path:
    return out_dir / f"{unit['task_id']}__{unit['config']}__r{unit['repeat']}.json"


def is_complete(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data.get("finished_at"))
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--configs", default="C0,C1,C2,C3")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个单元（调试）")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    dataset = json.loads((HERE / "data" / "svrbench_dataset.json").read_text(encoding="utf-8"))
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    out_dir = Path(args.out_dir) if args.out_dir else HERE / "results" / f"e1_{args.split}"
    out_dir.mkdir(parents=True, exist_ok=True)

    units = build_units(dataset, args.split, configs, args.repeats)
    if args.limit:
        units = units[: args.limit]

    pending = [u for u in units if not is_complete(unit_path(out_dir, u))]
    print(f"矩阵 {len(units)} 单元（{args.split} × {configs} × r{args.repeats}），"
          f"已完成 {len(units) - len(pending)}，待跑 {len(pending)}")

    failures: list[str] = []
    t0 = time.time()
    for i, unit in enumerate(pending, 1):
        out = unit_path(out_dir, unit)
        cmd = [sys.executable, "-m", "experiments.privacy_v3.run_unit",
               "--config", unit["config"], "--task-id", unit["task_id"],
               "--dataset", str(HERE / "data" / "svrbench_dataset.json"),
               "--out", str(out)]
        started = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=UNIT_TIMEOUT_S, cwd=str(HERE.parents[1]))
            if proc.returncode != 0:
                failures.append(f"{unit['task_id']}|{unit['config']}|r{unit['repeat']}: rc={proc.returncode}")
                tail = (proc.stderr or proc.stdout or "")[-300:]
                print(f"[{i}/{len(pending)}] FAIL {unit['task_id']} {unit['config']} r{unit['repeat']}\n  {tail}")
            else:
                # 读取单元结论
                verdict = ""
                try:
                    verdict = json.loads(out.read_text(encoding="utf-8")).get("tsr")
                except Exception:
                    pass
                print(f"[{i}/{len(pending)}] done {unit['task_id']} {unit['config']} "
                      f"r{unit['repeat']} tsr={verdict} ({time.time()-started:.0f}s)")
        except subprocess.TimeoutExpired:
            failures.append(f"{unit['task_id']}|{unit['config']}|r{unit['repeat']}: timeout")
            out.with_suffix(".timeout").write_text("timeout", encoding="utf-8")
            print(f"[{i}/{len(pending)}] TIMEOUT {unit['task_id']} {unit['config']} r{unit['repeat']}")

    print(f"\n完成：{len(pending) - len(failures)}/{len(pending)}，失败 {len(failures)}，"
          f"总耗时 {(time.time()-t0)/60:.1f} min")
    if failures:
        (out_dir / "_failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
