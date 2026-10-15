from __future__ import annotations
import time
from pathlib import Path
from typing import Protocol
from ..errors import VoiceError


class Synthesizer(Protocol):
    name: str

    def available(self) -> bool: ...

    def synthesize(self, text: str, out_path: Path) -> Path: ...


class Voice:
    """Sintetiza voz probando una cadena de motores con respaldo.

    El primer motor disponible que tenga éxito gana; si falla, se pasa al
    siguiente. Si todos fallan, se lanza VoiceError.
    """

    def __init__(self, synths: list[Synthesizer], out_dir: Path):
        self._synths = list(synths)
        self._out_dir = Path(out_dir)

    def say(self, text: str) -> Path:
        if not text or not text.strip():
            raise ValueError("No hay texto que decir")
        self._out_dir.mkdir(parents=True, exist_ok=True)
        out = self._out_dir / f"zara-{int(time.time()*1000)}.wav"
        errors = []
        for s in self._synths:
            try:
                if not s.available():
                    continue
                return s.synthesize(text, out)
            except Exception as exc:  # noqa: BLE001 - se registra y se prueba el siguiente
                errors.append(f"{getattr(s, 'name', s)}: {exc}")
        raise VoiceError("Ningún motor de voz funcionó: " + "; ".join(errors))
