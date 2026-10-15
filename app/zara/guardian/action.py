from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path


class Level(IntEnum):
    SAFE = 0
    CONFIRM = 1
    CRITICAL = 2
    BLOCKED = 3


@dataclass
class Action:
    kind: str
    description: str
    command: str | None = None
    paths: tuple[Path, ...] = ()
    url: str | None = None


@dataclass
class Decision:
    action: Action
    level: Level
    reason: str
    matched_rule: str | None
    needs_backup: bool
    prepared: bool = False
    backup_at: Path | None = None

    @property
    def requires_confirmation(self) -> bool:
        return self.level in (Level.CONFIRM, Level.CRITICAL)

    @property
    def is_blocked(self) -> bool:
        return self.level is Level.BLOCKED
