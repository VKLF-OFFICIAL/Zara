"""Punto de Entrada Principal (ZARA v2.0).

Orquesta el oído neuronal continuo (Silero VAD + faster-whisper), el motor
de voz streaming con interrupción Barge-in, el cerebro híbrido con ReAct
autónomo y la interfaz holográfica HUD en PySide6.
"""

from __future__ import annotations
import argparse
import logging
import signal
import sys
import threading
import time
from typing import Optional

from PySide6 import QtCore, QtWidgets

import config
from core.brain import ZaraBrain
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

    def __init__(self, cli_mode: bool = False):
        super().__init__()
        self.cli_mode = cli_mode
        self._stop_event = threading.Event()
        self._is_busy = False
        self._busy_lock = threading.Lock()

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

        # 4. Interfaz HUD Holográfica
        self.hud: Optional[ZaraHUD] = None
        if not self.cli_mode:
            self.hud = ZaraHUD(on_toggle_listen=self._toggle_listen)
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
        self.hud.barge_in_requested.connect(self._on_barge_in)

    def start(self) -> None:
        """Inicia todos los subsistemas de Zara."""
        log.info("Iniciando subsistemas de ZARA v2.0...")
        self.ear.start()
        self._worker_thread.start()

        if self.hud:
            self.hud.show()
            self.sig_hud_state.emit(HUDState.IDLE)

        # Saludo inicial táctico
        saludo = "Sistemas tácticos operativos, Señor VK. A su servicio."
        log.info("ZARA: %s", saludo)
        self.voice.say(saludo)

    def shutdown(self) -> None:
        """Cierre ordenado de todos los hilos y motores."""
        log.info("Apagando ZARA v2.0...")
        self._stop_event.set()
        self.ear.stop()
        self.voice.shutdown()
        log.info("Todos los subsistemas detenidos. Hasta pronto, Señor VK.")

    def _on_barge_in(self) -> None:
        """Interrupción inmediata: el Señor VK ha hablado sobre Zara."""
        self.voice.stop()
        self.sig_hud_state.emit(HUDState.LISTENING)

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

    def _toggle_listen(self) -> None:
        log.info("Conmutación de escucha solicitada por usuario.")

    def _voice_loop_worker(self) -> None:
        """Hilo continuo que procesa las locuciones del Señor VK."""
        for text in self.ear.listen_utterances():
            if self._stop_event.is_set():
                break

            clean_text = text.strip()
            if not clean_text:
                continue

            log.info("Señor VK dijo: '%s'", clean_text)
            self.sig_user_speech.emit(clean_text)
            self.sig_hud_state.emit(HUDState.THINKING)

            with self._busy_lock:
                self._is_busy = True
                try:
                    # Iniciar agente ReAct autónomo con streaming
                    token_gen = self.brain.run_react_agent(clean_text)
                    # Enviar stream de tokens a la síntesis por frases
                    self.voice.feed_token_stream(token_gen)
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
