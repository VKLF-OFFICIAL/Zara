from __future__ import annotations
import re

_PANEL_NOTE = " Le dejo el detalle en el panel."

# Normalizaciones comunes para síntesis de voz en español
_REPLACEMENTS = [
    (r"\b(\d+)\s*%", r"\1 por ciento"),
    (r"\b(\d+)\s*€", r"\1 euros"),
    (r"\b(\d+)\s*\$", r"\1 dólares"),
    (r"\b(\d+)\s*(?:°C|º|°)\b", r"\1 grados"),
    (r"\b(\d+)\s*km/h\b", r"\1 kilómetros por hora"),
    (r"\b(\d+)\s*(?:GB|GiB|gigas?)\b", r"\1 gigas"),
    (r"\b(\d+)\s*(?:MB|MiB|megas?)\b", r"\1 megas"),
    (r"\b(\d+)\s*(?:KB|KiB|kilos?)\b", r"\1 kilobytes"),
    (r"\b(\d+)\s*ms\b", r"\1 milisegundos"),
    (r"\bIP\b", "I P"),
    (r"\bKDE\b", "K D E"),
    (r"\bCPU\b", "C P U"),
    (r"\bRAM\b", "RAM"),
    (r"\bSSD\b", "S S D"),
    (r"\bUI\b", "interfaz"),
    (r"\bOS\b", "sistema"),
    (r"\bCLI\b", "consola"),
    (r"\bVAD\b", "V A D"),
    (r"\bSTT\b", "transcripción"),
    (r"\bTTS\b", "voz"),
]


def clean_for_speech(text: str) -> str:
    """Limpia una frase para TTS SIN truncar ni añadir nota de panel.

    Elimina todo rastro de Markdown, bloques y código en línea, URLs y símbolos
    impronunciables, y normaliza unidades. Es el saneador que usa el streaming
    por frases antes de enviar cada fragmento al motor de voz.
    """
    t = text or ""
    t = re.sub(r"```.*?(```|$)", " ", t, flags=re.S)        # bloques de código
    t = re.sub(r"`([^`]+)`", r"\1", t)                       # código en línea
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)           # [texto](url)
    t = re.sub(r"https?://\S+", "el enlace", t)              # URLs
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)      # encabezados
    t = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", t, flags=re.M)  # viñetas
    t = t.replace("&", " y ").replace("<", " menor que ").replace(">", " mayor que ")
    t = re.sub(r"[*_`#|~]", "", t)                           # símbolos sueltos
    for pattern, replacement in _REPLACEMENTS:
        t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


def speech_text(reply: str, max_chars: int = 420) -> str:
    """Convierte una respuesta (posible markdown) en texto apto para la voz.

    Quita símbolos de formato, sustituye bloques de código por notas de panel,
    limpia caracteres peligrosos para SSML/XML y normaliza unidades.
    """
    t = reply or ""
    had_code = "```" in t

    # Reemplazar bloques de código
    t = re.sub(r"```.*?(```|$)", " ", t, flags=re.S)


    # Reemplazar enlaces y URLs
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)            # [texto](url) → texto
    t = re.sub(r"https?://\S+", "el enlace", t)               # URLs sueltas

    # Limpiar encabezados y listas
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)       # encabezados
    t = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", t, flags=re.M)  # viñetas

    # Seguridad XML / SSML: evitar que rompan edge-tts
    t = t.replace("&", " y ").replace("<", " menor que ").replace(">", " mayor que ")

    # Símbolos de markdown sueltos
    t = re.sub(r"[*_`#|~]", "", t)

    # Normalización de unidades y términos
    for pattern, replacement in _REPLACEMENTS:
        t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)

    # Saltos de línea → pausas de frase
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    parts = []
    for ln in lines:
        parts.append(ln if ln[-1] in ".!?:;," else ln + ".")
    t = " ".join(parts)
    t = re.sub(r"\s+", " ", t).strip()

    truncated = had_code

    if len(t) > max_chars:
        cut = t[:max_chars]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        if end > 40:
            t = cut[:end + 1]
        else:
            # Buscar corte en coma o punto y coma
            clause = max(cut.rfind(", "), cut.rfind("; "))
            if clause > 40:
                t = cut[:clause] + "…"
            else:
                t = cut.rsplit(" ", 1)[0] + "…"
        truncated = True

    if truncated:
        t = (t.rstrip() + _PANEL_NOTE).strip()
    return t

