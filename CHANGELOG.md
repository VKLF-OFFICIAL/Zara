# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Mientras la versión sea
`0.y.z` no hay API estable: cualquier versión puede romper cosas.

## [Sin publicar]

### Añadido
- Cerebro **«Gemini · AI Pro»** vía Antigravity CLI (`agy -p`), sin API key.
- Panel rediseñado como chat: burbujas, cabecera con avatar y estado, chips de herramientas que pasan
  de «⋯» a «✓», tarjeta de confirmación, caja de escritura con botón de enviar y tecla Esc.
- Registros `[LATENCIA]` por fase: cerebro usado y su tiempo de respuesta, y síntesis de voz.

### Seguridad
- Los argumentos que se pasan a CLIs `.cmd` en Windows (claude, agy) se neutralizan: un `&` o `|`
  en el texto ya no puede ejecutar comandos a través de cmd.exe.

### Corregido
- El título del panel se cortaba al mostrar la versión.
- Cambiar o añadir un modelo de IA ya no obliga a reiniciar Zara: al abrir el panel o cambiar de
  cerebro se vuelven a consultar los modelos de Ollama (un `ollama pull` aparece al momento) y los
  de Gemini que ofrece la API; un modelo fijado a mano que no está en la lista se muestra igual, y
  cambiar el modelo de Gemini Live con la sesión abierta la reconecta con el modelo nuevo.

## [0.3.0-alpha]

Primera versión con número propio. Antes se anunciaba como "v2.0", pero no hay ni una 1.0 estable.

### Añadido
- Confirmación hablada (sí/no) de acciones sensibles.
- Descubrimiento automático del modelo Gemini / Gemini Live (`auto`).
- Guardarraíl ampliado y rutas protegidas en `file_manager`.
- CI en GitHub Actions y registro de latencia (`[LATENCIA]`) en el log.

### Cambiado
- Whisper elige el modelo según el dispositivo (`small` en CPU, `large-v3-turbo` en GPU) y limita el
  haz a 1 en CPU; el silencio que cierra una frase baja de 700 a 500 ms.
- La primera frase de cada respuesta empieza a sonar antes (mínimo de 18 caracteres).
- Ollama mantiene el modelo cargado 30 min; Gemini 2.5 Flash responde sin "pensar".
- `requests`/`urllib` → `httpx`; `web_search` usa `ddgs`.
- Versión única en `config.py` (`VERSION`, `RELEASE_STAGE`).

### Corregido
- Icono y bola flotante recortados: la ventana era más pequeña que el halo; avatar e icono rehechos
  con el anillo completo y fondo transparente.
- Fallback que ejecutaba el texto del usuario como comando de shell.
- Modelo Claude por defecto retirado; modelos Live retirados.

[0.3.0-alpha]: https://github.com/VKLF-OFFICIAL/Zara/releases
