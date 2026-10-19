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
       "negativo", "nada", "deniego", "denegado", "para", "pare", "ni", "jamas", "tampoco"}
# Palabras de relleno que pueden acompañar a un «sí» sin cambiar su sentido.
_FILLER = {"vale", "claro", "ok", "okay", "dale", "venga", "bueno", "pues", "por", "favor",
           "senor", "vk", "zara", "ya", "lo", "eso", "de", "acuerdo", "perfecto", "bien", "muy",
           "correcto", "exacto", "oye", "que"}
# [FIX SEG-10] Imperativos con pronombre enclítico que repiten la orden pedida («Sí, bórralo»,
# «sí, ciérrala», «sí, ejecútalo»). Solo cuentan junto a un «sí» explícito; la lista es cerrada
# para que un «si» condicional seguido de cualquier palabra («si borras...») no confirme.
_VERB_STEMS = ("borra", "elimina", "cierra", "ejecuta", "abre", "apaga", "reinicia", "mata",
               "termina", "envia", "manda", "instala", "desinstala", "guarda", "lanza", "mueve",
               "quita", "sobrescribe", "escribe", "vacia", "suspende", "hazme", "haz", "dale")
_CLITICS = ("lo", "la", "los", "las", "le", "les", "melo", "mela", "selo", "sela")
_YES_VERBS = {stem + clitic for stem in _VERB_STEMS for clitic in _CLITICS}
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
    no ejecutan una acción sensible por sí solas. Sí valen «Sí, bórralo» o «claro que sí»:
    el imperativo con pronombre (lista cerrada) solo acompaña a un «sí» explícito.
    """
    words_list = _normalize(text)
    words = set(words_list)
    if words & _NO:
        return False
    if (
        words & _YES
        and len(words_list) <= _MAX_YES_WORDS
        and words <= (_YES | _FILLER | _YES_VERBS)
    ):
        return True
    return None
