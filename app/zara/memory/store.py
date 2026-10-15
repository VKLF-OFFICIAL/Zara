from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

_STOPWORDS = {
    "hola", "que", "qué", "con", "los", "las", "del", "por", "para", "una",
    "uno", "hoy", "sigamos", "el", "la", "de", "y", "a", "en", "es", "hora",
}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower())
            if len(w) >= 4 and w not in _STOPWORDS}


class MemoryStore:
    """Memoria en Markdown: perfil, preferencias, proyectos y diario."""

    def __init__(self, base: Path, clock: Callable[[], datetime] = datetime.now):
        self.base = Path(base)
        self._clock = clock

    @property
    def perfil(self) -> Path:
        return self.base / "perfil.md"

    @property
    def preferencias(self) -> Path:
        return self.base / "preferencias.md"

    @property
    def proyectos(self) -> Path:
        return self.base / "proyectos"

    @property
    def diario(self) -> Path:
        return self.base / "diario"

    def load_relevant(self, query: str) -> str:
        parts: list[str] = []
        for f in (self.perfil, self.preferencias):
            if f.exists():
                parts.append(f.read_text(encoding="utf-8"))
        q = _tokens(query)
        if self.proyectos.is_dir():
            for pf in sorted(self.proyectos.glob("*.md")):
                hay = _tokens(pf.stem + " " + pf.read_text(encoding="utf-8"))
                if q & hay:
                    parts.append(pf.read_text(encoding="utf-8"))
        return "\n\n".join(parts).strip()

    def remember(self, text: str) -> None:
        self.preferencias.parent.mkdir(parents=True, exist_ok=True)
        with open(self.preferencias, "a", encoding="utf-8") as fh:
            fh.write(f"- {text}\n")

    def append_diary(self, entry: str) -> Path:
        self.diario.mkdir(parents=True, exist_ok=True)
        now = self._clock()
        f = self.diario / f"{now:%Y-%m-%d}.md"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"- {now:%H:%M} — {entry}\n")
        return f
