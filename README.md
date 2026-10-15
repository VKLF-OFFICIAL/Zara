# ZARA v2.0 — Asistente Táctico de IA Estilo J.A.R.V.I.S.
### Protocolo J.A.R.V.I.S. | Asistente de Alta Autonomía para el **Señor VK**

---

## 1. Visión General de la Arquitectura
**ZARA v2.0** es un asistente táctico autónomo diseñado con latencia ultrabaja, streaming por frases, tolerancia fonética al acento del sur de España (Granada/Andalucía) y ejecución silenciosa tipo *YOLO Seguro* sin confirmaciones constantes innecesarias.

```
                   ┌──────────────────────────────────┐
                   │    Micrófono continuo 16kHz      │
                   └─────────────────┬────────────────┘
                                     │
                             [Silero VAD v5]
                     (700ms silencio, 350ms pad)
                                     │
                                     ▼
                        [faster-whisper GPU/CPU]
                    (initial_prompt fonético andaluz)
                                     │
                                     ▼
                             [ZaraRouter <10ms]
                     ┌───────────────┼───────────────┐
                     ▼               ▼               ▼
                 [Claude]         [Gemini]       [Ollama]
                 (Código/Sec)     (Visión/Web)   (Offline)
                     │
                     ▼
             [ReAct Loop Silencioso (hasta 8 pasos)]
           run_shell | file_manager | os_control | inspect_screen
                     │
                     ▼ (Tokens en Streaming)
           [Sentence Chunker (delimitador >= 35 chars)]
                     │
                     ▼
         [Motor TTS In-Memory (Edge-TTS / Kokoro)]
             (PCM en RAM decodificado con miniaudio)
                     │
                     ▼ (Barge-in: si habla el usuario >250ms, aborta)
             [Salida de Audio + HUD Holográfico PySide6]
```

---

## 2. Subsistemas Principales

### Oído Neuronal y Captura (`core/ear.py`)
- **Captura continua:** `sounddevice.InputStream` a 16.000 Hz, mono, float32 con cola thread-safe.
- **VAD Neuronal:** **Silero VAD v5** con `min_silence_duration_ms=700` y `speech_pad_ms=350` para evitar cortes en pausas naturales.
- **Detección de Interrupción (Barge-in):** Si el Señor VK habla durante $>250\text{ ms}$ mientras Zara responde, se detiene la reproducción instantáneamente y pasa a escucha.
- **Transcripción sin sesgo de acento:** `faster-whisper` con `beam_size=5`, filtrado VAD e inyección de contexto fonético y jerga de hacking/sistemas (`initial_prompt`).

### Voz Dulce y Fluida (`core/voice.py`)
- **Streaming por frases (`SentenceChunker`):** Los tokens generados se agrupan hasta encontrar puntuación (`.`, `,`, `:`, `?`, `!`, `\n`) con al menos 35 caracteres y se envían de inmediato a la síntesis sin esperar la respuesta completa.
- **Motor en memoria (Edge-TTS + miniaudio):** Síntesis con voz `es-ES-XimenaNeural` (`pitch=+2Hz`, `rate=+6%`) decodificada directamente en RAM en PCM float32, sin escribir archivos MP3 temporales en disco. Fallback a **Kokoro-82M ONNX** local.

### Cerebro Híbrido y ReAct Autónomo (`core/brain.py` y `core/tools.py`)
- **ZaraRouter (< 10 ms):** Conmutación ultrarrápida entre Claude 3.7 / Claude Code, Gemini 2.5 Flash y Ollama (`qwen2.5-coder:7b`). Fallback automático ante caídas de red.
- **Filosofía YOLO Seguro:** Cero interrupciones para comandos diagnósticos, lectura/edición de ficheros, inspección de red o gestión de procesos.
- **Guardarraíl Estricto:** Bloqueo exclusivo de operaciones catastróficas irreversibles (`rm -rf /`, `mkfs`, `Format-Volume`, `dd if=`).

### Interfaz Holográfica (`ui/hud.py`)
- HUD transparente frameless en **PySide6** con reactividad a 60 FPS:
  - `IDLE`: Cian tenue pulsante.
  - `LISTENING`: Verde esmeralda reactivo al nivel RMS del micrófono.
  - `THINKING / TOOL`: Ámbar/Violeta con visualización en vivo de comandos.
  - `SPEAKING`: Onda sonora azul zafiro.

---

## 3. Despliegue y Uso

### En Windows 10/11
```powershell
# 1. Diagnóstico completo de subsistemas (Audio, VAD, Shell, HUD)
.\setup_and_run.ps1 -Check

# 2. Iniciar ZARA v2.0 completa con HUD holográfico
.\setup_and_run.ps1
# O simplemente haciendo doble clic en zara.cmd

# 3. Modo consola / terminal (sin HUD gráfico)
.\setup_and_run.ps1 -Cli
# O ejecutando zara-chat.cmd
```

### En Linux (Kali Linux / Fedora / Debian)
```bash
# 1. Conceder permisos de ejecución al lanzador
chmod +x setup_and_run.sh

# 2. Diagnóstico de subsistemas
./setup_and_run.sh --check

# 3. Iniciar ZARA v2.0
./setup_and_run.sh

# 4. Modo CLI
./setup_and_run.sh --cli
```

---

## 4. Configuración (`.env`)
Edite el archivo `.env` para personalizar API Keys o motores:
```env
# Claves de IA (Opcionales, fallback automático a Ollama local)
GEMINI_API_KEY=""
ANTHROPIC_API_KEY=""

# Configuración de Ollama Local
OLLAMA_HOST="http://localhost:11434"
OLLAMA_MODEL="qwen2.5-coder:7b"

# Voz y Síntesis
ZARA_TTS_ENGINE="edge"
ZARA_EDGE_VOICE="es-ES-XimenaNeural"
ZARA_EDGE_RATE="+6%"
ZARA_EDGE_PITCH="+2Hz"
```
