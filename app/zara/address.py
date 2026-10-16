"""Moderación de apelativos: que Zara no suene a contestador automático.

El Señor VK no quiere que le llamen «Señor VK» en cada frase. Aquí se centraliza
esa cortesía: se usa el apelativo al ARRANCAR una interacción importante y, el
resto del tiempo, solo muy de vez en cuando y de forma orgánica. En medio, Zara
habla directa y fluida.

`strip_vocatives` elimina los apelativos de un texto; `AddressModerator` decide,
con memoria de sesión, cuándo conservar UNO al inicio y cuándo quitarlos todos.
Se aplica en la capa de VOZ (y opcionalmente al texto del panel), nunca en el
normalizador de TTS (para no romper tests que preservan «Señor»).
"""
from __future__ import annotations
import re
import time
from typing import Callable

# Apelativos a moderar (con y sin acentos). Orden: los más largos primero.
_VOCATIVES = r"(?:Señor\s+VK|Se[ñn]or\s+VK|VKLF|Señor|Se[ñn]or|VK)"
# Vocativo al inicio de frase: «Señor VK, …» → se quita él y su coma.
_LEAD_RE = re.compile(r"^\s*" + _VOCATIVES + r"\b[\s,;:]+", re.IGNORECASE)
# Vocativo en medio/final: «…, Señor VK.» → se quita él y el separador PREVIO,
# conservando la puntuación de cierre que venga detrás.
_TAIL_RE = re.compile(r"[\s,;:]*\b" + _VOCATIVES + r"\b", re.IGNORECASE)
# Para detectar el apelativo presente (y cuál es) sin consumir puntuación.
_FIND_RE = re.compile(r"\b" + _VOCATIVES + r"\b", re.IGNORECASE)


def strip_vocatives(text: str) -> str:
    """Quita TODOS los apelativos, dejando la frase natural y bien puntuada."""
    if not text:
        return text
    out = _LEAD_RE.sub("", text)
    out = _TAIL_RE.sub("", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)       # espacios antes de signos
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"^[\s,;:]+", "", out)               # restos al inicio
    return out.strip()


def _first_vocative(text: str) -> str | None:
    m = _FIND_RE.search(text or "")
    return m.group(0).strip() if m else None


class AddressModerator:
    """Decide cuándo incluir el apelativo. Conserva como mucho UNO, al inicio.

    - La primera vez de la sesión: lo conserva (saludo/arranque).
    - Después: lo quita, salvo de forma esporádica (cada `every` turnos o tras
      `cooldown` segundos de silencio), para que suene natural y no repetitivo.
    """

    def __init__(self, every: int = 6, cooldown: float = 180.0,
                 clock: Callable[[], float] = time.monotonic):
        self.every = max(1, every)
        self.cooldown = cooldown
        self._clock = clock
        self._turns = 0
        self._last_addressed = float("-inf")

    def reset(self) -> None:
        self._turns = 0
        self._last_addressed = float("-inf")

    def _should_address(self) -> bool:
        now = self._clock()
        first = self._turns == 0
        due = (self._turns % self.every == 0)
        idle = (now - self._last_addressed) >= self.cooldown
        return first or due or idle

    def apply(self, text: str) -> str:
        """Modera los apelativos del texto según el estado de la sesión."""
        if not text or not text.strip():
            return text
        keep = self._should_address()
        self._turns += 1
        voc = _first_vocative(text)
        bare = strip_vocatives(text)
        if not bare:
            # El texto era solo apelativo (p. ej. «¿Sí, Señor VK?»): respétalo.
            return text
        if keep and voc:
            self._last_addressed = self._clock()
            # Reinserta UN apelativo al final de la primera frase, con naturalidad.
            m = re.search(r"[.!?]", bare)
            if m:
                i = m.start()
                return f"{bare[:i]}, {voc}{bare[i:]}"
            return f"{bare}, {voc}"
        return bare
