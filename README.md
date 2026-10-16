# ZARA — Asistente Táctico de IA estilo J.A.R.V.I.S.
### Protocolo J.A.R.V.I.S. | Asistente de alta autonomía para el **Señor VK**

Asistente de voz de latencia ultrabaja, con oído neuronal tolerante al acento del
sur de España, voz femenina en streaming, cerebros de IA nativos (sin subprocesos)
y autonomía real estilo Claude Code bajo un Guardián de seguridad.

---

## 1. Arquitectura unificada

Existe **una sola fuente de verdad: el paquete `app/zara/`** (con su suite de
tests en `app/tests/`). La antigua «v2.0» de la raíz (`core/`, `main.py`, `ui/`,
`config.py`) se ha fusionado dentro del paquete y se ha eliminado.

```
                 Micrófono continuo 16 kHz (sounddevice)
                                 │
                 Silero VAD v5  (audio/vad.py: SileroVad)
          (silencio ~600 ms, margen 350 ms, barge-in 250 ms)
                                 │
            faster-whisper CPU int8 · 8 hilos · beam 2
        (audio/stt.py: initial_prompt fonético andaluz + jerga)
                                 │
       verificación de huella de voz EN PARALELO (audio/identity.py)
                                 │
                 Enrutador / cerebro activo (brain/)
          ┌──────────────┬───────────────┬───────────────┐
      NativeGemini   NativeAnthropic   NativeOllama   (CLI: fallback)
     google-genai   AsyncAnthropic   /api/chat+tools
                                 │
        Tool Calling nativo → ReAct hasta 10 pasos (hands.py)
      run_shell · file_manager · os_control · inspect_screen
         cada acción pasa por el GUARDIÁN (guardian/)
                                 │  (tokens en streaming)
          SentenceChunker  (voice/stream.py, ≥35 car.)
                                 │
     Edge-TTS → PCM en RAM (miniaudio) · Kokoro/Piper local fallback
                                 │  (barge-in: corta al instante)
            Salida sounddevice + HUD PySide6 (ui/)
```

Rutas de datos (punto 6): las **calientes** (logs JSONL, caché, espacio de
trabajo, tareas) viven en el SSD nativo `~/.local/share/zara/`; `/mnt/almacen/ZARA`
queda para memoria persistente y copias de seguridad en frío. Si el almacén no
está montado, Zara no se bloquea: trabaja íntegramente sobre el SSD.

---

## 2. Subsistemas

- **Oído (`app/zara/audio/`)** — `SileroVad` neuronal con barge-in; `Transcriber`
  (faster-whisper) optimizado para CPU de 12 núcleos (`int8`, `cpu_threads=8`,
  `beam_size=2`) e `initial_prompt` rico en fonética andaluza y vocabulario de
  sistemas/pentesting. La huella de voz se verifica en paralelo con la
  transcripción y el umbral se relaja tras la primera autenticación de la sesión.
- **Voz (`app/zara/voice/`)** — `StreamingVoice` sintetiza y reproduce en memoria
  por frases (Edge-TTS `es-ES-XimenaNeural` decodificado a PCM con miniaudio;
  Kokoro-82M ONNX / Piper como fallback local). El normalizador limpia Markdown y
  código antes del TTS.
- **Cerebros (`app/zara/brain/`)** — adaptadores persistentes `NativeGemini`,
  `NativeAnthropic` (AsyncAnthropic) y `NativeOllama` con Tool Calling nativo. Los
  binarios CLI quedan solo como último recurso.
- **Manos y autonomía (`app/zara/hands.py`)** — `ReActAgent`: bucle acción→resultado
  de hasta **10 pasos** con **autocorrección** (si un comando falla, Zara lee el
  error, corrige y reintenta sola). Todo comando pasa por el Guardián.
- **Guardián (`app/zara/guardian/` + `config/guardian.toml`)** — administración
  cotidiana (`sudo`, `systemctl`, `dnf/apt/pip/uv`, `git`, `docker`, `kill`,
  `curl/wget`, pentesting) en **Seguro** (silencioso); **Confirmar** solo para
  borrados recursivos fuera del espacio; **Bloqueado** para lo catastrófico
  (`rm -rf /`, `mkfs`, `dd` a disco, fork bombs).
- **Interfaz (`app/zara/ui/`)** — HUD holográfico en PySide6.

---

## 3. Despliegue y uso

### Linux (Fedora / Kali / Debian)
```bash
chmod +x setup_and_run.sh
./setup_and_run.sh --check     # diagnóstico de subsistemas
./setup_and_run.sh             # HUD holográfico
./setup_and_run.sh --cli       # modo terminal
./setup_and_run.sh --enroll    # inscribir la huella de voz del Señor VK
```

### Windows 10/11
```powershell
.\setup_and_run.ps1 -Check
.\setup_and_run.ps1            # o doble clic en zara.cmd
.\setup_and_run.ps1 -Cli       # o zara-chat.cmd
.\setup_and_run.ps1 -Enroll    # o zara-enroll.cmd
```

Instalación de dependencias: `python setup_and_run.py --install`
(o `pip install -r requirements.txt`).

---

## 4. Configuración

- **`config/zara.toml`** — voz, STT/VAD, cerebro por defecto, autonomía
  (`manos_max_pasos = 10`, `pedir_confirmacion = false`).
- **`config/guardian.toml`** — niveles de seguridad (safe/confirm/critical/blocked).
- **`config/persona.md`** — identidad y tono (respeto máximo + ingenio JARVIS).
- **`.env`** — claves de API de los cerebros nativos:

```env
GEMINI_API_KEY=""
ANTHROPIC_API_KEY=""
OLLAMA_HOST="http://localhost:11434"
```

---

## 5. Tests

```bash
cd app && python -m pytest -q
```
