# Zara — Asistente personal del Señor VK

Asistente por voz + esfera flotante, cerebros de IA intercambiables, memoria y un
Guardián de seguridad. Todo vive en `/mnt/almacen/ZARA/` (enlace `~/Zara`).

## Cómo usarla

| Comando | Qué hace |
|---|---|
| `zara-enroll` | **Hágalo primero.** Inscribe su voz (lee 4 frases) para que solo le obedezca a usted. |
| `zara` | Lanza Zara: esfera flotante (arriba a la derecha, arrastrable) + voz. Diga «Zara, …». |
| `zara-chat` | Modo texto en la terminal (sin micrófono). |

Zara arranca sola con la sesión (autostart de KDE ya instalado).

## Comandos de voz / texto
Puede decir «Zara, …» de seguido, o «Zara» → (¿Sí, Señor VK?) → la orden.
También responde a **«Hey Jarvis, …»** (detector neural, más fiable con música sonando).
- **Conversar:** «Zara, ¿…?» — responde el cerebro activo (Claude puede consultar la web).
- **Mirar la pantalla:** «mira la pantalla», «¿qué ves en la pantalla?», «lee la pantalla y dime qué error sale». Captura, la interpreta Claude y **borra la captura** al terminar.
- **Tareas largas:** cuando una petición es una tarea («créame un script», «instala…»), Zara se pone con ella **en segundo plano**, sigue escuchando y **le avisa al terminar**.
- **Cambiar cerebro:** «Zara, usa qwen» / «usa claude» / «usa zara».
- **Recordar:** «Zara, recuerda que …».
- **Abrir apps / webs:** «abre firefox», «abre el navegador», «abre la terminal», «abre vklf.es».
- **Volumen:** «sube / baja el volumen», «pon el volumen al 40», «silencia», «quita el silencio».
- **Música:** «reproduce música en Spotify», «pausa la música», «reanuda», «siguiente canción», «canción anterior».
- **Cerrar apps:** «cierra spotify», «cierra el navegador» (siempre pide confirmación en el chat).
- **Carpetas:** «abre la carpeta descargas / documentos / imágenes / música / vídeos / escritorio».
- **Buscar:** «busca recetas de paella», «busca en YouTube música lofi».
- **Hora y fecha:** «¿qué hora es?», «¿qué día es hoy?».
- **Chat:** «abre el chat». **Callar:** botón ■ del chat o «Detener voz» en la bandeja.
- **Ejecutar comando:** «Zara, ejecuta …» — el Guardián decide si pide confirmación.

## Interfaz
- El orbe está siempre encima de todas las ventanas. Arrástrelo para moverlo; un clic abre el chat.
- El chat se abre pegado al orbe y le sigue al moverlo (script de KWin `zara-panel`).
- Si lanza `zara` estando ya abierta, solo se despliega el chat (instancia única).
- Registro de funcionamiento: `registros/zara.log`.

## Configuración
`config/zara.toml` — voz, modelo STT, umbral de reconocimiento, cerebro por defecto,
micrófono, etc. `config/persona.md` — cómo habla Zara. `config/guardian.toml` — reglas
de seguridad.

## Skills (capacidades a medida)
Puede añadir capacidades **sin tocar el código**: cree un `.toml` en
`config/skills/`. Tipos: `decir` (frase fija), `shell` (comando, pasa por el
Guardián) y `prompt` (plantilla al cerebro). Ejemplo ya incluido: «¿cuál es mi
IP pública?». Vea `config/skills/LEEME.md`.

## MCP (herramientas extra para el agente)
Copie `config/mcp.json.ejemplo` a `config/mcp.json` y declare sus servidores MCP.
Zara se los pasa al cerebro **agente** (y solo esos), que gana así esas
herramientas. Si el archivo no existe, no se usa MCP.

## Activación por voz
- **«Zara»** (reconocimiento fonético) y **«Hey Jarvis»** (modelo neural robusto a
  ruido/música). Se elige en `config/zara.toml` → `motor_wake = "texto" | "neural"
  | "ambos"` (por defecto `ambos`), `umbral_wake_neural` (0–1).

## Control del equipo con cualquier IA (manos)
Zara puede **actuar en el sistema con el cerebro que tenga activo** —Claude, Gemini
o un modelo local por Ollama—, no solo con el agente Claude. Cuando el cerebro
necesita ejecutar algo, lo pide y Zara lo ejecuta **pasándolo por el Guardián**
(que puede pedir confirmación o bloquearlo) y le devuelve el resultado. Así la
seguridad no depende del modelo (un local sin censura también queda bajo el
Guardián). Con Claude activo, las tareas grandes siguen yendo al agente Claude
(control nativo). Se desactiva con `manos = false` en `config/zara.toml`.

## Sonido
Zara habla por **la salida de audio activa del sistema en cada momento**
(`salida_audio = ""`). Si cambia el altavoz/cascos por defecto, ella le sigue sin
reiniciar. Puede fijar una salida concreta poniendo su nombre en `salida_audio`.

## Seguridad (Guardián)
Clasifica toda acción en SAFE / CONFIRM / CRITICAL / BLOCKED. Lo crítico exige copia de
seguridad + doble confirmación; lo prohibido no se hace nunca. Independiente del cerebro.

## Capacidades del cerebro Claude (modo agente)
Por defecto Claude actúa como **agente con control del equipo**: lee y escribe archivos,
ejecuta comandos, programa, automatiza e investiga, con las mismas herramientas que Claude Code.
Trabaja en `claude_directorio` (por defecto su carpeta personal). Para solo conversar y
consultar, ponga `cerebro_claude_modo = "consulta"` en `config/zara.toml`.
Aviso: en modo agente el Guardián de Zara no inspecciona lo que hace Claude por dentro;
es su propio Claude Code con permisos completos. Las órdenes directas de Zara
(«ejecuta…», «cierra…») sí pasan por el Guardián.

## Velocidad y agente
Para que responda rápido, la charla usa **Claude haiku** (~2,5 s). Cuando la petición es una
**tarea** («créame un script», «instala…», «arregla…», «modo agente»), Zara pasa sola al cerebro
**«agente»** (control total del equipo), avisa de que se pone con ello y puede tardar. Puede forzar
con «usa agente» / «usa claude».

## Cerebros
Claude (suscripción), Gemini (requiere instalar su CLI y login), y locales por Ollama
(qwen3.8-27b-obliterated, zara, llama3.2, etc.). El activo se cambia por voz o en el panel.
