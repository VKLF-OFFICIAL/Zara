from __future__ import annotations
from .base import Brain
from ..errors import BrainError

# Pistas que sugieren usar un modelo local sin restricciones (bug bounty, etc.)
_LOCAL_HINTS = ("bug bounty", "bugbounty", "exploit", "payload", "pentest",
                "sin censura", "sin filtro", "obliterated", "vulnerab")
# Pistas de tarea de programación.
_CODE_HINTS = ("código", "codigo", "programa", "function", "función", "bug ",
               "stacktrace", "compilar", "refactor")


class BrainRegistry:
    """Mantiene los cerebros disponibles y el activo; permite cambiar y sugerir."""

    def __init__(self, brains: list[Brain]):
        if not brains:
            raise BrainError("No hay cerebros configurados")
        self._brains = {b.name: b for b in brains}
        self._order = [b.name for b in brains]
        # El activo por defecto es el primero disponible, o el primero a secas.
        self._active = next((b.name for b in brains if b.available()),
                            self._order[0])

    @property
    def active_name(self) -> str:
        return self._active

    @property
    def active(self) -> Brain:
        return self._brains[self._active]

    def names(self) -> list[str]:
        return list(self._order)

    def get(self, name: str):
        return self._brains.get(name)

    def _resolve(self, query: str) -> str | None:
        if query in self._brains:
            return query
        q = query.lower()
        for name in self._order:
            if q in name.lower():
                return name
        return None

    def switch(self, name: str) -> str:
        resolved = self._resolve(name)
        if resolved is None:
            raise BrainError(
                f"Cerebro desconocido: {name}. Disponibles: {', '.join(self._order)}")
        self._active = resolved
        return resolved

    def suggest(self, task: str) -> str:
        t = task.lower()
        if any(h in t for h in _LOCAL_HINTS):
            # Prefiere un modelo local sin restricciones si lo hay.
            for name in self._order:
                low = name.lower()
                if "obliterated" in low or "qwen" in low or "local" in low:
                    return name
        return self._active

    def ask(self, prompt: str, context: str = "") -> str:
        return self.active.ask(prompt, context)
