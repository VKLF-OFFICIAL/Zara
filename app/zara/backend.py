from __future__ import annotations
import logging
import re
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

from .paths import ZaraPaths
from .config import load_toml
from .log import setup_logging
from .bootstrap import build_core
from .intents import parse_intent
from . import actions
from .audio.mic import Microphone
from .audio.vad import EnergyVad, SileroVad
from .audio.stt import Transcriber
from .audio.wake import WakeGate, has_wake
from .audio.echo import is_echo
from .audio.identity import SpeakerVerifier
from .voice.text import speech_text
from .voice.synth import to_wav, player_cmd
from .hands import PROTOCOL, extract_actions, strip_actions
from .guardian import Guardian, RuleSet, Action, Level
from .audit import AuditLog
from .errors import BrainError

log = logging.getLogger("zara.backend")

_HELP = (
    "Señor VK, esto es lo que puedo hacer por voz:\n"
    "• Conversar y trabajar: pregúnteme, o pídame tareas (programar, editar archivos, "
    "automatizar) — uso Claude con control del equipo.\n"
    "• Sistema: «estado del equipo», «qué hora es», «qué tiempo hace», «haz una captura».\n"
    "• Sonido: «sube/baja el volumen», «pon el volumen al 40», «silencia».\n"
    "• Música: «reproduce música en Spotify», «pausa», «siguiente canción».\n"
    "• Apps y archivos: «abre firefox», «cierra spotify», «abre la carpeta descargas».\n"
    "• Buscar: «busca X en Google/YouTube».\n"
    "• Recordatorios: «recuérdame en 10 minutos…», «pon una alarma a las 8:30».\n"
    "• Sesión: «bloquea el equipo», «suspende», «apaga el equipo» (con confirmación).\n"
    "• Cambiar cerebro: «usa claude», «usa qwen».\n"
    "• Comandos: «ejecuta …» (el Guardián decide si pide confirmación)."
)

_CONFIG_DEFAULTS = {
    "voz": "es-MX-DaliaNeural",
    "modelo_stt": "small",
    "stt_beam_size": 2,
    "vad": "silero",              # "silero" (neuronal, acento andaluz) | "energia"
    "vad_umbral": 0.5,
    "vad_silencio_ms": 600,       # silencio para cerrar la frase (no corta pausas)
    "vad_margen_ms": 350,         # relleno para no amputar consonantes iniciales/finales
    "vad_barge_in_ms": 250,       # voz continua para interrumpir a Zara
    "pedir_confirmacion": False,  # False = JARVIS autónomo: obedece y ejecuta directamente
    "umbral_voz": 0.72,
    "cerebro_por_defecto": "",
    "dispositivo_microfono": None,
    "verificar_voz": True,
    "modo_conversacion": False,   # si True, no exige «Zara» tras cada orden
    "ventana_escucha": 8.0,       # segundos tras decir «Zara» a secas
    "motor_wake": "ambos",        # "texto" | "neural" | "ambos" (admite «Zara» y «hey Jarvis»)
    "modelo_wake": "hey_jarvis_v0.1",
    "umbral_wake_neural": 0.5,    # 0..1; más alto = menos falsas activaciones
    "salida_audio": "",           # "" = salida por defecto del sistema; o nombre del sink
    "cerebro_claude_modo": "agente",
    "claude_directorio": "",
    "saludar_al_arrancar": True,
    "tono_activacion": True,
    "hablar": True,
    "manos": True,                # cualquier cerebro puede actuar en el sistema (vía Guardián)
    "manos_max_pasos": 10,        # vueltas máximas del bucle ReAct (autonomía estilo Claude Code)
}


class ConfirmBridge:
    """Puente de confirmación entre el hilo de trabajo y el panel (UI)."""

    def __init__(self, enabled: bool = False):
        self._ev = threading.Event()
        self._result = False
        self.enabled = enabled
        self.request_cb = None  # callable(mensaje:str) -> None (lo pone la app)

    def ask(self, message: str, timeout: float = 30.0) -> bool:
        if not self.enabled or self.request_cb is None:
            return True
        self._ev.clear()
        self.request_cb(message)
        got = self._ev.wait(timeout)
        return self._result if got else False

    def resolve(self, ok: bool):
        self._result = ok
        self._ev.set()


class Backend:
    def __init__(self, paths: ZaraPaths | None = None):
        self.paths = paths or ZaraPaths.resolve()
        setup_logging(self.paths.registros)
        self.cfg = load_toml(self.paths.config / "zara.toml", _CONFIG_DEFAULTS)
        self._pedir_confirmacion = bool(self.cfg.get("pedir_confirmacion", False))
        self.confirm = ConfirmBridge(enabled=self._pedir_confirmacion)
        self._ready = False
        self.core = None
        self.mic = None
        self._skills = None
        self._stt = None
        self._verifier = None
        self._verify_pool = None
        self._verified_once = False   # tras la 1ª auth OK, se relaja el umbral
        self._oww = None
        self._guardian = None
        self._gate = WakeGate(window=float(self.cfg["ventana_escucha"]),
                              conversation=bool(self.cfg["modo_conversacion"]))
        self._brain_lock = threading.Lock()
        self._speak_lock = threading.Lock()
        self._player = None
        self._stream_voice = False     # False = sin construir; None = no usable; obj = activo
        self._recent_spoken = deque(maxlen=5)
        self.speak_enabled = bool(self.cfg.get("hablar", True))
        self._manos_enabled = bool(self.cfg.get("manos", True))
        self._hands_max_steps = max(1, int(self.cfg.get("manos_max_pasos", 10)))
        from .tasks import BackgroundTasks
        self._tasks = BackgroundTasks()
        self.on_reminder = None          # la UI lo fija para mostrarlo en el panel
        self.on_task_done = None         # la UI lo fija: avisa al terminar una tarea
        from .reminders import ReminderScheduler
        self._reminders = ReminderScheduler(
            self.paths.tareas / "recordatorios.json", self._fire_reminder)
        self._reminders.start()
        self._chime_path = self._ensure_chime()
        self._clean_old_audio()

    # --- JARVIS: tono de activación y recordatorios -------------------------
    def _ensure_chime(self):
        """Genera un tono breve de acuse (dos notas ascendentes) la primera vez."""
        path = self.paths.config / "chime.wav"
        if path.exists():
            return path
        try:
            import wave
            import numpy as np
            sr = 44100
            def tone(freq, dur, fade=0.01):
                t = np.linspace(0, dur, int(sr * dur), False)
                w = 0.35 * np.sin(2 * np.pi * freq * t)
                n = int(sr * fade)
                w[:n] *= np.linspace(0, 1, n)
                w[-n:] *= np.linspace(1, 0, n)
                return w
            data = np.concatenate([tone(660, 0.07), tone(990, 0.1)])
            pcm = (data * 32767).astype("<i2")
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
                wf.writeframes(pcm.tobytes())
        except Exception:
            log.exception("no pude generar el tono de activación")
            return None
        return path

    def chime(self):
        """Reproduce el tono de activación sin bloquear."""
        if not self.cfg.get("tono_activacion", True) or not self._chime_path:
            return
        from .voice.synth import player_cmd
        try:
            subprocess.Popen(player_cmd(self._chime_path, self._sink()),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _fire_reminder(self, msg: str):
        texto = "Señor VK, le recuerdo" + (f": {msg}." if msg else ".")
        log.info("recordatorio: %s", msg)
        try:
            import shutil
            if shutil.which("notify-send"):
                subprocess.Popen(["notify-send", "-a", "Zara", "Recordatorio",
                                  msg or "Recordatorio"], stdin=subprocess.DEVNULL)
        except Exception:
            pass
        if self.on_reminder:
            try:
                self.on_reminder(texto)
            except Exception:
                pass
        self.speak(texto)

    def greeting_text(self) -> str:
        from . import system
        return system.greeting()

    def _clean_old_audio(self, max_age_s: float = 3600):
        """Borra audios de voz de más de una hora (restos de sesiones previas)."""
        voz = self.paths.espacio / "voz"
        now = time.time()
        for f in voz.glob("zara-*.*") if voz.is_dir() else []:
            try:
                if now - f.stat().st_mtime > max_age_s:
                    f.unlink()
            except OSError:
                pass

    # --- inicialización -------------------------------------------------
    def _build_core_and_guardian(self):
        self.core = build_core(self.paths, speak=False)
        if self.cfg.get("cerebro_por_defecto"):
            try:
                self.core.brains.switch(self.cfg["cerebro_por_defecto"])
            except Exception:
                log.warning("cerebro por defecto no disponible: %s",
                            self.cfg["cerebro_por_defecto"])
        audit = AuditLog(self.paths.registros)
        rules = RuleSet.from_toml(self.paths.config / "guardian.toml",
                                  zara_root=self.paths.root, espacio=self.paths.espacio)
        self._guardian = Guardian(rules, audit, copias_dir=self.paths.copias)
        from .skills import load_skills
        self._skills = load_skills(self.paths.config / "skills")
        log.info("núcleo listo; cerebros=%s activo=%s skills=%s",
                 self.core.brains.names(), self.core.brains.active_name,
                 self._skills.names())

    def ensure_ready_text(self):
        """Arranque ligero para modo texto: cerebro + guardián, sin audio."""
        if self.core is None:
            self._build_core_and_guardian()

    def ensure_ready(self):
        """Arranque completo (voz): carga STT, verificación y micrófono."""
        if self._ready:
            return
        self.ensure_ready_text()
        self._stt = Transcriber(
            model_size=self.cfg.get("modelo_stt", "small"),
            beam_size=int(self.cfg.get("stt_beam_size", 2)),
            cpu_threads=int(self.cfg.get("stt_cpu_threads", 8)),
        )
        self._verify_pool = ThreadPoolExecutor(max_workers=1)
        ref = self.paths.ssd_voz / "huella_vk.npy"
        self._verifier = SpeakerVerifier(ref, threshold=float(self.cfg["umbral_voz"]))
        if not self._verifier.is_enrolled():
            log.warning("sin huella de voz: Zara obedecerá a cualquiera (ejecute zara-enroll)")
        self._oww = self._build_wake_detector()
        vad = self._build_vad()
        self.mic = Microphone(device=self._device(), vad=vad,
                              on_barge_in=self._on_barge_in)
        self._ready = True

    def _build_vad(self):
        """Silero VAD v5 neuronal si está disponible (robusto con acento andaluz);
        si no, VAD por energía calibrado al ruido de fondo."""
        if self.cfg.get("vad", "silero") != "energia":
            silero = SileroVad(
                threshold=float(self.cfg.get("vad_umbral", 0.5)),
                min_silence_ms=int(self.cfg.get("vad_silencio_ms", 600)),
                speech_pad_ms=int(self.cfg.get("vad_margen_ms", 350)),
                barge_in_ms=int(self.cfg.get("vad_barge_in_ms", 250)))
            if silero.available():
                log.info("escucha lista con Silero VAD v5 (neuronal)")
                return silero
            log.warning("Silero VAD no disponible; uso VAD por energía")
        threshold = self._calibrate()
        log.info("escucha lista; umbral VAD por energía=%.4f", threshold)
        return EnergyVad(threshold=threshold)

    def _on_barge_in(self):
        """El Señor VK habla mientras Zara responde: corta la voz al instante."""
        log.info("barge-in: interrupción del Señor VK; abortando voz")
        self.stop_speaking()

    def _build_wake_detector(self):
        """Detector neural de wake word, si está habilitado y disponible."""
        if self.cfg.get("motor_wake", "ambos") not in ("neural", "ambos"):
            return None
        try:
            from .audio.oww import WakeWordDetector
            det = WakeWordDetector(model=self.cfg.get("modelo_wake", "hey_jarvis_v0.1"),
                                   threshold=float(self.cfg.get("umbral_wake_neural", 0.5)))
            if not det.available():
                log.warning("wake neural no disponible (falta openWakeWord o el modelo); "
                            "uso solo el reconocimiento por texto")
                return None
            det._ensure()  # carga el modelo ya, para que la 1ª activación no tarde
            log.info("wake neural activo: %s (umbral %.2f)", det.model_name, det.threshold)
            return det
        except Exception:
            log.exception("no pude iniciar el wake neural; sigo con texto")
            return None

    def _device(self):
        d = self.cfg.get("dispositivo_microfono")
        return d if d not in ("", None) else None

    def _calibrate(self) -> float:
        """Mide el ruido de fondo y fija un umbral de voz por encima de él."""
        try:
            m = Microphone(device=self._device())
            amb = m.record_seconds(1.2)
            floor = float(np.sqrt(np.mean(amb.astype(np.float64) ** 2)))
            # Umbral sensible por encima del ruido, adaptado para hablar cómodo sin gritar.
            thr = max(0.008, min(0.055, floor * 1.35 + 0.006))
            log.info("ruido de fondo RMS=%.4f → umbral VAD=%.4f", floor, thr)
            return thr
        except Exception:
            log.exception("calibración fallida; umbral por defecto")
            return 0.018

    # --- escucha ----------------------------------------------------------
    def handle_utterance(self, audio: np.ndarray) -> dict | None:
        """Procesa una locución. Devuelve None si no iba dirigida a Zara."""
        dur = len(audio) / 16000
        rms = float(np.sqrt(np.mean(np.asarray(audio, dtype=np.float64) ** 2)))
        # Lanza la verificación de huella de voz EN PARALELO con la transcripción:
        # el embedding de resemblyzer y Whisper corren a la vez, no en serie, así
        # la identidad no suma latencia a la respuesta.
        verify_fut = None
        if (self.cfg.get("verificar_voz") and self._verifier is not None
                and self._verifier.is_enrolled() and self._verify_pool is not None):
            verify_fut = self._verify_pool.submit(self._verify_audio, audio)
        text = self._transcribe_for_wake(audio)
        log.debug("locución %.1f s, RMS %.4f", dur, rms)
        if not text:
            log.info("locución %.1f s (RMS %.4f) sin texto", dur, rms)
            return None
        if is_echo(text, self._recent_spoken):
            log.info("eco descartado: %r", text)
            return None
        neural = False
        if self._oww is not None and not has_wake(text):
            try:
                neural = self._oww.detect(audio)
                if neural:
                    log.info("wake neural disparó (texto no casó «Zara»)")
            except Exception:
                log.exception("fallo en el wake neural; uso solo texto")
        kind, order = self._gate.route(text, neural=neural)
        log.info("oído: %r -> %s", text, kind)
        if kind is None:
            return None
        if kind == "ack":
            if verify_fut is not None:
                ok, score = verify_fut.result()
                log.info("verificación de wake («Zara»): ok=%s score=%.3f", ok, score)
                if not ok and score < 0.50:
                    return {"denied": True}
            self.chime()
            return {"ack": True}

        if verify_fut is not None:
            ok, score = verify_fut.result()
            log.info("verificación de voz: ok=%s score=%.3f", ok, score)
            if not ok:
                return {"denied": True}
        self.chime()
        res = self.respond(order)
        return {"order": order, "reply": res["reply"], "ui": res.get("ui")}


    def open_listen_window(self, after_reply: bool = False):
        """Abre la ventana de escucha sin «Zara» una vez Zara ha terminado de hablar.

        Tras el acuse («¿Sí, Señor VK?») siempre; tras una respuesta, solo en
        modo conversación.
        """
        if not after_reply or self.cfg.get("modo_conversacion"):
            self._gate.open()

    def _verify_audio(self, audio) -> tuple[bool, float]:
        """Verifica la huella de voz. Tras la primera autenticación correcta de la
        sesión, relaja el umbral (el Señor VK ya está confirmado presente): evita
        rechazos molestos en frases cortas o con ruido durante la conversación."""
        relax = 0.08 if self._verified_once else 0.0
        thr = max(0.45, self._verifier.threshold - relax)
        ok, score = self._verifier.verify(audio, threshold=thr)
        if ok:
            self._verified_once = True
        return ok, score

    def _transcribe_for_wake(self, audio) -> str:
        """Transcribe con faster-whisper usando normalización AGC, beam search y vocabulario de Zara."""
        return self._stt.transcribe_array(audio)

    # --- respuesta --------------------------------------------------------
    def respond(self, text: str, verified: bool = True) -> dict:
        """Intención directa, comando protegido o pregunta al cerebro.

        Nunca lanza: cualquier fallo se convierte en una respuesta y se registra.
        """
        try:
            self.ensure_ready_text()
            clean = re.sub(r"^\s*zara[,:\s]+", "", text, flags=re.I).strip()
            it = parse_intent(clean)
            if it is not None:
                log.info("intención %s (%s)", it.kind, it.arg)
                return self._do_intent(it)
            if self._skills is not None:
                sk, g = self._skills.match(clean)
                if sk is not None:
                    log.info("skill: %s", sk.nombre)
                    return self._run_skill(sk, g)
            # Elegir ejecutor: con Claude activo y una tarea, el agente nativo;
            # con cualquier otro cerebro (o Claude en charla), el cerebro activo
            # con MANOS (actúa en el sistema vía Guardián). Así el control del
            # equipo funciona con Claude, Gemini o un modelo local.
            from .brain.routing import needs_agent
            is_task = needs_agent(clean)
            override, hands = None, None
            active = self.core.brains.active
            if is_task and hasattr(active, "stream_tools"):
                # Cerebro nativo (Gemini/Claude/Ollama) con Tool Calling → ReAct.
                override, hands = active, self._hands
            elif is_task and self.core.brains.active_name == "claude":
                ag = self.core.brains.get("agente")
                if ag is not None and ag.available():
                    override = ag                     # agente Claude CLI (legado)
                else:
                    hands = self._hands
            elif self._manos_enabled:
                hands = self._hands                   # resto de cerebros: manos (legado)
            if is_task:
                quien = override.name if override is not None else self.core.brains.active_name
                log.info("tarea → segundo plano (%s)", quien)
                return self._run_async_task(clean, override, hands)
            with self._brain_lock:
                return self.core.handle_text(clean, brain_override=override, hands=hands)
        except BrainError as exc:
            log.warning("cerebro falló: %s", exc)
            return {"reply": f"Lo siento, Señor VK: el cerebro no respondió ({exc}).",
                    "brain": self.active_brain()}
        except Exception:
            log.exception("error procesando %r", text)
            return {"reply": "Lo siento, Señor VK, ha ocurrido un error. "
                             "Lo he dejado anotado en el registro.", "brain": "error"}

    def _run_skill(self, sk, g: dict) -> dict:
        """Ejecuta una skill declarativa (decir / shell / prompt)."""
        if sk.tipo == "decir":
            return {"reply": sk.render("respuesta", g) or "Hecho, Señor VK.",
                    "brain": "skill"}
        if sk.tipo == "shell":
            cmd = sk.render("comando", g).strip()
            if not cmd:
                return {"reply": f"La skill «{sk.nombre}» no tiene comando, Señor VK.",
                        "brain": "skill"}
            return self._guarded_run(cmd, force_confirm=sk.confirmar)
        if sk.tipo == "prompt":
            filled = sk.render("prompt", g)
            brain = self.core.brains.get("claude") or self.core.brains
            try:
                with self._brain_lock:
                    reply = brain.ask(filled)
                return {"reply": reply or "Sin respuesta, Señor VK.", "brain": "skill · claude"}
            except BrainError as exc:
                return {"reply": f"La skill no pudo consultar, Señor VK ({exc}).",
                        "brain": "skill"}
        return {"reply": "No sé ejecutar esa skill, Señor VK.", "brain": "skill"}

    def _run_async_task(self, clean: str, brain, hands=None) -> dict:
        """Lanza la tarea en segundo plano (agente Claude o cerebro con manos)
        y acusa al instante para no bloquear la escucha."""
        def work():
            try:
                with self._brain_lock:
                    return self.core.handle_text(clean, brain_override=brain, hands=hands)
            except BrainError as exc:
                return {"reply": f"La tarea no salió, Señor VK: {exc}.", "brain": "agente"}
            except Exception:
                log.exception("tarea en segundo plano falló")
                return {"reply": "La tarea falló, Señor VK; lo he anotado en el registro.",
                        "brain": "agente"}
        self._tasks.submit(clean[:60], work, self._task_finished)
        return {"reply": "Enseguida, Señor VK, me pongo con ello. Le aviso al terminar.",
                "brain": "agente", "async": True}

    def _task_finished(self, name: str, result):
        reply = result.get("reply") if isinstance(result, dict) else (result or "")
        reply = (reply or "Tarea terminada, Señor VK.").strip()
        texto = f"Ya está, Señor VK. {reply}" if not reply.lower().startswith(
            ("ya está", "ya esta", "listo", "hecho", "la tarea")) else reply
        log.info("tarea terminada: %s", name)
        cb = self.on_task_done
        if cb is not None:
            try:
                cb(texto)
                return
            except Exception:
                log.exception("fallo notificando la tarea a la interfaz")
        self.speak(texto)

    def tasks_pending(self) -> list[str]:
        return self._tasks.active()

    def _agent_for(self, clean: str):
        """Devuelve el cerebro agente si la petición es una tarea, o None."""
        from .brain.routing import needs_agent
        if self.core and self.core.brains.active_name == "claude" and needs_agent(clean):
            ag = self.core.brains.get("agente")
            if ag is not None and ag.available():
                return ag
        return None

    def will_use_agent(self, text: str) -> bool:
        self.ensure_ready_text()
        clean = re.sub(r"^\s*zara[,:\s]+", "", text, flags=re.I).strip()
        from .intents import parse_intent
        return parse_intent(clean) is None and self._agent_for(clean) is not None

    def _do_intent(self, it) -> dict:
        k = it.kind
        if k == "show_panel":
            return {"reply": "Aquí tiene, Señor VK.", "brain": "acción", "ui": "show_panel"}
        if k == "stop_speaking":
            self.stop_speaking()
            return {"reply": "", "brain": "acción"}
        if k == "time":
            return {"reply": actions.say_time(), "brain": "acción"}
        if k == "date":
            return {"reply": actions.say_date(), "brain": "acción"}
        if k in ("volume_up", "volume_down", "set_volume", "mute", "unmute"):
            return {"reply": actions.volume(k, it.arg, sink=self._sink()), "brain": "acción"}
        if k.startswith("media_"):
            return {"reply": actions.media(k), "brain": "acción"}
        if k == "play_in_app":
            return {"reply": actions.play_in_app(it.arg), "brain": "acción"}
        if k == "open_folder":
            return {"reply": actions.open_folder(it.arg), "brain": "acción"}
        if k == "web_search":
            engine, query = it.arg
            return {"reply": actions.web_search(engine, query), "brain": "acción"}
        if k == "close_app":
            return self._close_app(it.arg or "")
        if k == "open_app":
            return {"reply": actions.open_app(it.arg or ""), "brain": "acción"}
        if k == "run":
            return self._guarded_run(it.arg or "")
        if k == "status":
            from . import system
            return {"reply": system.status_report(), "brain": "acción"}
        if k == "weather":
            from . import weather
            return {"reply": weather.weather(it.arg or ""), "brain": "acción"}
        if k == "help":
            return {"reply": _HELP, "brain": "acción", "ui": "show_panel"}
        if k == "reminder":
            return self._add_reminder(it.arg or "")
        if k == "lock":
            return self._lock_session()
        if k == "screenshot":
            return self._screenshot()
        if k == "screen_look":
            return self._screen_look(it.arg or "")
        if k == "power":
            return self._power(it.arg or "")
        return {"reply": "No entendí la orden, Señor VK.", "brain": "acción"}

    # --- JARVIS: recordatorios, sesión, capturas, energía -------------------
    def _add_reminder(self, text: str) -> dict:
        from .reminders import parse_when
        when, msg = parse_when(text)
        if when is None:
            return {"reply": "¿Para cuándo se lo recuerdo, Señor VK? "
                    "Dígame «en diez minutos» o «a las ocho».", "brain": "acción"}
        self._reminders.add(when, msg)
        cuando = when.strftime("las %H:%M")
        de_que = f": {msg}" if msg else ""
        return {"reply": f"De acuerdo, Señor VK. Se lo recordaré a {cuando}{de_que}.",
                "brain": "acción"}

    def _lock_session(self) -> dict:
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.LockWorkStation()
                return {"reply": "Bloqueando la sesión, Señor VK.", "brain": "acción"}
            except Exception as exc:
                return {"reply": f"No pude bloquear la sesión, Señor VK ({exc}).", "brain": "acción"}
        import shutil as _sh
        if _sh.which("loginctl"):
            subprocess.run(["loginctl", "lock-session"], stdin=subprocess.DEVNULL)
            return {"reply": "Bloqueando la sesión, Señor VK.", "brain": "acción"}
        if _sh.which("qdbus-qt6"):
            subprocess.run(["qdbus-qt6", "org.freedesktop.ScreenSaver",
                            "/ScreenSaver", "Lock"], stdin=subprocess.DEVNULL)
            return {"reply": "Bloqueando la pantalla, Señor VK.", "brain": "acción"}
        return {"reply": "No encuentro cómo bloquear la sesión, Señor VK.", "brain": "acción"}

    def _screenshot(self) -> dict:
        import shutil as _sh
        from datetime import datetime
        pics = Path.home() / ("Pictures" if sys.platform == "win32" else "Imágenes")
        dest = pics / f"captura-{datetime.now():%Y%m%d-%H%M%S}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab()
                img.save(dest)
            except Exception:
                ps = (f"Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                      f"$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                      f"$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
                      f"$g = [System.Drawing.Graphics]::FromImage($bmp); "
                      f"$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
                      f"$bmp.Save('{dest.as_posix()}'); $g.Dispose(); $bmp.Dispose()")
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
            if dest.exists():
                return {"reply": "Captura guardada en Imágenes, Señor VK.", "brain": "acción"}
            return {"reply": "No he podido hacer la captura, Señor VK.", "brain": "acción"}

        if _sh.which("spectacle"):
            subprocess.run(["spectacle", "-b", "-n", "-f", "-o", str(dest)],
                           stdin=subprocess.DEVNULL, capture_output=True)
        if dest.exists():
            return {"reply": f"Captura guardada en Imágenes, Señor VK.", "brain": "acción"}
        return {"reply": "No he podido hacer la captura, Señor VK.", "brain": "acción"}

    def _screen_look(self, question: str) -> dict:
        """Captura la pantalla, se la pasa a Claude (multimodal) y la borra.

        Privacidad: la captura solo se toma con esta orden explícita, vive en la
        carpeta de trabajo de Claude mientras dura la consulta y se borra después.
        """
        import shutil as _sh
        from datetime import datetime
        from .brain.cli import ClaudeBrain
        sandbox = Path(ClaudeBrain.SANDBOX)
        try:
            sandbox.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        shot = sandbox / f"pantalla-{datetime.now():%H%M%S-%f}.png"
        if sys.platform == "win32":
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab()
                img.save(shot)
            except Exception:
                ps = (f"Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                      f"$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                      f"$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
                      f"$g = [System.Drawing.Graphics]::FromImage($bmp); "
                      f"$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
                      f"$bmp.Save('{shot.as_posix()}'); $g.Dispose(); $bmp.Dispose()")
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
        elif _sh.which("spectacle"):
            subprocess.run(["spectacle", "-b", "-n", "-f", "-o", str(shot)],
                           stdin=subprocess.DEVNULL, capture_output=True)
        else:
            return {"reply": "No encuentro con qué mirar la pantalla, Señor VK.",
                    "brain": "acción"}

        if not shot.exists():
            return {"reply": "No he podido capturar la pantalla, Señor VK.",
                    "brain": "acción"}
        q = question.strip() or "dime brevemente qué hay en ella"
        prompt = (f"Esta es una captura de la pantalla del Señor VK: {shot}\n"
                  f"Ábrela con la herramienta Read (es una imagen) y {q}. "
                  f"Responde en español, directo y sin tecnicismos innecesarios.")
        brain = None
        if self.core is not None:
            brain = self.core.brains.get("claude") or self.core.brains.get("agente")
        if brain is None:
            shot.unlink(missing_ok=True)
            return {"reply": "No tengo un cerebro capaz de ver imágenes ahora mismo, Señor VK.",
                    "brain": "acción"}
        try:
            with self._brain_lock:
                reply = brain.ask(prompt)
            return {"reply": reply or "No he sabido interpretar la pantalla, Señor VK.",
                    "brain": "claude · visión"}
        except BrainError as exc:
            return {"reply": f"No pude mirar la pantalla, Señor VK ({exc}).",
                    "brain": "claude · visión"}
        finally:
            try:
                shot.unlink(missing_ok=True)
            except OSError:
                pass

    def _power(self, what: str) -> dict:
        verbo = {"poweroff": "apagar", "reboot": "reiniciar", "suspend": "suspender"}[what]
        if what != "suspend" and self.confirm.enabled:
            if not self.confirm.ask(f"¿Seguro que quiere {verbo} el equipo, Señor VK?"):
                return {"reply": "De acuerdo, no lo hago, Señor VK.", "brain": "guardián"}
        if sys.platform == "win32":
            cmd = {
                "poweroff": ["shutdown.exe", "/s", "/t", "0"],
                "reboot": ["shutdown.exe", "/r", "/t", "0"],
                "suspend": ["rundll32.exe", "powrprof.dll,SetSuspendState", "Sleep"],
            }[what]
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
            return {"reply": f"A la orden, Señor VK. Voy a {verbo} el equipo.", "brain": "acción"}
        cmd = {"poweroff": ["systemctl", "poweroff"],
               "reboot": ["systemctl", "reboot"],
               "suspend": ["systemctl", "suspend"]}[what]
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
        return {"reply": f"A la orden, Señor VK. Voy a {verbo} el equipo.", "brain": "acción"}

    def _close_app(self, name: str) -> dict:
        """Cierra una aplicación por su nombre directamente."""
        procs = actions.find_app_processes(name)
        if not procs:
            return {"reply": f"No veo abierta ninguna aplicación «{name}», Señor VK.",
                    "brain": "acción"}
        nombres = ", ".join(sorted({c for _, c in procs}))
        act = Action(kind="process", description=f"cerrar {name}",
                     command=f"cerrar {nombres}")
        if self._guardian:
            self._guardian.classify(act)  # queda auditado
        if self.confirm.enabled and not self.confirm.ask(f"¿Cierro {nombres}? Se perderá lo que no esté guardado."):
            return {"reply": "De acuerdo, no la cierro, Señor VK.", "brain": "guardián"}
        n = actions.terminate(procs)
        return {"reply": f"Hecho, Señor VK: he cerrado {nombres}." if n else
                "No he podido cerrarla.", "brain": "acción"}

    def _run_guarded_raw(self, cmd: str, force_confirm: bool = False):
        """Ejecuta un comando pasando por el Guardián.

        Devuelve (estado, texto): estado ∈ {blocked, cancelled, ok, error}.
        Es el núcleo compartido por «ejecuta …», las skills y las manos de los
        cerebros: TODO comando del sistema pasa por aquí, venga de quien venga.
        """
        act = Action(kind="shell", description="comando solicitado", command=cmd)
        decision = self._guardian.classify(act)
        if decision.is_blocked:
            return ("blocked", decision.reason or "acción no permitida")
        if self.confirm.enabled and (decision.requires_confirmation or force_confirm):
            nivel = "crítica" if decision.level is Level.CRITICAL else "delicada"
            if not self.confirm.ask(f"Acción {nivel}: «{cmd}». ¿Confirma, Señor VK?"):
                return ("cancelled", "")
        if decision.requires_confirmation:
            try:
                self._guardian.prepare(decision)
            except Exception as exc:  # noqa: BLE001
                if self.confirm.enabled:
                    return ("cancelled", f"no pude preparar copia de seguridad ({exc})")
        try:
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                 timeout=120, stdin=subprocess.DEVNULL,
                                 cwd=str(self.paths.espacio))
            res = out.stdout.strip() or out.stderr.strip() or "(sin salida)"
            return ("ok", res)
        except Exception as exc:  # noqa: BLE001
            return ("error", str(exc))

    def _guarded_run(self, cmd: str, force_confirm: bool = False) -> dict:
        status, res = self._run_guarded_raw(cmd, force_confirm=force_confirm)
        if status == "blocked":
            return {"reply": f"No puedo ejecutar eso, Señor VK: {res}.", "brain": "guardián"}
        if status == "cancelled":
            extra = f" ({res})" if res else ""
            return {"reply": f"Cancelado, Señor VK{extra}.", "brain": "guardián"}
        if status == "error":
            return {"reply": f"Falló el comando: {res}", "brain": "acción"}
        if "\n" not in res and len(res) <= 80:
            return {"reply": f"Hecho, Señor VK: {res}.", "brain": "acción"}
        return {"reply": f"Hecho, Señor VK.\n```\n{res[:1500]}\n```", "brain": "acción"}

    def _execute_tool(self, name: str, args: dict) -> dict:
        """Ejecuta una herramienta pedida por un cerebro (Tool Calling nativo),
        pasando SIEMPRE por el Guardián lo que toca el sistema. Sin puntos ciegos."""
        from .brain import tools as toolmod
        try:
            if name == "run_shell":
                cmd = (args.get("command") or "").strip()
                if not cmd:
                    return {"success": False, "stderr": "comando vacío", "exit_code": 2}
                status, out = self._run_guarded_raw(cmd)
                if status == "blocked":
                    return {"success": False, "blocked": True, "stderr": out, "exit_code": 126}
                if status == "cancelled":
                    return {"success": False, "stderr": f"cancelado por el Señor VK ({out})", "exit_code": 130}
                if status == "error":
                    return {"success": False, "stderr": out, "exit_code": 1}
                return {"success": True, "stdout": out, "exit_code": 0}
            if name == "file_manager":
                action = (args.get("action") or "").lower().strip()
                path = args.get("path") or ""
                # Las escrituras/borrados pasan por el Guardián (alcance + copia).
                if action in ("write", "append", "edit", "delete") and self._guardian is not None:
                    act = Action(kind="file_delete" if action == "delete" else "file_write",
                                 description=f"file_manager {action}",
                                 command=f"{action} {path}",
                                 paths=(Path(path).expanduser(),))
                    decision = self._guardian.classify(act)
                    if decision.is_blocked:
                        return {"success": False, "blocked": True,
                                "stderr": decision.reason, "exit_code": 126}
                    if decision.needs_backup:
                        try:
                            self._guardian.prepare(decision)
                        except Exception:  # noqa: BLE001
                            pass
                return toolmod.file_manager(action, path, args.get("content", ""),
                                            args.get("old_str", ""), args.get("new_str", ""))
            if name == "os_control":
                return toolmod.os_control(args.get("action", ""), args.get("target", ""),
                                          args.get("value", ""))
            if name == "inspect_screen":
                res = toolmod.inspect_screen(args.get("prompt", ""))
                # No volcamos el base64 al bucle de texto; resumimos.
                if res.get("success"):
                    return {"success": True, "stdout":
                            f"Pantalla capturada ({res['width']}x{res['height']}). "
                            f"Use «mira la pantalla» para análisis visual con Zara."}
                return res
            return {"success": False, "stderr": f"herramienta desconocida: {name}", "exit_code": 127}
        except Exception as exc:  # noqa: BLE001
            log.exception("fallo ejecutando herramienta %s", name)
            return {"success": False, "error": str(exc), "exit_code": 1}

    def _hands(self, brain, prompt: str, context: str) -> str:
        """Punto de entrada de las MANOS. Elige el mecanismo según el cerebro:
        Tool Calling nativo (ReAct, preferente) si el cerebro lo soporta; si no,
        el protocolo de texto <ejecutar> (legado). En ambos casos TODO comando
        pasa por el Guardián."""
        if hasattr(brain, "stream_tools"):
            from .hands import ReActAgent
            agent = ReActAgent(brain, self._execute_tool,
                               max_steps=self._hands_max_steps)
            try:
                return agent.run(prompt, context)
            except Exception:  # noqa: BLE001
                log.exception("bucle ReAct nativo falló; uso el legado <ejecutar>")
        return self._hands_runner(brain, prompt, context)

    def _hands_runner(self, brain, prompt: str, context: str) -> str:
        """Protocolo de texto <ejecutar> para cerebros sin Tool Calling nativo.
        Bucle acotado; cada comando pasa por el Guardián. (Fallback del legado.)"""
        convo = f"{PROTOCOL}\n\n---\nPetición del Señor VK:\n{prompt}"
        reply = ""
        for _ in range(self._hands_max_steps):
            reply = brain.ask(convo, context=context)
            context = ""  # la memoria solo en el primer turno
            cmds = extract_actions(reply)
            if not cmds:
                return strip_actions(reply) or reply
            bloques = []
            for cmd in cmds:
                log.info("manos (%s) ejecuta: %s", self.active_brain(), cmd)
                status, out = self._run_guarded_raw(cmd)
                etiqueta = {"blocked": "[BLOQUEADO por el Guardián] ",
                            "cancelled": "[cancelado por el Señor VK] ",
                            "error": "[error] "}.get(status, "")
                bloques.append(f"$ {cmd}\n{etiqueta}{out}")
            convo = (f"{convo}\n\n{reply.strip()}\n\n---\nSalida de los comandos:\n"
                     + "\n\n".join(bloques)
                     + "\n\n---\nResponde ahora al Señor VK con el resultado, en "
                       "español y breve. Si necesitas más comandos, usa <ejecutar>.")
        return strip_actions(reply) or "He hecho lo que he podido, Señor VK."

    # --- voz ----------------------------------------------------------------
    def _build_stream_voice(self):
        """Cadena de voz en STREAMING en memoria (punto 3). None si no es usable."""
        if self._stream_voice is not False:
            return self._stream_voice
        try:
            from .voice.stream import (StreamingVoice, EdgeMemorySynth,
                                       KokoroMemorySynth, PiperMemorySynth)
            voz = self.cfg.get("voz", "es-ES-XimenaNeural")
            edge = EdgeMemorySynth(voice=voz,
                                   rate=self.cfg.get("edge_rate", "+6%"),
                                   pitch=self.cfg.get("edge_pitch", "+2Hz"))
            kokoro = KokoroMemorySynth(
                self.cfg.get("kokoro_modelo", ""), self.cfg.get("kokoro_voces", ""),
                voice=self.cfg.get("kokoro_voz", "ef_dora"))
            piper = PiperMemorySynth(self.cfg.get("voz_piper", ""))
            motor = self.cfg.get("motor_voz", "edge")
            if motor == "piper":
                chain = [piper, kokoro, edge]       # local primero: instantáneo offline
            elif motor == "kokoro":
                chain = [kokoro, piper, edge]
            else:
                chain = [edge, kokoro, piper]       # Edge natural, fallback local
            sv = StreamingVoice(chain, sink=self._sink())
            self._stream_voice = sv if sv.available() else None
        except Exception:
            log.exception("no pude construir la voz en streaming; uso archivo+reproductor")
            self._stream_voice = None
        return self._stream_voice

    def speak_stream(self, tokens):
        """Habla un FLUJO de tokens del cerebro troceándolo por frases: Zara empieza
        a hablar en < 400 ms sin esperar la respuesta completa. Devuelve el texto
        completo dicho (para memoria/panel). Si no hay streaming, acumula y habla."""
        sv = self._build_stream_voice() if self.speak_enabled else None
        if sv is None:
            full = "".join(tokens)
            self.speak(full)
            return full
        from .voice.text import clean_for_speech
        collected: list[str] = []

        def _gen():
            for tok in tokens:
                collected.append(tok)
                yield tok

        if self.mic is not None:
            self.mic.muted.set()
        try:
            sv.feed_tokens(_gen())
            while sv.is_speaking():
                time.sleep(0.03)
        finally:
            time.sleep(0.15)
            if self.mic is not None:
                self.mic.muted.clear()
        full = "".join(collected)
        said = clean_for_speech(full)
        if said:
            self._recent_spoken.append(said)
        return full

    def speak(self, text: str):
        """Habla (texto adaptado a voz). Silencia el micro mientras suena.

        Prefiere el motor en memoria (streaming, cero archivos); si no está
        disponible, recurre a la síntesis a archivo + reproductor del sistema."""
        if not self.speak_enabled:
            return
        self.ensure_ready_text()
        say = speech_text(text)
        if not say:
            return
        self._recent_spoken.append(say)
        sv = self._build_stream_voice()
        if sv is not None:
            if self.mic is not None:
                self.mic.muted.set()
            try:
                sv.say(say)
                # breve espera a que arranque y luego hasta que termine
                time.sleep(0.05)
                while sv.is_speaking():
                    time.sleep(0.03)
            except Exception:
                log.exception("fallo en la voz en streaming; pruebo archivo+reproductor")
            else:
                time.sleep(0.15)
                if self.mic is not None:
                    self.mic.muted.clear()
                return
            if self.mic is not None:
                self.mic.muted.clear()
        with self._speak_lock:
            try:
                path = self.core.voice.say(say)
            except Exception:
                log.exception("no se pudo sintetizar la voz")
                return
            if self.mic is not None:
                self.mic.muted.set()
            try:
                wav = to_wav(path)
                sink = self._sink()
                self._player = subprocess.Popen(
                    player_cmd(wav, sink), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                _out, err = self._player.communicate()
                if self._player.returncode not in (0, -15):
                    if err:
                        log.warning("el reproductor falló (rc=%s): %s",
                                    self._player.returncode,
                                    err.decode(errors="ignore")[:200])
                    if sink:
                        log.info("reintento de voz en la salida por defecto del sistema")
                        self._player = subprocess.Popen(
                            player_cmd(wav, None), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        self._player.communicate()
                    if self._player.returncode not in (0, -15):
                        self._play_sounddevice_fallback(wav)
            except Exception:
                log.exception("no se pudo reproducir la voz")
            finally:
                self._player = None
                for f in {path, path.with_suffix(".wav")}:
                    try:
                        f.unlink(missing_ok=True)
                    except OSError:
                        pass
                time.sleep(0.15)  # deja apagarse el eco antes de volver a escuchar
                if self.mic is not None:
                    self.mic.muted.clear()

    def _sink(self) -> str | None:
        s = self.cfg.get("salida_audio")
        return s or None

    def _play_sounddevice_fallback(self, wav_path: str | Path) -> bool:
        """Fallback directo con sounddevice (PortAudio) cuando fallan los reproductores del sistema."""
        try:
            import wave
            import sounddevice as sd
            with wave.open(str(wav_path), "rb") as wf:
                sr = wf.getframerate()
                nchannels = wf.getnchannels()
                frames = wf.readframes(wf.getnframes())
                dtype = np.int16 if wf.getsampwidth() == 2 else np.uint8
                data = np.frombuffer(frames, dtype=dtype)
                if nchannels > 1:
                    data = data.reshape(-1, nchannels)
                sd.play(data, sr)
                sd.wait()
                return True
        except Exception as exc:
            log.warning("fallback sounddevice no disponible: %s", exc)
            return False

    def stop_speaking(self):
        sv = self._stream_voice
        if sv not in (False, None):
            try:
                sv.stop()
            except Exception:
                pass
        p = self._player
        if p is not None and p.poll() is None:
            p.terminate()
            log.info("voz detenida por el usuario")

    # --- cerebros -----------------------------------------------------------
    def brain_names(self):
        self.ensure_ready_text()
        return self.core.brains.names()

    def active_brain(self):
        self.ensure_ready_text()
        return self.core.brains.active_name

    def switch_brain(self, name: str):
        self.ensure_ready_text()
        try:
            return self.core.brains.switch(name)
        except Exception:
            return self.active_brain()
