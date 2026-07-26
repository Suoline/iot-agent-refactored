from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


class TraceWriter:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write_json(self, relative_path: str, payload: dict):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.tmp.",
            suffix=".json",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        temp_path.replace(path)
        return path
