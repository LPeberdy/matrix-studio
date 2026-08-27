"""Constant-velocity reference for diagnosing end-to-end frame cadence."""
from __future__ import annotations

import numpy as np
from PIL import Image

from ..scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState, Scene

_PIXELS_PER_SECOND = 8.0


class MotionTest(Scene):
    name = "motion_test"
    description = "8 px/s reference bar for checking frame cadence and jitter."

    def __init__(self) -> None:
        self._columns = np.arange(PANEL_WIDTH, dtype=np.float32)

    def render(self, t: float, home: HomeState, controls: Controls) -> Image.Image:
        position = (t * _PIXELS_PER_SECOND) % PANEL_WIDTH
        # Signed wrapped distance keeps the bar continuous as it crosses the
        # right edge. Fractional edge coverage makes sub-pixel motion visible
        # instead of holding for several frames and then jumping one pixel.
        distance = (self._columns - position + PANEL_WIDTH / 2) % PANEL_WIDTH - PANEL_WIDTH / 2
        coverage = np.clip(1.5 - np.abs(distance), 0.0, 1.0)

        canvas = np.zeros((PANEL_HEIGHT, PANEL_WIDTH, 3), dtype=np.uint8)
        canvas[..., 1] = np.rint(coverage * 220).astype(np.uint8)
        canvas[..., 2] = np.rint(coverage * 255).astype(np.uint8)
        return Image.fromarray(canvas, mode="RGB")


SCENE = MotionTest()
