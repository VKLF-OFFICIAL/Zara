"""Voz en STREAMING por frases, en memoria y con latencia casi nula (punto 3).

Sustituye la reproducción en cascada con reproductores externos y archivos MP3
temporales por:

  • SentenceChunker: trocea el flujo de tokens del cerebro en frases en cuanto
    hay puntuación y ≥35 caracteres, para empezar a hablar en < 400 ms sin
    esperar la respuesta completa.
  • Motores en memoria: Edge-TTS (`es-ES-XimenaNeural`) decodificado directamente
    a PCM en RAM con miniaudio, y Kokoro-82M ONNX / Piper como fallback LOCAL
    instantáneo y offline.
  • Reproducción con sounddevice y BARGE-IN: `stop()` aborta al instante y vacía
    la cola si el Señor VK interrumpe.

Degrada con elegancia: si faltan sounddevice/miniaudio/edge-tts, `available()`
es False y el backend usa la ruta de voz por archivo + reproductor del sistema.
"""
from __future__ import annotations
import asyncio
import logging
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable, Generator, Iterable, Optional

import numpy as np

from .text import clean_for_speech

log = logging.getLogger("zara.voice.stream")

_DELIMS = re.compile(r"([.!?:;\n]+)")


class SentenceChunker:
    """Acumula tokens y emite frases al alcanzar puntuación y una longitud mínima."""

    def __init__(self, min_length: int = 35):
        self.min_length = min_length
        self._buffer = ""

    def push(self, token: str) -> list[str]:
        self._buffer += token or ""
        ready: list[str] = []
        while True:
            m = _DELIMS.search(self._buffer)
            if not m:
                break
            end = m.end()
            candidate = self._buffer[:end].strip()
            if len(candidate) >= self.min_length:
                cleaned = clean_for_speech(candidate)
                if cleaned:
                    ready.append(cleaned)
                self._buffer = self._buffer[end:].lstrip()
            else:
                nxt = _DELIMS.search(self._buffer[end:])
                if nxt:
                    new_end = end + nxt.end()
                    cleaned = clean_for_speech(self._buffer[:new_end].strip())
                    if cleaned:
                        ready.append(cleaned)
                    self._buffer = self._buffer[new_end:].lstrip()
                else:
                    break
        return ready

    def flush(self) -> list[str]:
        rem = clean_for_speech(self._buffer.strip())
        self._buffer = ""
        return [rem] if rem else []


# --- Motores TTS en memoria (PCM float32 en RAM) -----------------------------

class EdgeMemorySynth:
    """Edge-TTS decodificado a PCM en RAM con miniaudio (cero archivos temporales)."""

    name = "edge"

    def __init__(self, voice: str = "es-ES-XimenaNeural",
                 rate: str = "+6%", pitch: str = "+2Hz"):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch

    def available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            import miniaudio  # noqa: F401
            return True
        except Exception:
            return False

    def synth(self, text: str) -> Optional[tuple[np.ndarray, int]]:
        import edge_tts
        import miniaudio

        async def _fetch() -> bytes:
            comm = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
            buf = bytearray()
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            return bytes(buf)

        try:
            mp3 = asyncio.run(_fetch())
        except RuntimeError:
            # Ya hay un loop en este hilo: usa uno nuevo en un hilo aparte.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                mp3 = pool.submit(lambda: asyncio.run(_fetch())).result(timeout=16)
        if not mp3:
            return None
        decoded = miniaudio.decode(mp3, nchannels=1, sample_rate=24000)
        samples = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, decoded.sample_rate


class KokoroMemorySynth:
    """Kokoro-82M ONNX: voz local, instantánea y offline (fallback preferente)."""

    name = "kokoro"

    def __init__(self, model_path: str, voices_path: str,
                 voice: str = "ef_dora", speed: float = 1.05):
        self.model_path = model_path
        self.voices_path = voices_path
        self.voice = voice
        self.speed = speed
        self._k = None

    def available(self) -> bool:
        try:
            import kokoro_onnx  # noqa: F401
            return Path(self.model_path).is_file() and Path(self.voices_path).is_file()
        except Exception:
            return False

    def _engine(self):
        if self._k is None:
            from kokoro_onnx import Kokoro
            self._k = Kokoro(str(self.model_path), str(self.voices_path))
        return self._k

    def synth(self, text: str) -> Optional[tuple[np.ndarray, int]]:
        samples, sr = self._engine().create(text, voice=self.voice,
                                            speed=self.speed, lang="es")
        return np.asarray(samples, dtype=np.float32), sr


class PiperMemorySynth:
    """Piper (.onnx): fallback local offline, PCM en RAM."""

    name = "piper"
    _cache: dict = {}

    def __init__(self, model_path: str):
        self.model_path = model_path

    def available(self) -> bool:
        try:
            import piper  # noqa: F401
            return Path(self.model_path).is_file()
        except Exception:
            return False

    def _voice(self):
        v = PiperMemorySynth._cache.get(self.model_path)
        if v is None:
            from piper import PiperVoice
            v = PiperVoice.load(self.model_path)
            PiperMemorySynth._cache[self.model_path] = v
        return v

    def synth(self, text: str) -> Optional[tuple[np.ndarray, int]]:
        voice = self._voice()
        chunks = [np.frombuffer(a.audio_int16_bytes, dtype=np.int16)
                  for a in voice.synthesize(text)]
        if not chunks:
            return None
        pcm = np.concatenate(chunks).astype(np.float32) / 32768.0
        sr = getattr(voice.config, "sample_rate", 22050)
        return pcm, sr


class StreamingVoice:
    """Cola de síntesis+reproducción en memoria con interrupción (barge-in).

    Encola frases ya troceadas; un hilo las sintetiza y reproduce en orden. La
    cadena de motores prueba el primero disponible por frase, con fallback.
    """

    def __init__(self, synths: Iterable, on_state_change: Optional[Callable[[bool], None]] = None,
                 sink: str | None = None):
        self._synths = [s for s in synths]
        self.on_state_change = on_state_change
        self.sink = sink
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stop = threading.Event()
        self._abort = threading.Event()
        self._speaking = False
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def available(self) -> bool:
        try:
            import sounddevice  # noqa: F401
        except Exception:
            return False
        return any(s.available() for s in self._synths)

    def is_speaking(self) -> bool:
        with self._lock:
            return self._speaking

    def say(self, text: str) -> None:
        cleaned = clean_for_speech(text)
        if cleaned:
            self._abort.clear()
            self._queue.put(cleaned)

    def feed_tokens(self, tokens: Generator[str, None, None]) -> None:
        """Trocea un flujo de tokens del cerebro y lo encola por frases."""
        chunker = SentenceChunker()
        self._abort.clear()
        for tok in tokens:
            if self._stop.is_set():
                break
            for sentence in chunker.push(tok):
                self._queue.put(sentence)
        for sentence in chunker.flush():
            self._queue.put(sentence)

    def stop(self) -> None:
        """Barge-in: corta el audio y vacía la cola de frases pendientes."""
        self._abort.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._set_speaking(False)

    def _set_speaking(self, value: bool) -> None:
        with self._lock:
            if self._speaking != value:
                self._speaking = value
                if self.on_state_change:
                    try:
                        self.on_state_change(value)
                    except Exception:
                        pass

    def _synth(self, text: str) -> Optional[tuple[np.ndarray, int]]:
        for s in self._synths:
            try:
                if not s.available():
                    continue
                res = s.synth(text)
                if res is not None:
                    return res
            except Exception as exc:  # noqa: BLE001
                log.warning("motor de voz %s falló: %s", getattr(s, "name", s), exc)
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                text = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if text is None or self._abort.is_set():
                continue
            audio = self._synth(text)
            if audio is None:
                continue
            samples, sr = audio
            try:
                import sounddevice as sd
                self._set_speaking(True)
                sd.play(samples, samplerate=sr)
                stream = sd.get_stream()
                while stream and stream.active:
                    if self._abort.is_set() or self._stop.is_set():
                        sd.stop()
                        break
                    time.sleep(0.02)
            except Exception as exc:  # noqa: BLE001
                log.warning("reproducción en streaming falló: %s", exc)
            finally:
                if self._queue.empty():
                    self._set_speaking(False)

    def shutdown(self) -> None:
        self.stop()
        self._stop.set()
