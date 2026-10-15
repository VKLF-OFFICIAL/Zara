from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Callable


class AuditLog:
    def __init__(self, dir: Path, clock: Callable[[], datetime] = datetime.now):
        self._dir = dir
        self._clock = clock

    def record(self, event: str, data: dict) -> dict:
        now = self._clock()
        entry = {"ts": now.isoformat(), "event": event, **data}
        self._dir.mkdir(parents=True, exist_ok=True)
        f = self._dir / f"acciones-{now:%Y-%m-%d}.jsonl"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
