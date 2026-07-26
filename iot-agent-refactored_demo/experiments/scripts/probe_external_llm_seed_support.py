from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.planners.agent_planner import ExternalLLMClient
from experiments.trace.writer import TraceWriter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results-root",
        default=str(REPO_ROOT / "experiments" / "results" / "seed_probe"),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--probe-seed", type=int, default=1001)
    args = parser.parse_args()

    client = ExternalLLMClient()
    probe = client.probe_seed_support(probe_seed=args.probe_seed)
    report = {
        "run_id": args.run_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "provider": client.provider,
        "model": client.model,
        "transport": client._transport,
        **probe,
    }
    writer = TraceWriter(args.results_root)
    relative = f"reports/{args.run_id}/external_llm_seed_probe.json"
    path = writer.write_json(relative, report)
    print(path)


if __name__ == "__main__":
    main()
