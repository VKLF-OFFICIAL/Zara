from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Callable
from .action import Action, Level, Decision
from .rules import RuleSet
from ..audit import AuditLog
from ..backup import backup_path
from ..errors import BackupError

_BACKUP_CONFIRM_KINDS = {"file_write", "file_delete", "file_move"}


class Guardian:
    def __init__(self, rules: RuleSet, audit: AuditLog, copias_dir: Path,
                 clock: Callable[[], datetime] = datetime.now):
        self._rules = rules
        self._audit = audit
        self._copias = copias_dir
        self._clock = clock

    def classify(self, action: Action) -> Decision:
        level, reason, matched = self._rules.classify(action)
        needs_backup = level is Level.CRITICAL
        if level is Level.CONFIRM and action.kind in _BACKUP_CONFIRM_KINDS:
            needs_backup = any(Path(p).exists() for p in action.paths)
        decision = Decision(action=action, level=level, reason=reason,
                            matched_rule=matched, needs_backup=needs_backup)
        self._audit.record("classify", {
            "kind": action.kind, "command": action.command,
            "level": level.name, "reason": reason, "matched_rule": matched,
            "needs_backup": needs_backup,
        })
        return decision

    def prepare(self, decision: Decision) -> Decision:
        if decision.is_blocked:
            self._audit.record("blocked", {"command": decision.action.command,
                                           "reason": decision.reason})
            raise PermissionError(
                f"Acción prohibida por el Guardián: {decision.reason}")
        if not decision.needs_backup:
            decision.prepared = True
            self._audit.record("prepare", {"command": decision.action.command,
                                           "backup_at": None})
            return decision
        try:
            last = None
            for p in decision.action.paths:
                if Path(p).exists():
                    last = backup_path(Path(p), self._copias, clock=self._clock)
            if last is None:
                raise BackupError("No había rutas existentes que copiar")
        except BackupError as exc:
            decision.prepared = False
            self._audit.record("prepare_failed", {
                "command": decision.action.command, "error": str(exc)})
            raise
        decision.backup_at = last
        decision.prepared = True
        self._audit.record("prepare", {"command": decision.action.command,
                                        "backup_at": str(last)})
        return decision
