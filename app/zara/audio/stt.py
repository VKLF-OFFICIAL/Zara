from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

# Caché de modelos en el almacén (Linux) o en localappdata (Windows).
if sys.platform == "win32":
    _MODEL_DIR = str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "zara" / "models" / "whisper")
else:
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


def normalize_audio(audio, target_peak: float = 0.85, max_gain: float = 20.0):
    """Control automático de ganancia (AGC) y normalización de picos.

    Whisper requiere audio en un rango dinámico óptimo (-20 dB a -12 dB).
    Los micrófonos de PC o USB a menudo entregan niveles bajos (pico < 0.05),
    lo que provoca que Whisper pierda palabras o invente sílabas.
    """
    import numpy as np
    arr = np.asarray(audio, dtype=np.float32)
    if arr.size == 0:
        return arr
    peak = float(np.max(np.abs(arr)))
    if peak > 0.002:
        gain = min(target_peak / peak, max_gain)
        return arr * gain
    return arr


_HALLUCINATIONS = (
    "suscríbete", "suscribete", "suscríbanse", "suscribanse",
    "subtítulos", "subtitulos", "amara.org", "gracias por ver",
    "hasta la próxima", "hasta la proxima"
)

_DEFAULT_PROMPT = (
    "Zara, asistente virtual inteligente y leal para el Señor VK, inspirada en JARVIS. "
    "Comandos de voz: música en Spotify, volumen, silenciar, abrir aplicaciones, "
    "navegador web, terminal, capturas de pantalla, estado del equipo, recordatorios y consultas."
)

_DEFAULT_HOTWORDS = (
    "Zara Señor VK Jarvis Spotify YouTube Chrome Firefox volumen música "
    "pantalla captura terminal silencio reproduce pausa abre cierra"
)


class Transcriber:
    """Transcripción de voz a texto en local con faster-whisper (CPU)."""

    def __init__(self, model_size: str = "small", language: str = "es",
                 device: str = "cpu", compute_type: str = "int8",
                 download_root: str = _MODEL_DIR,
                 beam_size: int = 3, best_of: int = 3,
                 initial_prompt: str | None = None,
                 hotwords: str | None = None):
        self.model_size = model_size
        self.language = language
        self._device = device
        self._compute_type = compute_type
        self._download_root = download_root
        self.beam_size = beam_size
        self.best_of = best_of
        self.initial_prompt = initial_prompt or _DEFAULT_PROMPT
        self.hotwords = hotwords or _DEFAULT_HOTWORDS
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

    def transcribe_array(
        self,
        audio,
        hotwords: str | None = None,
        initial_prompt: str | None = None,
        beam_size: int | None = None,
    ) -> str:
        """Transcribe un array float32 mono 16 kHz con normalización AGC y beam search."""
        import numpy as np
        model = self._ensure()
        norm_audio = normalize_audio(audio)
        bs = beam_size if beam_size is not None else self.beam_size
        bo = max(bs, self.best_of)
        prompt = initial_prompt if initial_prompt is not None else self.initial_prompt
        hw = hotwords if hotwords is not None else self.hotwords

        segments, _info = model.transcribe(
            np.asarray(norm_audio, dtype=np.float32),
            language=self.language,
            vad_filter=False,
            beam_size=bs,
            best_of=bo,
            initial_prompt=prompt,
            hotwords=hw,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()

        # Filtrar alucinaciones típicas de Whisper sobre música o ruido residual
        low = text.lower().strip(" ¡!¿?.,-")
        if any(h in low for h in _HALLUCINATIONS) and len(low.split()) <= 4:
            return ""
        return text

