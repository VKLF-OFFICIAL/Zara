"""Tareas en segundo plano.

Las peticiones que van al agente (crear un script, instalar algo, arreglar
código…) pueden tardar minutos. Si se ejecutaran en el hilo de escucha, Zara
se quedaría sorda mientras tanto. Aquí se lanzan en hilos aparte: Zara acusa
«me pongo con ello», sigue escuchando y avisa por callback al terminar.
"""
from __future__ import annotations
import logging
import threading

log = logging.getLogger("zara.tasks")


class BackgroundTasks:
    def __init__(self):
        self._lock = threading.Lock()
        self._active: dict[int, str] = {}
        self._next = 0

    def active(self) -> list[str]:
        with self._lock:
            return list(self._active.values())

    def count(self) -> int:
        with self._lock:
            return len(self._active)

    def submit(self, name: str, fn, on_done=None) -> int:
        """Lanza fn() en un hilo. Al acabar llama on_done(name, resultado).

        fn no debe lanzar (si lanza, el resultado será None y queda en el log).
        """
        with self._lock:
            tid = self._next
            self._next += 1
            self._active[tid] = name

        def run():
            result = None
            try:
                result = fn()
            except Exception:
                log.exception("tarea en segundo plano %r falló", name)
            finally:
                with self._lock:
                    self._active.pop(tid, None)
                if on_done is not None:
                    try:
                        on_done(name, result)
                    except Exception:
                        log.exception("callback de la tarea %r falló", name)

        threading.Thread(target=run, name=f"zara-task-{tid}", daemon=True).start()
        return tid
