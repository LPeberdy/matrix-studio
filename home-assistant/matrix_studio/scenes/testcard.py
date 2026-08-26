"""Test card — static panel-verification pattern.

Not art: this is the scene to select when checking a freshly wired panel.
It shows colour bars, per-channel ramps, a 1 px white border and single-pixel
corner markers, so wiring faults (swapped R/B), a mirrored/rotated panel, a
wrong scan rate or a chopped edge are all obvious at a glance.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from ..scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState, Scene

_BARS = (
    (255, 255, 255),
    (255, 255, 0),
    (0, 255, 255),
    (0, 255, 0),
    (255, 0, 255),
    (255, 0, 0),
    (0, 0, 255),
    (0, 0, 0),
)


class TestCard(Scene):
    name = "testcard"
    description = "Static colour bars / ramps / corner markers for panel checks."

    def __init__(self) -> None:
        self._image = self._build()

    def _build(self) -> Image.Image:
        canvas = np.zeros((PANEL_HEIGHT, PANEL_WIDTH, 3), dtype=np.uint8)

        bar_width = PANEL_WIDTH // len(_BARS)
        for index, colour in enumerate(_BARS):
            canvas[0:24, index * bar_width : (index + 1) * bar_width] = colour

        ramp = (np.arange(PANEL_WIDTH, dtype=np.float32) / (PANEL_WIDTH - 1) * 255.0).astype(np.uint8)
        canvas[26:34, :, 0] = ramp
        canvas[34:42, :, 1] = ramp
        canvas[42:50, :, 2] = ramp
        canvas[50:58, :, 0] = ramp
        canvas[50:58, :, 1] = ramp
        canvas[50:58, :, 2] = ramp

        image = Image.fromarray(canvas, mode="RGB")
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, PANEL_WIDTH - 1, PANEL_HEIGHT - 1], outline=(255, 255, 255))
        # Distinct corner markers: top-left red, top-right green,
        # bottom-left blue, bottom-right white -> orientation at a glance.
        draw.point((1, 1), fill=(255, 0, 0))
        draw.point((PANEL_WIDTH - 2, 1), fill=(0, 255, 0))
        draw.point((1, PANEL_HEIGHT - 2), fill=(0, 0, 255))
        draw.point((PANEL_WIDTH - 2, PANEL_HEIGHT - 2), fill=(255, 255, 255))
        return image

    def render(self, t: float, home: HomeState, controls: Controls) -> Image.Image:
        return self._image.copy()


SCENE = TestCard()
