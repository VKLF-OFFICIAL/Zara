"""Pruebas unitarias para core/voice.py (ZARA v2.0)."""

import threading
import time

import numpy as np
import pytest

import core.voice as voice_module
from core.voice import SentenceChunker, VoiceSynthesizer, clean_text_for_speech


# =============================================================================
# [AUDITORÍA DE VOZ] Regresión: pipeline de síntesis adelantada.
#
# Medido en real contra EdgeTTS: cada llamada de síntesis tarda ~1.2-1.6s de
# ida y vuelta de red. La implementación anterior era estrictamente secuencial
# (sintetizar frase -> reproducirla -> sintetizar la siguiente -> ...), lo que
# dejaba un corte de silencio de más de un segundo entre cada frase de
# cualquier respuesta de más de una frase: eso es lo que se oía como "se corta
# cuando habla". Estas pruebas sustituyen sounddevice y la síntesis real por
# dobles controlables para verificar, de forma determinista, que la síntesis
# de la frase N+1 ahora se adelanta mientras suena la frase N.
# =============================================================================


class _FakeStream:
    """Simula un stream de sounddevice que está 'activo' durante `duration`
    segundos desde que se crea, y luego se considera terminado."""

    def __init__(self, duration: float):
        self._end_time = time.monotonic() + duration

    @property
    def active(self) -> bool:
        return time.monotonic() < self._end_time


def _install_fake_audio_backend(monkeypatch, play_duration: float, play_starts: list):
    """Sustituye sounddevice.play/get_stream/stop por dobles sin hardware
    real, simulando que cada reproducción dura `play_duration` segundos y
    registrando en `play_starts` cuándo empieza cada una (en orden)."""
    state = {"stream": None}

    def fake_play(samples, samplerate):
        play_starts.append(time.monotonic())
        state["stream"] = _FakeStream(play_duration)

    def fake_get_stream():
        return state["stream"]

    def fake_stop():
        state["stream"] = None

    monkeypatch.setattr(voice_module.sd, "play", fake_play)
    monkeypatch.setattr(voice_module.sd, "get_stream", fake_get_stream)
    monkeypatch.setattr(voice_module.sd, "stop", fake_stop)


def test_voice_pipeline_overlaps_synthesis_with_playback(monkeypatch):
    """[Regresión] La síntesis de la 2ª frase debe empezar ANTES de que
    termine de REPRODUCIRSE la 1ª (pipeline real), no solo antes de que
    empiece a sintetizarse — eso es lo que elimina el corte de silencio entre
    frases. Con la implementación secuencial antigua, esta prueba fallaría:
    la síntesis de la 2ª frase no empezaba hasta que la 1ª terminaba de
    sonar."""
    synth_latency = 0.3
    play_duration = 0.3
    play_starts: list = []
    _install_fake_audio_backend(monkeypatch, play_duration, play_starts)

    events = []
    events_lock = threading.Lock()

    def fake_synthesize_audio(self, text):
        with events_lock:
            events.append((text, "synth_start", time.monotonic()))
        time.sleep(synth_latency)
        with events_lock:
            events.append((text, "synth_end", time.monotonic()))
        return np.zeros(10, dtype=np.float32), 24000

    monkeypatch.setattr(VoiceSynthesizer, "_synthesize_audio", fake_synthesize_audio)

    voice = VoiceSynthesizer()
    try:
        voice.say("Primera frase de prueba suficientemente larga para no filtrarse.")
        voice.say("Segunda frase de prueba suficientemente larga para no filtrarse.")

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if len(play_starts) >= 2 and not voice.is_speaking():
                break
            time.sleep(0.02)
    finally:
        voice.shutdown()

    with events_lock:
        snapshot = list(events)

    assert len(play_starts) == 2, "no se reprodujeron ambas frases"
    play1_end = play_starts[0] + play_duration
    synth2_start = next(t for text, kind, t in snapshot if "Segunda" in text and kind == "synth_start")

    assert synth2_start < play1_end, (
        "la síntesis de la 2ª frase no se adelantó mientras sonaba la 1ª: "
        "el pipeline sigue siendo secuencial y se oirá un corte de silencio "
        "entre frases (regresión del bug de audio auditado)"
    )


def test_voice_stop_discards_in_flight_synthesis(monkeypatch):
    """[Regresión] Un barge-in (stop()) mientras se está sintetizando la
    siguiente frase no debe dejar que ese audio 'resucite' y se reproduzca
    después de haberse abortado la respuesta."""
    _install_fake_audio_backend(monkeypatch, play_duration=0.05, play_starts=[])

    release_synthesis = threading.Event()

    def slow_synthesize_audio(self, text):
        release_synthesis.wait(timeout=2.0)
        return np.zeros(10, dtype=np.float32), 24000

    monkeypatch.setattr(VoiceSynthesizer, "_synthesize_audio", slow_synthesize_audio)

    voice = VoiceSynthesizer()
    try:
        voice.say("Frase que será abortada a mitad de síntesis.")
        time.sleep(0.05)  # dar tiempo a que el hilo de síntesis la recoja
        voice.stop()
        release_synthesis.set()  # ahora "termina" la síntesis que iba en vuelo
        time.sleep(0.2)

        assert voice._audio_queue.empty(), "el audio abortado se encoló para reproducirse igualmente"
        assert voice.is_speaking() is False
    finally:
        release_synthesis.set()
        voice.shutdown()


def test_voice_playback_discards_stale_generation_item(monkeypatch):
    """[AUDITORÍA DE VOZ, regresión] Si un item de audio de una generación ya
    cancelada llegara a `_audio_queue` (p. ej. por una carrera), el hilo de
    reproducción debe descartarlo sin sonar y sin tocar el contador de la
    generación ACTUAL (que gestiona su propio trabajo pendiente)."""
    play_calls: list = []
    _install_fake_audio_backend(monkeypatch, play_duration=0.05, play_starts=play_calls)

    voice = VoiceSynthesizer()
    try:
        with voice._lock:
            voice._generation = 1  # la generación actual ya avanzó (hubo un barge-in)
            voice._pending_count = 1  # trabajo legítimo del turno nuevo (generación 1)

        # Item "fantasma" de la generación 0, ya obsoleta.
        stale_item = (np.zeros(10, dtype=np.float32), 24000, 0)
        voice._audio_queue.put_nowait(stale_item)

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and voice._audio_queue.qsize() > 0:
            time.sleep(0.02)
        time.sleep(0.1)

        assert play_calls == [], "se reprodujo un audio de una generación ya cancelada"
        with voice._lock:
            assert voice._pending_count == 1, (
                "descartar un item obsoleto no debe tocar el contador de la generación actual"
            )
    finally:
        voice.shutdown()


def test_voice_pending_count_reaches_zero_after_all_sentences_play(monkeypatch):
    """El contador de pendientes debe volver a 0 (is_speaking=False) solo
    cuando TODAS las frases encoladas han terminado de reproducirse, no antes."""
    _install_fake_audio_backend(monkeypatch, play_duration=0.05, play_starts=[])

    def fast_synthesize_audio(self, text):
        return np.zeros(10, dtype=np.float32), 24000

    monkeypatch.setattr(VoiceSynthesizer, "_synthesize_audio", fast_synthesize_audio)

    voice = VoiceSynthesizer()
    try:
        voice.say("Una.")
        voice.say("Dos.")
        voice.say("Tres.")

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and voice._pending_count > 0:
            time.sleep(0.02)

        assert voice._pending_count == 0
        assert voice.is_speaking() is False
    finally:
        voice.shutdown()


def test_clean_text_for_speech_markdown_removal():
    raw = "Hola **Señor VK**, he ejecutado `ls -la` y encontré *3* archivos."
    cleaned = clean_text_for_speech(raw)
    assert "**" not in cleaned
    assert "`" not in cleaned
    assert "*" not in cleaned
    assert "Señor VK" in cleaned
    assert "ls -la" in cleaned


def test_clean_text_code_block_replacement():
    raw = "Mire este código:\n```python\nprint('hello')\n```\n¿Desea ejecutarlo?"
    cleaned = clean_text_for_speech(raw)
    assert "print('hello')" not in cleaned
    assert "código omitido en voz" in cleaned


def test_sentence_chunker_basic():
    chunker = SentenceChunker(min_length=20)
    tokens = ["Sistemas ", "tácticos ", "operativos, ", "Señor VK. ", "Todo en orden."]
    chunks = []
    for tok in tokens:
        res = chunker.push(tok)
        chunks.extend(res)
    # Debe haber emitido al menos una frase completa antes del final
    assert len(chunks) >= 1
    assert "Sistemas tácticos operativos, Señor VK." in chunks[0]

    # Vaciar buffer remanente
    rem = chunker.flush()
    if rem:
        chunks.append(rem)
    assert len(chunks) >= 2


def test_sentence_chunker_short_abbreviation_not_cut():
    chunker = SentenceChunker(min_length=30)
    # "Sr. " es muy corto, no debe emitirse como frase aislada
    res1 = chunker.push("Buenas tardes Sr. ")
    assert len(res1) == 0

    res2 = chunker.push("VK, los escaneos de red han finalizado correctamente.")
    assert len(res2) == 1
    assert "Buenas tardes Sr. VK" in res2[0]


def test_sentence_chunker_flush_empty():
    chunker = SentenceChunker(min_length=20)
    assert chunker.flush() == []


def test_first_sentence_can_be_shorter_than_the_rest():
    chunker = SentenceChunker(min_length=35, first_min_length=10)
    first = chunker.push("Claro, Señor. Estoy ejecutando la tarea que me ha pedido ahora mismo.")
    assert first[0] == "Claro, Señor."
    # a partir de la primera, vuelve a regir el mínimo normal
    assert len(first) == 2 and len(first[1]) >= 35


def test_chunker_default_behaviour_unchanged():
    chunker = SentenceChunker(min_length=35)
    assert chunker.push("Claro, Señor. ") == []
