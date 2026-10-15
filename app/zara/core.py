from __future__ import annotations
import re

# Comandos reconocidos antes de ir al cerebro. Solo al INICIO de la frase:
# «¿para qué se usa Python?» o «no recuerda nada» no son órdenes.
_SWITCH_RE = re.compile(
    r"^(?:usa|cambia a|pon|activa)\s+(?:el\s+|a\s+)?(?:modelo\s+|cerebro\s+)?(.+)$", re.I)
_REMEMBER_RE = re.compile(r"^recuerda(?:\s+que)?\s+(.+)$", re.I)

# Prefijo opcional "Zara," al inicio.
_WAKE_PREFIX = re.compile(r"^\s*zara[,:\s]+", re.I)


class ZaraCore:
    """Orquesta: interpreta comandos, consulta memoria, pregunta al cerebro,
    registra en el diario y (si procede) habla."""

    def __init__(self, brains, voice, memory, speak: bool = True):
        self.brains = brains
        self.voice = voice
        self.memory = memory
        self.speak = speak

    def _known_brain(self, query: str) -> str | None:
        """Devuelve el cerebro que casa con la consulta, o None (sin lanzar)."""
        q = query.strip().rstrip(".!?").lower()
        if not q:
            return None
        for name in self.brains.names():
            if q == name.lower() or q in name.lower():
                return name
        return None

    def handle_text(self, text: str, brain_override=None, hands=None) -> dict:
        clean = _WAKE_PREFIX.sub("", text).strip()

        m = _REMEMBER_RE.match(clean)
        if m:
            hecho = m.group(1).strip().rstrip(".")
            self.memory.remember(hecho)
            return self._out(f"Anotado, Señor VK: {hecho}.", speak=True)

        m = _SWITCH_RE.match(clean)
        if m and self._known_brain(m.group(1)):
            nombre = self.brains.switch(self._known_brain(m.group(1)))
            return self._out(f"Hecho, Señor VK. Ahora uso {nombre}.",
                             brain=nombre, speak=True)

        # Pregunta normal → memoria + cerebro (el activo, o el indicado).
        context = self.memory.load_relevant(clean)
        brain = brain_override or self.brains
        nombre = getattr(brain_override, "name", None) or self.brains.active_name
        # hands: deja que el cerebro actúe en el sistema (vía Guardián) si procede.
        if hands is not None:
            reply = hands(brain, clean, context)
        else:
            reply = brain.ask(clean, context=context)
        self.memory.append_diary(
            f"VK: {clean[:80]} | Zara({nombre}): {reply[:80]}")
        return self._out(reply, brain=nombre, speak=True)

    def _out(self, reply: str, brain: str | None = None, speak: bool = False) -> dict:
        spoken = False
        if speak and self.speak:
            try:
                self.voice.say(reply)
                spoken = True
            except Exception:
                spoken = False
        return {"reply": reply, "brain": brain or self.brains.active_name,
                "spoken": spoken}
