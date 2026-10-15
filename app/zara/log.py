from __future__ import annotations
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(registros: Path, level: int = logging.INFO) -> logging.Logger:
    """Log a registros/zara.log (rota a 2 MB × 3) y a stderr."""
    root = logging.getLogger("zara")
    if root.handlers:
        return root
    root.setLevel(level)
    registros.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(registros / "zara.log", maxBytes=2_000_000,
                             backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(sh)
    return root
