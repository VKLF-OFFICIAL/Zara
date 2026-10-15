from __future__ import annotations
import asyncio
import shutil
import subprocess
from pathlib import Path


class EspeakSynth:
    """Respaldo offline con espeak-ng. Robótico pero siempre disponible."""

    name = "espeak"

    def __init__(self, voice: str = "es", speed: int = 160, pitch: int = 70):
        self.voice = voice
        self.speed = speed
        self.pitch = pitch

    def available(self) -> bool:
        return shutil.which("espeak-ng") is not None

    def synthesize(self, text: str, out_path: Path) -> Path:
        out = Path(out_path).with_suffix(".wav")
        subprocess.run(
            ["espeak-ng", "-v", self.voice, "-s", str(self.speed),
             "-p", str(self.pitch), "-w", str(out), text],
            check=True, capture_output=True,
        )
        return out


def _patch_aiohttp_resolver():
    try:
        import aiohttp.resolver
        import aiohttp.connector
        aiohttp.connector.DefaultResolver = aiohttp.resolver.ThreadedResolver
        aiohttp.AsyncResolver = aiohttp.resolver.ThreadedResolver
    except Exception:
        pass


class EdgeSynth:
    """Voz femenina suave, dulce y natural vía Microsoft Edge TTS (requiere red)."""

    name = "edge"

    def __init__(self, voice: str = "es-MX-DaliaNeural",
                 rate: str = "+0%", pitch: str = "+0Hz"):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch

    def available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except Exception:
            return False

    def synthesize(self, text: str, out_path: Path) -> Path:
        _patch_aiohttp_resolver()
        import edge_tts
        out = Path(out_path).with_suffix(".mp3")

        async def _run():
            comm = edge_tts.Communicate(text, self.voice, rate=self.rate,
                                        pitch=self.pitch)
            await asyncio.wait_for(comm.save(str(out)), timeout=15.0)

        try:
            # Si ya hay un event loop en ejecución en este hilo, usamos un ThreadPool
            # para evitar RuntimeError: asyncio.run() cannot be called from a running event loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(lambda: asyncio.run(_run())).result(timeout=16.0)
            else:
                asyncio.run(_run())
        except Exception as exc:
            raise RuntimeError(f"edge-tts falló al sintetizar: {exc}") from exc

        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError("edge-tts no produjo audio")
        return out


class PiperSynth:
    """Voz local instantánea y offline con Piper (modelo .onnx)."""

    name = "piper"
    _cache = {}

    def __init__(self, model_path: str):
        self.model_path = model_path

    def available(self) -> bool:
        try:
            import piper  # noqa: F401
            return Path(self.model_path).exists()
        except Exception:
            return False

    def _voice(self):
        v = PiperSynth._cache.get(self.model_path)
        if v is None:
            from piper import PiperVoice
            v = PiperVoice.load(self.model_path)
            PiperSynth._cache[self.model_path] = v
        return v

    def synthesize(self, text: str, out_path: Path) -> Path:
        import wave
        out = Path(out_path).with_suffix(".wav")
        with wave.open(str(out), "wb") as wf:
            self._voice().synthesize_wav(text, wf)
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError("piper no produjo audio")
        return out


def to_wav(path: Path) -> Path:
    """Convierte (mp3, …) a WAV 48 kHz estéreo. Si falla o falta ffmpeg, devuelve la ruta original."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        return path
    out = path.with_suffix(".wav")
    if shutil.which("ffmpeg"):
        try:
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                            "-ar", "48000", "-ac", "2", str(out)], check=True, timeout=10,
                           capture_output=True)
            if out.exists() and out.stat().st_size > 0:
                return out
        except Exception:
            pass
    return path


def player_cmd(wav: str | Path, sink: str | None = None) -> list[str]:
    """Orden para reproducir un archivo de audio, opcionalmente en una salida concreta."""
    import sys
    wav = str(wav)
    if shutil.which("pw-play"):
        return ["pw-play"] + (["--target", sink] if sink else []) + [wav]
    if shutil.which("paplay"):
        return ["paplay"] + ([f"--device={sink}"] if sink else []) + [wav]
    if shutil.which("aplay") and wav.lower().endswith(".wav"):
        return ["aplay", "-q"] + (["-D", sink] if sink else []) + [wav]
    if shutil.which("mpv"):
        return ["mpv", "--no-video", "--really-quiet"] + ([f"--audio-device={sink}"] if sink else []) + [wav]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", wav]
    if sys.platform == "win32":
        return ["powershell", "-c", f'(New-Object Media.SoundPlayer "{wav}").PlaySync()']
    return ["pw-play", wav]


def play(path: Path, sink: str | None = None) -> None:
    """Reproduce un archivo de audio (wav o mp3) y espera a que termine."""
    target = to_wav(path)
    cmd = player_cmd(target, sink)
    try:
        subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL, timeout=60)
    except Exception:
        pass

