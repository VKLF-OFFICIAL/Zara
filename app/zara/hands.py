"""Manos para cualquier cerebro.

Solo el cerebro «agente» (Claude Code) controla el equipo de forma nativa. Para
que CUALQUIER cerebro —Gemini o un modelo local por Ollama— pueda también actuar
en el sistema, aquí se define un protocolo sencillo: el modelo pide ejecutar
algo escribiéndolo entre etiquetas <ejecutar>…</ejecutar>. Zara ejecuta cada
comando PASÁNDOLO POR EL GUARDIÁN (que puede pedir confirmación o bloquearlo),
le devuelve la salida al modelo y este responde en lenguaje natural.

Así la seguridad no depende del modelo (un local sin censura también queda bajo
el Guardián), y el control del equipo es igual con Claude, Gemini o local.
"""
from __future__ import annotations
import re

PROTOCOL = (
    "Tienes MANOS para actuar en el equipo del Señor VK (Linux con KDE).\n"
    "Para EJECUTAR un comando de shell escríbelo EXACTAMENTE así, en su línea:\n"
    "<ejecutar>COMANDO</ejecutar>\n"
    "Ejemplo — si te pregunta cuánto lleva encendido el equipo, responde solo:\n"
    "<ejecutar>uptime -p</ejecutar>\n"
    "Reglas:\n"
    "- Usa la etiqueta <ejecutar> SOLO para ejecutar. NO uses bloques ``` para "
    "ejecutar (los bloques ``` son únicamente para MOSTRARle código al Señor VK, "
    "no se ejecutan).\n"
    "- Escribe la etiqueta y espera; te devolveré la salida real del comando.\n"
    "- Un Guardián de seguridad revisa cada comando y puede pedir confirmación o "
    "bloquearlo.\n"
    "- Con la salida, responde al Señor VK en español, breve y claro.\n"
    "- Si no hace falta ejecutar nada, responde directamente."
)

_ACTION_RE = re.compile(r"<ejecutar>\s*(.*?)\s*</ejecutar>", re.DOTALL | re.IGNORECASE)


def extract_actions(text: str) -> list[str]:
    """Comandos que el modelo pide ejecutar, en orden."""
    return [a.strip() for a in _ACTION_RE.findall(text or "") if a.strip()]


def strip_actions(text: str) -> str:
    """El texto del modelo sin las etiquetas de acción (lo que se le dice al VK)."""
    return _ACTION_RE.sub("", text or "").strip()
