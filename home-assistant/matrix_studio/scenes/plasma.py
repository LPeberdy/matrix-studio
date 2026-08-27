"""Plasma — a coherently drifting sine-interference colour field.

Doubles as a colour test: it sweeps the full hue circle and the full value
range, so banding, dead channels or a mis-wired panel show up immediately.

Every wave samples the same translated coordinate system.  The whole field
therefore moves at one constant velocity instead of independent temporal
frequencies forming a perceptible slow/fast beat.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from ..scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState, Scene

_TAU = 2.0 * np.pi
_X_PIXELS_PER_SECOND = 6.0
_Y_PIXELS_PER_SECOND = 3.0


class Plasma(Scene):
    name = "plasma"
    description = "Coherently drifting sine-interference field (also a colour test)."

    def __init__(self) -> None:
        rows, cols = np.mgrid[0:PANEL_HEIGHT, 0:PANEL_WIDTH]
        self._x_phase = (cols / PANEL_WIDTH * _TAU).astype(np.float32)
        self._y_phase = (rows / PANEL_HEIGHT * _TAU).astype(np.float32)

    def render(self, t: float, home: HomeState, controls: Controls) -> Image.Image:
        x = self._x_phase - t * _X_PIXELS_PER_SECOND * _TAU / PANEL_WIDTH
        y = self._y_phase - t * _Y_PIXELS_PER_SECOND * _TAU / PANEL_HEIGHT

        # Integer spatial harmonics make the field tile seamlessly at the
        # panel edges.  More importantly, every component uses the same x/y
        # translation, so their interference features travel together.
        field = np.sin(2.0 * x + y)
        field += np.sin(x - 3.0 * y)
        field += np.sin(3.0 * x + 2.0 * y)
        field += np.sin(4.0 * x - y)
        field *= 0.25  # back to roughly [-1, 1]

        phase = field * np.pi
        red = np.cos(phase) * 0.5 + 0.5
        green = np.cos(phase + _TAU / 3.0) * 0.5 + 0.5
        blue = np.cos(phase + 2.0 * _TAU / 3.0) * 0.5 + 0.5

        rgb = np.dstack((red, green, blue))
        rgb = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
        return Image.fromarray(rgb, mode="RGB")


SCENE = Plasma()
