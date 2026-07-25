from __future__ import annotations

import json
from pathlib import Path


class TraceWriter:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write_json(self, relative_path: str, payload: dict):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

