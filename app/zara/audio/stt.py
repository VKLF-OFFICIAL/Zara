from __future__ import annotations
import subprocess
from pathlib import Path

# Caché de modelos en el almacén (no en el SSD).
_MODEL_DIR = "/mnt/almacen/IA/whisper-models"


def load_audio_16k(path) -> "object":
    """Decodifica cualquier audio a float32 mono 16 kHz con ffmpeg.

    Evita depender de PyAV (versiones incompatibles con faster-whisper).
    """
    import numpy as np
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-threads", "1", "-i", str(path),
         "-f", "f32le", "-ac", "1", "-ar", "16000", "-loglevel", "quiet", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


_HALLUCINATIONS = (
    "suscríbete", "suscribete", "suscríbanse", "suscribanse",
    "subtítulos", "subtitulos", "amara.org", "gracias por ver",
    "hasta la próxima", "hasta la proxima", "chao", "adiós"
)


class Transcriber:
    """Transcripción de voz a texto en local con faster-whisper (CPU)."""

    def __init__(self, model_size: str = "small", language: str = "es",
                 device: str = "cpu", compute_type: str = "int8",
                 download_root: str = _MODEL_DIR):
        self.model_size = model_size
        self.language = language
        self._device = device
        self._compute_type = compute_type
        self._download_root = download_root
        self._model = None  # carga perezosa

    def _ensure(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.model_size, device=self._device,
                compute_type=self._compute_type,
                download_root=self._download_root,
            )
        return self._model

    def transcribe(self, audio_path: Path) -> str:
        return self.transcribe_array(load_audio_16k(audio_path))

    def transcribe_array(self, audio, hotwords: str | None = None) -> str:
        """Transcribe un array float32 mono 16 kHz.

        beam_size=1 (greedy) es mucho más rápido en CPU. vad_filter=False porque
        EnergyVad ya segmentó la voz con su pre-roll; volver a filtrar con Silero
        dentro de Whisper recortaba palabras suaves.
        """
        import numpy as np
        model = self._ensure()
        segments, _info = model.transcribe(
            np.asarray(audio, dtype=np.float32), language=self.language,
            vad_filter=False, beam_size=1, condition_on_previous_text=False,
            without_timestamps=True, hotwords=hotwords)
        text = " ".join(seg.text.strip() for seg in segments).strip()

        # Filtrar alucinaciones típicas de Whisper sobre música o ruido residual
        low = text.lower().strip(" ¡!¿?.,-")
        if any(h in low for h in _HALLUCINATIONS) and len(low.split()) <= 4:
            return ""
        return text

