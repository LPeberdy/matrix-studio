"""Editable user plasma scene.

A smooth sine-interference field. Keeping this in config/scenes makes the
shipped plasma easy to modify without rebuilding the add-on.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from matrix_studio.scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState

_TAU = 2.0 * np.pi
_ROWS, _COLS = np.mgrid[0:PANEL_HEIGHT, 0:PANEL_WIDTH]
_X = (_COLS / PANEL_WIDTH).astype(np.float32)
_Y = (_ROWS / PANEL_HEIGHT).astype(np.float32)
_RADIUS = np.sqrt((_X - 0.5) ** 2 + (_Y - 0.5) ** 2).astype(np.float32)


def render(t: float, home: HomeState, controls: Controls) -> Image.Image:
    field = np.sin(_X * 8.0 + t * 0.9)
    field += np.sin(_Y * 6.0 - t * 0.7)
    field += np.sin((_X + _Y) * 5.0 + t * 1.3)
    field += np.sin(_RADIUS * 18.0 - t * 2.1)
    field *= 0.25

    phase = field * np.pi + t * 0.35
    red = np.cos(phase) * 0.5 + 0.5
    green = np.cos(phase + _TAU / 3.0) * 0.5 + 0.5
    blue = np.cos(phase + 2.0 * _TAU / 3.0) * 0.5 + 0.5

    rgb = np.dstack((red, green, blue))
    rgb = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, mode="RGB")
