"""Pruebas de las correcciones de la auditoría v1 en core/live.py (grupo «live»).

Cubren AUD-1 (recepción por turnos), AUD-5 (reconexión y colas acotadas), AUD-3/SEG-14
(herramientas sin bloquear la recepción), SEG-3 (solo herramientas de consulta en Live),
AUD-9 (parada limpia), AUD-11 (OutputStream propio), AUD-12 (eco) y MUL-2 (sin PortAudio).
No abren micrófono ni altavoz reales ni conectan con Google: todo se simula.
"""

import asyncio
import logging
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from google.genai import types

import core.live as live
from core.live import GeminiLiveSession

REPO_ROOT = Path(__file__).resolve().parents[1]


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades de simulación
# ─────────────────────────────────────────────────────────────────────────────

def _audio_msg(data: bytes, turn_complete: bool = False) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                role="model",
                parts=[types.Part(inline_data=types.Blob(data=data, mime_type="audio/pcm;rate=24000"))],
            ),
            turn_complete=turn_complete or None,
        )
    )


def _text_in_msg(text: str) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(input_transcription=types.Transcription(text=text))
    )


def _tool_msg(*calls) -> types.LiveServerMessage:
    return types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=list(calls)))


class _TurnSession:
    """Imita AsyncSession.receive() del SDK: cada llamada entrega UN turno y termina."""

    def __init__(self, turns, after=None):
        self._turns = list(turns)
        self._after = after  # qué hacer cuando no quedan turnos
        self.receive_calls = 0
        self.tool_responses = []
        self.sent_audio = []

    async def receive(self):
        self.receive_calls += 1
        if not self._turns:
            if self._after == "hang":
                await asyncio.Event().wait()
            raise ConnectionError("WebSocket cerrado (1006)")
        for msg in self._turns.pop(0):
            yield msg

    async def send_tool_response(self, function_responses):
        self.tool_responses.extend(function_responses)

    async def send_realtime_input(self, audio):
        self.sent_audio.append(audio.data)


class _FakeOutputStream:
    instances = []

    def __init__(self, samplerate=None, channels=None, dtype=None, **_kw):
        self.samplerate, self.channels, self.dtype = samplerate, channels, dtype
        self.writes = []
        self.started = 0
        self.aborted = 0
        self.closed = False
        self.latency = 0.0
        self.on_write = None
        _FakeOutputStream.instances.append(self)

    def start(self):
        self.started += 1

    def write(self, data):
        self.writes.append(np.array(data, copy=True))
        if self.on_write:
            hook, self.on_write = self.on_write, None
            hook()

    def abort(self):
        self.aborted += 1

    def close(self):
        self.closed = True


class _FakeSd:
    """Sustituto de sounddevice: falla si se usa la API global sd.play/sd.stop/sd.wait."""

    def __init__(self, input_blocks=None):
        self.global_calls = []
        self.input_blocks = input_blocks or []
        self.input_streams = []
        _FakeOutputStream.instances = []
        self.OutputStream = _FakeOutputStream
        fake = self

        class _FakeInputStream:
            def __init__(self, callback=None, **kw):
                self.callback = callback
                self.kw = kw
                self.closed = False
                fake.input_streams.append(self)

            def __enter__(self):
                for block in fake.input_blocks:
                    self.callback(block.reshape(-1, 1), len(block), None, None)
                return self

            def __exit__(self, *exc):
                self.closed = True
                return False

        self.InputStream = _FakeInputStream

    def play(self, *a, **k):
        self.global_calls.append("play")

    def stop(self, *a, **k):
        self.global_calls.append("stop")

    def wait(self, *a, **k):
        self.global_calls.append("wait")


def _block(rms: float, n: int = live.LIVE_INPUT_CHUNK_SAMPLES, marker: float = 0.0) -> np.ndarray:
    """Bloque float32 de RMS dado (onda cuadrada); `marker` distingue unos bloques de otros."""
    sign = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    block = (sign * rms).astype(np.float32)
    block[0] = marker if marker else block[0]
    return block


# ─────────────────────────────────────────────────────────────────────────────
# AUD-1: la recepción sigue después del primer turno y turn_complete se detecta
# ─────────────────────────────────────────────────────────────────────────────

def test_receive_loop_atiende_varios_turnos_y_detecta_turn_complete():
    heard = []
    s = GeminiLiveSession(api_key="k", on_transcript_in=heard.append)
    s._running = True
    fake = _TurnSession([
        [_text_in_msg("hola"), _audio_msg(b"\x01\x00" * 4, turn_complete=True)],
        [_text_in_msg("segunda pregunta"), _audio_msg(b"\x02\x00" * 4, turn_complete=True)],
    ])

    # Al acabarse los turnos el WebSocket «cae»: el error debe llegar a _session_loop.
    with pytest.raises(ConnectionError):
        asyncio.run(asyncio.wait_for(s._receive_loop(fake, types), timeout=5))

    assert fake.receive_calls == 3
    assert heard == ["hola", "segunda pregunta"]
    items = []
    while not s._playback_queue.empty():
        items.append(s._playback_queue.get_nowait())
    assert items == [b"\x01\x00" * 4, live._TURN_END, b"\x02\x00" * 4, live._TURN_END]


def test_is_turn_complete_lee_server_content():
    assert live._is_turn_complete(types.LiveServerContent(turn_complete=True))
    assert live._is_turn_complete(types.LiveServerContent(interaction_status=types.InteractionStatus.IDLE))
    assert not live._is_turn_complete(types.LiveServerContent())
    assert not live._is_turn_complete(
        types.LiveServerContent(interaction_status=types.InteractionStatus.IN_PROGRESS)
    )


# ─────────────────────────────────────────────────────────────────────────────
# AUD-5: reconexión real, espera exponencial y colas acotadas
# ─────────────────────────────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, connect_impl):
        live_ns = type("LiveNS", (), {"connect": staticmethod(connect_impl)})()
        self.aio = type("AioNS", (), {"live": live_ns})()


def _patch_session_loop(monkeypatch, s, connect_impl, delays):
    import google.genai as genai

    monkeypatch.setattr(genai, "Client", lambda api_key=None: _FakeClient(connect_impl))
    monkeypatch.setattr(live, "get_system_prompt", lambda: "prompt de prueba")
    monkeypatch.setattr(s, "_resolve_live_model", lambda: "modelo-live-test")

    async def _record_delay(delay):
        delays.append(delay)

    monkeypatch.setattr(s, "_wait_before_retry", _record_delay)


def test_session_loop_reconecta_cuando_cae_la_recepcion(monkeypatch):
    states = []
    s = GeminiLiveSession(api_key="k", on_state_change=states.append)
    s._running = True
    delays = []
    connects = []

    async def _mic_vivo():
        await asyncio.Event().wait()  # el micrófono nunca termina por sí solo

    monkeypatch.setattr(s, "_mic_capture_loop", _mic_vivo)

    configs = []

    class _Conn:
        def __init__(self, model, config):
            connects.append(model)
            configs.append(config)

        async def __aenter__(self):
            if len(connects) >= 3:
                s._running = False
                raise ConnectionError("fin de la prueba")
            return _TurnSession([])  # receive() falla en cuanto se lee: WebSocket caído

        async def __aexit__(self, *exc):
            return False

    _patch_session_loop(monkeypatch, s, lambda model, config: _Conn(model, config), delays)
    asyncio.run(asyncio.wait_for(s._session_loop(), timeout=5))

    assert connects == ["modelo-live-test"] * 3
    # el modelo sabe que en Live solo tiene herramientas de consulta (SEG-3)
    assert "MODO LIVE" in str(configs[0].system_instruction)
    assert delays == [3.0, 6.0]
    assert states.count("reconnecting") == 2
    assert states.count("connected") == 2


def test_session_loop_espera_exponencial_con_tope(monkeypatch):
    s = GeminiLiveSession(api_key="k")
    s._running = True
    delays = []
    attempts = []

    class _Conn:
        async def __aenter__(self):
            attempts.append(1)
            if len(attempts) >= 8:
                s._running = False
            raise PermissionError("429 RESOURCE_EXHAUSTED")

        async def __aexit__(self, *exc):
            return False

    _patch_session_loop(monkeypatch, s, lambda model, config: _Conn(), delays)
    asyncio.run(asyncio.wait_for(s._session_loop(), timeout=5))

    assert delays == [3.0, 6.0, 12.0, 24.0, 48.0, 60.0, 60.0]


def test_send_audio_loop_propaga_el_fallo_de_envio():
    s = GeminiLiveSession(api_key="k")
    s._running = True

    class _Closed:
        async def send_realtime_input(self, audio):
            raise ConnectionError("WebSocket cerrado")

    async def _run():
        s._mic_queue = asyncio.Queue(maxsize=live.LIVE_MIC_QUEUE_MAX)
        s._mic_queue.put_nowait(b"\x00\x00")
        await s._send_audio_loop(_Closed())

    with pytest.raises(ConnectionError):
        asyncio.run(asyncio.wait_for(_run(), timeout=5))


def _run_mic_loop(s, fake_sd, monkeypatch, expected):
    monkeypatch.setattr(live, "sd", fake_sd)

    async def _run():
        s._mic_queue = asyncio.Queue(maxsize=live.LIVE_MIC_QUEUE_MAX)
        task = asyncio.create_task(s._mic_capture_loop())
        deadline = time.monotonic() + 3
        while s._mic_queue.qsize() < expected and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        s._running = False
        await asyncio.wait_for(task, timeout=3)
        out = []
        while not s._mic_queue.empty():
            out.append(s._mic_queue.get_nowait())
        return out

    return asyncio.run(_run())


def test_cola_del_microfono_acotada_descarta_lo_mas_antiguo(monkeypatch):
    s = GeminiLiveSession(api_key="k")
    s._running = True
    n = live.LIVE_MIC_QUEUE_MAX + 10
    blocks = [_block(0.3, marker=(i + 1) / 1000.0) for i in range(n)]
    fake_sd = _FakeSd(input_blocks=blocks)

    chunks = _run_mic_loop(s, fake_sd, monkeypatch, live.LIVE_MIC_QUEUE_MAX)

    assert len(chunks) == live.LIVE_MIC_QUEUE_MAX
    first = np.frombuffer(chunks[0], dtype=np.int16)[0]
    last = np.frombuffer(chunks[-1], dtype=np.int16)[0]
    assert first == int((n - live.LIVE_MIC_QUEUE_MAX + 1) / 1000.0 * 32767)  # los 10 primeros se perdieron
    assert last == int(n / 1000.0 * 32767)
    assert fake_sd.input_streams[0].closed  # el `with` cerró el micrófono


# ─────────────────────────────────────────────────────────────────────────────
# AUD-12: eco del altavoz mientras habla el modelo
# ─────────────────────────────────────────────────────────────────────────────

def test_microfono_envia_silencio_en_vez_del_eco_mientras_habla_el_modelo(monkeypatch):
    monkeypatch.setattr(live, "LIVE_BARGE_IN_RMS", 0.05)
    s = GeminiLiveSession(api_key="k")
    s._running = True
    s._is_model_speaking = True
    eco, voz = _block(0.01), _block(0.3)
    fake_sd = _FakeSd(input_blocks=[eco, voz])

    chunks = _run_mic_loop(s, fake_sd, monkeypatch, 2)

    assert len(chunks) == 2
    assert chunks[0] == bytes(len(chunks[0]))           # eco -> silencio (el flujo no se corta)
    assert np.frombuffer(chunks[1], dtype=np.int16).any()  # la voz fuerte pasa (barge-in)


def test_gate_de_eco_respeta_configuracion_y_cola_de_eco(monkeypatch):
    s = GeminiLiveSession(api_key="k")
    chunk = np.full(160, 300, dtype=np.int16).tobytes()

    monkeypatch.setattr(live, "LIVE_BARGE_IN_RMS", 0.05)
    assert s._gate_echo(chunk, 0.01) == chunk           # el modelo no habla: pasa tal cual
    s._echo_guard_until = time.monotonic() + 5          # justo después de terminar de hablar
    assert s._gate_echo(chunk, 0.01) == bytes(len(chunk))
    s._flush_playback(emit=False)                       # barge-in: no tapar la voz del usuario
    assert s._gate_echo(chunk, 0.01) == chunk

    s._is_model_speaking = True
    monkeypatch.setattr(live, "LIVE_BARGE_IN_RMS", 0.0)  # desactivado (auriculares)
    assert s._gate_echo(chunk, 0.01) == chunk


def test_umbral_de_eco_desde_el_entorno(monkeypatch):
    monkeypatch.setenv("ZARA_LIVE_BARGE_IN_RMS", "0,08  # comentario")
    assert live._env_float("ZARA_LIVE_BARGE_IN_RMS", 0.05) == pytest.approx(0.08)
    monkeypatch.setenv("ZARA_LIVE_BARGE_IN_RMS", "mucho")
    assert live._env_float("ZARA_LIVE_BARGE_IN_RMS", 0.05) == 0.05
    monkeypatch.setenv("ZARA_LIVE_BARGE_IN_RMS", "-1")
    assert live._env_float("ZARA_LIVE_BARGE_IN_RMS", 0.05) == 0.05
    monkeypatch.delenv("ZARA_LIVE_BARGE_IN_RMS")
    assert live._env_float("ZARA_LIVE_BARGE_IN_RMS", 0.05) == 0.05


# ─────────────────────────────────────────────────────────────────────────────
# SEG-3: en Live (sin palabra de activación) solo herramientas de consulta
# ─────────────────────────────────────────────────────────────────────────────

def test_live_solo_declara_herramientas_de_consulta():
    tools = GeminiLiveSession(api_key="k")._build_live_tools(types)
    decls = {d.name: d for d in tools[0].function_declarations}
    assert set(decls) == {"web_search", "inspect_screen", "os_control"}
    enum = set(decls["os_control"].parameters.properties["action"].enum)
    assert enum and enum <= live.LIVE_SAFE_OS_ACTIONS
    assert not enum & {"open_app", "close_app", "write_clipboard", "read_clipboard"}


def test_live_bloquea_herramientas_que_cambian_el_sistema(monkeypatch):
    calls = []
    monkeypatch.setattr(live, "execute_tool", lambda name, args: calls.append((name, args)) or {"success": True})
    s = GeminiLiveSession(api_key="k")
    s._running = True
    fake = _TurnSession([])
    tool_call = types.LiveServerToolCall(function_calls=[
        types.FunctionCall(id="1", name="run_shell", args={"command": "find ~/Documentos -delete"}),
        types.FunctionCall(id="2", name="os_control", args={"action": " System_Stats "}),
        types.FunctionCall(id="3", name="system_stats", args={"action": "open_app", "target": "x"}),
        types.FunctionCall(id="4", name="file_manager", args={"action": "write", "path": "~/a", "content": "x"}),
        types.FunctionCall(id="5", name="os_control", args={"action": "write_clipboard", "value": "x"}),
        types.FunctionCall(id="6", name="mute", args={}),
        types.FunctionCall(id="7", name="invoke_claude_code", args={"project_path": "~", "instruction": "x"}),
        types.FunctionCall(id="8", name="web_search", args={"query": "tiempo"}),
    ])

    asyncio.run(s._handle_tool_calls(fake, tool_call, types))

    assert calls == [
        ("os_control", {"action": "system_stats"}),
        ("os_control", {"action": "mute"}),
        ("web_search", {"query": "tiempo"}),
    ]
    by_id = {fr.id: fr.response["result"] for fr in fake.tool_responses}
    assert set(by_id) == {str(i) for i in range(1, 9)}
    for blocked in ("1", "3", "4", "5", "7"):
        assert by_id[blocked]["blocked"] is True
        assert by_id[blocked]["success"] is False
    for allowed in ("2", "6", "8"):
        assert by_id[allowed] == {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# AUD-3 / SEG-14: la recepción no se bloquea mientras corre una herramienta
# ─────────────────────────────────────────────────────────────────────────────

def test_transcripcion_llega_mientras_la_herramienta_espera(monkeypatch):
    heard = threading.Event()

    def _slow_tool(name, args):
        # Imita _ask_confirmation: espera a que el «sí» llegue por la transcripción.
        return {"success": True, "heard": heard.wait(timeout=1.5)}

    monkeypatch.setattr(live, "execute_tool", _slow_tool)
    s = GeminiLiveSession(api_key="k", on_transcript_in=lambda text: heard.set())
    s._running = True
    fake = _TurnSession(
        [[_tool_msg(types.FunctionCall(id="c1", name="web_search", args={"query": "x"})), _text_in_msg("sí")]],
        after="hang",
    )

    async def _run():
        recv = asyncio.create_task(s._receive_loop(fake, types))
        deadline = time.monotonic() + 4
        while not fake.tool_responses and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        recv.cancel()
        await asyncio.gather(recv, return_exceptions=True)

    asyncio.run(_run())

    assert len(fake.tool_responses) == 1
    assert fake.tool_responses[0].response["result"]["heard"] is True


# ─────────────────────────────────────────────────────────────────────────────
# AUD-9: parada limpia y sesión nueva sin restos
# ─────────────────────────────────────────────────────────────────────────────

def test_start_descarta_audio_y_estado_de_la_sesion_anterior(monkeypatch):
    s = GeminiLiveSession(api_key="k")
    s._playback_queue.put_nowait(b"audio-viejo")
    s._playback_queue.put_nowait(None)
    s._is_model_speaking = True
    monkeypatch.setattr(s, "is_available", lambda: True)
    monkeypatch.setattr(s, "_run_event_loop", lambda *a: None)
    monkeypatch.setattr(s, "_playback_worker", lambda *a: None)

    s.start()
    try:
        assert s._playback_queue.empty()
        assert s.is_model_speaking() is False
    finally:
        s.stop()


def test_stop_cancela_la_sesion_y_ejecuta_la_limpieza(monkeypatch, caplog):
    monkeypatch.setattr(live, "sd", _FakeSd())
    states = []
    s = GeminiLiveSession(api_key="k", on_state_change=states.append)
    monkeypatch.setattr(s, "is_available", lambda: True)
    started, cleaned = threading.Event(), threading.Event()

    async def _fake_session_loop():
        started.set()
        try:
            await asyncio.Event().wait()  # como `async with live.connect(...)` esperando
        finally:
            cleaned.set()                 # aquí se cerrarían el WebSocket y el micrófono

    monkeypatch.setattr(s, "_session_loop", _fake_session_loop)
    caplog.set_level(logging.DEBUG, logger="zara.live")

    s.start()
    assert started.wait(timeout=3)
    s.stop()

    assert cleaned.is_set()
    assert not s._thread.is_alive()
    assert not s._playback_thread.is_alive()
    assert s.is_running() is False
    assert states[-1] == "disconnected"
    assert "Error fatal" not in caplog.text


def test_hilo_de_una_sesion_vieja_no_apaga_la_nueva(monkeypatch):
    states = []
    s = GeminiLiveSession(api_key="k", on_state_change=states.append)

    async def _ends():
        return None

    monkeypatch.setattr(s, "_session_loop", _ends)
    s._generation = 2
    s._running = True  # la sesión 2 ya está en marcha
    s._run_event_loop(generation=1)  # termina el hilo de la sesión 1

    assert s._running is True
    assert "disconnected" not in states


# ─────────────────────────────────────────────────────────────────────────────
# AUD-11: OutputStream propio, sin la API global de sounddevice
# ─────────────────────────────────────────────────────────────────────────────

def test_reproduccion_usa_un_output_stream_propio(monkeypatch):
    fake_sd = _FakeSd()
    monkeypatch.setattr(live, "sd", fake_sd)
    states = []
    s = GeminiLiveSession(api_key="k", on_state_change=states.append)
    s._running = True
    s._is_model_speaking = True
    a = np.arange(3000, dtype=np.int16)
    b = np.arange(3000, 4000, dtype=np.int16)
    q = s._playback_queue
    for item in (a.tobytes(), b.tobytes(), live._TURN_END, None):
        q.put_nowait(item)

    s._playback_worker(q)

    assert fake_sd.global_calls == []
    assert len(_FakeOutputStream.instances) == 1
    out = _FakeOutputStream.instances[0]
    assert (out.samplerate, out.channels, out.dtype) == (live.LIVE_OUTPUT_SAMPLE_RATE, 1, "int16")
    assert np.array_equal(np.concatenate(out.writes), np.concatenate([a, b]))
    assert out.closed
    assert s.is_model_speaking() is False
    assert "listening" in states


def test_barge_in_corta_el_bufer_propio_sin_sd_stop(monkeypatch):
    fake_sd = _FakeSd()
    monkeypatch.setattr(live, "sd", fake_sd)
    s = GeminiLiveSession(api_key="k")
    s._running = True
    q = s._playback_queue
    largo = np.ones(live._PLAYBACK_SLICE_SAMPLES * 4, dtype=np.int16)
    nuevo = np.full(100, 7, dtype=np.int16)
    q.put_nowait(largo.tobytes())

    def _barge_in():
        s.interrupt()                  # el usuario habla encima a mitad del chunk
        q.put_nowait(nuevo.tobytes())  # respuesta nueva tras la interrupción
        q.put_nowait(None)

    # El gancho se instala al abrirse el stream y salta en su primer write
    orig_init = _FakeOutputStream.__init__

    def _init(self, *a, **k):
        orig_init(self, *a, **k)
        self.on_write = _barge_in

    monkeypatch.setattr(_FakeOutputStream, "__init__", _init)
    s._playback_worker(q)

    out = _FakeOutputStream.instances[0]
    assert fake_sd.global_calls == []           # sin sd.stop() global (cortaría la voz clásica)
    assert out.aborted >= 1                     # se descartó el audio pendiente del búfer
    assert len(out.writes) == 2                 # solo el primer trozo del chunk largo...
    assert np.array_equal(out.writes[-1], nuevo)  # ...y luego la respuesta nueva


# ─────────────────────────────────────────────────────────────────────────────
# MUL-2: sin libportaudio2 importar core.live no tumba la app
# ─────────────────────────────────────────────────────────────────────────────

def test_importar_live_sin_portaudio_no_falla():
    code = textwrap.dedent(
        """
        import importlib.abc, sys

        class _SinPortAudio(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name == "sounddevice":
                    raise OSError("PortAudio library not found")
                return None

        sys.meta_path.insert(0, _SinPortAudio())
        import core.live as live
        assert live.sd is None
        s = live.GeminiLiveSession(api_key="clave")
        assert s.is_available() is False
        s.start()
        assert s.is_running() is False
        print("OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
