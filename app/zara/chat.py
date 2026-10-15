from __future__ import annotations
import sys

from .backend import Backend


def main() -> int:
    backend = Backend()
    backend.ensure_ready_text()
    print("=== Zara (modo texto) ===  escriba 'salir' para terminar.")
    print(f"Cerebro activo: {backend.active_brain()}  |  disponibles: {', '.join(backend.brain_names())}\n")
    while True:
        try:
            texto = input("VK> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not texto:
            continue
        if texto.lower() in ("salir", "exit", "quit"):
            break
        res = backend.respond(texto, verified=True)
        print(f"Zara [{res.get('brain','')}]> {res['reply']}\n")
        if backend.speak_enabled:
            backend.speak(res["reply"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
