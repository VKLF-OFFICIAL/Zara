"""Motor de Voz Bidireccional en Tiempo Real (ZARA — Gemini Live).

Streaming de audio nativo full-duplex sobre WebSocket persistente con el modelo
Gemini Live. Elimina completamente la cascada secuencial STT → LLM → TTS:

  [Micrófono] ════(PCM 16kHz)══════> [ Gemini Live API ]
  [Altavoz]   <═════(PCM 24kHz)═════ [ (Procesamiento Nativo) ]

Ventajas frente al pipeline clásico:
  - Latencia ultra baja: el modelo procesa audio en streaming, no espera al
    final de la frase ni a la transcripción completa.
  - Barge-in nativo: si el Señor VK interrumpe, el servidor señaliza
    `server_content.interrupted` y corta la emisión de audio al instante.
  - Comprensión multimodal: el modelo entiende tono, énfasis, urgencia e
    idioma directamente de la onda de sonido, sin pérdida de información.
  - Tool calling en sesión: el modelo puede invocar herramientas de CONSULTA
    de ZARA (web_search, inspect_screen y os_control inocuo) durante la
    conversación en vivo y vocalizar el resultado sin cambiar de modo. Las
    herramientas que cambian algo (run_shell, file_manager, abrir/cerrar apps,
    portapapeles, Claude Code) solo están en el modo clásico, que exige la
    palabra de activación.
"""

from __future__ import annotations
import asyncio
import copy
import json
import logging
import math
import os
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("zara.live")

# [FIX MUL-2] En Linux sin la librería de sistema PortAudio (libportaudio2), importar
# sounddevice lanza OSError («PortAudio library not found»), no ImportError. Antes ese
# error tumbaba `import core.live` y con él main.py entero (también --cli). Ahora el modo
# Live queda desactivado (is_available() devuelve False) y el resto de Zara arranca.
try:
    import sounddevice as sd
except (ImportError, OSError) as _sd_exc:  # depende de las librerías del sistema
    sd = None
    _SD_IMPORT_ERROR = str(_sd_exc)
    log.warning(
        "sounddevice no disponible (%s): Gemini Live desactivado. En Linux instale PortAudio "
        "(sudo apt install libportaudio2 en Debian/Kali, sudo dnf install portaudio en Fedora).",
        _sd_exc,
    )
else:
    _SD_IMPORT_ERROR = ""

from config import (
    GEMINI_API_KEY,
    GEMINI_LIVE_MODEL,
    GEMINI_LIVE_MODEL_FALLBACK,
    GEMINI_LIVE_VOICE,
    get_system_prompt,
)
from core.gemini_models import resolve_gemini_model
from core.tools import TOOL_DEFINITIONS, execute_tool

# Formatos de audio requeridos por Gemini Live API
LIVE_INPUT_SAMPLE_RATE = 16000   # PCM 16-bit LE, mono, 16 kHz
LIVE_OUTPUT_SAMPLE_RATE = 24000  # PCM 16-bit LE, mono, 24 kHz
LIVE_CHUNK_DURATION_MS = 100     # Tamaño de cada chunk enviado (100 ms)
LIVE_INPUT_CHUNK_SAMPLES = int(LIVE_INPUT_SAMPLE_RATE * LIVE_CHUNK_DURATION_MS / 1000)

# Modelo por defecto para sesiones Live (debe soportar la API Live)
# (gemini-2.0-flash-live-001 y gemini-live-2.5-flash-preview se retiraron en dic-2025)
LIVE_MODEL_CANDIDATES = [
    "gemini-2.5-flash-native-audio-preview-12-2025",
]

# Voces disponibles en Gemini Live (español: Aoede y Fenrir suenan bien)
LIVE_VOICE_NAME = "Aoede"

# [FIX AUD-5] Colas acotadas y reconexión con espera exponencial.
LIVE_MIC_QUEUE_MAX = 50          # ~5 s de micrófono (chunks de 100 ms); se descarta lo más antiguo
LIVE_PLAYBACK_QUEUE_MAX = 200
LIVE_RECONNECT_MIN_S = 3.0
LIVE_RECONNECT_MAX_S = 60.0
LIVE_STABLE_SESSION_S = 60.0     # una sesión que duró esto reinicia la espera de reconexión

# Trozo máximo que se escribe de una vez en el altavoz (50 ms): entre trozos se atiende
# el barge-in, así que un chunk largo del servidor no retrasa la interrupción.
_PLAYBACK_SLICE_SAMPLES = LIVE_OUTPUT_SAMPLE_RATE // 20
_OUTPUT_RETRY_S = 2.0            # si no se pudo abrir el altavoz, reintentar pasado este tiempo
_TURN_END = b"__TURN_END__"      # marca de fin de turno en la cola de reproducción


def _env_float(name: str, default: float) -> float:
    """Lee un umbral no negativo del entorno (acepta coma decimal); si no vale, usa el defecto."""
    raw = os.environ.get(name, "")
    text = raw.split("#", 1)[0].strip().replace(",", ".")
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value) or value < 0:
        log.warning("%s=%r no es un número válido; uso %s.", name, raw, default)
        return default
    return value


# [FIX AUD-12] Supresión de eco sencilla: sounddevice no cancela el eco, así que con
# altavoces el micrófono capta la voz de Zara y el VAD del servidor la toma por el usuario
# (se interrumpe sola o se contesta a sí misma). Mientras el modelo habla (y un poco
# después, por la cola del altavoz y la reverberación) se envía silencio en lugar de los
# chunks por debajo de este RMS; una voz claramente más fuerte sí pasa (barge-in).
# ZARA_LIVE_BARGE_IN_RMS=0 lo desactiva (con auriculares o con module-echo-cancel de PipeWire).
LIVE_BARGE_IN_RMS = _env_float("ZARA_LIVE_BARGE_IN_RMS", 0.05)
LIVE_ECHO_TAIL_S = 0.35

# [FIX SEG-3] En Live no hay palabra de activación: el micrófono envía TODO lo que capta
# (TV, un vídeo, una llamada en altavoz), y la confirmación hablada podría contestarla esa
# misma fuente. Lo más seguro es no ofrecer a Live nada que cambie el sistema: solo
# consultas y controles inocuos. Lo demás se hace en el modo clásico con «Zara».
LIVE_SAFE_TOOLS = frozenset({"web_search", "inspect_screen"})
LIVE_SAFE_OS_ACTIONS = frozenset({
    "system_stats", "network_info", "list_processes",
    "media_play_pause", "media_next", "media_prev",
    "set_volume", "mute", "unmute",
    "show_desktop", "lock_screen", "notify",
})
LIVE_BLOCKED_TOOL_ERROR = (
    "En modo Live solo se permiten herramientas de consulta (sin palabra de activación, "
    "cualquier voz cercana podría pedirlas); use el modo clásico con palabra de activación."
)
# Se añade al prompt de sistema para que el modelo no prometa lo que en Live no puede hacer.
LIVE_PROMPT_NOTE = (
    "MODO LIVE: aquí no hay palabra de activación, así que solo dispones de herramientas de "
    "consulta (web_search, inspect_screen y os_control sin abrir ni cerrar aplicaciones ni "
    "tocar el portapapeles). Para ejecutar comandos, modificar archivos o abrir programas, "
    "pide al Señor VK que use el modo clásico."
)
_LIVE_OS_DESCRIPTION = (
    "Consultas y controles inocuos del sistema operativo: telemetría de CPU/RAM/disco "
    "(system_stats), red e IP (network_info), procesos (list_processes), multimedia "
    "(media_play_pause, media_next, media_prev), volumen (set_volume, mute, unmute), "
    "mostrar el escritorio (show_desktop), bloquear la pantalla (lock_screen) y "
    "notificaciones (notify)."
)


def _live_safe_call(name: Any, args: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Forma canónica de la llamada si está permitida en Live; None si hay que bloquearla.

    Se tienen en cuenta los alias que acepta execute_tool (p. ej. {"name": "mute"} en vez de
    os_control/mute), pero se devuelve la llamada ya resuelta para que lo que se ejecuta sea
    exactamente lo que se ha comprobado.
    """
    clean = str(name or "").strip().lower()
    args = dict(args) if isinstance(args, dict) else {}
    action = str(args.get("action") or "").strip().lower()
    if clean in LIVE_SAFE_TOOLS:
        return clean, args
    if clean == "os_control" and action in LIVE_SAFE_OS_ACTIONS:
        return "os_control", {**args, "action": action}
    if clean in LIVE_SAFE_OS_ACTIONS and action in ("", clean):
        return "os_control", {**args, "action": clean}
    return None


def _live_tool_definitions() -> List[Dict[str, Any]]:
    """Subconjunto de TOOL_DEFINITIONS que se declara al modelo en Live (ver SEG-3)."""
    defs: List[Dict[str, Any]] = []
    for t in TOOL_DEFINITIONS:
        name = t.get("name")
        if name in LIVE_SAFE_TOOLS:
            defs.append(t)
        elif name == "os_control":
            try:
                t = copy.deepcopy(t)
                props = t["parameters"]["properties"]
                action = props["action"]
                action["enum"] = [a for a in action.get("enum", []) if a in LIVE_SAFE_OS_ACTIONS]
                # target solo sirve para abrir/cerrar apps, que en Live no se ofrecen
                props.pop("target", None)
                t["description"] = _LIVE_OS_DESCRIPTION
                defs.append(t)
            except (KeyError, TypeError, AttributeError) as exc:
                log.warning("No se pudo adaptar os_control para Live: %s", exc)
    return defs


def _is_turn_complete(server_content: Any) -> bool:
    """Igual que el SDK: fin de turno por turn_complete o por interaction_status == IDLE."""
    if getattr(server_content, "turn_complete", False):
        return True
    status = getattr(server_content, "interaction_status", None)
    return status is not None and getattr(status, "name", str(status)) == "IDLE"


def _put_dropping_oldest(q: asyncio.Queue, item: Any) -> None:
    """Encola sin bloquear; si la cola está llena descarta el elemento más antiguo."""
    for _ in range(2):
        try:
            q.put_nowait(item)
            return
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass


class GeminiLiveSession:
    """Sesión bidireccional con Gemini Live API.

    Gestiona la captura de micrófono, el envío continuo de audio PCM al
    servidor, la recepción de audio de respuesta para reproducción inmediata,
    y la ejecución de herramientas cuando el modelo las solicita.
    """

    def __init__(
        self,
        api_key: str = GEMINI_API_KEY,
        model: str = GEMINI_LIVE_MODEL,
        voice_name: str = GEMINI_LIVE_VOICE,
        on_transcript_in: Optional[Callable[[str], None]] = None,
        on_transcript_out: Optional[Callable[[str], None]] = None,
        on_tool_event: Optional[Callable[[str, str], None]] = None,
        on_state_change: Optional[Callable[[str], None]] = None,
        on_rms_level: Optional[Callable[[float], None]] = None,
    ):
        self._api_key = api_key
        self.model = model
        self.voice_name = voice_name
        self._on_transcript_in = on_transcript_in    # Transcripción de voz del usuario
        self._on_transcript_out = on_transcript_out  # Transcripción de voz de Zara
        self._on_tool_event = on_tool_event
        self._on_state_change = on_state_change
        self._on_rms_level = on_rms_level

        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._main_task: Optional[asyncio.Task] = None
        self._generation = 0  # cada start() es una sesión nueva; la anterior no la pisa
        # [FIX AUD-3] Herramientas en curso (referencias fuertes: si no, el GC puede recogerlas)
        self._tool_tasks: set = set()

        # Control de reproducción y barge-in
        self._playback_queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=LIVE_PLAYBACK_QUEUE_MAX)
        self._playback_thread: Optional[threading.Thread] = None
        self._is_model_speaking = False
        self._echo_guard_until = 0.0     # instante (monotonic) hasta el que dura el eco del altavoz
        self._flush_requested = threading.Event()  # pide al hilo de reproducción cortar el audio
        self._muted = threading.Event()  # Si está set, el mic está silenciado

        # Cola de audio capturado del micrófono (PCM 16-bit LE bytes)
        self._mic_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=LIVE_MIC_QUEUE_MAX)

    def set_model(self, model: str) -> None:
        """Cambia el modelo Live para la próxima conexión."""
        self.model = model
        log.info("Modelo Gemini Live cambiado a: %s", model)

    def is_available(self) -> bool:
        """Comprueba si Gemini Live está disponible (API key + SDK + PortAudio)."""
        if not self._api_key:
            return False
        if sd is None:
            return False
        try:
            from google import genai  # noqa: F401
            return True
        except ImportError:
            return False

    def is_running(self) -> bool:
        return self._running

    def is_model_speaking(self) -> bool:
        return self._is_model_speaking

    def set_muted(self, muted: bool) -> None:
        if muted:
            self._muted.set()
        else:
            self._muted.clear()

    def is_muted(self) -> bool:
        return self._muted.is_set()

    def start(self) -> None:
        """Inicia la sesión Live en un hilo dedicado con su propio event loop."""
        if self._running:
            log.warning("La sesión Live ya está activa.")
            return

        if not self.is_available():
            if sd is None:
                log.error("Gemini Live no disponible: falta PortAudio (%s).", _SD_IMPORT_ERROR)
            else:
                log.error("Gemini Live no disponible: falta GEMINI_API_KEY o el SDK google-genai.")
            return

        # [FIX AUD-9] Estado limpio en cada sesión: antes la cola de reproducción era la
        # misma de la sesión anterior y podía conservar audio viejo y el None de parada,
        # con lo que la sesión nueva reproducía restos y luego se quedaba muda.
        self._playback_queue = queue.Queue(maxsize=LIVE_PLAYBACK_QUEUE_MAX)
        self._flush_requested.clear()
        self._is_model_speaking = False
        self._echo_guard_until = 0.0
        self._generation += 1
        generation = self._generation

        self._running = True
        self._thread = threading.Thread(
            target=self._run_event_loop, args=(generation,), daemon=True, name="zara-live"
        )
        self._thread.start()

        # Hilo de reproducción de audio de salida (con SU cola: un hilo viejo no roba audio)
        self._playback_thread = threading.Thread(
            target=self._playback_worker, args=(self._playback_queue,), daemon=True, name="zara-live-playback"
        )
        self._playback_thread.start()

        log.info("Sesión Gemini Live iniciada.")

    def stop(self) -> None:
        """Detiene la sesión Live de forma ordenada."""
        self._running = False
        # [FIX AUD-9] Vaciar la cola y cortar el audio pendiente antes de la señal de fin.
        self._flush_playback(emit=False)
        try:
            self._playback_queue.put_nowait(None)
        except queue.Full:
            pass

        # [FIX AUD-9] Cancelar la tarea principal en vez de loop.stop(): la cancelación
        # recorre las tareas hijas y ejecuta la salida de `with sd.InputStream` y de
        # `async with live.connect`, así que el micrófono y el WebSocket se cierran ya
        # (antes run_until_complete fallaba con «Event loop stopped before Future
        # completed» y ambos quedaban abiertos hasta que actuase el GC).
        loop, task = self._loop, self._main_task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:  # el bucle ya se cerró
                pass

        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=1.0)

        log.info("Sesión Gemini Live detenida.")

    def interrupt(self) -> None:
        """Interrumpe la reproducción actual (barge-in manual desde el HUD)."""
        self._flush_playback()

    # ─────────────────────────────────────────────────────────────────────
    # Event Loop Asíncrono
    # ─────────────────────────────────────────────────────────────────────

    def _run_event_loop(self, generation: Optional[int] = None) -> None:
        """Ejecuta el bucle asíncrono principal en un hilo dedicado."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        main_task: Optional[asyncio.Task] = None
        try:
            main_task = loop.create_task(self._session_loop())
            self._main_task = main_task
            loop.run_until_complete(main_task)
        except asyncio.CancelledError:
            log.debug("Sesión Gemini Live cancelada.")
        except Exception as exc:
            log.exception("Error fatal en la sesión Gemini Live: %s", exc)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            if self._main_task is main_task:
                self._main_task = None
            if self._loop is loop:
                self._loop = None
            # Si mientras tanto arrancó otra sesión, no se toca su estado.
            if generation is None or generation == self._generation:
                self._running = False
                self._emit_state("disconnected")

    async def _session_loop(self) -> None:
        """Bucle principal: conecta, envía audio y recibe respuestas (reconecta si cae)."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)

        # Construir declaraciones de herramientas para la sesión Live
        live_tools = self._build_live_tools(types)

        # Construir configuración de voz
        speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=self.voice_name or LIVE_VOICE_NAME,
                )
            )
        )

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=speech_config,
            system_instruction=f"{get_system_prompt()}\n\n{LIVE_PROMPT_NOTE}",
            tools=live_tools,
            temperature=0.7,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        # Determinar modelo Live
        model_id = await asyncio.to_thread(self._resolve_live_model)

        # [FIX AUD-5] Espera exponencial: un fallo permanente (clave sin acceso a Live,
        # cuota 429) ya no se reintenta cada 3 s para siempre.
        delay = LIVE_RECONNECT_MIN_S
        while self._running:
            connected_at: Optional[float] = None
            try:
                log.info("Conectando a Gemini Live con modelo: %s", model_id)
                self._emit_state("connecting")

                async with client.aio.live.connect(model=model_id, config=config) as session:
                    connected_at = time.monotonic()
                    log.info("Sesión Gemini Live conectada exitosamente.")
                    self._emit_state("connected")
                    await self._run_connected_session(session, types)

            except Exception as exc:
                if not self._running:
                    break
                if connected_at is not None and time.monotonic() - connected_at >= LIVE_STABLE_SESSION_S:
                    delay = LIVE_RECONNECT_MIN_S
                log.warning("Sesión Live desconectada: %s. Reconectando en %.0f s...", exc, delay)
                self._emit_state("reconnecting")
                await self._wait_before_retry(delay)
                delay = min(delay * 2, LIVE_RECONNECT_MAX_S)

    async def _run_connected_session(self, session, types_module) -> None:
        """Ejecuta envío, recepción y micrófono hasta que uno termine; entonces cierra los demás.

        [FIX AUD-5] Antes se usaba gather(): si el WebSocket caía, recepción y envío
        terminaban pero el micrófono seguía vivo, gather no acababa nunca, no se reconectaba
        y la cola del micrófono crecía sin límite. Ahora la primera tarea que termina cierra
        la sesión y su error (o un ConnectionError) llega a _session_loop, que reconecta.
        """
        # Reinicializar la cola de mic para la nueva conexión
        self._mic_queue = asyncio.Queue(maxsize=LIVE_MIC_QUEUE_MAX)

        tasks = {
            asyncio.create_task(self._send_audio_loop(session), name="zara-live-send"),
            asyncio.create_task(self._receive_loop(session, types_module), name="zara-live-recv"),
            asyncio.create_task(self._mic_capture_loop(), name="zara-live-mic"),
        }
        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            pending = tasks | set(self._tool_tasks)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self._tool_tasks.clear()
            # [FIX AUD-12] Si el WebSocket cae mientras Zara habla (p. ej. GoAway al límite
            # de la sesión) nunca llegará el turn_complete de ese turno: sin esto
            # _is_model_speaking se quedaba en True y el filtro de eco silenciaba el
            # micrófono en la sesión reconectada. El turno se perdió con la conexión, así
            # que se corta su audio pendiente. Con stop() en curso no se toca la cola (ya
            # se vació y lleva la señal de fin del hilo de reproducción).
            if self._running:
                self._flush_playback(emit=False)

        for t in done:
            if not t.cancelled() and t.exception() is not None:
                raise t.exception()
        if self._running:
            raise ConnectionError("la sesión Live terminó sin aviso del servidor")

    async def _wait_before_retry(self, delay: float) -> None:
        """Espera `delay` segundos antes de reconectar; sale antes si se detiene la sesión."""
        end = time.monotonic() + delay
        while self._running:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.25))

    async def _mic_capture_loop(self) -> None:
        """Captura continua de micrófono a 16 kHz y encola chunks PCM 16-bit."""
        if sd is None:
            raise RuntimeError(f"sounddevice no disponible: {_SD_IMPORT_ERROR}")
        loop = asyncio.get_running_loop()
        mic_buffer: asyncio.Queue = asyncio.Queue(maxsize=LIVE_MIC_QUEUE_MAX)

        def _sd_callback(indata, frames, time_info, status):
            if status:
                log.debug("sounddevice status: %s", status)
            if not self._running:
                return
            # Convertir float32 [-1, 1] a int16 PCM bytes (little-endian)
            pcm_float = indata[:, 0]
            rms = float(np.sqrt(np.mean(pcm_float ** 2))) if len(pcm_float) else 0.0

            # Emitir RMS para el HUD
            if self._on_rms_level and not self._muted.is_set():
                try:
                    self._on_rms_level(rms)
                except Exception:
                    pass

            pcm_int16 = np.clip(pcm_float * 32767, -32768, 32767).astype(np.int16)
            try:
                loop.call_soon_threadsafe(_put_dropping_oldest, mic_buffer, (pcm_int16.tobytes(), rms))
            except RuntimeError:  # el bucle ya se cerró (parada en curso)
                pass

        # Abrir stream de sounddevice
        stream = sd.InputStream(
            samplerate=LIVE_INPUT_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=LIVE_INPUT_CHUNK_SAMPLES,
            callback=_sd_callback,
        )

        # La cancelación (stop o caída de la sesión) se propaga: `with` cierra el micrófono.
        with stream:
            log.info("Captura de micrófono Live activa a %d Hz.", LIVE_INPUT_SAMPLE_RATE)
            while self._running:
                try:
                    chunk, rms = await asyncio.wait_for(mic_buffer.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if self._muted.is_set():
                    continue
                # [FIX AUD-5] Cola acotada: si el envío se atasca se pierde lo más antiguo
                # en lugar de acumular ~32 KB/s sin límite.
                _put_dropping_oldest(self._mic_queue, self._gate_echo(chunk, rms))

    def _model_audio_active(self) -> bool:
        """True mientras suena (o acaba de sonar) la voz del modelo por el altavoz."""
        return self._is_model_speaking or time.monotonic() < self._echo_guard_until

    def _gate_echo(self, chunk: bytes, rms: float) -> bytes:
        """[FIX AUD-12] Sustituye por silencio el eco del altavoz mientras habla el modelo.

        Se envía silencio (no se deja de enviar) para que el flujo siga siendo continuo y el
        VAD del servidor funcione; una voz por encima de LIVE_BARGE_IN_RMS pasa tal cual.
        """
        threshold = LIVE_BARGE_IN_RMS
        if threshold <= 0 or rms >= threshold or not self._model_audio_active():
            return chunk
        return bytes(len(chunk))

    async def _send_audio_loop(self, session) -> None:
        """Envía chunks de audio del micrófono al servidor en tiempo real."""
        from google.genai import types

        while self._running:
            try:
                chunk = await asyncio.wait_for(self._mic_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            # [FIX AUD-5] Un fallo de envío (WebSocket cerrado) se propaga para reconectar.
            await session.send_realtime_input(
                audio=types.Blob(
                    data=chunk,
                    mime_type=f"audio/pcm;rate={LIVE_INPUT_SAMPLE_RATE}",
                )
            )

    async def _receive_loop(self, session, types_module) -> None:
        """Recibe y procesa las respuestas del servidor (audio, texto, tool calls).

        [FIX AUD-1] `session.receive()` del SDK google-genai termina tras CADA turno completo
        (hace break al llegar turn_complete). Sin el bucle exterior, Zara solo contestaba la
        primera pregunta y luego nadie volvía a leer el WebSocket. Los errores de conexión
        no se tragan aquí: llegan a _session_loop, que reconecta.
        """
        while self._running:
            received = False
            async for response in session.receive():
                received = True
                if not self._running:
                    return
                self._handle_server_message(session, response, types_module)
            if not received:
                # El SDK siempre entrega al menos un mensaje antes de cerrar el turno.
                raise ConnectionError("el servidor Live cerró el flujo de mensajes")

    def _handle_server_message(self, session, response, types_module) -> None:
        """Procesa un LiveServerMessage sin bloquear el bucle de recepción."""
        # ── Tool Calls ──────────────────────────────────────────────────
        tool_call = getattr(response, "tool_call", None)
        if tool_call is not None:
            # [FIX AUD-3/SEG-14] No se espera aquí a la herramienta: si se esperase, el
            # bucle dejaría de leer mensajes y ni la transcripción del «sí» de una
            # confirmación ni el aviso de 'interrupted' llegarían hasta que acabase.
            task = asyncio.create_task(self._handle_tool_calls(session, tool_call, types_module))
            self._tool_tasks.add(task)
            task.add_done_callback(self._tool_tasks.discard)
            return

        cancellation = getattr(response, "tool_call_cancellation", None)
        if cancellation is not None:
            log.info("Gemini Live canceló llamadas a herramientas: %s", getattr(cancellation, "ids", None))

        go_away = getattr(response, "go_away", None)
        if go_away is not None:
            log.info(
                "Gemini Live cerrará la sesión pronto (time_left=%s); se reconectará.",
                getattr(go_away, "time_left", None),
            )

        # ── Server Content (audio + texto + interrupciones) ─────────────
        server_content = getattr(response, "server_content", None)
        if server_content is None:
            return

        # 1. Barge-in: el usuario interrumpió al modelo
        if getattr(server_content, "interrupted", False):
            log.info("Barge-in detectado por Gemini Live. Vaciando cola de reproducción.")
            self._flush_playback()
            return

        # 2. Contenido del modelo (audio chunks + transcripción)
        model_turn = getattr(server_content, "model_turn", None)
        for part in (getattr(model_turn, "parts", None) or []):
            # Audio de respuesta
            inline_data = getattr(part, "inline_data", None)
            if inline_data and inline_data.data:
                try:
                    self._playback_queue.put_nowait(inline_data.data)
                    if not self._is_model_speaking:
                        self._is_model_speaking = True
                        self._emit_state("speaking")
                except queue.Full:
                    log.debug("Cola de reproducción Live llena: chunk de audio descartado.")

            # Transcripción de texto del modelo
            text = getattr(part, "text", None)
            if text and self._on_transcript_out:
                try:
                    self._on_transcript_out(text)
                except Exception:
                    pass

        # 3. Transcripciones de audio de entrada del usuario
        input_transcription = getattr(server_content, "input_transcription", None)
        if input_transcription:
            transcript_text = getattr(input_transcription, "text", None)
            if transcript_text and self._on_transcript_in:
                try:
                    self._on_transcript_in(transcript_text)
                except Exception:
                    pass

        # Transcripción de salida del modelo
        output_transcription = getattr(server_content, "output_transcription", None)
        if output_transcription:
            transcript_text = getattr(output_transcription, "text", None)
            if transcript_text and self._on_transcript_out:
                try:
                    self._on_transcript_out(transcript_text)
                except Exception:
                    pass

        # ── Turno completado ────────────────────────────────────────────
        # [FIX AUD-1] turn_complete va en server_content (LiveServerMessage no tiene ese
        # campo): antes nunca se detectaba y _is_model_speaking quedaba en True para siempre.
        if _is_turn_complete(server_content):
            self._enqueue_turn_end()

    def _enqueue_turn_end(self) -> None:
        """Marca el fin de turno en la cola; si está llena, sacrifica el audio más antiguo."""
        for _ in range(2):
            try:
                self._playback_queue.put_nowait(_TURN_END)
                return
            except queue.Full:
                try:
                    self._playback_queue.get_nowait()
                except queue.Empty:
                    pass

    async def _handle_tool_calls(self, session, tool_call, types_module) -> None:
        """Ejecuta las herramientas solicitadas por el modelo y devuelve resultados."""
        function_calls = getattr(tool_call, "function_calls", None) or []
        if not function_calls:
            return

        function_responses = []

        for fc in function_calls:
            name = getattr(fc, "name", "") or ""
            args = dict(getattr(fc, "args", {}) or {})
            call_id = getattr(fc, "id", "")

            log.info("Gemini Live solicita herramienta: %s(%s)", name, json.dumps(args, default=str)[:80])

            safe_call = _live_safe_call(name, args)
            if safe_call is None:
                # [FIX SEG-3] Sin palabra de activación no se ejecuta nada que cambie el sistema.
                log.warning("Herramienta bloqueada en modo Live: %s(%s)", name, json.dumps(args, default=str)[:120])
                result: Dict[str, Any] = {"success": False, "blocked": True, "error": LIVE_BLOCKED_TOOL_ERROR}
                if self._on_tool_event:
                    try:
                        self._on_tool_event(name, "Bloqueada en modo Live (solo consultas).")
                    except Exception:
                        pass
            else:
                tool_name, tool_args = safe_call
                if self._on_tool_event:
                    try:
                        self._on_tool_event(tool_name, f"Ejecutando {tool_name}...")
                    except Exception:
                        pass

                self._emit_state("thinking")

                # Ejecutar la herramienta en un executor para no bloquear el event loop
                loop = asyncio.get_running_loop()
                try:
                    result = await loop.run_in_executor(None, execute_tool, tool_name, tool_args)
                except Exception as exc:
                    log.exception("Error ejecutando %s en Live: %s", tool_name, exc)
                    result = {"success": False, "error": f"Error ejecutando {tool_name}: {exc}"}

                if self._on_tool_event:
                    try:
                        self._on_tool_event(tool_name, "Completado.")
                    except Exception:
                        pass

            log.info("Resultado de %s: %s", name, json.dumps(result, ensure_ascii=False, default=str)[:120])

            fr = types_module.FunctionResponse(
                name=name,
                id=call_id,
                response={"result": result},
            )
            function_responses.append(fr)

        # Enviar respuestas de herramientas de vuelta al modelo
        try:
            await session.send_tool_response(function_responses=function_responses)
            log.info("Respuestas de herramientas enviadas a Gemini Live.")
        except Exception as exc:
            log.warning("Error enviando tool response a Live: %s", exc)

    # ─────────────────────────────────────────────────────────────────────
    # Reproducción de Audio
    # ─────────────────────────────────────────────────────────────────────

    def _open_output_stream(self):
        """Abre el altavoz propio de Live (int16 mono 24 kHz)."""
        stream = sd.OutputStream(samplerate=LIVE_OUTPUT_SAMPLE_RATE, channels=1, dtype="int16")
        stream.start()
        return stream

    def _discard_output(self, out):
        """Corta lo que quede en el búfer del altavoz (barge-in). Devuelve el stream utilizable."""
        if out is None:
            return None
        try:
            out.abort()
            out.start()
            return out
        except Exception as exc:
            log.debug("No se pudo reiniciar el altavoz Live tras el corte: %s", exc)
            try:
                out.close()
            except Exception:
                pass
            return None

    def _playback_worker(self, playback_queue: Optional[queue.Queue] = None) -> None:
        """Hilo dedicado a reproducir los chunks de audio PCM 24 kHz del modelo.

        [FIX AUD-11] Usa un OutputStream propio abierto una sola vez. Antes cada chunk se
        reproducía con sd.play(blocking=True): un stream PortAudio nuevo por chunk (clics,
        huecos, reproducción más lenta que el tiempo real) y, como sd.play/sd.stop actúan
        sobre el stream GLOBAL de sounddevice, Live y la voz clásica se cortaban entre sí.
        """
        q = playback_queue if playback_queue is not None else self._playback_queue
        out = None
        retry_at = 0.0
        try:
            while self._running:
                if self._flush_requested.is_set():
                    self._flush_requested.clear()
                    out = self._discard_output(out)
                try:
                    chunk = q.get(timeout=0.1)
                except queue.Empty:
                    continue

                if chunk is None:
                    # Señal de apagado
                    break

                if chunk == _TURN_END:
                    # Fin de turno: el eco de lo que queda en el búfer aún dura un poco.
                    # [FIX AUD-12] Solo si el turno llegó a sonar: tras un barge-in el servidor
                    # envía igualmente turn_complete, y _flush_playback ya dejó
                    # _is_model_speaking=False; armar aquí la ventana de eco taparía con
                    # silencio la voz del Señor VK justo mientras habla.
                    if self._is_model_speaking:
                        latency = float(getattr(out, "latency", 0.0) or 0.0) if out is not None else 0.0
                        self._echo_guard_until = time.monotonic() + LIVE_ECHO_TAIL_S + min(latency, 0.5)
                    self._is_model_speaking = False
                    self._emit_state("listening")
                    continue

                if not chunk:
                    continue

                if self._flush_requested.is_set():
                    # Chunk que llegó justo durante un barge-in: se corta lo anterior y se sigue
                    self._flush_requested.clear()
                    out = self._discard_output(out)

                if out is None:
                    if time.monotonic() < retry_at:
                        continue  # sin altavoz por ahora: se descarta el audio
                    try:
                        out = self._open_output_stream()
                    except Exception as exc:
                        log.warning("No se pudo abrir la salida de audio Live: %s", exc)
                        retry_at = time.monotonic() + _OUTPUT_RETRY_S
                        continue

                try:
                    # Decodificar PCM 16-bit LE (un byte suelto al final se ignora)
                    pcm_int16 = np.frombuffer(chunk[: len(chunk) - (len(chunk) % 2)], dtype=np.int16)
                    for start in range(0, len(pcm_int16), _PLAYBACK_SLICE_SAMPLES):
                        if not self._running or self._flush_requested.is_set():
                            break
                        out.write(pcm_int16[start:start + _PLAYBACK_SLICE_SAMPLES])
                except Exception as exc:
                    log.warning("Error reproduciendo audio Live: %s", exc)
                    out = self._discard_output(out)
        finally:
            if out is not None:
                try:
                    out.abort()
                except Exception:
                    pass
                try:
                    out.close()
                except Exception:
                    pass

    def _flush_playback(self, emit: bool = True) -> None:
        """Vacía la cola de reproducción y corta el audio en curso (barge-in)."""
        self._is_model_speaking = False
        self._echo_guard_until = 0.0  # el Señor VK está hablando: no tapar su voz
        # Drenar la cola
        while True:
            try:
                self._playback_queue.get_nowait()
            except queue.Empty:
                break
        # [FIX AUD-11] Sin sd.stop(): cortaría también la voz clásica. El hilo de
        # reproducción descarta su propio búfer al ver esta señal.
        self._flush_requested.set()
        if emit:
            self._emit_state("listening")

    # ─────────────────────────────────────────────────────────────────────
    # Utilidades
    # ─────────────────────────────────────────────────────────────────────

    def _emit_state(self, state: str) -> None:
        """Notifica cambio de estado al controlador."""
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception:
                pass

    def _build_live_tools(self, types_module) -> list:
        """Convierte las herramientas permitidas en Live al formato de Gemini Live."""
        function_declarations = []
        for t in _live_tool_definitions():
            try:
                fd = types_module.FunctionDeclaration(
                    name=t["name"],
                    description=t.get("description", ""),
                    parameters=t.get("parameters"),
                )
                function_declarations.append(fd)
            except Exception as exc:
                log.warning("No se pudo declarar herramienta '%s' para Live: %s", t.get("name"), exc)

        if function_declarations:
            return [types_module.Tool(function_declarations=function_declarations)]
        return []

    def _resolve_live_model(self) -> str:
        """Determina el modelo a usar para la sesión Live."""
        resolved = resolve_gemini_model(
            self._api_key, self.model, live=True,
            fallback=GEMINI_LIVE_MODEL_FALLBACK or LIVE_MODEL_CANDIDATES[0],
        )
        return resolved or LIVE_MODEL_CANDIDATES[0]
