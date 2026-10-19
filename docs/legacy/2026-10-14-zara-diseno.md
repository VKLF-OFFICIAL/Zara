# Zara — Asistente de voz personal para el Señor VK

**Fecha:** 2026-10-14
**Estado:** Diseño aprobado, pendiente de plan de implementación
**Equipo:** Fedora 44 · KDE Plasma · Wayland · 12 núcleos · 46 GB RAM · sin GPU NVIDIA

---

## 1. Propósito

Zara es un asistente personal por voz e interfaz visual, al estilo del JARVIS de
Iron Man pero mejorado, al servicio exclusivo del Señor VK. Escucha una palabra
de activación, transcribe la voz en local, la envía a un cerebro de IA
intercambiable (Claude, Gemini, modelos locales o gratuitos), aplica una capa de
seguridad propia sobre cualquier acción en el sistema, y responde con voz
femenina suave además de mostrarlo en una esfera flotante.

### Criterios de éxito

1. Zara arranca con la sesión y escucha de forma continua **solo en local**.
2. Responde a «Zara» y **solo obedece al Señor VK** (verificación de voz).
3. Cambia de cerebro a petición del usuario; puede proponer otro para una tarea.
4. **Ninguna acción crítica se ejecuta sin verificación del estado real y doble
   confirmación del usuario.**
5. La esfera flotante refleja el estado (reposo, escucha, pensar, hablar,
   confirmación) sin parpadeos en Wayland.
6. Memoria persistente entre sesiones, editable por el usuario.
7. Cada parte se valida con pruebas antes de integrarse.

---

## 2. Identidad y trato (persona)

- **Nombre:** Zara (voz femenina).
- **Trato al usuario:** siempre formal y respetuoso, alternando con naturalidad
  «Señor», «Señor VK», «VKLF», «VK».
- **Tono:** cercano pero profesional, eficiente; cálido, nunca servil en exceso
  ni frío. Resume por voz; el detalle largo va al panel.
- **Hábitos:** confirma antes de lo delicado, avisa cuando algo tardará, no
  adivina cuando hay ambigüedad (pregunta).
- Definido en `config/persona.md`, editable por el usuario.

---

## 3. Arquitectura general

Dos procesos independientes que se comunican por un socket local (Unix domain
socket + JSON-RPC). Si la interfaz se cierra, el motor sigue funcionando por voz.

```
┌──────────────────────┐        socket local        ┌─────────────────────┐
│   zara-core (Python) │  <────  JSON-RPC  ────>     │  zara-ui (Qt/QML)   │
│   servicio de usuario│                             │  esfera flotante     │
└──────────────────────┘                             └─────────────────────┘
```

### 3.1 zara-core — módulos

| Módulo | Función | Tecnología |
|---|---|---|
| **Oído** | Captura de micrófono + detección de voz (VAD) | PipeWire (`pw-cat`/`sounddevice`) + Silero VAD |
| **Activación** | Palabra «Zara» + atajo de teclado de respaldo | openWakeWord (modelo propio para «Zara») |
| **Transcripción** | Voz → texto, local y privado (español) | faster-whisper (CPU, modelo `small`/`medium`) |
| **Identidad** | Verifica que el hablante es el Señor VK | SpeechBrain ECAPA-TDNN (embeddings) |
| **Cerebro** | Selector de modelos con un adaptador por proveedor | Ver §5 |
| **Guardián** | Clasifica y controla toda acción sobre el sistema | Motor de reglas propio (ver §6) |
| **Voz** | Síntesis de voz femenina suave | Edge TTS (principal) + Piper (offline) |
| **Memoria** | Lee/escribe recuerdos relevantes | Markdown en `memoria/` (ver §7) |

### 3.2 zara-ui — interfaz (Qt 6 / QML)

Esfera flotante nativa de KDE (ver §4). Capa de presentación pura: todo el
estado y la lógica viven en el core.

---

## 4. Interfaz visual

### Esfera flotante
- ~80 px, **por defecto arriba a la derecha**, arrastrable y reubicable en
  cualquier parte de la pantalla **y entre monitores** (posición recordada en
  config), semitransparente, siempre encima (`layer-shell` de Wayland vía capa
  overlay de KDE).
- Avatar de Zara (imagen del usuario) dentro de un anillo cian con *glitch* sutil.
- **Estados:** reposo (brillo tenue) · escucha (latido cian + onda de voz) ·
  pensar (giro cian→violeta→rosa) · hablar (onda al ritmo de la voz) ·
  confirmación 🟡 ámbar / 🔴 rojo.

### Panel desplegable (clic o «Zara, abre el panel»)
- **Conversación:** chat con entrada de texto además de voz.
- **Barra de estado:** cerebro activo (selector manual), micrófono on/off,
  verificación del Señor VK.
- **Tarjetas de confirmación:** explicación + comando exacto + ruta de copia de
  seguridad + botones Confirmar / Cancelar.
- **Acción en curso** con botón Detener.
- **Accesos:** memoria, tareas, registros.

### Paleta (de la imagen de referencia)
- Cian `#2EF2E6` (principal), violeta `#5A4BFF`, rosa neón `#E070FF`,
  fondo negro `#07080C`. *Glitch* sutil en bordes y transiciones.

### Control
- Voz: «Zara…».
- Teclado: `Super+Z` activar sin hablar; atajo para silenciar micrófono.
- Icono en bandeja del sistema: salir / pausar / reiniciar.

---

## 5. Cerebros intercambiables

Interfaz común `Brain` con un adaptador por proveedor. Todos reciben persona +
memoria relevante + petición, y devuelven texto y/o peticiones de herramienta
que **siempre** pasan por el Guardián.

| Cerebro | Acceso | Notas |
|---|---|---|
| **Claude** | `claude` CLI (suscripción del usuario) | Herramientas nativas de Claude Code bajo un *hook* que consulta al Guardián |
| **Gemini** | Gemini CLI (suscripción Pro, login Google) | Igual patrón de *hook* |
| **Local (Ollama)** | Ollama en `/mnt/almacen/IA/ollama-models` y `ModelosGGUF` (p. ej. Qwen3-27B-Obliterated) | Usa las herramientas de Zara, que pasan por el Guardián |
| **Gratuitos (opcional)** | Groq / OpenRouter (plan gratuito, API key) | Opcional, activable en config |

### Selección de cerebro
1. **Manual:** «Zara, usa Claude/Gemini/Qwen». El activo se muestra en la esfera.
2. **Sugerencia:** Zara puede proponer otro cerebro para una tarea; el usuario
   confirma el cambio. Nunca cambia sola sin avisar.

### Separación clave: seguridad del sistema vs. contenido del modelo
- El **Guardián** (§6) protege el *hardware y los datos* del usuario. Es
  independiente del modelo y se aplica con **cualquier** cerebro.
- El **contenido** de las respuestas lo determina cada modelo: los modelos de
  Anthropic siguen sus propias pautas; los modelos locales del usuario corren en
  su máquina y Zara no filtra su salida. El selector permite alternar libremente.
- Zara da soporte pleno a seguridad ofensiva **autorizada** (bug bounty con
  permiso, CTF, pentesting con alcance, auditoría de código propio), apoyándose
  en las carpetas y skills existentes del usuario (`BugBounty/`,
  `Claude-BugHunter/`).

---

## 6. Guardián (seguridad del sistema)

Toda acción que el cerebro quiera ejecutar sobre el sistema (comando de shell,
edición/borrado de archivo, red, proceso) se clasifica **antes** de ejecutarse.

### Niveles

| Nivel | Ejemplos | Comportamiento |
|---|---|---|
| 🟢 **Seguro** | Leer archivos, buscar, abrir apps, volumen, música, trabajar dentro de `~/Zara/espacio/` | Ejecuta directamente |
| 🟡 **Confirmar** | Modificar/mover/borrar fuera de `~/Zara/`, instalar paquetes, cerrar procesos, enviar algo a internet/terceros | Explica + pregunta. Confirmación por voz verificada o botón. 30 s sin respuesta → cancela |
| 🔴 **Crítico** | `sudo`, borrado masivo, config del sistema, credenciales, `git push --force` | **Verificación del estado real** (la ruta existe y es la correcta, el efecto es el esperado) + **copia de seguridad** en `copias/` + **doble confirmación**: frase «Confirmo, Zara» (voz verificada) **y** clic en pantalla |
| ⛔ **Bloqueado** | Formatear discos, `dd` sobre dispositivos, `rm -rf /`, modificar `/boot` o `/etc/fstab` | Nunca lo hace. Explica cómo hacerlo manualmente si de verdad se requiere |

### Garantías para acciones críticas (requisito explícito del usuario)
1. **Verificación previa al 100%:** antes de actuar, el Guardián comprueba el
   estado real del sistema (existencia, ruta, alcance, reversibilidad) y
   construye el comando exacto. Si algo no cuadra, **no actúa**: pregunta.
2. **Copia de seguridad** de lo que se vaya a modificar/borrar, en `copias/`.
3. **Doble confirmación** (voz verificada + clic). Respuesta ambigua ⇒ cancela.
4. Registro completo en `registros/`.

### Alcance de archivos
- Dentro de `~/Zara/`: libre, salvo acciones críticas.
- Fuera de `~/Zara/`: solo lee, o actúa si el usuario lo pide explícitamente;
  cualquier modificación es al menos 🟡.

### Implementación
- Reglas en `config/guardian.toml` (patrones + nivel), editables.
- Para Claude/Gemini CLI: *hook* `PreToolUse` que consulta al Guardián por
  socket y bloquea/pide confirmación según el nivel.
- Para cerebros locales: las herramientas de Zara llaman al Guardián antes de
  actuar.
- Auditoría append-only en `registros/acciones-AAAA-MM-DD.jsonl`.

---

## 7. Memoria

```
memoria/
├── perfil.md          quién es el Señor VK, trato, datos estables
├── preferencias.md    gustos y ajustes de comportamiento
├── proyectos/         un .md por proyecto del usuario
└── diario/            AAAA-MM-DD.md: resumen de cada día
```

- **Automática:** tras cada conversación, actualiza perfil/preferencias/proyecto
  y añade al diario.
- **Bajo demanda:** «Zara, recuerda que…» / «Zara, olvida…».
- **Carga relevante:** al iniciar una conversación carga solo los recuerdos
  pertinentes (búsqueda por palabras clave sobre los .md), no todo.
- Todo en Markdown, legible y editable por el usuario.

---

## 8. Reconocimiento de voz del Señor VK

- **Alta:** en la instalación, el usuario graba varias frases → se calcula un
  *embedding* de referencia (ECAPA) guardado **cifrado en el SSD**
  (`~/.local/share/zara/voz/`, permisos `600`), no en el disco NTFS.
- **Verificación:** cada petición se compara con la referencia; por debajo del
  umbral de confianza, Zara no obedece y lo registra.
- **Umbral ajustable** tras la instalación para equilibrar falsos
  positivos/negativos.
- **Nota de seguridad:** la verificación de voz distingue al usuario de otras
  personas o del audio ambiente, pero no es infalible frente a una grabación de
  su voz; por eso las acciones 🟡/🔴 **siempre** requieren confirmación además
  de la voz.

---

## 9. Ubicación y entorno

| Qué | Dónde | Motivo |
|---|---|---|
| Datos de Zara (memoria, espacio, tareas, registros, copias, config, voz pública) | `/mnt/almacen/ZARA/` (enlace `~/Zara`) | 414 GB libres; aquí vive el proyecto |
| Entorno Python (venv) | `~/.local/share/zara/venv` (SSD) | NTFS no da permisos ni rendimiento para venv |
| Huella de voz (cifrada) | `~/.local/share/zara/voz/` (SSD, `600`) | Dato sensible que requiere permisos Linux |
| Modelos locales | `/mnt/almacen/IA/ollama-models`, `/mnt/almacen/ModelosGGUF` | Ya existentes |

- **Python 3.12** vía `uv` (aislado; el sistema usa 3.14, incompatible con
  varias librerías de voz).
- Si `/mnt/almacen` no está montado, Zara avisa en vez de fallar en silencio.
- Servicio `systemd --user` para arranque con la sesión.

---

## 10. Pila tecnológica (resumen)

- **Lenguaje núcleo:** Python 3.12 (uv).
- **Audio:** PipeWire, sounddevice, Silero VAD.
- **Wake word:** openWakeWord.
- **STT:** faster-whisper.
- **Verificación de voz:** SpeechBrain (ECAPA-TDNN).
- **TTS:** Edge TTS (online) + Piper (offline).
- **Cerebros:** `claude` CLI, Gemini CLI, Ollama, opcional Groq/OpenRouter.
- **UI:** Qt 6 / QML (PySide6).
- **IPC:** Unix socket + JSON-RPC.
- **Servicio:** systemd --user.

---

## 11. Riesgos y límites (honestidad)

1. El STT puede entender mal alguna frase; ante la duda, Zara pregunta.
2. La verificación de voz no resiste una grabación del usuario ⇒ confirmaciones
   obligatorias en 🟡/🔴.
3. El modelo local (Qwen 27B) en CPU irá lento (pocas palabras/seg).
4. «Sin fallos» se persigue con pruebas por módulo, no se garantiza como absoluto.
5. Edge TTS requiere internet; Piper es el respaldo offline.

---

## 12. Orden de construcción propuesto

1. Esqueleto: estructura, venv, config, socket core↔ui, servicio systemd.
2. Guardián + registros + copias (con pruebas) — **la base de seguridad primero**.
3. Voz (TTS) y esfera básica con estados.
4. Oído + VAD + wake word + STT.
5. Verificación de voz del Señor VK.
6. Cerebros: Claude → Gemini → Ollama → (opcional) gratuitos.
7. Memoria.
8. Panel completo, tarjetas de confirmación, atajos, bandeja.
9. Integración, ajuste de umbrales y pruebas de extremo a extremo.

El plan detallado de implementación se redacta tras la aprobación de este diseño.
