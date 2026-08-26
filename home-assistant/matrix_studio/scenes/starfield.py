"""Starfield — a warp-speed particle field with motion trails.

Stateful between frames (particle positions plus a decaying trail buffer), so
it is also the reference example of how to write a scene that integrates over
time without assuming a fixed frame interval: `dt` is derived from `t` and
clamped, which keeps it stable if the engine stutters, is stepped by tests, or
is scrubbed by the preview tool.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from ..scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState, Scene

STAR_COUNT = 160
#: Largest time step honoured in one frame; protects against a long stall
#: teleporting every particle across the panel at once.
MAX_DT = 0.1
TRAIL_DECAY = 0.80


class Starfield(Scene):
    name = "starfield"
    description = "Warp-speed particle starfield with trails."

    def __init__(self, seed: int = 20240607) -> None:
        self._rng = np.random.default_rng(seed)
        self._trails = np.zeros((PANEL_HEIGHT, PANEL_WIDTH, 3), dtype=np.float32)
        self._last_t: float | None = None
        self._spawn(np.ones(STAR_COUNT, dtype=bool), initial=True)

    def _spawn(self, mask: np.ndarray, initial: bool = False) -> None:
        count = int(mask.sum())
        if initial:
            self._x = self._rng.uniform(-1.0, 1.0, STAR_COUNT).astype(np.float32)
            self._y = self._rng.uniform(-1.0, 1.0, STAR_COUNT).astype(np.float32)
            self._z = self._rng.uniform(0.05, 1.0, STAR_COUNT).astype(np.float32)
            self._hue = self._rng.uniform(0.0, 1.0, STAR_COUNT).astype(np.float32)
            return
        if not count:
            return
        self._x[mask] = self._rng.uniform(-1.0, 1.0, count)
        self._y[mask] = self._rng.uniform(-1.0, 1.0, count)
        self._z[mask] = 1.0
        self._hue[mask] = self._rng.uniform(0.0, 1.0, count)

    def render(self, t: float, home: HomeState, controls: Controls) -> Image.Image:
        dt = 0.0 if self._last_t is None else min(max(t - self._last_t, 0.0), MAX_DT)
        self._last_t = t

        # A gentle speed wobble keeps it from looking metronomic.
        speed = 0.30 + 0.12 * float(np.sin(t * 0.21))
        self._z -= dt * speed
        self._spawn(self._z <= 0.02)

        self._trails *= TRAIL_DECAY

        z = np.maximum(self._z, 0.02)
        screen_x = (self._x / z) * 16.0 + PANEL_WIDTH / 2.0
        screen_y = (self._y / z) * 16.0 + PANEL_HEIGHT / 2.0
        visible = (
            (screen_x >= 0)
            & (screen_x < PANEL_WIDTH)
            & (screen_y >= 0)
            & (screen_y < PANEL_HEIGHT)
        )
        if visible.any():
            columns = screen_x[visible].astype(np.int32)
            rows = screen_y[visible].astype(np.int32)
            intensity = np.clip(1.0 - z[visible], 0.05, 1.0) ** 1.6
            hue = self._hue[visible]
            # Cheap hue ramp: cool blue-white through to warm amber.
            red = intensity * (0.55 + 0.45 * hue)
            green = intensity * (0.65 + 0.25 * np.abs(hue - 0.5))
            blue = intensity * (1.0 - 0.55 * hue)
            np.add.at(self._trails, (rows, columns, 0), red)
            np.add.at(self._trails, (rows, columns, 1), green)
            np.add.at(self._trails, (rows, columns, 2), blue)

        rgb = np.clip(self._trails, 0.0, 1.0) * 255.0
        return Image.fromarray(rgb.astype(np.uint8), mode="RGB")


SCENE = Starfield()
