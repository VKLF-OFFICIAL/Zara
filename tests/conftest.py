import os
import sys
import tempfile
from pathlib import Path


def pytest_configure(config):
    """En Windows, evita el WinError 5 por carpetas bloqueadas en %TEMP%\\pytest-of-*
    y asegura que los directorios temporales de tests queden fuera de la ruta de Zara
    (ya que BASE_DIR está protegida por el guardarraíl de seguridad)."""
    if not getattr(config.option, "basetemp", None) and sys.platform == "win32":
        tmp_root = Path(tempfile.gettempdir()) / "zara_pytest_workspace"
        tmp_root.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(tmp_root)
