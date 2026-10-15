from __future__ import annotations
import sys


def main() -> int:
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication(sys.argv)
    from .ui.app import ZaraApp
    if ZaraApp.notify_running_instance():
        print("Zara ya está en marcha: le he pedido que muestre el chat.")
        return 0
    from .backend import Backend
    backend = Backend()
    return ZaraApp(backend).run()


if __name__ == "__main__":
    sys.exit(main())
