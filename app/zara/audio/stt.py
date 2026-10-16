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

# initial_prompt rico en FONÉTICA DEL SUR DE ESPAÑA (Granada/Andalucía) para que
# Whisper no falle aunque el Señor VK hable rápido, aspire o elida las «s» finales
# («lo comando», «do minuto», «pa» por «para», «ehtao» por «estado»...), más el
# vocabulario técnico de administración de sistemas y pentesting.
_DEFAULT_PROMPT = (
    "Conversación técnica en español de Andalucía (Granada) con el Señor VK. "
    "El hablante aspira o elide las eses finales y habla rápido, pero se escribe "
    "en español estándar correcto. Nombres y términos frecuentes: "
    "Zara, Señor, Señor VK, VK, VKLF, Jarvis. "
    "Terminal, bash, shell, sudo, systemctl, servicio, daemon, dnf, apt, pip, uv, "
    "git, commit, push, pull, rama, docker, contenedor, imagen, kubectl, ssh, "
    "nmap, ffuf, gobuster, dirb, nikto, sqlmap, hydra, hashcat, john, metasploit, "
    "msfconsole, burp suite, wireshark, payload, exploit, shell inversa, puerto, "
    "escaneo, directorio, fichero, archivo, proceso, kernel, Fedora, Kali, KDE, "
    "Plasma, Wayland, Ollama, Claude, Gemini, Python, repositorio, script. "
    "Comandos de voz: pon música en Spotify, sube o baja el volumen, silencia, "
    "abre el navegador, haz una captura, estado del equipo, pon un recordatorio."
)

_DEFAULT_HOTWORDS = (
    "Zara Señor VK VKLF Jarvis terminal bash sudo systemctl dnf apt pip uv git "
    "docker ssh nmap ffuf gobuster sqlmap hydra hashcat metasploit payload exploit "
    "puerto directorio Fedora Kali KDE Ollama Claude Gemini Spotify YouTube Firefox "
    "volumen música pantalla captura silencio reproduce pausa abre cierra"
)


class Transcriber:
    """Transcripción de voz a texto en local con faster-whisper (CPU)."""

    def __init__(self, model_size: str = "small", language: str = "es",
                 device: str = "cpu", compute_type: str = "int8",
                 download_root: str = _MODEL_DIR,
                 beam_size: int = 2, best_of: int = 2,
                 cpu_threads: int = 8, num_workers: int = 1,
                 initial_prompt: str | None = None,
                 hotwords: str | None = None):
        self.model_size = model_size
        self.language = language
        self._device = device
        # CPU de 12 núcleos sin GPU: int8 + 8 hilos es el punto dulce de latencia.
        self._compute_type = compute_type
        self._download_root = download_root
        self._cpu_threads = cpu_threads
        self._num_workers = num_workers
        self.beam_size = beam_size
        self.best_of = best_of
        self.initial_prompt = initial_prompt or _DEFAULT_PROMPT
        self.hotwords = hotwords or _DEFAULT_HOTWORDS
        self._model = None  # carga perezosa

    def _ensure(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            kwargs = {}
            if self._device == "cpu":
                kwargs["cpu_threads"] = self._cpu_threads
                kwargs["num_workers"] = self._num_workers
            self._model = WhisperModel(
                self.model_size, device=self._device,
                compute_type=self._compute_type,
                download_root=self._download_root, **kwargs,
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

