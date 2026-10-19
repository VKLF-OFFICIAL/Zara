"""La bola flotante no debe recortar su halo ni su anillo (ui/geometry.py, sin Qt)."""

import math

import pytest

from ui.geometry import HALO_LAYERS, PULSE_MAX, SEGMENT_LENGTH, core_radius, max_extent, orb_layout, window_side


@pytest.mark.parametrize("diameter", [64, 96, 118, 160, 240])
def test_window_contains_everything_that_is_painted(diameter):
    side, r_core, r_seg = orb_layout(diameter)
    half = side / 2
    # núcleo, anillo segmentado y cada capa del halo (con su grosor) deben caber
    assert r_seg + SEGMENT_LENGTH < half
    for width, _alpha in HALO_LAYERS:
        outer = r_core * PULSE_MAX + width * 0.9 + width / 2
        assert outer < half, f"halo de {width}px recortado en d={diameter}"
    assert max_extent(diameter) < half


def test_window_is_larger_than_core_for_default_orb():
    side = window_side(118)
    assert side > 118  # con ventana == diámetro (el bug original) el halo se cortaba
    assert core_radius(118) * 2 < side


def test_layout_scales_monotonically():
    assert window_side(100) < window_side(120) < window_side(200)
    assert math.isclose(core_radius(100), 30.0)
