from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from .action import Action, Level
from ..config import load_toml
from ..errors import ConfigError

_DEFAULTS = {
    "blocked": [r'\bmkfs\b', r'\bdd\s+if=',
                r'\brm\s+-rf\s+/\s*(\*|$)',
                r'\brm\s+-rf\s+/(bin|boot|dev|etc|lib|lib64|proc|root|sbin|sys|usr|var|home)(/|\b)',
                r'\brm\s+-rf\s+~(/|\s|$)',
                r'/etc/fstab', r'/boot(/|\b)', r':\(\)\s*\{', r'\bshutdown\b',
                r'\breboot\b'],
    "critical": [r'\bsudo\b', r'\bsu\b', r'\bchown\b\s+-R', r'\bchmod\b\s+-R',
                 r'git\s+push\s+.*--force', r'\bpasswd\b', r'\buserdel\b',
                 r'\bvisudo\b', r'\.ssh/', r'\.gnupg/', r'credential',
                 r'id_rsa', r'id_ed25519'],
    "confirm": [r'\bpip\s+install\b', r'\buv\s+pip\s+install\b', r'\bdnf\b',
                r'\bapt\b', r'\bsystemctl\b', r'\bkill\b', r'\bpkill\b',
                r'\bcurl\b.*-o', r'\bwget\b'],
    "safe": [r'^\s*ls\b', r'^\s*cat\b', r'^\s*grep\b', r'^\s*find\b',
             r'^\s*echo\b', r'^\s*pactl\b', r'^\s*playerctl\b', r'^\s*xdg-open\b'],
}

_WRITE_KINDS = {"file_write", "file_delete", "file_move"}


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p) for p in patterns]


@dataclass
class RuleSet:
    zara_root: Path
    espacio: Path
    blocked: list[re.Pattern]
    critical: list[re.Pattern]
    confirm: list[re.Pattern]
    safe: list[re.Pattern]

    @classmethod
    def from_toml(cls, path: Path, zara_root: Path, espacio: Path) -> "RuleSet":
        try:
            cfg = load_toml(path, _DEFAULTS)
        except ConfigError:
            # Fail-safe: un guardian.toml corrupto no debe impedir arrancar ni
            # abrir permisos; se usan las reglas seguras por defecto.
            cfg = dict(_DEFAULTS)
        return cls(
            zara_root=zara_root.resolve(), espacio=espacio.resolve(),
            blocked=_compile(cfg["blocked"]), critical=_compile(cfg["critical"]),
            confirm=_compile(cfg["confirm"]), safe=_compile(cfg["safe"]),
        )

    def _haystack(self, action: Action) -> str:
        parts = [action.command or "", action.url or ""]
        parts += [str(p) for p in action.paths]
        return " ".join(parts)

    def _match(self, patterns, text) -> str | None:
        for p in patterns:
            if p.search(text):
                return p.pattern
        return None

    def _path_scope_level(self, action: Action) -> tuple[Level, str, str | None]:
        # Resuelve symlinks para evitar fugas del espacio.
        for raw in action.paths:
            real = Path(raw).resolve()
            try:
                real.relative_to(self.espacio)
                inside_espacio = True
            except ValueError:
                inside_espacio = False
            if not inside_espacio:
                try:
                    real.relative_to(self.zara_root)
                    return Level.CONFIRM, "escritura dentro de ZARA (no espacio)", "scope:zara"
                except ValueError:
                    return Level.CONFIRM, "escritura fuera de ZARA", "scope:fuera"
        return Level.SAFE, "escritura dentro del espacio de Zara", "scope:espacio"

    def classify(self, action: Action) -> tuple[Level, str, str | None]:
        text = self._haystack(action)

        # Lo prohibido gana siempre, de inmediato.
        m = self._match(self.blocked, text)
        if m:
            return Level.BLOCKED, "acción prohibida", m

        candidates: list[tuple[Level, str, str | None]] = []
        m = self._match(self.critical, text)
        if m:
            candidates.append((Level.CRITICAL, "patrón crítico", m))
        m = self._match(self.confirm, text)
        if m:
            candidates.append((Level.CONFIRM, "requiere confirmación", m))

        # Las escrituras producen SIEMPRE una decisión de alcance definida.
        if action.kind in _WRITE_KINDS:
            candidates.append(self._path_scope_level(action))

        m = self._match(self.safe, text)
        if m:
            candidates.append((Level.SAFE, "comando de solo lectura permitido", m))

        if not candidates:
            # Desconocido → fail-safe
            return Level.CONFIRM, "acción no reconocida; se pide confirmación por seguridad", None

        # Gana el nivel más restrictivo.
        return max(candidates, key=lambda t: t[0])
