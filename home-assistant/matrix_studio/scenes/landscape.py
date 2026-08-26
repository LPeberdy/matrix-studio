"""Landscape — an endless generative ridgeline under a cycling sky.

Three parallax ridges are generated from seeded periodic value noise and scroll
at different speeds, under a sky that runs through a dawn/day/dusk/night cycle
every `DAY_LENGTH_S` seconds with a sun that rises and sets, and stars that
fade in at night. Purely time-driven — nothing here depends on Home Assistant.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from ..scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState, Scene

#: Seconds for a full dawn -> day -> dusk -> night cycle.
DAY_LENGTH_S = 180.0
_NOISE_PERIOD = 256

# (top colour, bottom/horizon colour) keyframes around the cycle.
_SKY_KEYS = (
    (0.00, (10, 12, 34), (60, 40, 70)),  # night -> pre-dawn
    (0.15, (58, 46, 92), (232, 128, 96)),  # dawn
    (0.30, (44, 108, 186), (150, 200, 235)),  # morning
    (0.50, (30, 96, 180), (128, 186, 226)),  # midday
    (0.70, (52, 60, 132), (236, 132, 78)),  # dusk
    (0.85, (18, 20, 58), (96, 58, 92)),  # twilight
    (1.00, (10, 12, 34), (60, 40, 70)),  # night again
)

_RIDGE_LAYERS = (
    # (scroll px/s, base height fraction, amplitude px, colour, noise offset)
    (1.6, 0.52, 9.0, (36, 44, 74), 0.0),
    (3.4, 0.68, 7.0, (24, 30, 52), 37.0),
    (6.5, 0.84, 5.0, (12, 15, 28), 91.0),
)


def _smoothstep(v: np.ndarray) -> np.ndarray:
    return v * v * (3.0 - 2.0 * v)


class _ValueNoise:
    """Periodic 1-D value noise with a few octaves; deterministic per seed."""

    def __init__(self, seed: int, period: int = _NOISE_PERIOD) -> None:
        rng = np.random.default_rng(seed)
        self._table = rng.uniform(0.0, 1.0, period).astype(np.float32)
        self._period = period

    def sample(self, positions: np.ndarray) -> np.ndarray:
        total = np.zeros_like(positions, dtype=np.float32)
        amplitude = 1.0
        norm = 0.0
        frequency = 1.0
        for _ in range(3):
            scaled = positions * frequency
            index = np.floor(scaled).astype(np.int64)
            frac = _smoothstep((scaled - index).astype(np.float32))
            left = self._table[index % self._period]
            right = self._table[(index + 1) % self._period]
            total += (left * (1.0 - frac) + right * frac) * amplitude
            norm += amplitude
            amplitude *= 0.5
            frequency *= 2.17
        return total / norm


def _lerp_colour(a: tuple[int, int, int], b: tuple[int, int, int], f: float) -> np.ndarray:
    return np.array(a, dtype=np.float32) * (1.0 - f) + np.array(b, dtype=np.float32) * f


def _sky_colours(phase: float) -> tuple[np.ndarray, np.ndarray]:
    for (start, top_a, bottom_a), (end, top_b, bottom_b) in zip(_SKY_KEYS, _SKY_KEYS[1:]):
        if start <= phase <= end:
            f = (phase - start) / (end - start) if end > start else 0.0
            return _lerp_colour(top_a, top_b, f), _lerp_colour(bottom_a, bottom_b, f)
    return np.array(_SKY_KEYS[0][1], dtype=np.float32), np.array(_SKY_KEYS[0][2], dtype=np.float32)


class Landscape(Scene):
    name = "landscape"
    description = "Endless generative ridgeline under a day/night sky."

    def __init__(self, seed: int = 1848) -> None:
        self._noise = [_ValueNoise(seed + i * 101) for i in range(len(_RIDGE_LAYERS))]
        self._columns = np.arange(PANEL_WIDTH, dtype=np.float32)
        self._rows = np.arange(PANEL_HEIGHT, dtype=np.float32).reshape(-1, 1)
        rng = np.random.default_rng(seed + 7)
        star_count = 45
        self._star_x = rng.integers(0, PANEL_WIDTH, star_count)
        self._star_y = rng.integers(0, int(PANEL_HEIGHT * 0.6), star_count)
        self._star_brightness = rng.uniform(0.35, 1.0, star_count).astype(np.float32)
        self._star_twinkle = rng.uniform(0.0, 6.28, star_count).astype(np.float32)

    def render(self, t: float, home: HomeState, controls: Controls) -> Image.Image:
        phase = (t % DAY_LENGTH_S) / DAY_LENGTH_S
        top, bottom = _sky_colours(phase)

        gradient = (self._rows / max(PANEL_HEIGHT - 1, 1)).astype(np.float32)
        canvas = top.reshape(1, 1, 3) * (1.0 - gradient[..., None]) + bottom.reshape(1, 1, 3) * gradient[..., None]
        canvas = np.repeat(canvas, PANEL_WIDTH, axis=1) if canvas.shape[1] == 1 else canvas

        night = float(np.clip(np.cos(phase * 2.0 * np.pi) * 0.5 + 0.5, 0.0, 1.0))
        self._draw_stars(canvas, t, night)
        self._draw_sun(canvas, phase)
        self._draw_ridges(canvas, t)

        return Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), mode="RGB")

    def _draw_stars(self, canvas: np.ndarray, t: float, night: float) -> None:
        if night <= 0.05:
            return
        twinkle = 0.65 + 0.35 * np.sin(t * 2.3 + self._star_twinkle)
        value = np.clip(self._star_brightness * twinkle * night, 0.0, 1.0) * 255.0
        canvas[self._star_y, self._star_x, 0] = np.maximum(canvas[self._star_y, self._star_x, 0], value)
        canvas[self._star_y, self._star_x, 1] = np.maximum(canvas[self._star_y, self._star_x, 1], value)
        canvas[self._star_y, self._star_x, 2] = np.maximum(canvas[self._star_y, self._star_x, 2], value * 0.95)

    def _draw_sun(self, canvas: np.ndarray, phase: float) -> None:
        # Sun tracks a shallow arc from the left horizon to the right horizon
        # across the daylight half of the cycle; a moon does the same at night.
        daylight = 0.12 <= phase <= 0.78
        travel = (phase - 0.12) / 0.66 if daylight else ((phase - 0.78) % 1.0) / 0.34
        cx = travel * (PANEL_WIDTH + 16) - 8
        cy = PANEL_HEIGHT * 0.52 - np.sin(np.clip(travel, 0.0, 1.0) * np.pi) * PANEL_HEIGHT * 0.42
        radius = 4.5 if daylight else 3.0
        colour = np.array((255, 238, 190), dtype=np.float32) if daylight else np.array((214, 222, 255), dtype=np.float32)

        dx = self._columns.reshape(1, -1) - cx
        dy = self._rows - cy
        distance = np.sqrt(dx * dx + dy * dy)
        disc = np.clip(1.0 - (distance - radius), 0.0, 1.0)
        glow = np.clip(1.0 - distance / (radius * 4.0), 0.0, 1.0) ** 2 * 0.55
        weight = np.clip(disc + glow, 0.0, 1.0)[..., None]
        np.copyto(canvas, canvas * (1.0 - weight) + colour.reshape(1, 1, 3) * weight)

    def _draw_ridges(self, canvas: np.ndarray, t: float) -> None:
        for noise, (speed, base, amplitude, colour, offset) in zip(self._noise, _RIDGE_LAYERS):
            positions = (self._columns + offset + t * speed) * 0.06
            heights = PANEL_HEIGHT * base - noise.sample(positions) * amplitude
            mask = self._rows >= heights.reshape(1, -1)
            # A one-pixel lighter rim reads as a lit ridge edge.
            rim = (self._rows >= heights.reshape(1, -1)) & (self._rows < heights.reshape(1, -1) + 1.0)
            base_colour = np.array(colour, dtype=np.float32)
            canvas[mask] = base_colour
            canvas[rim] = np.clip(base_colour * 1.9 + 18.0, 0, 255)


SCENE = Landscape()
