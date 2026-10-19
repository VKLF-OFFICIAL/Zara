# ZARA — Asistente Táctico de IA Estilo J.A.R.V.I.S.
### Protocolo J.A.R.V.I.S. | Asistente de Alta Autonomía para el **Señor VK**

> **Versión 0.3.0-alpha — pre-lanzamiento.** Funciona de extremo a extremo, pero aún es software en desarrollo: sin API estable, sin empaquetado y con partes sin validar en hardware real. Ver [CHANGELOG.md](CHANGELOG.md).

---

## 1. Visión General de la Arquitectura
**ZARA** es un asistente táctico autónomo diseñado con latencia ultrabaja, streaming por frases, tolerancia fonética al acento del sur de España (Granada/Andalucía) y ejecución silenciosa tipo *YOLO Seguro* sin confirmaciones constantes innecesarias.

```
                   ┌──────────────────────────────────┐
                   │    Micrófono continuo 16kHz      │
                   └─────────────────┬────────────────┘
                                     │
                             [Silero VAD v5]
                     (500ms silencio, 350ms pad)
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
             [ReAct Loop Silencioso (hasta `ZARA_MAX_REACT_STEPS` pasos, 8 por defecto)]
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
- **VAD Neuronal:** **Silero VAD v5** con `min_silence_duration_ms=500` y `speech_pad_ms=350` para evitar cortes en pausas naturales.
- **Detección de Interrupción (Barge-in):** Si el Señor VK habla durante $>250\text{ ms}$ mientras Zara responde, se detiene la reproducción instantáneamente y pasa a escucha.
- **Transcripción sin sesgo de acento:** `faster-whisper` (modelo `auto`: `large-v3-turbo` en GPU, `small` en CPU), `beam_size=1`, filtrado VAD e inyección de contexto fonético y jerga de hacking/sistemas (`initial_prompt`).

### Voz Dulce y Fluida (`core/voice.py`)
- **Streaming por frases (`SentenceChunker`):** Los tokens generados se agrupan hasta encontrar puntuación (`.`, `,`, `:`, `?`, `!`, `\n`) con al menos 35 caracteres y se envían de inmediato a la síntesis sin esperar la respuesta completa.
- **Motor en memoria (Edge-TTS + miniaudio):** Síntesis con voz `es-ES-XimenaNeural` (`pitch=+2Hz`, `rate=+6%`) decodificada directamente en RAM en PCM float32, sin escribir archivos MP3 temporales en disco. Fallback a **Kokoro-82M ONNX** local.

### Cerebro Híbrido y ReAct Autónomo (`core/brain.py` y `core/tools.py`)
- **ZaraRouter (< 10 ms):** Conmutación ultrarrápida entre Claude (modelo configurable con `ANTHROPIC_MODEL`) / Claude Code, Gemini 2.5 Flash y Ollama (`qwen2.5-coder:7b`). Fallback automático ante caídas de red.
- **Filosofía YOLO Seguro:** Cero interrupciones para comandos diagnósticos, lectura/edición de ficheros, inspección de red o gestión de procesos.
- **Guardarraíl Estricto:** Bloqueo de operaciones catastróficas irreversibles en `run_shell` (`rm -rf /`, `rm -rf ~`, `mkfs`, `Format-Volume`, `dd if=`, `shutdown`/`reboot`, escritura en `/etc/fstab`...) y, en `file_manager`, bloqueo de `write`/`append`/`edit`/`delete` sobre rutas protegidas (`/etc`, `/boot`, `/usr`, `~/.ssh`, `~/.gnupg`, `.git`, el propio HOME...). Las listas viven en `config.py` (`FORBIDDEN_COMMANDS_REGEX`, `PROTECTED_TREES`, `PROTECTED_EXACT`). No es un sandbox: es una red de seguridad contra errores del modelo.
- **Confirmación hablada:** las acciones sensibles (borrar, `sudo`, instalar paquetes, matar procesos, `git push`, cerrar apps, `invoke_claude_code`...) piden un «sí/no» al Señor VK antes de ejecutarse (hablado, escrito en el panel o, en modo Live, por voz). Sin respuesta en 25 s se cancelan. Los patrones están en `CONFIRM_COMMANDS_REGEX` (`config.py`).

### Interfaz Holográfica (`ui/hud.py`)
- HUD transparente frameless en **PySide6** con reactividad a 60 FPS:
  - `IDLE`: Cian tenue pulsante.
  - `LISTENING`: Verde esmeralda reactivo al nivel RMS del micrófono.
  - `THINKING / TOOL`: Ámbar/Violeta con visualización en vivo de comandos.
  - `SPEAKING`: Onda sonora azul zafiro.

---

## 3. Instalación desde Cero

### Requisitos previos
- **Python 3.10+** (recomendado 3.12).
- **[Ollama](https://ollama.com)** instalado si quieres un cerebro 100% local y sin internet (`ollama serve` corriendo).
- Micrófono y, preferiblemente, **auriculares** (ver sección 6 — evita el eco que confunde a Zara con una interrupción).
- Windows 10/11 o Linux (Kali/Fedora/Debian).

### Paso 1 — Clonar e instalar dependencias
```powershell
# Windows (PowerShell)
git clone https://github.com/VKLF-OFFICIAL/Zara.git
cd Zara
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```
```bash
# Linux
git clone https://github.com/VKLF-OFFICIAL/Zara.git
cd Zara
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # opcional: solo hace falta para claves de API
```
O deja que el propio script lo haga por ti, incluyendo la copia de `.env`:
```powershell
python setup_and_run.py --install --check
```

### Paso 2 — Preparar el modelo local de Ollama (opcional pero recomendado)
```bash
ollama serve                     # si no corre ya como servicio
ollama pull qwen2.5-coder:7b     # o el modelo que prefieras
```
No necesitas tocar `OLLAMA_MODEL` en `.env`: si lo dejas vacío (por defecto), Zara autodetecta los modelos instalados con `ollama list` y elige uno automáticamente (prioriza variantes "coder"). Solo rellena `OLLAMA_MODEL` si quieres fijar uno en concreto entre varios instalados — en ese caso debe coincidir **exactamente** con el nombre que muestra `ollama list` (Ollama es estricto: `qwen2.5-coder:7b` y `qwen2.5-coder:7b-instruct` son modelos distintos para él). También puedes cambiarlo en caliente desde el selector del panel táctico del HUD.

### Paso 3 — Claves de API (opcionales)
Edita `.env`:
- `GEMINI_API_KEY`: clave de la API (gratis en [Google AI Studio](https://aistudio.google.com/apikey), con límites; **no** va incluida en la suscripción Google AI Pro). Sirve para visión de pantalla (`inspect_screen`) y búsqueda web con Gemini 2.5 Flash.
- `GEMINI_MODEL` / `ZARA_GEMINI_LIVE_MODEL`: por defecto `auto` (Zara consulta la API y elige el Flash estable más reciente; evita que se rompa cuando Google retira modelos). Puedes fijar un ID concreto.
- `ANTHROPIC_API_KEY`: para tareas de código/seguridad con Claude. Sin esta clave, si tienes el CLI `claude` instalado y en el `PATH`, Zara lo usa automáticamente como alternativa.

Sin ninguna de las dos, Zara funciona igual de bien **100% offline** con `ZARA_DEFAULT_BRAIN="ollama"`.

### Paso 3b — Gemini con tu suscripción Google AI Pro (sin API key)
La suscripción Google AI Pro/Ultra **no** incluye una clave de API, pero sí da acceso a Gemini a través de **Antigravity CLI** (`agy`), el sustituto oficial de Gemini CLI desde el 18 de junio de 2026. Zara puede usarlo como cerebro:
1. Instala Antigravity CLI siguiendo las instrucciones oficiales de Google y comprueba que `agy` funciona en una terminal.
2. Ejecuta `agy` una vez e inicia sesión con la misma cuenta de Google que tiene la suscripción.
3. En el panel de Zara elige el cerebro **«Gemini · AI Pro»** (o pon `ZARA_DEFAULT_BRAIN="antigravity"` en `.env`). Con `ZARA_AGY_MODEL` puedes fijar un modelo de los que lista `agy models`.

Limitaciones: cada respuesta arranca un proceso de `agy`, así que es más lento que la API (varios segundos); y hay usuarios de AI Pro a los que Antigravity les asigna solo la cuota básica ("Starter Quota") o les rechaza la cuenta (`FREE_TIER_USER_NOT_ELIGIBLE`). Si eso pasa, Zara lo indicará en el panel. Alternativas: la clave gratuita de [Google AI Studio](https://aistudio.google.com/apikey) (solo modelos Flash, con límites) o los 10 $/mes de créditos de Google Cloud que incluye AI Pro a través del Google Developer Program, que desde octubre de 2026 solo se aplican a la API tras haber recargado saldo de prepago.

### Paso 4 — Verificar antes de arrancar
```powershell
python setup_and_run.py --check
```
Esto comprueba la versión de Python, las dependencias, los dispositivos de audio por defecto y si Ollama responde, sin llegar a abrir el HUD.

---

## 4. Despliegue y Uso

### En Windows 10/11
```powershell
# 1. Diagnóstico completo de subsistemas (Audio, VAD, Shell, HUD)
.\setup_and_run.ps1 -Check

# 2. Iniciar ZARA completa con HUD holográfico
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

# 3. Iniciar ZARA
./setup_and_run.sh

# 4. Modo CLI
./setup_and_run.sh --cli
```

---

## 5. Configuración

Zara lee sus ajustes de dos archivos (y de las variables de entorno del sistema, que mandan sobre ambos):

| Archivo | ¿Se sube a GitHub? | Para qué |
|---|---|---|
| `config/ajustes.env` | **Sí** (repositorio público) | Preferencias: cerebro por defecto, voz, transcripción... Es la configuración con la que arranca Zara. |
| `.env` | **No** (`.gitignore`) | Lo privado de este equipo: claves de API y cualquier ajuste que quieras cambiar solo aquí. Gana sobre `config/ajustes.env`. |

Por defecto Zara usa **Gemini con la suscripción Google AI Pro** (`ZARA_DEFAULT_BRAIN="antigravity"`, ver Paso 3b). Lo que elijas en el panel (cerebro, modelo) se guarda en `.env`.

> **Nunca** pongas claves, contraseñas ni datos personales en `config/ajustes.env`: cualquiera puede leer el repositorio.

Si tu `.env` viene de una versión anterior, Zara quita automáticamente (una sola vez, con copia en `.env.bak`) las líneas copiadas de la plantilla antigua que tapaban los ajustes nuevos; tus claves y tus elecciones no se tocan.

---

## 6. Cómo dejarlo funcionando a la perfección

### Usa auriculares, no los altavoces del equipo
Sin cancelación de eco por hardware, el propio audio de Zara puede filtrarse del altavoz al micrófono y confundirse con una interrupción real, cortándola a media frase. Zara ya filtra esto exigiendo un volumen mínimo (`ZARA_VAD_BARGE_IN_MIN_RMS`) y un periodo de gracia al empezar cada frase (`ZARA_VAD_BARGE_IN_GRACE_MS`), pero unos auriculares eliminan el problema de raíz. Si aun así se corta sola, sube `ZARA_VAD_BARGE_IN_MIN_RMS` (p. ej. `0.04`).

### Elige un modelo de Ollama que siga bien las instrucciones
No todos los modelos locales manejan igual el "function calling". Zara es robusta frente a formatos no estándar (JSON embebido en texto, tags `<function>`/`<tool_call>`, o que el modelo confunda el valor de una acción con el nombre de la herramienta), pero cuanto mejor siga instrucciones el modelo, más fiable y rápida será. Con 8 GB de VRAM o más, `qwen2.5-coder:7b` (o superior) va bien. Sin GPU, usa un modelo más pequeño y acepta más latencia.

### Di los nombres de las apps como las conoces, en español
Para abrir aplicaciones ("abre el bloc de notas", "abre la calculadora", "abre el navegador"), Zara ya traduce los nombres comunes en español al ejecutable real de Windows. Si una app no abre, prueba a decir su nombre tal cual aparece en el menú Inicio, o el nombre técnico del ejecutable.

### Si Zara tarda en responder
Mira el log: cada orden imprime líneas `[LATENCIA]` con el tiempo de cada fase:
`STT` (transcripción; `x1.00` = tiempo real), `<cerebro> respondió en` (qué cerebro y cuánto tardó en empezar), `primer token`, `TTS` (síntesis de cada frase) y `turno completo`. La fase con el número más alto es el cuello de botella.
- **STT lento (`x` > 1):** estás en CPU con un modelo demasiado grande. Usa `ZARA_WHISPER_MODEL="base"` (o `tiny`) y borra de tu `.env` las líneas antiguas `ZARA_WHISPER_MODEL=...large-v3-turbo...` y `ZARA_WHISPER_BEAM_SIZE=5` (Zara ya las ignora en CPU salvo `ZARA_WHISPER_FORCE=1`).
- **Primer token lento:** un modelo local grande sin GPU es lo más lento de la cadena; prueba con Gemini (`GEMINI_API_KEY`) o un modelo Ollama más pequeño.

### Comprueba que Ollama responde ANTES de arrancar Zara
```bash
curl http://localhost:11434/api/tags
```
Si no responde nada, arranca `ollama serve` en otra terminal antes de lanzar Zara.

### Ejecuta los tests tras cualquier cambio
```powershell
pip install -r requirements-dev.txt
pytest tests/ -v
```
El CI ejecuta solo los tests que no necesitan audio ni GPU (`test_tools`, `test_brain`, `test_config`); el resto se ejecutan en local. Todos deben pasar en verde antes de dar por buena una modificación.

### Problemas comunes

| Síntoma | Causa probable | Solución |
|---|---|---|
| Zara se corta a media frase sin que hables | Eco del altavoz detectado como interrupción | Usa auriculares, o sube `ZARA_VAD_BARGE_IN_MIN_RMS` |
| No te reconoce bien / tarda en reaccionar | Micrófono lejos o volumen bajo | Acércate al micrófono; revisa `ZARA_VAD_THRESHOLD` |
| "Abre X" dice que funcionó pero no pasa nada | Nombre de la app no resoluble por Windows | Usa el nombre común en español o el nombre del ejecutable |
| Respuestas con JSON crudo en vez de ejecutar la acción | El modelo local alucinó un formato no reconocido | Prueba con un modelo más grande/reciente; revisa los logs (`zara.brain`) |
| Arranca pero no detecta Ollama | `ollama serve` no corre, o `OLLAMA_HOST`/`OLLAMA_MODEL` no coinciden | `ollama list` para ver nombres exactos; `curl $OLLAMA_HOST/api/tags` |
| Corte de más de 1s entre frases de una misma respuesta | Síntesis de voz esperando a la red (EdgeTTS) | Ya mitigado por el pipeline de síntesis adelantada; si persiste, revisa tu conexión |
