from __future__ import annotations

import json
from pathlib import Path


def load_scenario(path: str | Path) -> dict:
    """Load a .yaml file that uses JSON syntax, or plain JSON."""
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def load_config(path: str | Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text)
