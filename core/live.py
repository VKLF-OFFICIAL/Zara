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
  - Tool calling en sesión: el modelo puede invocar herramientas de ZARA
    (run_shell, web_search, os_control...) durante la conversación en vivo
    y vocalizar el resultado sin cambiar de modo.
"""

from __future__ import annotations
import asyncio
import json
import logging
import queue
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from config import (
    GEMINI_API_KEY,
    GEMINI_LIVE_MODEL,
    GEMINI_LIVE_MODEL_FALLBACK,
    GEMINI_LIVE_VOICE,
    get_system_prompt,
)
from core.gemini_models import resolve_gemini_model
from core.tools import TOOL_DEFINITIONS, execute_tool

log = logging.getLogger("zara.live")

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

        # Control de reproducción y barge-in
        self._playback_queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=200)
        self._playback_thread: Optional[threading.Thread] = None
        self._is_model_speaking = False
        self._muted = threading.Event()  # Si está set, el mic está silenciado

        # Cola de audio capturado del micrófono (PCM 16-bit LE bytes)
        self._mic_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def set_model(self, model: str) -> None:
        """Cambia el modelo Live para la próxima conexión."""
        self.model = model
        log.info("Modelo Gemini Live cambiado a: %s", model)

    def is_available(self) -> bool:
        """Comprueba si Gemini Live está disponible (API key + SDK)."""
        if not self._api_key:
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
            log.error("Gemini Live no disponible: falta GEMINI_API_KEY o el SDK google-genai.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True, name="zara-live")
        self._thread.start()

        # Hilo de reproducción de audio de salida
        self._playback_thread = threading.Thread(target=self._playback_worker, daemon=True, name="zara-live-playback")
        self._playback_thread.start()

        log.info("Sesión Gemini Live iniciada.")

    def stop(self) -> None:
        """Detiene la sesión Live de forma ordenada."""
        self._running = False

        # Señalizar fin de reproducción
        try:
            self._playback_queue.put_nowait(None)
        except queue.Full:
            pass

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=2.0)

        log.info("Sesión Gemini Live detenida.")

    def interrupt(self) -> None:
        """Interrumpe la reproducción actual (barge-in manual desde el HUD)."""
        self._flush_playback()

    # ─────────────────────────────────────────────────────────────────────
    # Event Loop Asíncrono
    # ─────────────────────────────────────────────────────────────────────

    def _run_event_loop(self) -> None:
        """Ejecuta el bucle asíncrono principal en un hilo dedicado."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session_loop())
        except Exception as exc:
            log.exception("Error fatal en la sesión Gemini Live: %s", exc)
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            self._running = False
            self._emit_state("disconnected")

    async def _session_loop(self) -> None:
        """Bucle principal: conecta, envía audio y recibe respuestas."""
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
            system_instruction=get_system_prompt(),
            tools=live_tools,
            temperature=0.7,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        # Determinar modelo Live
        model_id = await asyncio.to_thread(self._resolve_live_model)

        while self._running:
            try:
                log.info("Conectando a Gemini Live con modelo: %s", model_id)
                self._emit_state("connecting")

                async with client.aio.live.connect(model=model_id, config=config) as session:
                    log.info("Sesión Gemini Live conectada exitosamente.")
                    self._emit_state("connected")

                    # Reinicializar la cola de mic para el nuevo event loop
                    self._mic_queue = asyncio.Queue()

                    # Lanzar tareas concurrentes
                    send_task = asyncio.create_task(self._send_audio_loop(session))
                    recv_task = asyncio.create_task(self._receive_loop(session, types))

                    # Iniciar captura de micrófono
                    mic_task = asyncio.create_task(self._mic_capture_loop())

                    try:
                        await asyncio.gather(send_task, recv_task, mic_task)
                    except asyncio.CancelledError:
                        pass
                    finally:
                        for t in (send_task, recv_task, mic_task):
                            if not t.done():
                                t.cancel()

            except Exception as exc:
                log.warning("Sesión Live desconectada: %s. Reconectando en 3s...", exc)
                self._emit_state("reconnecting")
                await asyncio.sleep(3.0)

    async def _mic_capture_loop(self) -> None:
        """Captura continua de micrófono a 16 kHz y encola chunks PCM 16-bit."""
        loop = asyncio.get_running_loop()
        mic_buffer = asyncio.Queue()

        def _sd_callback(indata, frames, time_info, status):
            if status:
                log.debug("sounddevice status: %s", status)
            if not self._running:
                return
            # Convertir float32 [-1, 1] a int16 PCM bytes (little-endian)
            pcm_float = indata[:, 0]

            # Emitir RMS para el HUD
            if self._on_rms_level and not self._muted.is_set():
                rms = float(np.sqrt(np.mean(pcm_float ** 2)))
                try:
                    self._on_rms_level(rms)
                except Exception:
                    pass

            pcm_int16 = np.clip(pcm_float * 32767, -32768, 32767).astype(np.int16)
            pcm_bytes = pcm_int16.tobytes()
            loop.call_soon_threadsafe(mic_buffer.put_nowait, pcm_bytes)

        # Abrir stream de sounddevice
        stream = sd.InputStream(
            samplerate=LIVE_INPUT_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=LIVE_INPUT_CHUNK_SAMPLES,
            callback=_sd_callback,
        )

        with stream:
            log.info("Captura de micrófono Live activa a %d Hz.", LIVE_INPUT_SAMPLE_RATE)
            while self._running:
                try:
                    chunk = await asyncio.wait_for(mic_buffer.get(), timeout=0.5)
                    if not self._muted.is_set():
                        await self._mic_queue.put(chunk)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

    async def _send_audio_loop(self, session) -> None:
        """Envía chunks de audio del micrófono al servidor en tiempo real."""
        from google.genai import types

        while self._running:
            try:
                chunk = await asyncio.wait_for(self._mic_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=chunk,
                        mime_type="audio/pcm;rate=16000",
                    )
                )
            except Exception as exc:
                log.warning("Error enviando audio a Gemini Live: %s", exc)
                break

    async def _receive_loop(self, session, types_module) -> None:
        """Recibe y procesa las respuestas del servidor (audio, texto, tool calls)."""
        try:
            async for response in session.receive():
                if not self._running:
                    break

                # ── Tool Calls ──────────────────────────────────────────
                tool_call = getattr(response, "tool_call", None)
                if tool_call is not None:
                    await self._handle_tool_calls(session, tool_call, types_module)
                    continue

                # ── Server Content (audio + texto + interrupciones) ─────
                server_content = getattr(response, "server_content", None)
                if server_content is not None:
                    # 1. Barge-in: el usuario interrumpió al modelo
                    if getattr(server_content, "interrupted", False):
                        log.info("Barge-in detectado por Gemini Live. Vaciando cola de reproducción.")
                        self._flush_playback()
                        continue

                    # 2. Contenido del modelo (audio chunks + transcripción)
                    model_turn = getattr(server_content, "model_turn", None)
                    if model_turn and hasattr(model_turn, "parts"):
                        for part in model_turn.parts:
                            # Audio de respuesta
                            inline_data = getattr(part, "inline_data", None)
                            if inline_data and inline_data.data:
                                try:
                                    self._playback_queue.put_nowait(inline_data.data)
                                    if not self._is_model_speaking:
                                        self._is_model_speaking = True
                                        self._emit_state("speaking")
                                except queue.Full:
                                    pass

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

                # ── Turno completado ────────────────────────────────────
                if getattr(response, "turn_complete", False):
                    # Señalizar fin de turno para la cola de reproducción
                    try:
                        self._playback_queue.put_nowait(b"__TURN_END__")
                    except queue.Full:
                        pass

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("Error en el bucle de recepción Live: %s", exc)

    async def _handle_tool_calls(self, session, tool_call, types_module) -> None:
        """Ejecuta las herramientas solicitadas por el modelo y devuelve resultados."""
        function_calls = getattr(tool_call, "function_calls", [])
        if not function_calls:
            return

        function_responses = []

        for fc in function_calls:
            name = getattr(fc, "name", "")
            args = dict(getattr(fc, "args", {}) or {})
            call_id = getattr(fc, "id", "")

            log.info("Gemini Live solicita herramienta: %s(%s)", name, json.dumps(args)[:80])

            if self._on_tool_event:
                try:
                    self._on_tool_event(name, f"Ejecutando {name}...")
                except Exception:
                    pass

            self._emit_state("thinking")

            # Ejecutar la herramienta en un executor para no bloquear el event loop
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, execute_tool, name, args)

            if self._on_tool_event:
                try:
                    self._on_tool_event(name, "Completado.")
                except Exception:
                    pass

            log.info("Resultado de %s: %s", name, json.dumps(result, ensure_ascii=False)[:120])

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

    def _playback_worker(self) -> None:
        """Hilo dedicado a reproducir los chunks de audio PCM 24 kHz del modelo."""
        while self._running:
            try:
                chunk = self._playback_queue.get(timeout=0.3)
            except queue.Empty:
                continue

            if chunk is None:
                # Señal de apagado
                break

            if chunk == b"__TURN_END__":
                # Fin de turno: esperar a que termine la reproducción actual
                try:
                    sd.wait()
                except Exception:
                    pass
                self._is_model_speaking = False
                self._emit_state("listening")
                continue

            if not chunk:
                continue

            try:
                # Decodificar PCM 16-bit LE a float32 para sounddevice
                pcm_int16 = np.frombuffer(chunk, dtype=np.int16)
                pcm_float = pcm_int16.astype(np.float32) / 32768.0

                # Reproducir (sd.play espera a que termine el chunk anterior)
                sd.play(pcm_float, samplerate=LIVE_OUTPUT_SAMPLE_RATE, blocking=True)
            except Exception as exc:
                log.warning("Error reproduciendo audio Live: %s", exc)

    def _flush_playback(self) -> None:
        """Vacía la cola de reproducción (barge-in)."""
        self._is_model_speaking = False
        try:
            sd.stop()
        except Exception:
            pass
        # Drenar la cola
        while not self._playback_queue.empty():
            try:
                self._playback_queue.get_nowait()
            except queue.Empty:
                break
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
        """Convierte TOOL_DEFINITIONS de ZARA al formato de Gemini Live."""
        function_declarations = []
        for t in TOOL_DEFINITIONS:
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
