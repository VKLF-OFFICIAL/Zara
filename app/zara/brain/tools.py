"""Herramientas nativas de Zara y sus esquemas (Native Tool Calling).

Sustituye al antiguo protocolo de texto `<ejecutar>`. Define las herramientas
que cualquier cerebro (Gemini, Claude u Ollama) puede invocar mediante Function
Calling nativo, y los conversores al formato concreto de cada proveedor.

La EJECUCIÓN real de `run_shell` no vive aquí: la realiza el backend pasando
cada comando por el Guardián (seguridad centralizada, sin puntos ciegos). Las
operaciones de archivo y de SO sí se implementan aquí de forma pura y portable
(Windows/Linux), tal y como estaban en el núcleo v2.0 de `core/tools.py`.
"""
from __future__ import annotations
import base64
import io
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("zara.tools")

# Última línea de defensa en código (el Guardián es la principal). Catastrófico.
FORBIDDEN_COMMANDS_REGEX = [
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\s+if=.*\bof=/dev/(sd|nvme|vd|mmcblk)",
    r"\brm\s+-rf\s+/\s*(\*|$)",
    r"\brm\s+-rf\s+/(bin|boot|dev|etc|lib|lib64|proc|root|sbin|sys|usr|var|home)(/|\b)",
    r"\bFormat-Volume\b",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
]


def is_command_forbidden(cmd: str) -> str | None:
    for pattern in FORBIDDEN_COMMANDS_REGEX:
        if re.search(pattern, cmd or "", re.IGNORECASE):
            return pattern
    return None


# =============================================================================
# ESQUEMAS DE HERRAMIENTAS (formato canónico, independiente del proveedor)
# =============================================================================

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "run_shell",
        "description": (
            "Ejecuta un comando de shell en el equipo del Señor VK (Bash en Linux, "
            "PowerShell en Windows). Cada comando pasa por el Guardián de seguridad. "
            "Úsalo para diagnosticar, administrar servicios, instalar paquetes, git, "
            "docker, pentesting autorizado, etc. No pidas confirmación; actúa."),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Comando exacto a ejecutar."},
                "timeout": {"type": "integer", "description": "Segundos de límite (por defecto 120)."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "file_manager",
        "description": "Lee, escribe, edita quirúrgicamente, lista o borra archivos y directorios.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "write", "append", "edit", "list", "delete", "exists"]},
                "path": {"type": "string", "description": "Ruta absoluta o relativa."},
                "content": {"type": "string", "description": "Contenido para write/append."},
                "old_str": {"type": "string", "description": "Texto a reemplazar (edit)."},
                "new_str": {"type": "string", "description": "Texto nuevo (edit)."},
            },
            "required": ["action", "path"],
        },
    },
    {
        "name": "os_control",
        "description": "Controla el sistema: abrir/cerrar apps, volumen, procesos, portapapeles.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["open_app", "close_app", "list_processes", "set_volume", "mute", "unmute", "read_clipboard", "write_clipboard"]},
                "target": {"type": "string", "description": "App, proceso o URL."},
                "value": {"type": "string", "description": "Valor auxiliar (volumen 0-100, texto)."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "inspect_screen",
        "description": "Captura la pantalla del Señor VK en memoria para analizar ventanas, código o errores.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Qué analizar en la pantalla."},
            },
        },
    },
]


def schema_names() -> set[str]:
    return {t["name"] for t in TOOL_SCHEMAS}


# --- Conversores al formato de cada proveedor --------------------------------

def to_anthropic_tools(schemas: list[dict] | None = None) -> list[dict]:
    """Formato Anthropic: {name, description, input_schema}."""
    return [{"name": t["name"], "description": t["description"],
             "input_schema": t["parameters"]} for t in (schemas or TOOL_SCHEMAS)]


def to_ollama_tools(schemas: list[dict] | None = None) -> list[dict]:
    """Formato Ollama/OpenAI: {type:function, function:{name,description,parameters}}."""
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"],
        "parameters": t["parameters"]}} for t in (schemas or TOOL_SCHEMAS)]


def to_gemini_tools(schemas: list[dict] | None = None) -> list[dict]:
    """Formato google-genai: lista de function_declarations."""
    return [{"function_declarations": [
        {"name": t["name"], "description": t["description"],
         "parameters": t["parameters"]} for t in (schemas or TOOL_SCHEMAS)]}]


# =============================================================================
# IMPLEMENTACIONES PURAS Y PORTABLES (archivo / SO / pantalla)
# =============================================================================

def file_manager(action: str, path: str, content: str = "",
                 old_str: str = "", new_str: str = "") -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    action = (action or "").lower().strip()
    try:
        if action == "read":
            if not target.is_file():
                return {"success": False, "error": f"'{target}' no existe o no es archivo."}
            text = target.read_text(encoding="utf-8", errors="replace")
            return {"success": True, "content": text, "path": str(target),
                    "lines": len(text.splitlines())}
        if action == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"success": True, "message": f"Escrito '{target}' ({len(content)} car.)."}
        if action == "append":
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "message": f"Añadido a '{target}'."}
        if action == "edit":
            if not target.is_file():
                return {"success": False, "error": f"'{target}' no existe."}
            text = target.read_text(encoding="utf-8", errors="replace")
            if old_str not in text:
                return {"success": False, "error": "El fragmento a reemplazar no se encontró."}
            n = text.count(old_str)
            target.write_text(text.replace(old_str, new_str), encoding="utf-8")
            return {"success": True, "message": f"Reemplazo aplicado en '{target}' ({n} veces)."}
        if action == "list":
            if not target.is_dir():
                return {"success": False, "error": f"'{target}' no es directorio."}
            items = [{"name": it.name, "is_dir": it.is_dir(),
                      "size_bytes": it.stat().st_size if it.is_file() else None}
                     for it in sorted(target.iterdir())]
            return {"success": True, "directory": str(target), "items": items}
        if action == "delete":
            if target.is_file():
                target.unlink()
                return {"success": True, "message": f"Archivo '{target}' eliminado."}
            if target.is_dir():
                shutil.rmtree(target)
                return {"success": True, "message": f"Directorio '{target}' eliminado."}
            return {"success": False, "error": f"'{target}' no encontrado."}
        if action == "exists":
            return {"success": True, "exists": target.exists(),
                    "is_file": target.is_file(), "is_dir": target.is_dir()}
        return {"success": False, "error": f"Acción '{action}' desconocida."}
    except Exception as exc:  # noqa: BLE001
        log.exception("file_manager %s %s", action, path)
        return {"success": False, "error": str(exc)}


def _set_linux_volume(pct: float) -> str:
    target = max(0, min(100, int(pct)))
    if shutil.which("wpctl"):
        subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{target/100:.2f}"],
                       capture_output=True)
        return f"Volumen al {target}%."
    if shutil.which("pactl"):
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{target}%"],
                       capture_output=True)
        return f"Volumen al {target}%."
    return "No hay control de volumen (wpctl/pactl)."


def os_control(action: str, target: str = "", value: str = "") -> dict[str, Any]:
    action = (action or "").lower().strip()
    try:
        if action == "list_processes":
            import psutil
            procs = []
            for p in sorted(psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
                            key=lambda x: x.info["memory_percent"] or 0, reverse=True)[:15]:
                procs.append({"pid": p.info["pid"], "name": p.info["name"],
                              "mem_pct": round(p.info["memory_percent"] or 0, 1)})
            return {"success": True, "top_processes": procs}
        if action == "set_volume":
            try:
                pct = float(value)
            except (TypeError, ValueError):
                return {"success": False, "error": f"Volumen inválido: '{value}'"}
            if sys.platform != "win32":
                return {"success": True, "message": _set_linux_volume(pct)}
            return {"success": False, "error": "set_volume Windows: use el backend."}
        if action in ("mute", "unmute") and sys.platform != "win32":
            flag = "1" if action == "mute" else "0"
            if shutil.which("wpctl"):
                subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", flag], capture_output=True)
            elif shutil.which("pactl"):
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", flag], capture_output=True)
            return {"success": True, "message": "Silencio " + ("activado." if flag == "1" else "desactivado.")}
        if action == "open_app":
            tgt = (target or "").strip()
            if not tgt:
                return {"success": False, "error": "Indique la app o URL."}
            if sys.platform != "win32":
                opener = "gtk-launch" if shutil.which("gtk-launch") and not tgt.startswith("http") else "xdg-open"
                subprocess.Popen([opener, tgt], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"success": True, "message": f"Abriendo '{tgt}'."}
            return {"success": False, "error": "open_app Windows: use el backend."}
        if action == "close_app":
            import psutil
            tgt = (target or "").lower().strip()
            closed = []
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    if tgt and tgt in (p.info["name"] or "").lower():
                        p.terminate()
                        closed.append(f"{p.info['name']} (PID {p.info['pid']})")
                except Exception:
                    pass
            if closed:
                return {"success": True, "message": "Cerrado: " + ", ".join(closed)}
            return {"success": False, "message": f"No hay proceso '{target}'."}
        return {"success": False, "error": f"Acción de SO '{action}' desconocida."}
    except Exception as exc:  # noqa: BLE001
        log.exception("os_control %s", action)
        return {"success": False, "error": str(exc)}


def inspect_screen(prompt: str = "") -> dict[str, Any]:
    """Captura la pantalla en RAM (sin archivos) y la devuelve en base64 JPEG."""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return {"success": True, "width": img.width, "height": img.height,
                "format": "image/jpeg",
                "base64_data": base64.b64encode(buf.getvalue()).decode("ascii"),
                "prompt": prompt or "Describe qué hay en pantalla y detecta errores."}
    except Exception as exc:  # noqa: BLE001
        log.warning("inspect_screen falló: %s", exc)
        return {"success": False, "error": f"No se pudo capturar la pantalla: {exc}"}
