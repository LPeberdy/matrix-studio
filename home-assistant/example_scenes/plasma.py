"""Editable coherent-motion plasma scene.

A smooth sine-interference field that drifts at one constant velocity. Keeping
this in config/scenes makes the shipped plasma easy to modify without rebuilding
the add-on.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from matrix_studio.scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState

_TAU = 2.0 * np.pi
_X_PIXELS_PER_SECOND = 6.0
_Y_PIXELS_PER_SECOND = 3.0
_ROWS, _COLS = np.mgrid[0:PANEL_HEIGHT, 0:PANEL_WIDTH]
_X_PHASE = (_COLS / PANEL_WIDTH * _TAU).astype(np.float32)
_Y_PHASE = (_ROWS / PANEL_HEIGHT * _TAU).astype(np.float32)


def render(t: float, home: HomeState, controls: Controls) -> Image.Image:
    x = _X_PHASE - t * _X_PIXELS_PER_SECOND * _TAU / PANEL_WIDTH
    y = _Y_PHASE - t * _Y_PIXELS_PER_SECOND * _TAU / PANEL_HEIGHT

    field = np.sin(2.0 * x + y)
    field += np.sin(x - 3.0 * y)
    field += np.sin(3.0 * x + 2.0 * y)
    field += np.sin(4.0 * x - y)
    field *= 0.25

    phase = field * np.pi
    red = np.cos(phase) * 0.5 + 0.5
    green = np.cos(phase + _TAU / 3.0) * 0.5 + 0.5
    blue = np.cos(phase + 2.0 * _TAU / 3.0) * 0.5 + 0.5

    rgb = np.dstack((red, green, blue))
    rgb = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, mode="RGB")
