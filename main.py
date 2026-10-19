"""Punto de Entrada Principal de ZARA.

Orquesta el oído neuronal continuo (Silero VAD + faster-whisper), el motor
de voz streaming con interrupción Barge-in, el cerebro híbrido con ReAct
autónomo y la interfaz holográfica HUD en PySide6 (bola flotante + panel
táctico desplegable).
"""

from __future__ import annotations
import argparse
import ctypes
import errno
import logging
import logging.handlers
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import config

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=_LOG_FORMAT,
    datefmt="%H:%M:%S",
)
log = logging.getLogger("zara.main")

CONFIRM_TIMEOUT_S = 25.0  # sin respuesta clara en este tiempo, la acción se cancela
NO_REPLY_MESSAGE = "Señor VK, el cerebro no devolvió respuesta. Revise el modelo seleccionado."
LOG_MAX_BYTES = 2_000_000
LOG_BACKUPS = 5
# [FIX V1_-13] Tope de espera a que la voz pendiente empiece a sonar antes de devolver la bola
# a reposo (si la síntesis falla o se cuelga, la bola no se queda en «PROCESANDO» para siempre).
VOICE_SETTLE_TIMEOUT_S = 30.0


# --- Registro a archivo (diagnóstico en producción) ---
def _resolve_log_level(name: Optional[str]) -> int:
    """Traduce ZARA_LOG_LEVEL («debug», «INFO», «20»...) a un nivel de logging (INFO si no vale)."""
    value = (name or "").strip().upper()
    if value.isdigit():
        return int(value)
    level = logging.getLevelName(value) if value else logging.INFO
    return level if isinstance(level, int) else logging.INFO


def setup_logging(log_dir: Path, level_name: Optional[str] = None) -> Path:
    """[FIX V1_-12] Log también a archivo con rotación, nivel configurable y excepciones no capturadas.

    Lanzada con doble clic o desde el inicio automático, la consola se cierra al fallar y
    el traceback se perdía. Ahora queda en DATA_DIR/logs/zara.log (2 MB x 5 copias).
    """
    root = logging.getLogger()
    level = _resolve_log_level(level_name if level_name is not None else os.environ.get("ZARA_LOG_LEVEL"))
    root.setLevel(level)

    log_dir = Path(log_dir)
    log_file = log_dir / "zara.log"
    already = any(getattr(h, "_zara_file_log", False) for h in root.handlers)
    if not already:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
            handler._zara_file_log = True  # type: ignore[attr-defined]  # evita duplicarlo
            root.addHandler(handler)
        except OSError as exc:
            log.warning("No se pudo abrir el log en %s: %s", log_file, exc)

    def _log_uncaught(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical("Excepción no capturada", exc_info=(exc_type, exc_value, exc_tb))

    def _log_thread_exception(args) -> None:  # noqa: ANN001
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "?"
        log.critical("Excepción no capturada en el hilo %s", name,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _log_uncaught
    threading.excepthook = _log_thread_exception
    return log_file


# [FIX V1_-12] Lanzado como programa, el log a archivo se activa ANTES de importar Qt, torch
# (core.ear), httpx (core.brain) o psutil (core.tools): si uno falla al importar, el traceback
# queda en zara.log en vez de perderse con la consola. Importado desde los tests, no se toca.
if __name__ == "__main__":
    setup_logging(config.DATA_DIR / "logs")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from core.confirm import parse_confirmation  # noqa: E402
from core.brain import ZaraBrain, list_ollama_models, _pick_default_ollama_model  # noqa: E402
from core.ear import ContinuousEar, contains_wake_word  # noqa: E402
from core.live import GeminiLiveSession  # noqa: E402
from core.tools import set_confirmation_handler  # noqa: E402
from core.voice import VoiceSynthesizer  # noqa: E402
from ui.hud import HUDState, ZaraHUD  # noqa: E402


# --- Instancia única ---
def acquire_single_instance_lock(lock_path: Path) -> Optional[QtCore.QLockFile]:
    """[FIX V1_-8] Devuelve el bloqueo si esta es la única Zara en marcha, o None si ya hay otra.

    Dos instancias abrirían el mismo micrófono y ejecutarían cada orden dos veces. Con
    staleLockTime=0 solo se considera huérfano el bloqueo de un proceso que ya no existe
    (Windows y Linux).
    """
    lock = QtCore.QLockFile(str(lock_path))
    lock.setStaleLockTime(0)
    if lock.tryLock(100):
        return lock
    return None


# --- Plataforma gráfica en Linux (X11 / Wayland) ---
def _has_xcb_cursor() -> bool:
    """True si el cargador encuentra libxcb-cursor.so.0, que el plugin xcb de Qt >= 6.5 necesita."""
    try:
        import PySide6
        bundled = Path(PySide6.__file__).resolve().parent / "Qt" / "lib"
        if any(bundled.glob("libxcb-cursor.so*")):
            return True
    except Exception:
        pass
    try:
        ctypes.CDLL("libxcb-cursor.so.0")
        return True
    except OSError:
        return False


_XCB_CURSOR_HELP = (
    "Falta la librería libxcb-cursor, que Qt necesita para abrir ventanas en X11. Instálela con "
    "«sudo apt install libxcb-cursor0» (Debian, Ubuntu, Kali) o «sudo dnf install xcb-util-cursor» "
    "(Fedora) y vuelva a lanzar Zara, o use «--cli» para el modo consola."
)


def prepare_qt_platform(env=None, platform: Optional[str] = None, xcb_cursor_ok: Optional[bool] = None) -> Optional[str]:
    """Ajusta QT_QPA_PLATFORM en Linux ANTES de crear la QApplication.

    Devuelve un mensaje de error si la interfaz gráfica no puede arrancar (Qt abortaría el
    proceso con qFatal, sin excepción que Python pueda capturar), o None si se puede seguir.

    [FIX MUL-5/V1_-7] En Wayland un cliente no puede colocar su ventana ni quedar siempre
    encima: move() y WindowStaysOnTopHint se ignoran. Si el usuario no fijó la plataforma y
    hay XWayland, se usa xcb (con wayland de respaldo si xcb no carga).
    [FIX MUL-13] En X11, sin libxcb-cursor Qt aborta: se avisa antes con un mensaje claro.
    En Windows no hace nada (el plugin qwindows no tiene dependencias externas).
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if not platform.startswith("linux"):
        return None

    def _xcb_ok() -> bool:
        return _has_xcb_cursor() if xcb_cursor_ok is None else xcb_cursor_ok

    forced = env.get("QT_QPA_PLATFORM", "").strip()
    if not forced:
        has_x11 = bool(env.get("DISPLAY"))
        is_wayland = env.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(env.get("WAYLAND_DISPLAY"))
        if not has_x11 and not env.get("WAYLAND_DISPLAY"):
            return "No hay sesión gráfica (DISPLAY y WAYLAND_DISPLAY vacíos). Use «--cli» para el modo consola."
        if is_wayland:
            if has_x11 and _xcb_ok():
                env["QT_QPA_PLATFORM"] = "xcb;wayland"
                log.warning(
                    "Sesión Wayland: la bola se abre con XWayland (xcb) para poder colocarla, arrastrarla "
                    "y mantenerla encima. Para forzar Wayland nativo, defina QT_QPA_PLATFORM=wayland."
                )
            else:
                log.warning(
                    "Sesión Wayland sin XWayland o sin libxcb-cursor: se usa Wayland nativo y la bola no "
                    "podrá colocarse sola ni quedar siempre encima. Instale libxcb-cursor0 (apt) o "
                    "xcb-util-cursor (dnf) para usar XWayland."
                )
            return None
        forced = "xcb"  # sesión X11: Qt usará el plugin xcb

    # Solo es fatal si xcb es la ÚNICA plataforma pedida («xcb;wayland» tiene respaldo).
    candidates = [p.strip().lower() for p in forced.split(";") if p.strip()]
    if candidates == ["xcb"] and not _xcb_ok():
        return _XCB_CURSOR_HELP
    return None


class ZaraController(QtCore.QObject):
    """Controlador central que comunica los hilos de audio, el cerebro y el HUD."""

    # Señales Qt thread-safe para actualizar la UI desde hilos secundarios
    sig_hud_state = QtCore.Signal(object)
    sig_rms_level = QtCore.Signal(float)
    sig_tool_event = QtCore.Signal(str, str)
    sig_user_speech = QtCore.Signal(str)
    sig_assistant_reply = QtCore.Signal(str)
    sig_ollama_models = QtCore.Signal(list, str)
    # [FIX AUD-4] Línea del panel (prefijo, texto): los widgets Qt solo se tocan en el hilo
    # de la GUI, así que los hilos del agente y de Live escriben en el panel con esta señal
    # (conexión en cola automática entre hilos).
    sig_panel_line = QtCore.Signal(str, str)

    def __init__(self, cli_mode: bool = False):
        super().__init__()
        self.cli_mode = cli_mode
        self._stop_event = threading.Event()
        self._is_busy = False
        self._busy_lock = threading.Lock()
        self._preferred_provider = config.DEFAULT_BRAIN
        # Confirmación hablada de acciones sensibles (ver core.tools.needs_confirmation)
        self._confirm_queue: Optional[queue.Queue] = None
        self._confirm_lock = threading.Lock()
        # [FIX AUD-10] Número de la última orden recibida: un turno que espera el lock y ya
        # ha sido sustituido por otra orden (o por «Parar») no se ejecuta.
        self._turn_seq = 0
        self._turn_lock = threading.Lock()
        # [FIX AUD-2/V1_-6] Estado del consumidor de locuciones del modo clásico.
        self._worker_lock = threading.Lock()
        self._worker_running = False
        self._classic_active = False

        # 1. Motor de Voz (TTS) con Sentence Chunking y Barge-in
        self.voice = VoiceSynthesizer(on_state_change=self._on_voice_state_change)

        # 2. Cerebro y Agente Táctico ReAct
        self.brain = ZaraBrain(on_tool_event=self._on_brain_tool_event)

        # 3. Oído Continuo (VAD v5 + faster-whisper + Barge-in)
        self.ear = ContinuousEar(
            barge_in_callback=self._on_barge_in,
            is_speaking_fn=self.voice.is_speaking,
            rms_callback=self._on_rms_level,
        )

        # 4. Sesión Gemini Live (Streaming nativo de ultra-baja latencia)
        self.live_session = GeminiLiveSession(
            api_key=config.GEMINI_API_KEY,
            model=config.GEMINI_LIVE_MODEL,
            voice_name=config.GEMINI_LIVE_VOICE,
            on_transcript_in=self._on_live_transcript_in,
            on_transcript_out=self._on_live_transcript_out,
            on_tool_event=self._on_brain_tool_event,
            on_state_change=self._on_live_state_change,
            on_rms_level=self._on_rms_level,
        )

        # 5. Interfaz HUD Holográfica (bola flotante + panel táctico)
        self.hud: Optional[ZaraHUD] = None
        if not self.cli_mode:
            self.hud = ZaraHUD()
            self._connect_signals()
        else:
            self._connect_cli_signals()

        set_confirmation_handler(self._ask_confirmation)

        # Hilo de escucha en bucle (se crea de nuevo en _start_classic_ear si ha terminado)
        self._worker_thread = threading.Thread(target=self._voice_loop_worker, daemon=True)

    def _connect_cli_signals(self) -> None:
        """[FIX V1_-10] Sin HUD, las respuestas y los avisos (p. ej. CONFIRMAR) salen por la terminal."""
        self.sig_assistant_reply.connect(lambda text: print(f"ZARA: {text}", flush=True))
        self.sig_panel_line.connect(lambda prefix, text: print(f"[{prefix}] {text}", flush=True))

    def _panel_line(self, prefix: str, text: str) -> None:
        """Escribe en el panel (o en la terminal en --cli) desde cualquier hilo."""
        self.sig_panel_line.emit(prefix, text)

    def _connect_signals(self) -> None:
        if not self.hud:
            return
        self.sig_panel_line.connect(self.hud.panel.log_line)
        self.sig_hud_state.connect(self.hud.set_hud_state)
        self.sig_rms_level.connect(self.hud.set_rms_level)
        self.sig_tool_event.connect(self.hud.update_tool_status)
        self.sig_user_speech.connect(self.hud.update_user_speech)
        self.sig_assistant_reply.connect(self.hud.update_assistant_reply)
        self.sig_ollama_models.connect(self.hud.set_ollama_models)
        self.hud.barge_in_requested.connect(self._on_barge_in)
        self.hud.mic_toggle_requested.connect(self._on_mic_toggle)
        self.hud.brain_changed.connect(self._on_brain_changed)
        self.hud.model_changed.connect(self._on_model_changed)
        self.hud.quit_requested.connect(self._on_quit_requested)
        self.hud.text_command_submitted.connect(self._on_text_command)

    def _ask_confirmation(self, description: str) -> bool:
        """Pide al Señor VK un sí/no hablado (o escrito) antes de una acción sensible.

        Se ejecuta en el hilo del agente; bloquea hasta la respuesta, el timeout
        o un cierre. La frase evita las palabras «sí/no» para que el eco del
        propio altavoz no se interprete como respuesta.
        """
        with self._confirm_lock:
            answers: queue.Queue = queue.Queue(maxsize=1)
            self._confirm_queue = answers
            try:
                log.info("Pidiendo confirmación: %s", description)
                # Este método corre en el hilo del agente: nada de widgets directos (AUD-4).
                self._panel_line("CONFIRMAR", f"¿{description}? Responda «sí» o «no».")
                self.voice.say(f"Señor VK, necesito su confirmación para {description}.")
                try:
                    return bool(answers.get(timeout=CONFIRM_TIMEOUT_S))
                except queue.Empty:
                    self.voice.say("Sin confirmación. Acción cancelada.")
                    return False
            finally:
                self._confirm_queue = None

    def _zara_is_speaking(self) -> bool:
        return self.voice.is_speaking() or self.live_session.is_model_speaking()

    def _route_confirmation(self, text: str, from_voice: bool = False) -> bool:
        """Si hay una confirmación pendiente, consume `text` como respuesta.

        Devuelve True si había una pendiente (la frase no debe procesarse como orden nueva).
        [FIX SEG-10] Por voz, lo que llega mientras Zara habla se descarta (eco o ruido sobre
        su propia pregunta): la respuesta debe darse con Zara en silencio. Hablarle encima
        corta su voz (barge-in) y la frase siguiente sí cuenta.
        """
        answers = self._confirm_queue
        if answers is None:
            return False
        if from_voice and self._zara_is_speaking():
            log.info("Respuesta de confirmación ignorada (Zara estaba hablando): '%s'", text)
            return True
        verdict = parse_confirmation(text)
        if verdict is not None:
            try:
                answers.put_nowait(verdict)
            except queue.Full:
                pass
        return True

    def _on_live_transcript_in(self, text: str) -> None:
        clean = text.strip()
        # Los tool-calls de Live se ejecutan sin palabra de activación: la respuesta a una
        # confirmación llega por aquí. Se ignora lo que se transcribe mientras Zara habla (eco).
        if clean:
            self._route_confirmation(clean, from_voice=True)
        if clean:
            log.info("[LIVE USER] %s", clean)
            self.sig_user_speech.emit(clean)

    def _on_live_transcript_out(self, text: str) -> None:
        clean = text.strip()
        if clean:
            log.info("[LIVE ZARA] %s", clean)
            self.sig_assistant_reply.emit(clean)

    def _on_live_state_change(self, state: str) -> None:
        if state == "speaking":
            self.sig_hud_state.emit(HUDState.SPEAKING)
        elif state == "thinking":
            self.sig_hud_state.emit(HUDState.THINKING)
        elif state == "listening":
            self.sig_hud_state.emit(HUDState.LISTENING)
        elif state == "connected":
            self.sig_hud_state.emit(HUDState.IDLE)
            # Se llama desde el hilo asyncio de Live: el panel se toca por señal (AUD-4).
            self._panel_line("SISTEMA", "Gemini Live: WebSocket conectado.")
        elif state in ("connecting", "reconnecting"):
            self.sig_hud_state.emit(HUDState.THINKING)
            self._panel_line("SISTEMA", f"Gemini Live: {state}...")
        else:
            self.sig_hud_state.emit(HUDState.IDLE)

    def start(self) -> None:
        """Inicia todos los subsistemas de Zara."""
        log.info("Iniciando subsistemas de %s...", config.APP_TITLE)
        reason = self._brain_unavailable_reason(self._preferred_provider)
        if reason:
            # Un cerebro guardado que ya no funciona (p. ej. Gemini Live sin clave) no debe dejar
            # a Zara sin cerebro: se usa la suscripción si está, o el modo automático.
            fallback = "antigravity" if self.brain.antigravity.is_available() else "auto"
            log.warning("Cerebro guardado '%s' no disponible (%s). Usando '%s'.", self._preferred_provider, reason, fallback)
            self._preferred_provider = fallback
        if config.ENV_MIGRATED_KEYS:
            log.info(
                "Ajustes antiguos quitados de .env (ahora mandan los de config/ajustes.env; copia en .env.bak): %s",
                ", ".join(config.ENV_MIGRATED_KEYS),
            )
        if self._preferred_provider == "gemini_live" and self.live_session.is_available():
            log.info("Iniciando directamente en modo Gemini Live...")
            self.live_session.start()
        else:
            self._start_classic_ear()

        if self.hud:
            self.hud.show()
            self.sig_hud_state.emit(HUDState.IDLE)
            self.hud.set_brain(self._preferred_provider)
            self.hud.set_model_selection("ollama", self.brain.ollama.model)
            self.hud.set_model_selection("claude", self.brain.claude.cli_model_override)
            self.hud.set_model_selection("antigravity", self.brain.antigravity.model)
            self.hud.set_model_selection("gemini", self.brain.gemini.model)
            self.hud.set_model_selection("gemini_live", config.GEMINI_LIVE_MODEL)
            threading.Thread(target=self._load_ollama_models, daemon=True).start()

        # Saludo inicial táctico
        saludo = "Sistemas tácticos operativos, Señor VK. A su servicio."
        log.info("ZARA: %s", saludo)
        self.voice.say(saludo)

    def _load_ollama_models(self) -> None:
        """Consulta los modelos realmente instalados en Ollama (hilo aparte, red)."""
        models = list_ollama_models(self.brain.ollama.host)
        if models:
            # [FIX] Si Ollama no respondió a tiempo durante el arranque
            # (OllamaProvider.__init__ ya intentó autodetectar), el modelo
            # pudo quedar vacío. Con la lista real ya disponible, completar
            # la autodetección ahora en vez de dejarlo sin modelo.
            if not self.brain.ollama.model:
                self.brain.ollama.set_model(_pick_default_ollama_model(models))
            self.sig_ollama_models.emit(models, self.brain.ollama.model)

    def _ear_is_running(self) -> bool:
        """True si la captura del oído clásico está activa."""
        is_running = getattr(self.ear, "is_running", None)
        if callable(is_running):
            return bool(is_running())
        # Respaldo por si ContinuousEar aún no expone is_running().
        thread = getattr(self.ear, "_audio_thread", None)
        stop_event = getattr(self.ear, "_stop_event", None)
        return bool(thread is not None and thread.is_alive() and not (stop_event and stop_event.is_set()))

    def _start_classic_ear(self) -> None:
        """[FIX AUD-2/V1_-6] Arranca (o rearranca) el oído clásico Y su consumidor de locuciones.

        Al pasar a Gemini Live, ear.stop() termina listen_utterances() y con él el hilo
        _voice_loop_worker. Al volver (o si se arrancó en Live) no basta con ear.start():
        sin consumidor las locuciones se acumulan sin transcribirse y Zara queda sorda.
        """
        with self._worker_lock:
            if not self._ear_is_running():
                self.ear.start()
            self._classic_active = True
            if not self._worker_running or not self._worker_thread.is_alive():
                self._worker_running = True
                if self._worker_thread.ident is not None:  # un Thread solo se puede arrancar una vez
                    self._worker_thread = threading.Thread(target=self._voice_loop_worker, daemon=True)
                self._worker_thread.start()

    def _stop_classic_ear(self) -> None:
        """Detiene el oído clásico (paso a Gemini Live); su consumidor termina solo."""
        with self._worker_lock:
            self._classic_active = False
        self.ear.stop()

    def shutdown(self) -> None:
        """Cierre ordenado de todos los hilos y motores (idempotente)."""
        if self._stop_event.is_set():
            return
        log.info("Apagando %s...", config.APP_TITLE)
        self._stop_event.set()
        with self._turn_lock:
            self._turn_seq += 1  # ningún turno en espera debe arrancar ya
        self.live_session.stop()
        self.ear.stop()
        self.voice.shutdown()
        log.info("Todos los subsistemas detenidos. Hasta pronto, Señor VK.")

    def _on_barge_in(self) -> None:
        """Interrupción inmediata: el Señor VK ha hablado sobre Zara (o pulsó 'Parar')."""
        if self.live_session.is_running():
            self.live_session.interrupt()
        confirming = self._confirm_queue is not None
        # [FIX AUD-8] Hablar mientras Zara pide confirmación es contestarle: solo se corta el
        # audio y el resto del turno sigue sonando; si no, se cancela también el flujo de tokens.
        self.voice.stop(cancel_streams=not confirming)
        if confirming:
            return
        # [FIX AUDITORÍA] Detener AUDIO y cancelar el turno del cerebro
        self._next_turn_id()  # [FIX AUD-10] los turnos que esperaban el lock ya no se ejecutan
        self.brain.cancel_current_turn()
        self.sig_hud_state.emit(HUDState.IDLE if self.ear.is_muted() else HUDState.LISTENING)

    def _on_voice_state_change(self, is_speaking: bool) -> None:
        """Callback cuando Zara empieza o termina de reproducir voz."""
        if is_speaking:
            self.sig_hud_state.emit(HUDState.SPEAKING)
        else:
            self.sig_hud_state.emit(HUDState.IDLE)

    def _on_rms_level(self, rms: float) -> None:
        if not self.voice.is_speaking() and not self.live_session.is_model_speaking():
            self.sig_rms_level.emit(rms)

    def _on_brain_tool_event(self, tool_name: str, desc: str) -> None:
        log.info("[TOOL] %s: %s", tool_name, desc)
        self.sig_tool_event.emit(tool_name, desc)
        self.sig_hud_state.emit(HUDState.THINKING)

    def _on_mic_toggle(self) -> None:
        """Pausa o reanuda la escucha continua (botón de micrófono del panel)."""
        is_live = self.live_session.is_running()
        muted = not self.live_session.is_muted() if is_live else not self.ear.is_muted()
        self.ear.set_muted(muted)
        self.live_session.set_muted(muted)
        if self.hud:
            self.hud.set_muted(muted)
            self.sig_hud_state.emit(HUDState.IDLE)

    def _brain_unavailable_reason(self, provider: str) -> Optional[str]:
        """Motivo por el que un cerebro no se puede usar ahora mismo (None si está listo)."""
        if provider == "gemini_live" and not self.live_session.is_available():
            return (
                "Gemini Live necesita una clave de API (GEMINI_API_KEY en .env, gratis en aistudio.google.com). "
                "Con su suscripción AI Pro use el cerebro «Gemini · AI Pro»."
            )
        if provider == "gemini" and not self.brain.gemini.is_available():
            return (
                "Gemini por API necesita GEMINI_API_KEY en .env. "
                "Con su suscripción AI Pro use el cerebro «Gemini · AI Pro»."
            )
        if provider == "antigravity" and not self.brain.antigravity.is_available():
            return (
                "Gemini con su suscripción necesita Antigravity CLI. Instálelo, abra una terminal NUEVA, "
                "ejecute «agy» una vez para iniciar sesión con su cuenta de Google y reinicie Zara."
            )
        if provider == "claude" and not self.brain.claude.is_available():
            return "Claude necesita ANTHROPIC_API_KEY en .env o el CLI «claude» instalado."
        return None

    def _on_brain_changed(self, provider: str) -> None:
        """Cambia en caliente el cerebro LLM preferido desde el panel táctico."""
        reason = self._brain_unavailable_reason(provider)
        if reason:
            # No se activa ni se guarda: el panel vuelve al cerebro que funcionaba.
            log.warning("Cerebro %s no disponible: %s", provider, reason)
            self._panel_line("ERROR", reason)
            if self.hud:
                self.hud.set_brain(self._preferred_provider)
            return

        self._preferred_provider = provider
        log.info("Cerebro preferido cambiado a: %s", provider)
        try:
            config.persist_env_var("ZARA_DEFAULT_BRAIN", provider)  # se recuerda al reiniciar
        except Exception as exc:
            log.warning("No se pudo guardar el cerebro elegido: %s", exc)

        if provider == "gemini_live":
            # Pausar oído clásico para evitar conflicto de captura de micrófono
            self._stop_classic_ear()
            self.voice.stop()
            self.live_session.start()
            self._panel_line("SISTEMA", "Modo Live activado: streaming de audio bidireccional.")
        else:
            # Si veníamos de Live, detenerlo. El oído clásico y su consumidor se (re)arrancan
            # SIEMPRE: también si se arrancó en Live o si la sesión Live murió sola.
            if self.live_session.is_running():
                self.live_session.stop()
            self._start_classic_ear()
            if self.hud:
                self._panel_line("SISTEMA", f"Cerebro cambiado a: {self.hud.panel.brain_combo.currentText()}")

    def _on_model_changed(self, provider: str, model: str) -> None:
        """Cambia en caliente el modelo concreto del proveedor seleccionado y lo persiste."""
        if provider == "ollama":
            self.brain.ollama.set_model(model)
            config.persist_env_var("OLLAMA_MODEL", model)
            label = model
        elif provider == "claude":
            self.brain.claude.set_cli_model(model)
            config.persist_env_var("ZARA_CLAUDE_MODEL", model)
            label = model or "por defecto"
        elif provider == "gemini":
            self.brain.gemini.model = model
            config.persist_env_var("GEMINI_MODEL", model)
            label = model
        elif provider == "antigravity":
            self.brain.antigravity.set_model(model)
            config.persist_env_var("ZARA_AGY_MODEL", model)
            label = model or "por defecto"
        elif provider == "gemini_live":
            self.live_session.set_model(model)
            config.persist_env_var("ZARA_GEMINI_LIVE_MODEL", model)
            label = model
        else:
            return

        log.info("Modelo de %s cambiado a: %s", provider, label)
        self._panel_line("SISTEMA", f"Modelo de {provider} -> {label}")

    def _on_quit_requested(self) -> None:
        """Cierre limpio solicitado desde el botón 'Salir' del panel táctico (o al cerrar la bola)."""
        if self._stop_event.is_set():
            return  # ya se está cerrando (Ctrl+C, aboutToQuit o un cierre anterior)
        log.info("Cierre solicitado desde el panel táctico.")
        self.shutdown()
        app = QtCore.QCoreApplication.instance()
        if app:
            app.quit()

    def _on_text_command(self, text: str) -> None:
        """Procesa una orden escrita manualmente en el panel táctico (o en la terminal con --cli)."""
        text = text.strip()
        if not text:
            return
        if self._route_confirmation(text):
            return
        if self._is_busy:
            self.voice.say("Un momento, Señor VK, estoy procesando otra orden.")
            return
        turn_id = self._next_turn_id()
        threading.Thread(target=self._process_query, args=(text, turn_id), daemon=True).start()

    # --- turnos ---
    def _next_turn_id(self) -> int:
        """Registra una orden (o una interrupción) nueva y devuelve su número."""
        with self._turn_lock:
            self._turn_seq += 1
            return self._turn_seq

    def _is_latest_turn(self, turn_id: Optional[int]) -> bool:
        return turn_id is None or turn_id == self._turn_seq

    def _turn_cancelled(self, turn_id: Optional[int]) -> bool:
        """True si el turno fue sustituido por otra orden o cancelado (barge-in, «Parar»)."""
        if not self._is_latest_turn(turn_id):
            return True
        cancel_event = getattr(self.brain, "_cancel_event", None)
        return bool(cancel_event is not None and cancel_event.is_set())

    def _voice_loop_worker(self) -> None:
        """Hilo continuo que procesa las locuciones del Señor VK.

        [FIX AUDITORÍA] Antes llamaba a _process_query() de forma síncrona
        en este mismo hilo: mientras el turno anterior seguía "ocupado"
        (generando o esperando una herramienta), listen_utterances() no
        volvía a transcribir nada nuevo hasta que terminara, así que una
        nueva locución real del Señor VK podía quedar escuchada pero sin
        procesar durante varios segundos. Ahora cada locución se procesa en
        su propio hilo (igual que ya hacían las órdenes de texto del panel);
        si todavía hay un turno ocupado, se trata como una interrupción
        implícita: se cancela el turno en curso en vez de dejarlo terminar o
        descartar la orden nueva.

        [FIX AUD-2/V1_-6] listen_utterances() termina cuando se detiene el oído (paso a
        Gemini Live). Si entretanto se ha vuelto al modo clásico se sigue escuchando; si no,
        el hilo termina y _start_classic_ear() lo relanza al volver.
        """
        while True:
            for text in self.ear.listen_utterances():
                if self._stop_event.is_set():
                    break
                self._handle_utterance(text)
            with self._worker_lock:
                if not self._classic_active or self._stop_event.is_set():
                    self._worker_running = False
                    return
            time.sleep(0.2)  # el oído se relanzó (o se paró solo): no girar en vacío

    def _handle_utterance(self, text: str) -> None:
        """Decide qué hacer con una locución transcrita del modo clásico."""
        clean_text = text.strip()
        if not clean_text:
            return

        # [FIX] Palabra de activación obligatoria para órdenes de VOZ: el
        # micrófono escucha de forma continua, así que sin esto cualquier
        # ruido ambiente (TV, conversaciones, música) con palabras como
        # "pausa" o "minimiza" podía disparar acciones reales (minimizar
        # ventanas, pausar/reproducir multimedia...) sin que el Señor VK
        # se dirigiera a Zara en absoluto. Las órdenes escritas en el
        # panel táctico (_on_text_command) NO pasan por aquí: teclear ya
        # es una acción deliberada y no está sujeta a ruido ambiente.
        # Respuesta a una confirmación pendiente: no exige «Zara» ni inicia otro turno,
        # pero debe ser un sí/no claro dicho con Zara en silencio (SEG-10).
        if self._route_confirmation(clean_text, from_voice=True):
            return

        if not contains_wake_word(clean_text):
            log.debug("Locución ignorada (sin mencionar a Zara): '%s'", clean_text)
            return

        if self._is_busy:
            log.info("Nueva locución mientras se procesaba un turno anterior: cancelándolo.")
            self.voice.stop()
            self.brain.cancel_current_turn()

        turn_id = self._next_turn_id()
        threading.Thread(target=self._process_query, args=(clean_text, turn_id), daemon=True).start()

    def _process_query(self, clean_text: str, turn_id: Optional[int] = None) -> None:
        """Procesa una orden (de voz o texto) a través del agente ReAct y la sintetiza."""
        log.info("Señor VK dijo: '%s'", clean_text)
        self.sig_user_speech.emit(clean_text)
        self.sig_hud_state.emit(HUDState.THINKING)

        settle_hud = False
        with self._busy_lock:
            if not self._is_latest_turn(turn_id):
                # [FIX AUD-10] Mientras esperaba el lock llegó otra orden (o se pulsó «Parar»):
                # solo se ejecuta la última. threading.Lock no es FIFO y run_react_agent limpia
                # la cancelación al empezar, así que sin esto se ejecutaban todas, en desorden.
                log.info("Orden sustituida por otra posterior; no se ejecuta: '%s'", clean_text)
                return
            self._is_busy = True
            t_start = time.monotonic()
            try:
                token_gen = self.brain.run_react_agent(clean_text, preferred_provider=self._preferred_provider)

                full_reply_parts = []

                def _tee_tokens():
                    for tok in token_gen:
                        if not full_reply_parts:
                            log.info("[LATENCIA] primer token del cerebro a los %.2f s", time.monotonic() - t_start)
                        full_reply_parts.append(tok)
                        yield tok

                # Enviar stream de tokens a la síntesis por frases
                self.voice.feed_token_stream(_tee_tokens())

                log.info("[LATENCIA] turno completo en %.2f s", time.monotonic() - t_start)
                reply_text = "".join(full_reply_parts).strip()
                if reply_text:
                    self.sig_assistant_reply.emit(reply_text)
                elif not self._turn_cancelled(turn_id):
                    # [FIX V1_-13] Un turno vacío (p. ej. modelo de Ollama borrado y sin claves
                    # para el respaldo) dejaba a Zara muda y la bola en «PROCESANDO».
                    log.warning("El turno terminó sin respuesta del cerebro: '%s'", clean_text)
                    self.sig_assistant_reply.emit(NO_REPLY_MESSAGE)
                    self.voice.say(NO_REPLY_MESSAGE)
            except Exception as exc:
                log.exception("Error en procesamiento de orden: %s", exc)
                self.voice.say(f"Señor VK, ocurrió una anomalía: {exc}")
            finally:
                self._is_busy = False
                settle_hud = True
        if settle_hud:
            # Fuera del lock: esperar a la voz no debe retrasar la orden siguiente.
            self._return_hud_to_idle_when_silent(turn_id)

    def _voice_pending(self) -> bool:
        """True si quedan frases encoladas o sintetizándose que aún no han empezado a sonar."""
        try:
            return int(getattr(self.voice, "_pending_count", 0) or 0) > 0
        except (TypeError, ValueError):
            return False

    def _return_hud_to_idle_when_silent(self, turn_id: Optional[int],
                                        timeout: Optional[float] = None, poll: float = 0.05) -> None:
        """[FIX V1_-13] Devuelve la bola a reposo al acabar el turno si Zara no va a hablar.

        VoiceSynthesizer solo marca is_speaking() al REPRODUCIR la primera frase, no mientras
        edge-tts la sintetiza (1-2 s): emitir IDLE en ese hueco hacía parpadear la bola
        PROCESANDO -> REPOSO -> HABLANDO. Mientras quede voz pendiente se espera; si empieza a
        sonar, _on_voice_state_change pondrá IDLE al terminar. Si la síntesis falla (la cola
        se vacía sin sonar nada) o pasa el tope, se pone IDLE aquí. Otra orden posterior o
        «Parar» cortan la espera: el estado ya no es de este turno.
        """
        deadline = time.monotonic() + (VOICE_SETTLE_TIMEOUT_S if timeout is None else timeout)
        while True:
            if not self._is_latest_turn(turn_id) or self._stop_event.is_set():
                return
            if self.voice.is_speaking():
                return
            if not self._voice_pending() or time.monotonic() >= deadline:
                self.sig_hud_state.emit(HUDState.IDLE)
                return
            self._stop_event.wait(poll)


def _stdin_in_background(stream) -> bool:
    """True si `stream` es la terminal de control y este proceso está en segundo plano (POSIX)."""
    if not hasattr(os, "tcgetpgrp"):  # Windows: no hay control de trabajos
        return False
    try:
        return os.tcgetpgrp(stream.fileno()) != os.getpgrp()
    except (OSError, ValueError, AttributeError):
        return False


def ignore_background_tty_reads(stream=None) -> bool:
    """[FIX V1_-10] En POSIX, leer de la terminal estando en segundo plano («zara --cli &»,
    Ctrl+Z y «bg») envía SIGTTIN, que DETIENE el proceso entero (micrófono y voz incluidos).
    Ignorada la señal, esa lectura falla con EIO y read_stdin_commands espera a volver al
    primer plano. Solo si stdin es una terminal; en Windows no existe SIGTTIN.
    """
    stream = sys.stdin if stream is None else stream
    if not hasattr(signal, "SIGTTIN") or stream is None:
        return False
    try:
        if not stream.isatty():
            return False
    except (OSError, ValueError, AttributeError):
        return False
    signal.signal(signal.SIGTTIN, signal.SIG_IGN)
    return True


def read_stdin_commands(controller: ZaraController, stream=None, retry_delay: float = 0.5) -> None:
    """[FIX V1_-10] Modo --cli: cada línea escrita es una orden (o la respuesta a una confirmación)."""
    stream = sys.stdin if stream is None else stream
    if stream is None:  # pythonw.exe en Windows no tiene consola
        return
    while not controller._stop_event.is_set():
        try:
            line = stream.readline()
        except OSError as exc:
            # En segundo plano (SIGTTIN ignorada) la lectura da EIO: se reintenta hasta volver
            # al primer plano. Cualquier otro error (terminal cerrada) termina la lectura.
            if exc.errno == errno.EIO and _stdin_in_background(stream):
                controller._stop_event.wait(retry_delay)
                continue
            return
        except ValueError:
            return  # stdin cerrado
        if not line:
            return  # fin de la entrada (Ctrl+D, tubería agotada)
        if controller._stop_event.is_set():
            return
        controller._on_text_command(line)


def forget_injected_qt_platform(previous: Optional[str], env=None) -> None:
    """[FIX MUL-5] Tras crear la QApplication, deja QT_QPA_PLATFORM como lo puso el usuario.

    prepare_qt_platform() lo fija («xcb;wayland») solo para esta app; Qt ya eligió el plugin
    al crear la QApplication, y si se queda en os.environ lo heredan todos los hijos
    (gtk-launch, xdg-open, las apps que abre Zara), forzando XWayland en apps Qt nativas.
    """
    env = os.environ if env is None else env
    if previous is None:
        env.pop("QT_QPA_PLATFORM", None)
    else:
        env["QT_QPA_PLATFORM"] = previous


_INSTANCE_LOCK: Optional[QtCore.QLockFile] = None


def main() -> None:
    global _INSTANCE_LOCK
    parser = argparse.ArgumentParser(description=f"{config.APP_TITLE} - Asistente Táctico de IA Estilo J.A.R.V.I.S.")
    parser.add_argument("--version", action="version", version=config.APP_TITLE)
    parser.add_argument("--cli", action="store_true", help="Ejecutar en modo terminal sin HUD gráfico.")
    args = parser.parse_args()

    log_file = setup_logging(config.DATA_DIR / "logs")  # idempotente: ya activo si es __main__
    log.info("Registro de %s en %s", config.APP_TITLE, log_file)

    # [FIX V1_-8] Una sola Zara a la vez: dos abrirían el micrófono y duplicarían cada orden.
    lock_path = config.DATA_DIR / "zara.lock"
    _INSTANCE_LOCK = acquire_single_instance_lock(lock_path)
    if _INSTANCE_LOCK is None:
        log.error("%s ya está en ejecución (bloqueo: %s). Ciérrela antes de abrir otra.", config.APP_TITLE, lock_path)
        sys.exit(0)

    if args.cli:
        # [FIX V1_-10] Sin interfaz gráfica: QCoreApplication solo aporta el bucle de eventos,
        # así que funciona por SSH o en un TTY sin display ni plugin xcb.
        app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication(sys.argv)
    else:
        previous_qpa = os.environ.get("QT_QPA_PLATFORM")
        platform_error = prepare_qt_platform()
        if platform_error:
            log.error(platform_error)
            sys.exit(1)
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        forget_injected_qt_platform(previous_qpa)
    app.setApplicationName("ZARA")
    app.setApplicationVersion(config.VERSION_FULL)
    if not args.cli:
        # [DISEÑO] Icono de aplicación (barra de tareas/Alt+Tab si el SO lo expone
        # pese a Qt.Tool, y para cualquier ventana futura que herede el icono).
        icon_path = config.BASE_DIR / "config" / "zara-icon.ico"
        if not icon_path.is_file():
            icon_path = config.BASE_DIR / "config" / "zara-icon.png"
        if icon_path.is_file():
            app.setWindowIcon(QtGui.QIcon(str(icon_path)))

    controller = ZaraController(cli_mode=args.cli)
    # [FIX V1_-15] Salga como salga el bucle Qt (cierre de sesión, cerrar la bola...),
    # se apagan micrófono y motores; shutdown() es idempotente.
    app.aboutToQuit.connect(controller.shutdown)

    # Manejar Ctrl+C limpiamente en la terminal
    signal.signal(signal.SIGINT, lambda sig, frame: controller.shutdown() or app.quit())

    # Timer en bucle Qt para procesar señales de interrupción de Python
    sig_timer = QtCore.QTimer()
    sig_timer.timeout.connect(lambda: None)
    sig_timer.start(250)

    controller.start()

    if args.cli:
        print("Modo consola: escriba una orden y pulse Intro (Ctrl+C para salir).", flush=True)
        ignore_background_tty_reads()
        threading.Thread(target=read_stdin_commands, args=(controller,), daemon=True, name="zara-stdin").start()

    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        controller.shutdown()


if __name__ == "__main__":
    main()
