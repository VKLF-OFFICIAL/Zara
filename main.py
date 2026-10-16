"""Punto de Entrada Principal (ZARA v2.0).

Orquesta el oído neuronal continuo (Silero VAD + faster-whisper), el motor
de voz streaming con interrupción Barge-in, el cerebro híbrido con ReAct
autónomo y la interfaz holográfica HUD en PySide6 (bola flotante + panel
táctico desplegable).
"""

from __future__ import annotations
import argparse
import logging
import signal
import sys
import threading
from typing import Optional

from PySide6 import QtCore, QtWidgets

import config
from core.brain import ZaraBrain, list_ollama_models
from core.ear import ContinuousEar
from core.voice import VoiceSynthesizer
from ui.hud import HUDState, ZaraHUD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("zara.main")


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

        # 4. Interfaz HUD Holográfica (bola flotante + panel táctico)
        self.hud: Optional[ZaraHUD] = None
        if not self.cli_mode:
            self.hud = ZaraHUD()
            self._connect_signals()

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

    def start(self) -> None:
        """Inicia todos los subsistemas de Zara."""
        log.info("Iniciando subsistemas de ZARA v2.0...")
        self.ear.start()
        self._worker_thread.start()

        if self.hud:
            self.hud.show()
            self.sig_hud_state.emit(HUDState.IDLE)
            self.hud.set_brain(self._preferred_provider)
            self.hud.set_model_selection("ollama", self.brain.ollama.model)
            self.hud.set_model_selection("claude", self.brain.claude.cli_model_override)
            threading.Thread(target=self._load_ollama_models, daemon=True).start()

        # Saludo inicial táctico
        saludo = "Sistemas tácticos operativos, Señor VK. A su servicio."
        log.info("ZARA: %s", saludo)
        self.voice.say(saludo)

    def _load_ollama_models(self) -> None:
        """Consulta los modelos realmente instalados en Ollama (hilo aparte, red)."""
        models = list_ollama_models(self.brain.ollama.host)
        if models:
            self.sig_ollama_models.emit(models, self.brain.ollama.model)

    def shutdown(self) -> None:
        """Cierre ordenado de todos los hilos y motores."""
        log.info("Apagando ZARA v2.0...")
        self._stop_event.set()
        self.ear.stop()
        self.voice.shutdown()
        log.info("Todos los subsistemas detenidos. Hasta pronto, Señor VK.")

    def _on_barge_in(self) -> None:
        """Interrupción inmediata: el Señor VK ha hablado sobre Zara (o pulsó 'Parar')."""
        self.voice.stop()
        self.sig_hud_state.emit(HUDState.IDLE if self.ear.is_muted() else HUDState.LISTENING)

    def _on_voice_state_change(self, is_speaking: bool) -> None:
        """Callback cuando Zara empieza o termina de reproducir voz."""
        if is_speaking:
            self.sig_hud_state.emit(HUDState.SPEAKING)
        else:
            self.sig_hud_state.emit(HUDState.IDLE)

    def _on_rms_level(self, rms: float) -> None:
        if not self.voice.is_speaking():
            self.sig_rms_level.emit(rms)

    def _on_brain_tool_event(self, tool_name: str, desc: str) -> None:
        log.info("[TOOL] %s: %s", tool_name, desc)
        self.sig_tool_event.emit(tool_name, desc)
        self.sig_hud_state.emit(HUDState.THINKING)

    def _on_mic_toggle(self) -> None:
        """Pausa o reanuda la escucha continua (botón de micrófono del panel)."""
        muted = not self.ear.is_muted()
        self.ear.set_muted(muted)
        if self.hud:
            self.hud.set_muted(muted)
            self.sig_hud_state.emit(HUDState.IDLE)

    def _on_brain_changed(self, provider: str) -> None:
        """Cambia en caliente el cerebro LLM preferido desde el panel táctico."""
        self._preferred_provider = provider
        log.info("Cerebro preferido cambiado a: %s", provider)
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
        if self._is_busy:
            self.voice.say("Un momento, Señor VK, estoy procesando otra orden.")
            return
        threading.Thread(target=self._process_query, args=(text,), daemon=True).start()

    def _voice_loop_worker(self) -> None:
        """Hilo continuo que procesa las locuciones del Señor VK."""
        for text in self.ear.listen_utterances():
            if self._stop_event.is_set():
                break

            clean_text = text.strip()
            if not clean_text:
                continue

            self._process_query(clean_text)

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
