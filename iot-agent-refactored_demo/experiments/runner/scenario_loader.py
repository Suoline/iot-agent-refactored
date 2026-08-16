from __future__ import annotations

import json
from pathlib import Path


def iter_scenario_paths(root: str | Path, *, protocol: str = "legacy") -> list[Path]:
    """Return an explicit scenario set; never mix legacy and v4 assets."""
    root = Path(root)
    if protocol == "legacy":
        roots = [root / f"category_{letter}" for letter in "abcdefgh"]
    elif protocol == "v4":
        roots = [root / "protocol_v4"]
    else:
        raise ValueError(f"unknown scenario protocol: {protocol}")
    return sorted(path for scenario_root in roots for path in scenario_root.glob("*.yaml"))


def load_scenario(path: str | Path) -> dict:
    """Load a .yaml file that uses JSON syntax, or plain JSON."""
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def load_config(path: str | Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text)
