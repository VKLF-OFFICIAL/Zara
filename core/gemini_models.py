"""Descubrimiento dinámico de modelos Gemini.

Los IDs de modelo de Google cambian (y se retiran) con frecuencia: fijarlos en el
código acaba rompiendo Zara. Con el valor ``auto`` se consulta ``models.list()``
y se elige el Flash estable más reciente que soporte la operación necesaria.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List

log = logging.getLogger("zara.gemini_models")

AUTO = "auto"

# IDs retirados por Google: un .env antiguo (o la elección persistida desde el HUD) puede
# seguir apuntando a ellos. Se tratan como "auto" para que Zara no falle al conectar.
RETIRED_MODELS = {
    "gemini-2.5-flash-preview-native-audio-dialog",
    "gemini-2.0-flash-live-001",
    "gemini-live-2.5-flash-preview",
    "gemini-2.0-flash-exp",
    "gemini-2.0-flash",
}

# Variantes que no sirven como cerebro de chat general.
_EXCLUDE_CHAT = ("lite", "image", "tts", "embedding", "live", "audio", "robotics", "computer-use", "translate")
_EXCLUDE_LIVE = ("lite", "image", "tts", "embedding", "translate")


def _version_key(name: str) -> tuple:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    version = float(m.group(1)) if m else 0.0
    stable = 0 if re.search(r"preview|exp", name) else 1
    date = re.findall(r"(\d{2}-\d{4}|\d{8})", name)
    # Primero estable sobre preview; después la versión más alta.
    return (stable, version, date[-1] if date else "")


def choose_gemini_model(
    available: Dict[str, List[str]],
    preferred: str = AUTO,
    live: bool = False,
    fallback: str = "",
) -> str:
    """Elige un modelo entre los disponibles.

    `available` mapea nombre (con o sin prefijo ``models/``) -> acciones soportadas.
    Si `preferred` existe y soporta la operación, se respeta; con ``auto`` (o si el
    preferido ya no existe) se elige el más reciente. Sin candidatos, `fallback`.
    """
    action = "bidiGenerateContent" if live else "generateContent"
    usable = {
        n.removeprefix("models/"): acts for n, acts in available.items()
        if action in (acts or [])
    }
    if preferred and preferred != AUTO and preferred in usable:
        return preferred

    exclude = _EXCLUDE_LIVE if live else _EXCLUDE_CHAT
    candidates = [
        n for n in usable
        if n.startswith("gemini-") and "flash" in n and not any(x in n for x in exclude)
        and (not live or "live" in n or "native-audio" in n)
    ]
    if not candidates:
        return fallback or (preferred if preferred != AUTO else "")
    best = max(candidates, key=_version_key)
    if preferred and preferred != AUTO:
        log.warning("El modelo Gemini '%s' ya no está disponible; usando '%s'.", preferred, best)
    return best


def list_gemini_models(api_key: str) -> Dict[str, List[str]]:
    """Consulta la API y devuelve {modelo: [acciones soportadas]} (vacío si falla)."""
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        return {
            m.name: list(getattr(m, "supported_actions", None) or [])
            for m in client.models.list()
            if getattr(m, "name", None)
        }
    except Exception as exc:
        log.warning("No se pudieron listar los modelos de Gemini: %s", exc)
        return {}


def resolve_gemini_model(api_key: str, preferred: str, live: bool, fallback: str) -> str:
    """Resuelve `preferred` (posiblemente ``auto``) a un ID real; nunca lanza."""
    if preferred in RETIRED_MODELS:
        log.warning("El modelo Gemini '%s' fue retirado; se elegirá uno vigente.", preferred)
        preferred = AUTO
    if preferred and preferred != AUTO:
        return preferred  # el usuario fijó un modelo concreto: no tocar ni gastar red
    available = list_gemini_models(api_key) if api_key else {}
    return choose_gemini_model(available, preferred, live=live, fallback=fallback)
