"""Glitch Life — Conway-style cells with trails, scanlines and signal tears."""
from __future__ import annotations

import numpy as np
from PIL import Image

from matrix_studio.scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState, Scene


class GlitchLife(Scene):
    name = "glitch_life"
    description = "Game-of-Life cells with phosphor trails and occasional digital tears."

    def __init__(self) -> None:
        self._rng = np.random.default_rng(0x4D53)
        self._grid = self._rng.random((PANEL_HEIGHT, PANEL_WIDTH)) < 0.18
        self._trail = self._grid.astype(np.float32)
        self._tick = -1
        self._glitch_tick = -1
        self._band_y = 0
        self._band_h = 0
        self._band_shift = 0
        self._channel_shift = 0

    def _step(self) -> None:
        neighbours = np.zeros_like(self._grid, dtype=np.uint8)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                neighbours += np.roll(np.roll(self._grid, dy, axis=0), dx, axis=1)

        survive = self._grid & ((neighbours == 2) | (neighbours == 3))
        born = (~self._grid) & (neighbours == 3)
        self._grid = survive | born

        population = int(self._grid.sum())
        if population < 24 or population > PANEL_WIDTH * PANEL_HEIGHT * 0.62:
            self._grid = self._rng.random((PANEL_HEIGHT, PANEL_WIDTH)) < 0.18

        self._trail *= 0.78
        self._trail[self._grid] = 1.0

    def _refresh_glitch(self, t: float) -> None:
        glitch_tick = int(t * 5.0)
        if glitch_tick == self._glitch_tick:
            return
        self._glitch_tick = glitch_tick

        if self._rng.random() < 0.48:
            self._band_y = int(self._rng.integers(0, PANEL_HEIGHT - 2))
            self._band_h = int(self._rng.integers(1, 8))
            self._band_shift = int(self._rng.integers(-10, 11))
        else:
            self._band_h = 0
            self._band_shift = 0

        self._channel_shift = int(self._rng.integers(-2, 3)) if self._rng.random() < 0.28 else 0

    def render(self, t: float, home: HomeState, controls: Controls) -> Image.Image:
        target_tick = int(t / 0.12)
        catch_up = 0
        while self._tick < target_tick and catch_up < 8:
            self._step()
            self._tick += 1
            catch_up += 1

        intensity = np.clip(self._trail, 0.0, 1.0)
        frame = np.zeros((PANEL_HEIGHT, PANEL_WIDTH, 3), dtype=np.uint8)
        frame[..., 0] = (intensity * 22).astype(np.uint8)
        frame[..., 1] = (intensity * 205).astype(np.uint8)
        frame[..., 2] = (intensity * 160).astype(np.uint8)

        # Faint CRT-ish scanlines keep the image textural without large bright areas.
        frame[1::2] = (frame[1::2].astype(np.uint16) * 3 // 4).astype(np.uint8)

        self._refresh_glitch(t)
        if self._band_h:
            end = min(PANEL_HEIGHT, self._band_y + self._band_h)
            frame[self._band_y:end] = np.roll(frame[self._band_y:end], self._band_shift, axis=1)
        if self._channel_shift:
            frame[..., 0] = np.roll(frame[..., 0], self._channel_shift, axis=1)

        return Image.fromarray(frame, mode="RGB")


SCENE = GlitchLife()
