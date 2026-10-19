"""Pruebas de regresión del grupo «oído y voz» (core/ear.py, core/voice.py).

Cubren: detección de GPU para Whisper (MUL-1), arranque sin red ni modelo (V1_-5),
importación sin PortAudio (MUL-2), reintentos y remuestreo del micrófono (AUD-7/V1_-9),
start()/stop() repetibles del oído, barge-in corto (AUD-14), cola de audio llena
(AUD-6) y los flujos de tokens tras un barge-in o una confirmación (AUD-8).

Todo el hardware (sounddevice, Whisper, CUDA) se sustituye por dobles de prueba, así
que las ramas de Windows se ejercitan simulando la plataforma con monkeypatch.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

import core.ear as ear_module
import core.voice as voice_module
from config import FRAME_SIZE, SAMPLE_RATE, VAD_MIN_SILENCE_MS, VAD_SPEECH_PAD_MS, VAD_THRESHOLD, resolve_whisper_model
from core.ear import ContinuousEar
from core.voice import VoiceSynthesizer

ROOT = Path(__file__).resolve().parent.parent


def _wait_until(cond, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return cond()


# =============================================================================
# MUL-1: detección de GPU y respaldo a CPU
# =============================================================================


class _FakeWhisperFactory:
    """Genera una clase falsa de faster_whisper.WhisperModel configurable."""

    def __init__(self, load_ok=lambda name, device: True, cuda_transcribe_ok: bool = True):
        self.load_ok = load_ok
        self.cuda_transcribe_ok = cuda_transcribe_ok
        self.attempts: list[tuple[str, str]] = []
        factory = self

        class _FakeWhisper:
            def __init__(self, name, device="cpu", compute_type="int8", **kwargs):
                factory.attempts.append((str(name), device))
                if not factory.load_ok(str(name), device):
                    raise OSError(f"sin conexión: no se puede descargar {name}")
                self.name = str(name)
                self.device = device
                self.compute_type = compute_type

            def transcribe(self, audio, **kwargs):
                if self.device == "cuda" and not factory.cuda_transcribe_ok:
                    raise RuntimeError("Library libcublas.so.12 is not found or cannot be loaded")
                return iter([]), None

        self.cls = _FakeWhisper


def _ear_with_real_whisper_init(monkeypatch, factory: _FakeWhisperFactory, **kwargs) -> ContinuousEar:
    import faster_whisper

    monkeypatch.setattr(ContinuousEar, "_init_vad", lambda self: None)
    monkeypatch.setattr(faster_whisper, "WhisperModel", factory.cls)
    monkeypatch.setattr(ear_module, "WHISPER_MODEL_PRIMARY", "auto")
    return ContinuousEar(**kwargs)


def test_detect_device_uses_ctranslate2_cuda_device_count(monkeypatch):
    """[MUL-1] La GPU se detecta con ctranslate2.get_cuda_device_count() (la API
    anterior, get_supported_devices, no existe y tumbaba el arranque)."""
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
    monkeypatch.setattr(ear_module, "_missing_cuda_libs", lambda: [])
    assert ear_module._detect_whisper_device() == ("cuda", "float16")

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 0)
    assert ear_module._detect_whisper_device() == ("cpu", "int8")

    def _broken():
        raise RuntimeError("CUDA driver version is insufficient for CUDA runtime version")

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", _broken)
    assert ear_module._detect_whisper_device() == ("cpu", "int8")


def test_detect_device_falls_back_to_cpu_without_cuda_libraries(monkeypatch):
    """[MUL-1] Con GPU pero sin cuBLAS/cuDNN, faster-whisper puede abortar el proceso
    al transcribir: en ese caso se elige CPU desde el principio."""
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
    monkeypatch.setattr(ear_module, "_missing_cuda_libs", lambda: ["libcudnn.so.9"])
    assert ear_module._detect_whisper_device() == ("cpu", "int8")


def test_cuda_library_names_per_platform_and_version(monkeypatch):
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "__version__", "4.6.2")  # 4.5 a 4.6.2: cuDNN 9 obligatorio
    monkeypatch.setattr(sys, "platform", "win32")
    win = ear_module._cuda_runtime_libraries()
    assert "cublas64_12.dll" in win and "cudnn64_9.dll" in win
    assert all(name.endswith(".dll") for name in win)

    monkeypatch.setattr(sys, "platform", "linux")
    linux = ear_module._cuda_runtime_libraries()
    assert "libcublas.so.12" in linux and "libcudnn.so.9" in linux

    monkeypatch.setattr(ctranslate2, "__version__", "4.4.0")  # ctranslate2 < 4.5 usa cuDNN 8
    assert "libcudnn.so.8" in ear_module._cuda_runtime_libraries()


@pytest.mark.parametrize("version", ["4.6.3", "4.7.0", "4.8.3", "5.0.0.dev1"])
def test_cudnn_not_required_since_ctranslate2_4_6_3(monkeypatch, version):
    """[MUL-1, ronda 2] Desde ctranslate2 4.6.3 cuDNN es opcional (Conv1d CUDA pura):
    exigirlo mandaba a CPU a quien solo tiene cuBLAS. Windows simulado y Linux."""
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "__version__", version)
    monkeypatch.setattr(sys, "platform", "win32")
    assert ear_module._cuda_runtime_libraries() == ["cublas64_12.dll"]
    monkeypatch.setattr(sys, "platform", "linux")
    assert ear_module._cuda_runtime_libraries() == ["libcublas.so.12"]

    # Con cuBLAS pero sin cuDNN se usa la GPU.
    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
    monkeypatch.setattr(ear_module, "_can_load_library", lambda name: "cudnn" not in name)
    assert ear_module._detect_whisper_device() == ("cuda", "float16")


def test_can_load_library_searches_windows_path(monkeypatch):
    """[MUL-1, Windows simulado] ctypes no busca en el PATH desde Python 3.8, pero
    ctranslate2.dll sí: la comprobación debe encontrar la DLL donde la encontraría él."""
    import ctypes
    import ctypes.util

    monkeypatch.setattr(sys, "platform", "win32")
    loaded = []

    def fake_cdll(name, *args, **kwargs):
        if "\\" not in name and "/" not in name:
            raise OSError(f"no se encuentra {name}")
        loaded.append(name)
        return object()

    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(
        ctypes.util, "find_library",
        lambda n: rf"C:\CUDA\v12.4\bin\{n}" if n == "cublas64_12.dll" else None,
    )
    assert ear_module._can_load_library("cublas64_12.dll") is True
    assert loaded == [r"C:\CUDA\v12.4\bin\cublas64_12.dll"]
    assert ear_module._can_load_library("cudnn64_9.dll") is False


def test_init_whisper_does_not_crash_when_torch_reports_cuda(monkeypatch):
    """[MUL-1, regresión] Escenario del fallo: torch.cuda.is_available() devuelve True
    (torch de PyPI en Linux con NVIDIA). Antes se evaluaba una API inexistente de
    ctranslate2 y ContinuousEar() lanzaba AttributeError: Zara no arrancaba."""
    import ctranslate2
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 0)
    factory = _FakeWhisperFactory()
    ear = _ear_with_real_whisper_init(monkeypatch, factory)

    assert ear.stt_available()
    assert ear._device == "cpu"
    assert ear._whisper_model.device == "cpu"


def test_cuda_warmup_failure_falls_back_to_cpu_model(monkeypatch):
    """[MUL-1] Si el modelo carga en CUDA pero la primera transcripción falla (faltan
    cuBLAS/cuDNN de CUDA 12), se pasa a CPU con el modelo de CPU, no queda sorda."""
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
    monkeypatch.setattr(ear_module, "_missing_cuda_libs", lambda: [])
    factory = _FakeWhisperFactory(cuda_transcribe_ok=False)
    ear = _ear_with_real_whisper_init(monkeypatch, factory)

    assert factory.attempts[0][1] == "cuda"
    assert ear._device == "cpu"
    assert ear._whisper_model.device == "cpu"
    assert ear._whisper_model.name == resolve_whisper_model("auto", "cpu")
    assert ear.stt_available()


# =============================================================================
# V1_-5: sin red y sin modelo descargado
# =============================================================================


def _make_snapshot(root: Path, repo: str, with_tokenizer: bool = True) -> Path:
    snap = root / "whisper" / f"models--{repo}" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"\0")
    if with_tokenizer:
        (snap / "tokenizer.json").write_text("{}", encoding="utf-8")
    return snap


def test_offline_uses_any_cached_whisper_model(monkeypatch, tmp_path):
    """[V1_-5] Sin red, si el modelo elegido no está en caché pero hay otro completo
    descargado, se usa ese en vez de fallar."""
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 0)
    monkeypatch.setattr(ear_module, "MODELS_DIR", tmp_path)
    cached = _make_snapshot(tmp_path, "Systran--faster-whisper-base")
    _make_snapshot(tmp_path, "Systran--faster-whisper-small", with_tokenizer=False)  # incompleto

    factory = _FakeWhisperFactory(load_ok=lambda name, device: Path(name).is_dir())
    ear = _ear_with_real_whisper_init(monkeypatch, factory)

    assert ear.stt_available()
    assert Path(ear._whisper_model.name) == cached
    assert ear._whisper_model.device == "cpu"


def test_offline_without_any_model_keeps_app_alive(monkeypatch, tmp_path):
    """[V1_-5, regresión] Sin red y sin ningún modelo, el «último fallback» lanzaba la
    excepción y ZaraController no llegaba a crearse. Ahora el oído queda sin STT, con
    un aviso claro, y transcribe_array devuelve texto vacío."""
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 0)
    monkeypatch.setattr(ear_module, "MODELS_DIR", tmp_path)
    statuses = []
    factory = _FakeWhisperFactory(load_ok=lambda name, device: False)
    ear = _ear_with_real_whisper_init(
        monkeypatch, factory, status_callback=lambda lvl, msg: statuses.append((lvl, msg)),
    )

    assert not ear.stt_available()
    assert "internet" in ear.stt_error
    assert statuses and statuses[0][0] == "ERROR"
    loud = np.full(SAMPLE_RATE, 0.3, dtype=np.float32)
    assert ear.transcribe_array(loud) == ""


# =============================================================================
# MUL-2: importar sin PortAudio (sounddevice lanza OSError)
# =============================================================================


class _NoPortAudioFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Simula Linux sin libportaudio2: importar sounddevice lanza OSError."""

    def find_spec(self, fullname, path, target=None):
        if fullname == "sounddevice":
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raise OSError("PortAudio library not found")


@pytest.fixture
def no_portaudio(monkeypatch):
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    finder = _NoPortAudioFinder()
    monkeypatch.setattr(sys, "meta_path", [finder] + sys.meta_path)
    return finder


def _load_module_copy(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_voice_imports_and_degrades_without_portaudio(no_portaudio, monkeypatch):
    """[MUL-2, regresión] Antes `import core.voice` lanzaba OSError y tumbaba main.py.
    Ahora el módulo carga, avisa de cómo instalar PortAudio y la voz descarta las
    frases sin dejar a Zara «hablando» para siempre."""
    with pytest.raises(OSError):
        import sounddevice  # noqa: F401  (el doble de prueba está activo)

    voice_copy = _load_module_copy("zara_voice_sin_portaudio", "core/voice.py")
    assert voice_copy.sd is None
    assert "libportaudio2" in voice_copy.AUDIO_OUTPUT_ERROR

    synth_calls = []
    monkeypatch.setattr(
        voice_copy.VoiceSynthesizer, "_synthesize_audio",
        lambda self, text: synth_calls.append(text) or (np.zeros(10, dtype=np.float32), 24000),
    )
    voice = voice_copy.VoiceSynthesizer()
    try:
        assert voice.output_available() is False
        voice.say("Sistemas tácticos operativos, Señor VK.")
        assert _wait_until(lambda: voice._pending_count == 0 and voice._tts_queue.empty())
        assert voice.is_speaking() is False
        assert synth_calls == []  # sin salida de audio no se gasta red sintetizando
        voice.stop()  # no debe fallar sin sounddevice
    finally:
        voice.shutdown()


def test_ear_reports_missing_portaudio_and_keeps_retrying(no_portaudio, monkeypatch):
    """[MUL-2 + V1_-9] Sin PortAudio el hilo de captura moría con una excepción sin
    aviso. Ahora avisa (con la orden para instalarlo) y sigue reintentando, por si se
    instala con Zara abierta."""
    monkeypatch.setattr(ear_module, "_MIC_RETRY_MIN_S", 0.01)
    monkeypatch.setattr(ear_module, "_MIC_RETRY_MAX_S", 0.02)
    monkeypatch.setattr(ContinuousEar, "_init_vad", lambda self: None)
    monkeypatch.setattr(ContinuousEar, "_init_whisper", lambda self: None)
    statuses = []
    ear = ContinuousEar(status_callback=lambda lvl, msg: statuses.append((lvl, msg)))

    stop_event = threading.Event()
    worker = threading.Thread(target=ear._capture_audio_worker, args=(stop_event,), daemon=True)
    worker.start()
    try:
        assert _wait_until(lambda: len(statuses) >= 1)
        time.sleep(0.1)  # varios reintentos
        assert worker.is_alive(), "el hilo de captura murió en vez de reintentar"
        assert statuses[0][0] == "ERROR" and "libportaudio2" in statuses[0][1]
        assert len(statuses) == 1, "el mismo error no debe repetirse en el panel en cada reintento"
    finally:
        stop_event.set()
        worker.join(timeout=2.0)
    assert not worker.is_alive()


# =============================================================================
# AUD-7 / V1_-9: micrófono ausente, desconectado o sin 16 kHz
# =============================================================================


class _FakeInputStream:
    def __init__(self, sd, samplerate, channels, dtype, blocksize, callback):
        sd.open_attempts.append({"samplerate": samplerate, "blocksize": blocksize})
        if sd.fail_opens > 0:
            sd.fail_opens -= 1
            raise sd.PortAudioError("Error opening InputStream: Device unavailable [PaErrorCode -9985]")
        self._sd = sd
        self._blocksize = blocksize
        self._callback = callback
        self._closed = threading.Event()
        self._stall_after = sd.stall_plan.pop(0) if sd.stall_plan else None
        self.active = False

    def __enter__(self):
        self.active = True
        self._sd.opened += 1
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        sent = 0
        while not self._closed.is_set():
            if self._stall_after is None or sent < self._stall_after:
                block = np.full((self._blocksize, 1), 0.1, dtype=np.float32)
                self._callback(block, self._blocksize, None, None)
                sent += 1
            time.sleep(0.005)

    def __exit__(self, *exc):
        self._closed.set()
        self.active = False
        return False


class _FakeSoundDevice:
    """Doble de prueba del módulo sounddevice (sin hardware)."""

    class PortAudioError(Exception):
        pass

    def __init__(self, fail_opens=0, supported_rates=(SAMPLE_RATE,), native_rate=SAMPLE_RATE, stall_plan=None):
        self.fail_opens = fail_opens
        self.supported_rates = set(supported_rates)
        self.native_rate = native_rate
        self.stall_plan = list(stall_plan or [])
        self.open_attempts: list[dict] = []
        self.opened = 0

    def InputStream(self, samplerate, channels, dtype, blocksize, callback):  # noqa: N802
        return _FakeInputStream(self, samplerate, channels, dtype, blocksize, callback)

    def check_input_settings(self, samplerate=None, channels=None, dtype=None):
        if samplerate not in self.supported_rates:
            raise self.PortAudioError("Invalid sample rate [PaErrorCode -9997]")

    def query_devices(self, kind=None):
        return {"name": "Micro de prueba", "default_samplerate": float(self.native_rate)}

    def stop(self):
        pass

    def _terminate(self):
        pass

    def _initialize(self):
        pass


def _bare_ear(monkeypatch, **kwargs) -> ContinuousEar:
    monkeypatch.setattr(ContinuousEar, "_init_vad", lambda self: None)
    monkeypatch.setattr(ContinuousEar, "_init_whisper", lambda self: None)
    return ContinuousEar(**kwargs)


@pytest.fixture
def fast_mic_retries(monkeypatch):
    monkeypatch.setattr(ear_module, "_MIC_RETRY_MIN_S", 0.01)
    monkeypatch.setattr(ear_module, "_MIC_RETRY_MAX_S", 0.05)
    monkeypatch.setattr(ear_module, "_MIC_STALL_S", 0.3)


def _run_capture(ear: ContinuousEar):
    stop_event = threading.Event()
    worker = threading.Thread(target=ear._capture_audio_worker, args=(stop_event,), daemon=True)
    worker.start()
    return stop_event, worker


def test_capture_retries_until_microphone_appears(monkeypatch, fast_mic_retries):
    """[AUD-7/V1_-9, regresión] Sin micrófono al arrancar, el hilo de captura moría
    para siempre. Ahora reintenta, avisa una vez y, al aparecer el micro, se recupera."""
    fake_sd = _FakeSoundDevice(fail_opens=3)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    statuses = []
    ear = _bare_ear(monkeypatch, status_callback=lambda lvl, msg: statuses.append((lvl, msg)))

    stop_event, worker = _run_capture(ear)
    try:
        assert _wait_until(lambda: ear._raw_queue.qsize() >= 3), "el oído no se recuperó al aparecer el micrófono"
        assert len(fake_sd.open_attempts) == 4
        assert [lvl for lvl, _ in statuses] == ["ERROR", "SISTEMA"]
        assert ear.mic_error == ""
        frame = ear._raw_queue.get_nowait()
        assert frame.shape == (FRAME_SIZE,)
    finally:
        stop_event.set()
        worker.join(timeout=2.0)
    assert not worker.is_alive()


def test_capture_reopens_when_device_stops_sending_audio(monkeypatch, fast_mic_retries):
    """[AUD-7] Micrófono USB/Bluetooth desconectado en marcha: PortAudio deja de llamar
    al callback y antes nada lo detectaba. Ahora se reabre el dispositivo."""
    fake_sd = _FakeSoundDevice(stall_plan=[2])  # el primer stream se «cuelga» tras 2 bloques
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    ear = _bare_ear(monkeypatch)

    stop_event, worker = _run_capture(ear)
    try:
        assert _wait_until(lambda: fake_sd.opened >= 2, timeout=3.0), "no se reabrió el micrófono colgado"
        assert _wait_until(lambda: ear._raw_queue.qsize() >= 5)
    finally:
        stop_event.set()
        worker.join(timeout=2.0)


def test_capture_uses_native_rate_when_16k_is_not_supported(monkeypatch, fast_mic_retries):
    """[V1_-9] Si el dispositivo no admite 16 kHz (ALSA hw directo), se captura a su
    frecuencia nativa y se remuestrea a bloques de 512 muestras a 16 kHz."""
    fake_sd = _FakeSoundDevice(supported_rates=(48000,), native_rate=48000)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    ear = _bare_ear(monkeypatch)

    stop_event, worker = _run_capture(ear)
    try:
        assert _wait_until(lambda: ear._raw_queue.qsize() >= 2)
        assert fake_sd.open_attempts[0] == {"samplerate": 48000, "blocksize": FRAME_SIZE * 3}
        frame = ear._raw_queue.get_nowait()
        assert frame.shape == (FRAME_SIZE,)
        assert frame.dtype == np.float32
    finally:
        stop_event.set()
        worker.join(timeout=2.0)


def test_portaudio_reinit_is_rate_limited_without_microphone(monkeypatch):
    """[AUD-7, ronda 2, regresión] Sin micrófono por defecto (Windows sin dispositivo de
    grabación), PortAudio se reinicializaba en cada reintento, cada 10 s para siempre.
    Ahora como mucho una vez cada _PA_REFRESH_INTERVAL_S, y nunca con Zara hablando."""
    calls = {"terminate": 0, "initialize": 0}

    class _NoMicSD(_FakeSoundDevice):
        def query_devices(self, kind=None):
            raise self.PortAudioError("Error querying device -1")

        def _terminate(self):
            calls["terminate"] += 1

        def _initialize(self):
            calls["initialize"] += 1

    monkeypatch.setattr(sys, "platform", "win32")
    clock = {"now": 1000.0}
    monkeypatch.setattr(ear_module, "time", types.SimpleNamespace(monotonic=lambda: clock["now"]))
    speaking = {"value": True}
    ear = _bare_ear(monkeypatch, is_speaking_fn=lambda: speaking["value"])
    sd = _NoMicSD()

    ear._refresh_audio_devices(sd)  # Zara hablando: no se toca PortAudio
    assert calls["terminate"] == 0
    speaking["value"] = False
    ear._refresh_audio_devices(sd)
    assert calls == {"terminate": 1, "initialize": 1}
    for _ in range(5):  # reintentos cada 10 s
        clock["now"] += 10.0
        ear._refresh_audio_devices(sd)
    assert calls["terminate"] == 1, "PortAudio se reinicializó en cada reintento"
    clock["now"] += ear_module._PA_REFRESH_INTERVAL_S
    ear._refresh_audio_devices(sd)
    assert calls["terminate"] == 2


@pytest.mark.parametrize("src_rate", [48000, 44100, 22050])
def test_stream_resampler_keeps_duration_and_pitch(src_rate):
    """[V1_-9] El remuestreo en streaming conserva duración y frecuencia de la voz."""
    freq = 440.0
    seconds = 2.0
    t = np.arange(int(src_rate * seconds)) / src_rate
    signal = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    resampler = ear_module._StreamResampler(src_rate)
    block = max(1, round(FRAME_SIZE * src_rate / SAMPLE_RATE))
    frames = []
    for i in range(0, signal.size, block):
        frames.extend(resampler.process(signal[i:i + block]))

    assert frames and all(f.shape == (FRAME_SIZE,) for f in frames)
    out = np.concatenate(frames)
    assert abs(out.size - SAMPLE_RATE * seconds) <= FRAME_SIZE + 2
    body = out[200:-200]
    crossings = np.count_nonzero(np.diff(np.signbit(body)))
    estimated = crossings / 2 / (body.size / SAMPLE_RATE)
    assert abs(estimated - freq) < 5.0
    assert 0.3 < float(np.max(np.abs(body))) <= 0.51


# =============================================================================
# start()/stop() repetibles (main.py vuelve a arrancar el oído al salir de Live)
# =============================================================================


class _SilentModel:
    def __init__(self):
        self.prob = 0.0

    def __call__(self, tensor, sr):
        prob = self.prob

        class _P:
            def item(self_inner):
                return prob

        return _P()

    def reset_states(self):
        pass


def _ear_with_fake_vad(monkeypatch, **kwargs) -> ContinuousEar:
    from silero_vad import VADIterator

    ear = _bare_ear(monkeypatch, **kwargs)
    ear._vad_model = _SilentModel()
    ear._vad_model_bargein = _SilentModel()
    ear._vad_iterator = VADIterator(
        ear._vad_model, threshold=VAD_THRESHOLD, sampling_rate=SAMPLE_RATE,
        min_silence_duration_ms=VAD_MIN_SILENCE_MS, speech_pad_ms=VAD_SPEECH_PAD_MS,
    )
    ear._barge_in_required_frames = 2
    ear._pre_roll_capacity = 10
    ear._reset_segmentation_state()
    return ear


def test_ear_can_be_restarted_and_listen_again(monkeypatch, fast_mic_retries):
    """start() tras stop() debe crear hilos nuevos, vaciar las colas (nada de
    locuciones viejas) y permitir volver a iterar listen_utterances()."""
    fake_sd = _FakeSoundDevice()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    ear = _ear_with_fake_vad(monkeypatch)
    monkeypatch.setattr(ear, "transcribe_array", lambda audio: "viejo" if audio[0] < 0.2 else "nuevo")

    assert ear.is_running() is False
    ear.start()
    first_threads = (ear._audio_thread, ear._vad_thread)
    assert _wait_until(lambda: fake_sd.opened == 1)
    assert ear.is_running() is True
    ear.start()  # llamada repetida con el oído en marcha: no duplica hilos
    assert (ear._audio_thread, ear._vad_thread) == first_threads

    old_gen = ear.listen_utterances()
    ear.stop()
    assert ear.is_running() is False
    assert not any(t.is_alive() for t in first_threads)
    assert list(old_gen) == []  # el generador anterior termina con stop()

    ear._utterance_queue.put(np.full(FRAME_SIZE, 0.1, dtype=np.float32))  # locución vieja
    ear.start()
    try:
        assert all(t.is_alive() for t in (ear._audio_thread, ear._vad_thread))
        assert ear._audio_thread is not first_threads[0]
        assert not ear._stop_event.is_set()
        assert ear._utterance_queue.empty(), "start() debe descartar locuciones de la tanda anterior"
        assert _wait_until(lambda: fake_sd.opened == 2)

        ear._utterance_queue.put(np.full(FRAME_SIZE, 0.5, dtype=np.float32))
        got = []
        gen = ear.listen_utterances()
        reader = threading.Thread(target=lambda: got.append(next(gen)), daemon=True)
        reader.start()
        reader.join(timeout=2.0)
        assert got == ["nuevo"]
    finally:
        ear.stop()
    assert not ear._audio_thread.is_alive() and not ear._vad_thread.is_alive()


# =============================================================================
# AUD-14: barge-in corto seguido de silencio
# =============================================================================


def test_short_bargein_closes_utterance_after_silence(monkeypatch):
    """[AUD-14, regresión] Tras un «¡basta!» que dura lo justo para confirmar el
    barge-in, el VADIterator seguía sin «triggered» y nunca emitía 'end': la locución
    acumulaba silencio hasta 25 s. Ahora se cierra tras VAD_MIN_SILENCE_MS."""
    speaking = {"value": True}

    def _on_barge_in():
        speaking["value"] = False

    ear = _ear_with_fake_vad(monkeypatch, barge_in_callback=_on_barge_in, is_speaking_fn=lambda: speaking["value"])
    monkeypatch.setattr(ear_module, "VAD_BARGE_IN_GRACE_MS", 0)
    monkeypatch.setattr(ear_module, "VAD_BARGE_IN_MIN_RMS", 0.02)

    loud = np.full(FRAME_SIZE, 0.3, dtype=np.float32)
    ear._vad_model_bargein.prob = 0.95
    ear._process_frame(loud)
    ear._process_frame(loud)
    assert speaking["value"] is False and ear._is_speech_active

    # Solo silencio (para el VAD) a partir de aquí: la locución debe cerrarse sola.
    ear._vad_model.prob = 0.0
    quiet = np.full(FRAME_SIZE, 0.05, dtype=np.float32)
    frame_ms = FRAME_SIZE / SAMPLE_RATE * 1000
    limit = int(VAD_MIN_SILENCE_MS / frame_ms) + 15
    for _ in range(limit):
        ear._process_frame(quiet)
        if not ear._utterance_queue.empty():
            break

    assert not ear._utterance_queue.empty(), "la locución del barge-in no se cerró tras el silencio"
    assert ear._is_speech_active is False


def test_iterator_reset_when_zara_speaks_again_before_bargein_end(monkeypatch):
    """[AUD-14, ronda 2, regresión] Tras confirmar el barge-in el VADIterator queda con
    triggered=True. Si Zara vuelve a hablar antes del 'end', la locución se descarta pero
    el iterador seguía «dentro» de una locución: no emitía 'start' y la siguiente orden
    del Señor VK se perdía."""
    speaking = {"value": True}

    def _on_barge_in():
        speaking["value"] = False

    ear = _ear_with_fake_vad(monkeypatch, barge_in_callback=_on_barge_in, is_speaking_fn=lambda: speaking["value"])
    monkeypatch.setattr(ear_module, "VAD_BARGE_IN_GRACE_MS", 0)
    monkeypatch.setattr(ear_module, "VAD_BARGE_IN_MIN_RMS", 0.02)

    loud = np.full(FRAME_SIZE, 0.3, dtype=np.float32)
    ear._vad_model_bargein.prob = 0.95
    ear._process_frame(loud)
    ear._process_frame(loud)
    assert ear._is_speech_active and ear._vad_iterator.triggered

    # Zara vuelve a hablar (p. ej. «Sin confirmación...») antes del 'end'.
    speaking["value"] = True
    ear._vad_model_bargein.prob = 0.0
    ear._process_frame(np.zeros(FRAME_SIZE, dtype=np.float32))
    assert ear._is_speech_active is False
    assert ear._vad_iterator.triggered is False, "el VADIterator quedó dentro de una locución"

    # Zara calla y el Señor VK da una orden: debe empezar una locución nueva.
    speaking["value"] = False
    ear._vad_model.prob = 0.95
    ear._process_frame(loud)
    assert ear._is_speech_active, "no se detectó el inicio de la orden siguiente"


# =============================================================================
# Voz: AUD-6 (cola llena) y AUD-8 (frase a medias tras barge-in)
# =============================================================================


class _FakeStream:
    def __init__(self, duration: float):
        self._end_time = time.monotonic() + duration

    @property
    def active(self) -> bool:
        return time.monotonic() < self._end_time


def _install_fake_playback(monkeypatch, play_duration: float, played: list):
    state = {"stream": None}

    def fake_play(samples, samplerate):
        played.append(samples)
        state["stream"] = _FakeStream(play_duration)

    monkeypatch.setattr(voice_module.sd, "play", fake_play)
    monkeypatch.setattr(voice_module.sd, "get_stream", lambda: state["stream"])
    monkeypatch.setattr(voice_module.sd, "stop", lambda: state.update(stream=None))


def _record_synthesis(monkeypatch) -> list:
    texts: list = []

    def fake_synthesize(self, text):
        texts.append(text)
        return np.zeros(10, dtype=np.float32), 24000

    monkeypatch.setattr(VoiceSynthesizer, "_synthesize_audio", fake_synthesize)
    return texts


def test_long_reply_does_not_drop_sentences_when_audio_queue_is_full(monkeypatch):
    """[AUD-6, regresión] La síntesis va por delante de la reproducción: con la cola
    de audio llena (maxsize=5) se descartaban frases ya sintetizadas sin aviso."""
    played: list = []
    _install_fake_playback(monkeypatch, play_duration=0.03, played=played)
    _record_synthesis(monkeypatch)

    voice = VoiceSynthesizer()
    try:
        total = 12
        for i in range(total):
            voice.say(f"Frase número {i} de una respuesta larga del asistente.")
        assert _wait_until(lambda: len(played) >= total and voice._pending_count == 0, timeout=5.0)
        assert len(played) == total, f"se perdieron {total - len(played)} frases"
        assert voice.is_speaking() is False
    finally:
        voice.shutdown()


def test_bargein_does_not_flush_the_half_sentence(monkeypatch):
    """[AUD-8, regresión] Tras un barge-in, el cerebro cancela el turno, el bucle de
    tokens termina y antes chunker.flush() encolaba la media frase pendiente con la
    generación nueva: Zara seguía hablando después de mandarla callar."""
    played: list = []
    _install_fake_playback(monkeypatch, play_duration=0.01, played=played)
    texts = _record_synthesis(monkeypatch)
    voice = VoiceSynthesizer()

    def tokens():
        yield "Primera frase completa de la respuesta de Zara. "
        yield "Segunda frase que se queda a med"
        voice.stop()  # barge-in: el cerebro cancela y ya no llegan más tokens

    try:
        voice.feed_token_stream(tokens())
        time.sleep(0.3)
        assert not any("Segunda" in t for t in texts), f"sonó la frase a medias: {texts}"
        with voice._lock:
            assert voice._pending_count == 0
        assert voice.is_speaking() is False
    finally:
        voice.shutdown()


def test_bargein_cancels_stream_even_if_tokens_arrive_before_brain_cancel(monkeypatch):
    """[AUD-8, ronda 2, regresión] main llama a voice.stop() antes que a
    brain.cancel_current_turn(): los tokens que llegan entre ambos (fin de la frase
    cortada y frases nuevas enteras) sonaban porque la heurística solo saltaba hasta el
    primer punto. Con stop() (cancel_streams=True) el flujo queda cancelado del todo."""
    played: list = []
    _install_fake_playback(monkeypatch, play_duration=0.01, played=played)
    texts = _record_synthesis(monkeypatch)
    voice = VoiceSynthesizer()

    def tokens():
        yield "Primera frase completa de la respuesta de Zara. "
        yield "Segunda frase que se queda a med"
        voice.stop()  # barge-in; el cerebro aún no ha cancelado el turno
        yield "ias y que no debe sonar nunca. "
        yield "Tercera frase entera que tampoco debe sonar tras el barge-in. "
        yield "Y un final sin punto"

    try:
        voice.feed_token_stream(tokens())
        time.sleep(0.3)
        assert not any(w in t for t in texts for w in ("Segunda", "no debe sonar", "Tercera", "final")), texts
        with voice._lock:
            assert voice._pending_count == 0
            assert voice._feeds == set()  # el flujo terminado ya no se sigue
    finally:
        voice.shutdown()


def test_stop_keeping_streams_speaks_rest_of_turn_after_confirmation(monkeypatch):
    """[AUD-8, ronda 2, regresión] El Señor VK contesta encima del aviso de confirmación:
    solo se corta el audio (stop(cancel_streams=False)) y el turno sigue. Antes la
    heuristica se comía la primera frase nueva si lo pendiente no acababa en punto."""
    played: list = []
    _install_fake_playback(monkeypatch, play_duration=0.01, played=played)
    texts = _record_synthesis(monkeypatch)
    voice = VoiceSynthesizer()

    def tokens():
        yield "Voy a borrar el archivo que me ha indicado, Señor VK"
        voice.say("Señor VK, necesito su confirmación para borrar el archivo.")
        voice.stop(cancel_streams=False)  # la respuesta se dijo encima del aviso
        yield ". Hecho, el archivo se ha borrado correctamente. "
        yield "Quedan tres archivos más en la carpeta de descargas."

    try:
        voice.feed_token_stream(tokens())
        assert _wait_until(lambda: any("Quedan tres" in t for t in texts))
        assert any("Hecho" in t for t in texts), texts
        assert any("Voy a borrar" in t for t in texts), texts  # no se había dicho aún
    finally:
        voice.shutdown()


def test_stale_generation_sentences_are_never_synthesized(monkeypatch):
    """[AUD-8] Las frases llevan su generación: una frase de una tanda cancelada no se
    encola ni se sintetiza, y no toca el contador de la generación actual."""
    texts = _record_synthesis(monkeypatch)
    _install_fake_playback(monkeypatch, play_duration=0.01, played=[])
    voice = VoiceSynthesizer()
    try:
        old_gen = voice._current_generation()
        voice.stop()
        assert voice._enqueue_sentence("Frase de la tanda cancelada.", old_gen) is False
        with voice._lock:
            assert voice._pending_count == 0
            voice._pending_count = 1  # trabajo legítimo del turno actual
            voice._tts_queue.put((old_gen, "Otra frase vieja que llegó tarde."))
        time.sleep(0.3)
        assert texts == []
        with voice._lock:
            assert voice._pending_count == 1
    finally:
        voice.shutdown()
