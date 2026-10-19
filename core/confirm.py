"""Interpretación de respuestas hablables de confirmación (sí / no)."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

_YES = {"si", "confirmo", "confirmado", "adelante", "hazlo", "procede", "vale", "ok", "okay",
        "afirmativo", "claro", "dale", "autorizo", "autorizado", "yes"}
_NO = {"no", "cancela", "cancelo", "cancelar", "cancelado", "detente", "alto", "nunca",
       "negativo", "nada", "deniego", "denegado", "para", "pare"}


def _normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z]+", text)


def parse_confirmation(text: str) -> Optional[bool]:
    """True = confirma, False = rechaza, None = no es una respuesta clara.

    Ante la mezcla («sí, no...») gana el rechazo: ante la duda, no se ejecuta.
    """
    words = set(_normalize(text))
    if words & _NO:
        return False
    if words & _YES:
        return True
    return None
