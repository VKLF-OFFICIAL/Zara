"""Filtro de eco: descarta lo que el micro capta de la propia voz de Zara."""
from __future__ import annotations
import re
import unicodedata

# Palabras tan frecuentes en las respuestas de Zara que no deben contar como eco
# por sí solas.
_FILLER = {"senor", "vk", "vklf", "si", "no", "de", "la", "el", "en", "que", "y", "a"}


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _content_tokens(text: str) -> set[str]:
    return {w for w in normalize(text).split() if w not in _FILLER}


def is_echo(heard: str, spoken_history, threshold: float = 0.6) -> bool:
    """True si lo oído coincide en buena parte con algo que Zara acaba de decir."""
    norm_heard = normalize(heard)
    if not norm_heard:
        return False
    # Respuestas de confirmación válidas: solo son eco si Zara las acaba de pronunciar
    if norm_heard in ("si", "no", "confirmo", "cancelo"):
        for spoken in spoken_history:
            if norm_heard in normalize(spoken).split():
                return True
        return False
    h = _content_tokens(heard)
    if not h:
        return True  # solo relleno de tratamiento (p. ej. «Señor VK»): no es orden
    for spoken in spoken_history:
        s = _content_tokens(spoken)
        if not s:
            continue
        overlap = len(h & s) / len(h)
        if overlap >= threshold:
            return True
    return False

