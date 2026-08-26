"""Plasma — smoothly cycling sine-interference colour field.

Doubles as a colour test: it sweeps the full hue circle and the full value
range, so banding, dead channels or a mis-wired panel show up immediately.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from ..scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState, Scene

_TAU = 2.0 * np.pi


class Plasma(Scene):
    name = "plasma"
    description = "Cycling sine-interference colour field (also a colour test)."

    def __init__(self) -> None:
        rows, cols = np.mgrid[0:PANEL_HEIGHT, 0:PANEL_WIDTH]
        self._x = (cols / PANEL_WIDTH).astype(np.float32)
        self._y = (rows / PANEL_HEIGHT).astype(np.float32)
        self._radius = np.sqrt((self._x - 0.5) ** 2 + (self._y - 0.5) ** 2).astype(np.float32)

    def render(self, t: float, home: HomeState, controls: Controls) -> Image.Image:
        x, y, radius = self._x, self._y, self._radius
        field = np.sin(x * 8.0 + t * 0.9)
        field += np.sin(y * 6.0 - t * 0.7)
        field += np.sin((x + y) * 5.0 + t * 1.3)
        field += np.sin(radius * 18.0 - t * 2.1)
        field *= 0.25  # back to roughly [-1, 1]

        phase = field * np.pi + t * 0.35
        red = np.cos(phase) * 0.5 + 0.5
        green = np.cos(phase + _TAU / 3.0) * 0.5 + 0.5
        blue = np.cos(phase + 2.0 * _TAU / 3.0) * 0.5 + 0.5

        rgb = np.dstack((red, green, blue))
        rgb = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
        return Image.fromarray(rgb, mode="RGB")


SCENE = Plasma()
