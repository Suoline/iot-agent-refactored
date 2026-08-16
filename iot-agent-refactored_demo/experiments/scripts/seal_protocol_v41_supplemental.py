from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a SHA-256 receipt for a v4.1 supplemental result root.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    excluded = {args.output.resolve()}
    files = [path for path in sorted(args.root.rglob("*")) if path.is_file() and path.resolve() not in excluded]
    receipt = {
        "protocol": "v4.1-supplemental-ingestion-20260816",
        "root": str(args.root),
        "file_count": len(files),
        "files": [{"path": str(path.relative_to(args.root)), "sha256": _digest(path)} for path in files],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
