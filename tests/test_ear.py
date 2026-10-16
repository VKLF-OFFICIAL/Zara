"""Pruebas unitarias para core/ear.py (ZARA v2.0)."""

import numpy as np
import pytest
import torch

from core.ear import normalize_audio, ContinuousEar
from config import FRAME_SIZE, SAMPLE_RATE


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
