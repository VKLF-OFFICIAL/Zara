# Skills declarativas de Zara

Añada capacidades a Zara **sin tocar el código**: cree aquí un archivo `.toml`
por skill. Zara los carga al arrancar. Las frases se comparan sin distinguir
mayúsculas ni acentos.

## Tipos

### `decir` — responde una frase fija
```toml
nombre = "saluda"
frases = ["buenos dias", "buenas"]
tipo = "decir"
respuesta = "Buenos días, Señor VK."
```

### `shell` — ejecuta un comando (pasa por el Guardián)
```toml
nombre = "mi ip pública"
frases = ["cual es mi ip", "mi ip publica"]
tipo = "shell"
comando = "curl -s ifconfig.me"
confirmar = false   # true = pide confirmación aunque el Guardián lo vea seguro
```
El Guardián clasifica el comando igual que cualquier otro: si es delicado o
crítico, pedirá confirmación y hará copia de seguridad.

### `prompt` — pregunta al cerebro con una plantilla
```toml
nombre = "resume web"
frases = ["^resume (.+)$"]
tipo = "prompt"
prompt = "Resume en 3 frases el contenido de: {arg}"
```

## Argumentos
Si una frase lleva un grupo de captura `(...)`, su contenido queda disponible
como `{arg}` en `comando`, `respuesta` o `prompt`. `{0}` es toda la coincidencia.
Las frases con símbolos de regex (`^ $ ( ) [ ] + * ? \`) se tratan como
expresiones regulares; el resto, como texto que debe aparecer en la orden.

## Prioridad
Las órdenes integradas de Zara (volumen, música, abrir apps…) van primero.
Luego las skills. Lo que no case con nada va al cerebro.
