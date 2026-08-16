from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _revision() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _working_tree_clean() -> bool:
    return not subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True).strip()


def build_bundle(output: Path, *, matrix: Path, result_paths: list[Path], include_paths: list[Path]) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    matrix = matrix if matrix.is_absolute() else REPO_ROOT / matrix
    files = [matrix, *result_paths, *include_paths]
    hashes = []
    for path in files:
        if not path.exists():
            continue
        path = path if path.is_absolute() else REPO_ROOT / path
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes.append({"path": str(path.relative_to(REPO_ROOT)), "sha256": digest, "size": path.stat().st_size})
    manifest = {
        "bundle_version": "artifact-bundle-v1",
        "git_revision": _revision(),
        "working_tree_clean": _working_tree_clean(),
        "matrix": str(matrix.relative_to(REPO_ROOT)),
        "files": hashes,
        "rebuild_command": "PYTHONDONTWRITEBYTECODE=1 python3 experiments/scripts/generate_v4_artifacts.py --results-root experiments/results",
        "note": "Raw traces may remain in external object storage; this manifest pins every published local artifact.",
    }
    target = output / "artifact_manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include", nargs="*", type=Path, default=[])
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    print(build_bundle(args.output, matrix=args.matrix, result_paths=args.paths, include_paths=args.include))


if __name__ == "__main__":
    main()
