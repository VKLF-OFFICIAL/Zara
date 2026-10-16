"""Manos de Zara: cómo cualquier cerebro actúa sobre el equipo.

Dos mecanismos conviven:

1. NATIVO (preferente) — `ReActAgent`: el cerebro pide herramientas mediante
   Function Calling nativo (Gemini/Claude/Ollama). Cada invocación de `run_shell`
   pasa por el Guardián en milisegundos (sin el punto ciego del subproceso CLI).
   Bucle ReAct de hasta 10 pasos con AUTOCORRECCIÓN: si un comando devuelve error
   en stderr, Zara lee el error, corrige y reintenta sola, sin preguntar.

2. LEGADO (fallback) — protocolo de texto `<ejecutar>…</ejecutar>` para cerebros
   que solo exponen `.ask()` y no herramientas nativas. Se conserva intacto por
   compatibilidad (y porque la seguridad sigue recayendo en el Guardián).
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any, Callable

from .brain.tools import TOOL_SCHEMAS

log = logging.getLogger("zara.hands")

# =============================================================================
# LEGADO: protocolo de texto <ejecutar> (compatibilidad con cerebros sin tools)
# =============================================================================

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


# =============================================================================
# NATIVO: bucle ReAct con Function Calling y autocorrección
# =============================================================================

def _summarize_result(name: str, args: dict, result: Any) -> str:
    """Convierte el resultado de una herramienta en texto de realimentación
    para el modelo, marcando claramente los errores para que autocorrija."""
    if isinstance(result, dict):
        if result.get("blocked"):
            return f"[{name}] BLOQUEADO por el Guardián: {result.get('stderr') or result.get('reason') or 'acción no permitida'}"
        if result.get("success") is False or result.get("exit_code") not in (None, 0):
            err = result.get("stderr") or result.get("error") or result.get("message") or ""
            return (f"[{name}] ERROR (exit={result.get('exit_code')}): {err}\n"
                    f"Lee el error, CORRIGE los argumentos o el comando y REINTENTA tú misma.")
        payload = result.get("stdout")
        if payload is None:
            payload = result.get("content") or result.get("message") or json.dumps(
                {k: v for k, v in result.items() if k not in ("success",)}, ensure_ascii=False)
        return f"[{name}] OK: {str(payload)[:2000]}"
    return f"[{name}] {str(result)[:2000]}"


class ReActAgent:
    """Conduce a un cerebro con Tool Calling nativo en un bucle acción→resultado.

    brain:   objeto con stream_tools(messages, tools) -> eventos
             ({"type": "token"|"tool_call"|"error", ...}).
    execute: callable(name, args) -> dict  (lo ejecuta el backend vía Guardián).
    """

    def __init__(self, brain, execute: Callable[[str, dict], Any],
                 tools: list[dict] | None = None, max_steps: int = 10,
                 on_event: Callable[[str, str], None] | None = None):
        self.brain = brain
        self.execute = execute
        self.tools = tools if tools is not None else TOOL_SCHEMAS
        self.max_steps = max(1, int(max_steps))
        self.on_event = on_event

    def _emit(self, kind: str, detail: str) -> None:
        if self.on_event:
            try:
                self.on_event(kind, detail)
            except Exception:
                pass

    def run(self, prompt: str, context: str = "") -> str:
        from .brain.framing import frame_user
        messages: list[dict] = [{"role": "user", "content": frame_user(prompt, context)}]
        last_text = ""

        for step in range(self.max_steps):
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            error: str | None = None

            for ev in self.brain.stream_tools(messages, self.tools):
                t = ev.get("type")
                if t == "token":
                    text_parts.append(ev.get("token", ""))
                elif t == "tool_call":
                    tool_calls.append(ev)
                elif t == "error":
                    error = ev.get("error", "error desconocido")

            text = "".join(text_parts).strip()
            if text:
                last_text = text

            if error and not tool_calls:
                return last_text or f"Lo siento, ha fallado: {error}."

            if not tool_calls:
                return last_text or "Hecho."

            # Ejecutar las herramientas pedidas y realimentar los resultados.
            feedback: list[str] = []
            for tc in tool_calls:
                name, args = tc.get("name", ""), tc.get("args", {}) or {}
                self._emit("tool", f"{name} {json.dumps(args, ensure_ascii=False)[:120]}")
                log.info("ReAct paso %d: %s(%s)", step + 1, name, args)
                try:
                    result = self.execute(name, args)
                except Exception as exc:  # noqa: BLE001
                    result = {"success": False, "error": str(exc)}
                feedback.append(_summarize_result(name, args, result))

            # El turno del asistente (texto + petición de herramientas) y la
            # realimentación como turno de usuario: formato uniforme y robusto
            # entre proveedores (el modelo PIDE herramientas de forma nativa;
            # los resultados se le devuelven como contexto para autocorregir).
            assistant_note = text or ("Invoco: " + ", ".join(
                tc.get("name", "") for tc in tool_calls))
            messages.append({"role": "assistant", "content": assistant_note})
            messages.append({"role": "user", "content":
                             "Resultado de las herramientas:\n" + "\n\n".join(feedback) +
                             "\n\nSi la tarea está completa, responde en español, breve "
                             "y claro (sin apelativos repetidos). Si algo falló, "
                             "corrígelo y vuelve a intentarlo con las herramientas."})

        return last_text or "He hecho lo que he podido."
