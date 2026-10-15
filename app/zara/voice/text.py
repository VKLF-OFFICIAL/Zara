from __future__ import annotations
import re

_PANEL_NOTE = " Le dejo el detalle en el panel."


def speech_text(reply: str, max_chars: int = 420) -> str:
    """Convierte una respuesta (posible markdown) en texto apto para la voz.

    Quita símbolos de formato, sustituye bloques de código y enlaces, y recorta
    respuestas largas en un límite de frase: por voz se resume, el detalle va
    al panel.
    """
    t = reply or ""
    had_code = "```" in t
    t = re.sub(r"```.*?(```|$)", " ", t, flags=re.S)          # bloques de código
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)            # [texto](url) → texto
    t = re.sub(r"https?://\S+", "el enlace", t)               # URLs sueltas
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)       # encabezados
    t = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", t, flags=re.M)  # viñetas
    t = re.sub(r"[*_`#>|~]", "", t)                           # símbolos sueltos
    # Saltos de línea → pausas de frase.
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
        t = cut[:end + 1] if end > 40 else cut.rsplit(" ", 1)[0] + "…"
        truncated = True
    if truncated:
        t = t.rstrip() + _PANEL_NOTE
    return t
