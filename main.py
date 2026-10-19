"""Punto de Entrada Principal (ZARA v2.0).

Orquesta el oído neuronal continuo (Silero VAD + faster-whisper), el motor
de voz streaming con interrupción Barge-in, el cerebro híbrido con ReAct
autónomo y la interfaz holográfica HUD en PySide6 (bola flotante + panel
táctico desplegable).
"""

from __future__ import annotations
import argparse
import logging
import queue
import signal
import sys
import threading
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

import config
from core.confirm import parse_confirmation
from core.brain import ZaraBrain, list_ollama_models, _pick_default_ollama_model
from core.ear import ContinuousEar, contains_wake_word
from core.live import GeminiLiveSession
from core.tools import set_confirmation_handler
from core.voice import VoiceSynthesizer
from ui.hud import HUDState, ZaraHUD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("zara.main")

CONFIRM_TIMEOUT_S = 25.0  # sin respuesta clara en este tiempo, la acción se cancela


class ZaraController(QtCore.QObject):
    """Controlador central que comunica los hilos de audio, el cerebro y el HUD."""

    # Señales Qt thread-safe para actualizar la UI desde hilos secundarios
    sig_hud_state = QtCore.Signal(object)
    sig_rms_level = QtCore.Signal(float)
    sig_tool_event = QtCore.Signal(str, str)
    sig_user_speech = QtCore.Signal(str)
    sig_assistant_reply = QtCore.Signal(str)
    sig_ollama_models = QtCore.Signal(list, str)

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

        set_confirmation_handler(self._ask_confirmation)

        # Hilo de escucha en bucle
        self._worker_thread = threading.Thread(target=self._voice_loop_worker, daemon=True)

    def _connect_signals(self) -> None:
        if not self.hud:
            return
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
                if self.hud:
                    self.hud.panel.log_line("CONFIRMAR", f"¿{description}?")
                self.voice.say(f"Señor VK, necesito su confirmación para {description}.")
                try:
                    return bool(answers.get(timeout=CONFIRM_TIMEOUT_S))
                except queue.Empty:
                    self.voice.say("Sin confirmación. Acción cancelada.")
                    return False
            finally:
                self._confirm_queue = None

    def _route_confirmation(self, text: str) -> bool:
        """Si hay una confirmación pendiente, consume `text` como respuesta.

        Devuelve True si había una pendiente (la frase no debe procesarse como orden nueva).
        """
        answers = self._confirm_queue
        if answers is None:
            return False
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
        if clean and not self.voice.is_speaking():
            self._route_confirmation(clean)
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
            if self.hud:
                self.hud.panel.log_line("SISTEMA", "Gemini Live: WebSocket conectado.")
        elif state in ("connecting", "reconnecting"):
            self.sig_hud_state.emit(HUDState.THINKING)
            if self.hud:
                self.hud.panel.log_line("SISTEMA", f"Gemini Live: {state}...")
        else:
            self.sig_hud_state.emit(HUDState.IDLE)

    def start(self) -> None:
        """Inicia todos los subsistemas de Zara."""
        log.info("Iniciando subsistemas de ZARA v2.0...")
        if self._preferred_provider == "gemini_live" and self.live_session.is_available():
            log.info("Iniciando directamente en modo Gemini Live...")
            self.live_session.start()
        else:
            self.ear.start()
            self._worker_thread.start()

        if self.hud:
            self.hud.show()
            self.sig_hud_state.emit(HUDState.IDLE)
            self.hud.set_brain(self._preferred_provider)
            self.hud.set_model_selection("ollama", self.brain.ollama.model)
            self.hud.set_model_selection("claude", self.brain.claude.cli_model_override)
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

    def shutdown(self) -> None:
        """Cierre ordenado de todos los hilos y motores."""
        log.info("Apagando ZARA v2.0...")
        self._stop_event.set()
        self.live_session.stop()
        self.ear.stop()
        self.voice.shutdown()
        log.info("Todos los subsistemas detenidos. Hasta pronto, Señor VK.")

    def _on_barge_in(self) -> None:
        """Interrupción inmediata: el Señor VK ha hablado sobre Zara (o pulsó 'Parar')."""
        if self.live_session.is_running():
            self.live_session.interrupt()
        self.voice.stop()
        if self._confirm_queue is not None:
            # Hablar mientras Zara pide confirmación es contestarle, no cancelar el turno.
            return
        # [FIX AUDITORÍA] Detener AUDIO y cancelar el turno del cerebro
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

    def _on_brain_changed(self, provider: str) -> None:
        """Cambia en caliente el cerebro LLM preferido desde el panel táctico."""
        self._preferred_provider = provider
        log.info("Cerebro preferido cambiado a: %s", provider)

        if provider == "gemini_live":
            if not self.live_session.is_available():
                log.warning("Gemini Live no disponible: se requiere GEMINI_API_KEY en .env")
                if self.hud:
                    self.hud.panel.log_line("ERROR", "Gemini Live requiere GEMINI_API_KEY en .env")
                self.voice.say("Señor VK, para activar Gemini Live en streaming se requiere configurar GEMINI_API_KEY.")
                return

            # Pausar oído clásico para evitar conflicto de captura de micrófono
            self.ear.stop()
            self.voice.stop()
            self.live_session.start()
            if self.hud:
                self.hud.panel.log_line("SISTEMA", "Modo Live activado: streaming de audio bidireccional.")
        else:
            # Si veníamos de Live, detenerlo y arrancar oído clásico
            if self.live_session.is_running():
                self.live_session.stop()
                self.ear.start()
            if self.hud:
                self.hud.panel.log_line("SISTEMA", f"Cerebro cambiado a: {provider}")

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
        elif provider == "gemini_live":
            self.live_session.set_model(model)
            config.persist_env_var("ZARA_GEMINI_LIVE_MODEL", model)
            label = model
        else:
            return

        log.info("Modelo de %s cambiado a: %s", provider, label)
        if self.hud:
            self.hud.panel.log_line("SISTEMA", f"Modelo de {provider} -> {label}")

    def _on_quit_requested(self) -> None:
        """Cierre limpio solicitado desde el botón 'Salir' del panel táctico."""
        log.info("Cierre solicitado desde el panel táctico.")
        self.shutdown()
        app = QtWidgets.QApplication.instance()
        if app:
            app.quit()

    def _on_text_command(self, text: str) -> None:
        """Procesa una orden escrita manualmente en el panel táctico."""
        text = text.strip()
        if not text:
            return
        if self._route_confirmation(text):
            return
        if self._is_busy:
            self.voice.say("Un momento, Señor VK, estoy procesando otra orden.")
            return
        threading.Thread(target=self._process_query, args=(text,), daemon=True).start()

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
        """
        for text in self.ear.listen_utterances():
            if self._stop_event.is_set():
                break

            clean_text = text.strip()
            if not clean_text:
                continue

            # [FIX] Palabra de activación obligatoria para órdenes de VOZ: el
            # micrófono escucha de forma continua, así que sin esto cualquier
            # ruido ambiente (TV, conversaciones, música) con palabras como
            # "pausa" o "minimiza" podía disparar acciones reales (minimizar
            # ventanas, pausar/reproducir multimedia...) sin que el Señor VK
            # se dirigiera a Zara en absoluto. Las órdenes escritas en el
            # panel táctico (_on_text_command) NO pasan por aquí: teclear ya
            # es una acción deliberada y no está sujeta a ruido ambiente.
            # Respuesta a una confirmación pendiente: no exige «Zara» ni inicia otro turno.
            if self._route_confirmation(clean_text):
                continue

            if not contains_wake_word(clean_text):
                log.debug("Locución ignorada (sin mencionar a Zara): '%s'", clean_text)
                continue

            if self._is_busy:
                log.info("Nueva locución mientras se procesaba un turno anterior: cancelándolo.")
                self.voice.stop()
                self.brain.cancel_current_turn()

            threading.Thread(target=self._process_query, args=(clean_text,), daemon=True).start()

    def _process_query(self, clean_text: str) -> None:
        """Procesa una orden (de voz o texto) a través del agente ReAct y la sintetiza."""
        log.info("Señor VK dijo: '%s'", clean_text)
        self.sig_user_speech.emit(clean_text)
        self.sig_hud_state.emit(HUDState.THINKING)

        with self._busy_lock:
            self._is_busy = True
            try:
                token_gen = self.brain.run_react_agent(clean_text, preferred_provider=self._preferred_provider)

                full_reply_parts = []

                def _tee_tokens():
                    for tok in token_gen:
                        full_reply_parts.append(tok)
                        yield tok

                # Enviar stream de tokens a la síntesis por frases
                self.voice.feed_token_stream(_tee_tokens())

                reply_text = "".join(full_reply_parts).strip()
                if reply_text:
                    self.sig_assistant_reply.emit(reply_text)
            except Exception as exc:
                log.exception("Error en procesamiento de orden: %s", exc)
                self.voice.say(f"Señor VK, ocurrió una anomalía: {exc}")
            finally:
                self._is_busy = False


def main() -> None:
    parser = argparse.ArgumentParser(description="ZARA v2.0 - Asistente Táctico de IA Estilo J.A.R.V.I.S.")
    parser.add_argument("--cli", action="store_true", help="Ejecutar en modo terminal sin HUD gráfico.")
    args = parser.parse_args()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("ZARA v2.0")
    # [DISEÑO] Icono de aplicación (barra de tareas/Alt+Tab si el SO lo expone
    # pese a Qt.Tool, y para cualquier ventana futura que herede el icono).
    icon_path = config.BASE_DIR / "config" / "zara-icon.ico"
    if not icon_path.is_file():
        icon_path = config.BASE_DIR / "config" / "zara-icon.png"
    if icon_path.is_file():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))

    controller = ZaraController(cli_mode=args.cli)

    # Manejar Ctrl+C limpiamente en la terminal
    signal.signal(signal.SIGINT, lambda sig, frame: controller.shutdown() or app.quit())

    # Timer en bucle Qt para procesar señales de interrupción de Python
    sig_timer = QtCore.QTimer()
    sig_timer.timeout.connect(lambda: None)
    sig_timer.start(250)

    controller.start()

    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        controller.shutdown()


if __name__ == "__main__":
    main()
