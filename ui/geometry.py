"""Geometría de la bola flotante (sin dependencia de Qt, para poder testearla).

La bola dibuja un halo difuso y un anillo segmentado MÁS GRANDES que el núcleo con
el avatar. Si la ventana mide lo mismo que el núcleo, Qt recorta esas capas por los
cuatro lados y el círculo "no entra". Aquí se calcula el tamaño de ventana necesario.
"""

from __future__ import annotations

import math
from typing import Tuple

CORE_RATIO = 0.30          # radio del núcleo respecto al diámetro de la bola
HALO_LAYERS = ((22, 26), (14, 55), (7, 110))  # (grosor del trazo, alfa)
PULSE_MAX = 1.16           # 1 + 0.16 * nivel máximo
HALO_OFFSET = 0.9          # desplazamiento del halo = grosor * HALO_OFFSET
SEGMENT_RATIO = 1.55       # radio del anillo segmentado respecto al núcleo
SEGMENT_LENGTH = 5
MARGIN = 4                 # holgura extra para el antialiasing


def core_radius(diameter: float) -> float:
    return diameter * CORE_RATIO


def max_extent(diameter: float) -> float:
    """Distancia máxima al centro a la que se pinta algo (peor caso del pulso)."""
    r = core_radius(diameter)
    halo = max(r * PULSE_MAX + width * HALO_OFFSET + width / 2 for width, _ in HALO_LAYERS)
    segments = r * SEGMENT_RATIO + SEGMENT_LENGTH + 1
    wave = r + 7 + 5 + 9 + 1  # onda reactiva de SPEAKING/LISTENING
    return max(halo, segments, wave)


def window_side(diameter: float) -> int:
    """Lado (cuadrado) de la ventana para que nada se recorte."""
    return int(math.ceil(2 * max_extent(diameter))) + MARGIN


def orb_layout(diameter: float) -> Tuple[int, float, float]:
    """(lado de ventana, radio del núcleo, radio del anillo segmentado)."""
    r = core_radius(diameter)
    return window_side(diameter), r, r * SEGMENT_RATIO
