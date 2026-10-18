"""Pruebas unitarias para core/ear.py (ZARA v2.0)."""

import numpy as np
import pytest
import torch

import core.ear as ear_module
from core.ear import normalize_audio, clean_transcript, contains_wake_word, ContinuousEar
from config import FRAME_SIZE, SAMPLE_RATE


def test_contains_wake_word_detects_zara_anywhere_in_the_phrase():
    """[Regresión] Palabra de activación: sin mencionar a Zara, ruido
    ambiente (TV, conversaciones) con palabras como 'pausa' o 'minimiza' no
    debe tratarse como una orden dirigida a ella."""
    assert contains_wake_word("Zara, abre el bloc de notas") is True
    assert contains_wake_word("oye zara pon pausa a la musica") is True
    assert contains_wake_word("abre el bloc de notas zara") is True
    assert contains_wake_word("ZARA minimiza todo") is True

    # Sin la palabra "zara": debe ignorarse, aunque contenga palabras de
    # control del sistema (el escenario real del bug: TV/conversación).
    assert contains_wake_word("pon pausa a la musica") is False
    assert contains_wake_word("minimiza eso un poco") is False
    assert contains_wake_word("") is False
    assert contains_wake_word("mazara") is False  # no es la palabra completa


# =============================================================================
# [AUDITORÍA DE VOZ] Regresión: barge-in con modelo VAD dedicado + pre-roll de
# la interrupción + gate de RMS/periodo de gracia anti-eco.
#
# Se simulan los modelos Silero VAD (pesados y no deterministas) con dobles de
# prueba controlables, para poder dirigir la máquina de estados de
# `ContinuousEar._process_frame` de forma determinista y sin hardware real.
# =============================================================================


class _FakeProb:
    def __init__(self, value: float):
        self._value = value

    def item(self) -> float:
        return self._value


class _FakeBargeinModel:
    """Doble de prueba para el modelo VAD dedicado al barge-in."""

    def __init__(self):
        self.prob = 0.0
        self.reset_calls = 0

    def __call__(self, tensor, sr):
        return _FakeProb(self.prob)

    def reset_states(self) -> None:
        self.reset_calls += 1


class _FakeVadIterator:
    """Doble de prueba para silero_vad.VADIterator: por defecto no detecta
    nada; el test fuerza manualmente los eventos "start"/"end" que necesite."""

    def __init__(self):
        self.next_result = None
        self.reset_calls = 0

    def __call__(self, tensor_1d):
        result = self.next_result
        self.next_result = None
        return result

    def reset_states(self) -> None:
        self.reset_calls += 1


def _make_bare_ear(monkeypatch):
    """Crea un ContinuousEar SIN cargar los modelos reales (Whisper/Silero),
    con dobles de prueba inyectados, para testear la máquina de estados de
    segmentación/barge-in de forma rápida y determinista."""
    monkeypatch.setattr(ContinuousEar, "_init_vad", lambda self: None)
    monkeypatch.setattr(ContinuousEar, "_init_whisper", lambda self: None)

    speaking_flag = {"value": False}
    barge_in_calls = []

    def _is_speaking():
        return speaking_flag["value"]

    def _on_barge_in():
        barge_in_calls.append(True)
        speaking_flag["value"] = False  # como haría voice.stop() en main.py

    ear = ContinuousEar(barge_in_callback=_on_barge_in, is_speaking_fn=_is_speaking)

    ear._vad_model = object()
    ear._vad_model_bargein = _FakeBargeinModel()
    ear._vad_iterator = _FakeVadIterator()
    ear._barge_in_required_frames = 2
    ear._pre_roll_capacity = 10
    ear._reset_segmentation_state()

    return ear, speaking_flag, barge_in_calls


def _loud_frame(value: float = 0.3) -> np.ndarray:
    return np.full(FRAME_SIZE, value, dtype=np.float32)


def test_bargein_uses_dedicated_model_instance(monkeypatch):
    """[Regresión] El modelo de barge-in debe ser una instancia DISTINTA del
    que usa el VADIterator de segmentación normal, para no corromper su
    estado interno (bug detectado en la auditoría: llamadas directas al mismo
    modelo compartido degradaban el reconocimiento justo tras cada respuesta)."""
    ear, _, _ = _make_bare_ear(monkeypatch)
    assert ear._vad_model_bargein is not ear._vad_model


def test_bargein_respects_grace_period(monkeypatch):
    """[Regresión] Justo al empezar una frase, aunque el VAD puntúe alto, no
    debe dispararse barge-in dentro del periodo de gracia (evita falsos
    positivos por el arranque brusco del TTS)."""
    ear, speaking_flag, barge_in_calls = _make_bare_ear(monkeypatch)
    monkeypatch.setattr(ear_module, "VAD_BARGE_IN_GRACE_MS", 100)
    monkeypatch.setattr(ear_module, "VAD_BARGE_IN_MIN_RMS", 0.02)

    fake_time = {"t": 0.0}
    monkeypatch.setattr(ear_module.time, "monotonic", lambda: fake_time["t"])

    speaking_flag["value"] = True
    ear._vad_model_bargein.prob = 0.95

    for _ in range(5):
        ear._process_frame(_loud_frame())

    assert barge_in_calls == []
    assert speaking_flag["value"] is True


def test_bargein_blocked_by_quiet_echo(monkeypatch):
    """[Regresión] Eco de altavoz: el VAD puede puntuar alto una señal débil,
    pero si el volumen (RMS) no alcanza el mínimo, no debe interpretarse como
    que el Señor VK está hablando de verdad."""
    ear, speaking_flag, barge_in_calls = _make_bare_ear(monkeypatch)
    monkeypatch.setattr(ear_module, "VAD_BARGE_IN_GRACE_MS", 0)
    monkeypatch.setattr(ear_module, "VAD_BARGE_IN_MIN_RMS", 0.02)

    speaking_flag["value"] = True
    ear._vad_model_bargein.prob = 0.95

    quiet_echo_frame = _loud_frame(0.005)  # por debajo de VAD_BARGE_IN_MIN_RMS
    for _ in range(10):
        ear._process_frame(quiet_echo_frame)

    assert barge_in_calls == []
    assert speaking_flag["value"] is True


def test_bargein_triggers_and_preserves_interruption_preroll(monkeypatch):
    """[Regresión] Al confirmarse un barge-in real (voz alta y sostenida, sin
    eco), el arranque de la interrupción no debe perderse: el pre-roll
    acumulado mientras Zara hablaba debe formar parte de la locución que
    finalmente se encola para transcribir (antes se descartaba por completo,
    cortando las primeras palabras de lo que decía el Señor VK)."""
    ear, speaking_flag, barge_in_calls = _make_bare_ear(monkeypatch)
    monkeypatch.setattr(ear_module, "VAD_BARGE_IN_GRACE_MS", 0)
    monkeypatch.setattr(ear_module, "VAD_BARGE_IN_MIN_RMS", 0.02)
    ear._barge_in_required_frames = 2

    speaking_flag["value"] = True
    ear._vad_model_bargein.prob = 0.95
    loud = _loud_frame(0.3)

    ear._process_frame(loud)
    assert barge_in_calls == []  # 1er frame: aún no alcanza el umbral consecutivo

    ear._process_frame(loud)
    assert barge_in_calls == [True]  # 2º frame: barge-in confirmado
    assert speaking_flag["value"] is False  # el callback ya detuvo la voz (voice.stop())

    preroll_frames_captured = len(ear._speech_frames)
    assert preroll_frames_captured == 2, "el pre-roll de la interrupción no se sembró"
    assert ear._is_speech_active is True

    # El Señor VK sigue hablando tras la interrupción: suficientes frames para
    # superar el mínimo de 350 ms exigido antes de aceptar la locución.
    needed_samples = int(SAMPLE_RATE * 0.35)
    frames_needed = -(-needed_samples // FRAME_SIZE) + 2  # ceil + margen
    for _ in range(frames_needed):
        ear._process_frame(loud)

    # Señalar el fin real de la locución a través del VADIterator (doble de prueba).
    ear._vad_iterator.next_result = {"end": True}
    ear._process_frame(loud)

    assert not ear._utterance_queue.empty(), "la locución interrumpida nunca se encoló"
    utterance = ear._utterance_queue.get_nowait()

    expected_total_frames = preroll_frames_captured + frames_needed + 1
    assert len(utterance) == expected_total_frames * FRAME_SIZE, (
        "la locución no incluye el pre-roll de la interrupción: "
        f"se perdieron {expected_total_frames * FRAME_SIZE - len(utterance)} muestras"
    )


def test_segmentation_does_not_duplicate_first_frame(monkeypatch):
    """[AUDITORÍA DE VOZ, regresión] El frame que dispara el evento "start"
    del VADIterator no debe duplicarse en _speech_frames (bug: se añadía una
    vez al pre-roll y otra vez manualmente a continuación)."""
    ear, speaking_flag, _ = _make_bare_ear(monkeypatch)
    speaking_flag["value"] = False

    frame = _loud_frame(0.3)
    ear._vad_iterator.next_result = {"start": True}
    ear._process_frame(frame)

    assert ear._is_speech_active is True
    assert len(ear._speech_frames) == 1, "el primer frame de la locución se duplicó"
    assert np.array_equal(ear._speech_frames[0], frame)


def test_normalize_audio_empty():
    empty = np.array([], dtype=np.float32)
    norm = normalize_audio(empty)
    assert norm.size == 0


def test_normalize_audio_silence():
    silence = np.zeros(1024, dtype=np.float32)
    norm = normalize_audio(silence)
    assert np.all(norm == 0.0)


def test_normalize_audio_gain_applied():
    low_audio = np.full(1000, 0.05, dtype=np.float32)
    norm = normalize_audio(low_audio, target_peak=0.85, max_gain=20.0)
    assert np.max(norm) > 0.05
    assert np.max(norm) <= 0.86


def test_silero_vad_tensor_shape_compatibility():
    # Silero VAD v5 espera [1, 512] al llamarse directamente: model(tensor, sr)
    frame = np.zeros(FRAME_SIZE, dtype=np.float32)
    tensor_chunk = torch.from_numpy(frame).unsqueeze(0)
    assert tensor_chunk.shape == (1, 512)

    # VADIterator espera tensor 1D: [512]
    tensor_1d = tensor_chunk.squeeze(0)
    assert tensor_1d.shape == (512,)


def test_rms_energy_calculation():
    # Audio por debajo de 0.008 debe considerarse silencio (RMS gate)
    quiet_frame = np.random.uniform(-0.005, 0.005, size=FRAME_SIZE).astype(np.float32)
    rms_quiet = float(np.sqrt(np.mean(quiet_frame ** 2)))
    assert rms_quiet < 0.008

    # Audio con voz real debe superar con creces 0.008
    voice_frame = np.sin(np.linspace(0, 2 * np.pi * 440, FRAME_SIZE)).astype(np.float32) * 0.3
    rms_voice = float(np.sqrt(np.mean(voice_frame ** 2)))
    assert rms_voice > 0.008


def test_frame_padding_on_short_chunks():
    # Si llega un bloque menor a FRAME_SIZE, debe rellenarse con ceros a 512 muestras
    short_frame = np.ones(300, dtype=np.float32)
    if short_frame.shape[0] < FRAME_SIZE:
        padded = np.pad(short_frame, (0, FRAME_SIZE - short_frame.shape[0]))
    assert padded.shape == (512,)
    assert padded[299] == 1.0
    assert padded[300] == 0.0


def test_clean_transcript_valid_commands():
    assert clean_transcript("Hola Zara") == "Hola Zara"
    assert clean_transcript("¿Qué hora es?") == "¿Qué hora es?"
    assert clean_transcript("Abre el navegador") == "Abre el navegador"
    assert clean_transcript("  Comprueba mi IP...  ") == "Comprueba mi IP"


def test_clean_transcript_hallucinations_suppressed():
    # Subtítulos de YouTube/Amara
    assert clean_transcript("Subtítulos realizados por la comunidad de Amara.org") == ""
    assert clean_transcript("Subtítulos por Amara.org") == ""
    assert clean_transcript("¡Muchas gracias por ver el video!") == ""
    assert clean_transcript("Suscríbete a mi canal y dale like") == ""
    assert clean_transcript("Hasta la próxima") == ""
    assert clean_transcript("Thank you for watching!") == ""

    # Puntuación o ruido sin texto
    assert clean_transcript("...") == ""
    assert clean_transcript("---") == ""
    assert clean_transcript("?") == ""

    # Repetición patológica
    assert clean_transcript("gracias gracias gracias gracias") == ""

