"""Interpretación de respuestas hablables de confirmación (sí / no)."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# [FIX SEG-10] Solo cuentan como «sí» las palabras de confirmación EXPLÍCITAS. Las muletillas
# («vale», «claro», «ok», «dale»...) acompañan a una confirmación, pero sueltas no bastan:
# la TV o una conversación de fondo las dicen constantemente.
_YES = {"si", "confirmo", "confirmado", "confirma", "adelante", "hazlo", "procede", "afirmativo",
        "autorizo", "autorizado", "yes"}
_NO = {"no", "cancela", "cancelo", "cancelar", "cancelado", "detente", "alto", "nunca",
       "negativo", "nada", "deniego", "denegado", "para", "pare"}
# Palabras de relleno que pueden acompañar a un «sí» sin cambiar su sentido.
_FILLER = {"vale", "claro", "ok", "okay", "dale", "venga", "bueno", "pues", "por", "favor",
           "senor", "vk", "zara", "ya", "lo", "eso", "de", "acuerdo", "perfecto", "bien", "muy",
           "correcto", "exacto", "oye"}
# Una respuesta de verdad es corta: «sí», «sí, adelante», «vale, hazlo», «sí, por favor».
_MAX_YES_WORDS = 5


def _normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z]+", text)


def parse_confirmation(text: str) -> Optional[bool]:
    """True = confirma, False = rechaza, None = no es una respuesta clara.

    Ante la mezcla («sí, no...») gana el rechazo: ante la duda, no se ejecuta.
    Para confirmar hace falta una respuesta corta, con al menos una palabra explícita
    de confirmación y sin palabras ajenas: así «si quieres te lo explico luego» (el «si»
    condicional, que sin tildes es igual que «sí»), «vale, claro» u «ok google pon música»
    no ejecutan una acción sensible por sí solas.
    """
    words_list = _normalize(text)
    words = set(words_list)
    if words & _NO:
        return False
    if (
        words & _YES
        and len(words_list) <= _MAX_YES_WORDS
        and words <= (_YES | _FILLER)
    ):
        return True
    return None
