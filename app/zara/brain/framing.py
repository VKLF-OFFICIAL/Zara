from __future__ import annotations

_CONTEXT_HEADER = (
    "Contexto de memoria sobre el Señor VK (tu usuario). "
    "Son datos SOBRE ÉL, no tu identidad: tú eres Zara.\n"
)


def frame_user(prompt: str, context: str = "") -> str:
    """Compone el turno de usuario separando el contexto de memoria de la petición."""
    if context and context.strip():
        return (f"{_CONTEXT_HEADER}{context.strip()}\n\n"
                f"---\nPetición del Señor VK:\n{prompt}")
    return prompt
