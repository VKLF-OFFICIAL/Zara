"""Subsistema de Voz y Síntesis (ZARA).

Streaming por frases (Sentence Chunking), TTS dual (Kokoro-ONNX local + EdgeTTS en RAM),
cero escritura en disco para latencia mínima y capacidad de interrupción inmediata (Barge-in).

BUGS CORREGIDOS:
  - [BUG-5] EdgeTTSEngine: uso incorrecto de asyncio.get_event_loop() en Python >=3.10
    lanza DeprecationWarning y en hilos secundarios lanza RuntimeError. Reemplazado
    por asyncio.new_event_loop() con gestión explícita del bucle.
  - [BUG-6] _playback_worker: sd.get_stream() lanza RuntimeError si sounddevice
    no tiene stream activo aún. Protegido con try/except.
  - [BUG-7] VoiceSynthesizer.stop() vaciaba la cola de TTS pero NO establecía
    _is_speaking=False antes de que el worker notara el cambio, provocando que el
    HUD se quedara en estado SPEAKING tras un barge-in. Corregido: la actualización
    del estado ocurre atómicamente antes de vaciar la cola.
  - [BUG-8] SentenceChunker: el bucle interno en push() podía entrar en bucle
    infinito si el buffer nunca decrece (ej. texto sin delimitadores con coma
    que forman candidato largo). Añadido guard de progreso.
"""

from __future__ import annotations
import asyncio
import logging
import queue
import re
import threading
import time
from typing import AsyncGenerator, Callable, Generator, List, Optional
import numpy as np
import sounddevice as sd

from config import (
    EDGE_PITCH,
    EDGE_RATE,
    EDGE_VOICE,
    KOKORO_SPEED,
    KOKORO_VOICE,
    MODELS_DIR,
    TTS_PRIMARY_ENGINE,
)

log = logging.getLogger("zara.voice")


def clean_text_for_speech(text: str) -> str:
    """Elimina markdown pesado, asteriscos, código y caracteres impronunciables."""
    # Eliminar bloques de código markdown
    t = re.sub(r"```[\s\S]*?```", " código omitido en voz. ", text)
    # Eliminar código en línea
    t = re.sub(r"`([^`]+)`", r"\1", t)
    # Eliminar asteriscos de negrita/cursiva y almohadillas
    t = re.sub(r"[*#_~>]+", " ", t)
    # Eliminar URLs largas
    t = re.sub(r"https?://\S+", " enlace ", t)
    # Reemplazar saltos de línea por pausas
    t = re.sub(r"\n+", ". ", t)
    # Limpiar espacios múltiples
    t = re.sub(r"\s+", " ", t).strip()
    return t


FIRST_SENTENCE_MIN_CHARS = 18  # "Claro, Señor." ya es una primera frase válida


class SentenceChunker:
    """Acumulador de streaming de tokens que emite frases en cuanto alcanzan

    un delimitador válido y una longitud mínima (35 caracteres).
    """

    DELIMITERS = re.compile(r"([.!?:;\n]+)")

    def __init__(self, min_length: int = 35, first_min_length: Optional[int] = None):
        self.min_length = min_length
        # La PRIMERA frase de cada respuesta puede ser más corta: es la que marca cuánto
        # tarda en empezar a sonar Zara; las siguientes se sintetizan mientras habla.
        self.first_min_length = first_min_length if first_min_length is not None else min_length
        self._emitted_any = False
        self._buffer = ""

    def push(self, token: str) -> List[str]:
        """Añade un fragmento de texto y devuelve una lista de frases listas."""
        self._buffer += token
        ready: List[str] = []

        # [BUG-8 FIX] Track buffer length before each iteration to detect no-progress
        while True:
            prev_len = len(self._buffer)
            match = self.DELIMITERS.search(self._buffer)
            if not match:
                break

            end_idx = match.end()
            candidate = self._buffer[:end_idx].strip()

            threshold = self.min_length if self._emitted_any else self.first_min_length
            if len(candidate) >= threshold:
                cleaned = clean_text_for_speech(candidate)
                if cleaned:
                    ready.append(cleaned)
                    self._emitted_any = True
                self._buffer = self._buffer[end_idx:].lstrip()
            else:
                # Si es muy corto (ej: "Sra."), buscar el siguiente delimitador
                next_match = self.DELIMITERS.search(self._buffer[end_idx:])
                if next_match:
                    new_end = end_idx + next_match.end()
                    cand2 = self._buffer[:new_end].strip()
                    cleaned = clean_text_for_speech(cand2)
                    if cleaned:
                        ready.append(cleaned)
                        self._emitted_any = True
                    self._buffer = self._buffer[new_end:].lstrip()
                else:
                    break

            # [BUG-8 FIX] Guard: si el buffer no decreció, romper para evitar bucle infinito
            if len(self._buffer) >= prev_len:
                break

        return ready

    def flush(self) -> List[str]:
        """Vacía el contenido restante al terminar la generación del LLM."""
        ready: List[str] = []
        rem = clean_text_for_speech(self._buffer.strip())
        if rem:
            ready.append(rem)
        self._buffer = ""
        return ready


class KokoroEngine:
    """Motor TTS primario local ultrarrápido con Kokoro-ONNX."""

    def __init__(self):
        self._kokoro = None
        self._available = False
        self._init()

    def _init(self) -> None:
        try:
            from kokoro_onnx import Kokoro
            model_path = MODELS_DIR / "kokoro" / "kokoro-v0_19.onnx"
            voices_path = MODELS_DIR / "kokoro" / "voices.bin"

            if model_path.is_file() and voices_path.is_file():
                self._kokoro = Kokoro(str(model_path), str(voices_path))
                self._available = True
                log.info("Kokoro-ONNX cargado exitosamente.")
            else:
                log.info("Archivos de modelo Kokoro no encontrados en %s. Fallback a EdgeTTS.", model_path.parent)
        except Exception as exc:
            log.warning("No se pudo iniciar Kokoro-ONNX (%s). Fallback a EdgeTTS.", exc)

    def available(self) -> bool:
        return self._available

    def synthesize(self, text: str) -> Optional[tuple[np.ndarray, int]]:
        if not self._available or not self._kokoro:
            return None
        try:
            samples, sr = self._kokoro.create(
                text,
                voice=KOKORO_VOICE,
                speed=KOKORO_SPEED,
                lang="es",
            )
            return samples.astype(np.float32), sr
        except Exception as exc:
            log.warning("Kokoro falló sintetizando '%s': %s", text[:30], exc)
            return None


class EdgeTTSEngine:
    """Motor TTS alternativo de alta expresividad y cero latencia en RAM."""

    def __init__(self):
        self.voice = EDGE_VOICE
        self.rate = EDGE_RATE
        self.pitch = EDGE_PITCH

    def synthesize(self, text: str) -> Optional[tuple[np.ndarray, int]]:
        """Sintetiza texto en memoria y decodifica a PCM con miniaudio (cero archivos en disco)."""
        import edge_tts
        import miniaudio

        async def _fetch():
            comm = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
            buf = bytearray()
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            return bytes(buf)

        try:
            # [BUG-5 FIX] Crear siempre un nuevo event loop aislado para este hilo.
            # asyncio.get_event_loop() está deprecado en Python >=3.10 en hilos
            # que no son el principal y lanza RuntimeError en algunos entornos.
            loop = asyncio.new_event_loop()
            try:
                mp3_bytes = loop.run_until_complete(_fetch())
            finally:
                loop.close()

            if not mp3_bytes:
                return None

            # Decodificar MP3 en RAM directamente a PCM float32
            decoded = miniaudio.decode(mp3_bytes, nchannels=1, sample_rate=24000)
            samples = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32) / 32768.0
            return samples, decoded.sample_rate

        except Exception as exc:
            log.exception("Fallo en la síntesis con EdgeTTS: %s", exc)
            return None


class VoiceSynthesizer:
    """Gestor de voz de Zara con síntesis adelantada (pipeline), reproducción en
    streaming y cancelación Barge-in.

    [FIX] AUDITORÍA DE VOZ: medido en real, cada llamada de síntesis a EdgeTTS
    tarda ~1.2-1.6s de ida y vuelta de red. La implementación anterior era
    estrictamente secuencial (sintetizar frase -> reproducirla -> sintetizar la
    siguiente -> reproducirla...), así que entre CADA frase de cualquier
    respuesta de más de una frase había un corte de silencio de más de un
    segundo mientras se esperaba a la red: eso es lo que se percibía como
    "se corta cuando habla". Ahora hay dos hilos independientes: uno de
    SÍNTESIS que va por delante sintetizando la siguiente frase mientras la
    actual está sonando, y uno de REPRODUCCIÓN que solo consume audio ya listo.
    """

    def __init__(self, on_state_change: Optional[Callable[[bool], None]] = None):
        self.on_state_change = on_state_change

        self._kokoro = KokoroEngine()
        self._edge = EdgeTTSEngine()

        self._tts_queue: queue.Queue[Optional[str]] = queue.Queue()
        # [FIX] Cola de audio YA sintetizado, lista para reproducir sin más
        # espera. El hilo de síntesis la alimenta por delante del de reproducción.
        # Cada item es (samples, sr, generation) — ver _synthesis_worker/_playback_worker.
        self._audio_queue: queue.Queue[tuple] = queue.Queue(maxsize=5)
        self._stop_event = threading.Event()
        self._is_speaking = False
        # [FIX AUDITORÍA] RLock en vez de Lock: defensa en profundidad para que
        # un futuro callback de on_state_change que reentre en is_speaking()/
        # say() desde el mismo hilo no provoque un deadlock.
        self._lock = threading.RLock()
        # Contador de frases pendientes (encoladas para sintetizar o ya
        # sintetizadas y pendientes de reproducir). Solo cuando llega a 0
        # consideramos que Zara ha terminado de hablar de verdad.
        self._pending_count = 0
        # Generación monótona: al abortar por barge-in se incrementa, así el
        # hilo de síntesis descarta cualquier audio "en vuelo" de la tanda
        # anterior en vez de dejarlo resucitar en la cola de reproducción.
        self._generation = 0

        self._synthesis_thread = threading.Thread(target=self._synthesis_worker, daemon=True)
        self._playback_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self._synthesis_thread.start()
        self._playback_thread.start()

    def is_speaking(self) -> bool:
        with self._lock:
            return self._is_speaking

    def stop(self) -> None:
        """Interrupción inmediata (Barge-in): detiene el audio y vacía las colas.

        [BUG-7 FIX] Actualizar _is_speaking=False ANTES de llamar sd.stop() y
        vaciar las colas, para que el worker no emita on_state_change(False)
        redundantemente después de que nosotros ya lo hemos hecho.
        """
        with self._lock:
            if self._is_speaking:
                self._is_speaking = False
                if self.on_state_change:
                    try:
                        self.on_state_change(False)
                    except Exception:
                        pass
            self._pending_count = 0
            self._generation += 1

        # Detener reproducción actual
        try:
            sd.stop()
        except Exception:
            pass

        # Vaciar colas de frases y audio pendientes
        for q in (self._tts_queue, self._audio_queue):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

        log.info("Reproducción de voz abortada por Barge-in.")

    def say(self, text: str) -> None:
        """Encola una frase completa para ser sintetizada."""
        clean = clean_text_for_speech(text)
        if clean:
            with self._lock:
                self._pending_count += 1
            self._tts_queue.put(clean)

    def feed_token_stream(self, token_generator: Generator[str, None, None]) -> None:
        """Procesa un generador síncrono de tokens del LLM usando SentenceChunker."""
        chunker = SentenceChunker(min_length=35, first_min_length=FIRST_SENTENCE_MIN_CHARS)
        for token in token_generator:
            if self._stop_event.is_set():
                break
            for sentence in chunker.push(token):
                self._enqueue_sentence(sentence)

        for sentence in chunker.flush():
            self._enqueue_sentence(sentence)

    async def feed_async_token_stream(self, async_generator: AsyncGenerator[str, None]) -> None:
        """Procesa un generador asíncrono de tokens del LLM usando SentenceChunker."""
        chunker = SentenceChunker(min_length=35, first_min_length=FIRST_SENTENCE_MIN_CHARS)
        async for token in async_generator:
            if self._stop_event.is_set():
                break
            for sentence in chunker.push(token):
                self._enqueue_sentence(sentence)

        for sentence in chunker.flush():
            self._enqueue_sentence(sentence)

    def _enqueue_sentence(self, sentence: str) -> None:
        with self._lock:
            self._pending_count += 1
        self._tts_queue.put(sentence)

    def _synthesize_audio(self, text: str) -> Optional[tuple[np.ndarray, int]]:
        """Intenta sintetizar con Kokoro si está habilitado; de lo contrario EdgeTTS."""
        if TTS_PRIMARY_ENGINE == "kokoro" and self._kokoro.available():
            res = self._kokoro.synthesize(text)
            if res is not None:
                return res

        return self._edge.synthesize(text)

    def _synthesis_worker(self) -> None:
        """[FIX] Hilo dedicado a la síntesis, por delante de la reproducción.

        Convierte texto en audio en segundo plano mientras la frase anterior
        suena, para que la latencia de red de EdgeTTS (~1.2-1.6s medidos) no
        se perciba como un corte de silencio entre cada frase.

        [FIX AUDITORÍA] Antes la comprobación de generación ("¿sigue vigente
        este turno?") y el `self._audio_queue.put(...)` ocurrían en dos
        bloques `with self._lock` SEPARADOS. Entre uno y otro, `stop()` podía
        ejecutarse en otro hilo e incrementar la generación / vaciar la cola
        *antes* de que este `put()` insertara el audio — y ese audio ya
        "cancelado" acababa resucitando en `_audio_queue` igualmente. Ahora
        todo el ciclo comprobar-generación + encolar es atómico bajo el mismo
        `with self._lock`, y cada item se etiqueta con su generación para que
        `_playback_worker` pueda volver a comprobarla justo antes de sonar.
        """
        while not self._stop_event.is_set():
            try:
                text = self._tts_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if text is None:
                with self._lock:
                    self._pending_count = max(0, self._pending_count - 1)
                continue

            with self._lock:
                gen = self._generation

            t0 = time.monotonic()
            audio_data = self._synthesize_audio(text)
            log.info("[LATENCIA] TTS %.2f s para %d caracteres", time.monotonic() - t0, len(text))

            with self._lock:
                # Tanda abortada por barge-in mientras sintetizábamos: esta
                # generación ya no es la actual. NO tocamos _pending_count:
                # ese contador pertenece íntegramente a la generación ACTUAL
                # (stop() ya lo puso a 0 cuando canceló esta); descontar aquí
                # restaría trabajo de un turno nuevo que nada tiene que ver.
                if gen != self._generation:
                    continue
                if audio_data is None:
                    self._pending_count = max(0, self._pending_count - 1)
                    continue
                try:
                    self._audio_queue.put_nowait((audio_data[0], audio_data[1], gen))
                except queue.Full:
                    log.warning("Cola de audio llena: frase sintetizada descartada.")
                    self._pending_count = max(0, self._pending_count - 1)

    def _playback_worker(self) -> None:
        """Hilo trabajador que solo reproduce audio ya sintetizado y listo.

        [FIX AUDITORÍA] Cada item lleva la generación con la que se sintetizó.
        Si cambió (barge-in) mientras esperaba en cola o a mitad de
        reproducción, se descarta/corta sin tocar el contador de la
        generación actual (que ya gestiona su propio trabajo pendiente).
        """
        while not self._stop_event.is_set():
            try:
                item = self._audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            samples, sr, item_gen = item

            with self._lock:
                if item_gen != self._generation:
                    # Turno ya cancelado mientras esperaba en cola: descartar.
                    continue
                self._is_speaking = True
                if self.on_state_change:
                    try:
                        self.on_state_change(True)
                    except Exception:
                        pass

            try:
                sd.play(samples, samplerate=sr)
                # [BUG-6 FIX] sd.get_stream() lanza RuntimeError si no hay stream;
                # usar sd.wait() con intervalo de polling manual es más seguro.
                while True:
                    with self._lock:
                        still_current = item_gen == self._generation
                    if not still_current or not self._is_speaking or self._stop_event.is_set():
                        sd.stop()
                        break
                    try:
                        stream = sd.get_stream()
                        if stream is None or not stream.active:
                            break
                    except RuntimeError:
                        break
                    time.sleep(0.02)
            except Exception as exc:
                log.warning("Error durante la reproducción en sounddevice: %s", exc)
            finally:
                with self._lock:
                    if item_gen == self._generation:
                        self._pending_count = max(0, self._pending_count - 1)
                        if self._pending_count == 0:
                            self._is_speaking = False
                            if self.on_state_change:
                                try:
                                    self.on_state_change(False)
                                except Exception:
                                    pass

    def shutdown(self) -> None:
        """Cierre ordenado del motor de voz."""
        self.stop()
        self._stop_event.set()
        if self._synthesis_thread.is_alive():
            self._synthesis_thread.join(timeout=1.0)
        if self._playback_thread.is_alive():
            self._playback_thread.join(timeout=1.0)
