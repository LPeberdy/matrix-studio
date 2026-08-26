"""Home Pulse — the scene that visibly reacts to live Home Assistant state.

Mapping (every entity id comes from add-on options; nothing is hardcoded):

* **lights** -> a ring of orbs, one per configured light, lit warm when that
  light is on. With no `entities.lights` configured, the state adapter
  auto-discovers the `light.` domain, so the ring reflects "how much of the
  house is lit".
* **outdoor temperature** -> background hue, deep blue (cold) through to
  ember orange (hot).
* **indoor temperature** -> the central orb's colour temperature.
* **occupancy** -> pulse rate: slow and calm when empty, quicker when occupied.
* **weather** -> overlay: rain streaks, drifting snow, or a clear-sky sparkle.

When Home Assistant is unavailable the scene still renders, using neutral
defaults, and shows a dim amber "stale state" pip in the bottom-right corner
rather than failing.
"""
from __future__ import annotations

import colorsys

import numpy as np
from PIL import Image, ImageDraw

from ..scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState, Scene

MAX_ORBS = 20
#: Outdoor temperature range (degrees) mapped onto the background hue ramp.
COLD_C = -5.0
HOT_C = 32.0

_RAIN_WORDS = ("rain", "pouring", "drizzle", "shower", "hail", "lightning")
_SNOW_WORDS = ("snow", "sleet")
_CLEAR_WORDS = ("sunny", "clear")


def _fraction(value: float | None, low: float, high: float, default: float = 0.5) -> float:
    if value is None:
        return default
    if high <= low:
        return default
    return float(min(1.0, max(0.0, (value - low) / (high - low))))


class HomePulse(Scene):
    name = "home_pulse"
    description = "Reacts to lights, temperature, occupancy and weather from Home Assistant."

    def __init__(self, seed: int = 4242) -> None:
        rng = np.random.default_rng(seed)
        self._rows = np.arange(PANEL_HEIGHT, dtype=np.float32).reshape(-1, 1)
        self._columns = np.arange(PANEL_WIDTH, dtype=np.float32).reshape(1, -1)
        self._drop_x = rng.uniform(0, PANEL_WIDTH, 48).astype(np.float32)
        self._drop_y = rng.uniform(0, PANEL_HEIGHT, 48).astype(np.float32)
        self._drop_speed = rng.uniform(0.6, 1.4, 48).astype(np.float32)
        self._sparkle_phase = rng.uniform(0, 6.28, 24).astype(np.float32)
        self._sparkle_x = rng.integers(0, PANEL_WIDTH, 24)
        self._sparkle_y = rng.integers(0, PANEL_HEIGHT, 24)

    # ------------------------------------------------------------------ render

    def render(self, t: float, home: HomeState, controls: Controls) -> Image.Image:
        outdoor = home.outdoor_temperature
        indoor = home.indoor_temperature
        warmth = _fraction(outdoor, COLD_C, HOT_C, default=0.45)
        occupied = home.occupied
        pulse_hz = 0.55 if occupied else 0.22
        pulse = 0.5 + 0.5 * float(np.sin(t * pulse_hz * 2.0 * np.pi))

        canvas = self._background(warmth, pulse)
        self._weather_overlay(canvas, t, (home.weather or "").lower())

        image = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), mode="RGB")
        draw = ImageDraw.Draw(image)
        self._draw_centre(draw, indoor, pulse, occupied)
        self._draw_light_ring(draw, home, t, pulse)
        if not home.available:
            # Dim amber pip: state is stale/unavailable but the panel is alive.
            draw.point((PANEL_WIDTH - 1, PANEL_HEIGHT - 1), fill=(90, 55, 0))
        return image

    # -------------------------------------------------------------- components

    def _background(self, warmth: float, pulse: float) -> np.ndarray:
        cold = np.array((6, 16, 48), dtype=np.float32)
        hot = np.array((52, 14, 6), dtype=np.float32)
        base = cold * (1.0 - warmth) + hot * warmth
        gradient = (self._rows / max(PANEL_HEIGHT - 1, 1)).astype(np.float32)
        canvas = base.reshape(1, 1, 3) * (0.45 + 0.85 * gradient[..., None])
        canvas = np.repeat(canvas, PANEL_WIDTH, axis=1)

        # A slow breathing vignette centred on the panel.
        dx = self._columns - (PANEL_WIDTH - 1) / 2.0
        dy = self._rows - (PANEL_HEIGHT - 1) / 2.0
        distance = np.sqrt(dx * dx + dy * dy) / (PANEL_WIDTH * 0.7)
        halo = np.clip(1.0 - distance, 0.0, 1.0) ** 2 * (10.0 + 26.0 * pulse)
        canvas += halo[..., None] * np.array((1.0, 0.75, 0.45), dtype=np.float32)
        return canvas

    def _weather_overlay(self, canvas: np.ndarray, t: float, weather: str) -> None:
        if any(word in weather for word in _RAIN_WORDS):
            self._draw_precipitation(canvas, t, speed=26.0, length=3, colour=(120, 160, 220))
        elif any(word in weather for word in _SNOW_WORDS):
            self._draw_precipitation(canvas, t, speed=7.0, length=1, colour=(220, 230, 245))
        elif any(word in weather for word in _CLEAR_WORDS):
            twinkle = np.clip(0.5 + 0.5 * np.sin(t * 1.7 + self._sparkle_phase), 0.0, 1.0) * 70.0
            canvas[self._sparkle_y, self._sparkle_x] += twinkle[:, None] * np.array(
                (1.0, 0.95, 0.7), dtype=np.float32
            )

    def _draw_precipitation(
        self, canvas: np.ndarray, t: float, *, speed: float, length: int, colour: tuple[int, int, int]
    ) -> None:
        colour_array = np.array(colour, dtype=np.float32)
        y = (self._drop_y + t * speed * self._drop_speed) % PANEL_HEIGHT
        x = self._drop_x.astype(np.int32) % PANEL_WIDTH
        for offset in range(length):
            rows = (y.astype(np.int32) - offset) % PANEL_HEIGHT
            fade = 0.85 ** offset
            canvas[rows, x] = np.clip(canvas[rows, x] * (1.0 - 0.55 * fade) + colour_array * 0.55 * fade, 0, 255)

    def _draw_centre(self, draw: ImageDraw.ImageDraw, indoor: float | None, pulse: float, occupied: bool) -> None:
        comfort = _fraction(indoor, 15.0, 26.0, default=0.5)
        hue = (0.58 - 0.5 * comfort) % 1.0  # cyan-ish when cool, amber when warm
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.75, 0.55 + 0.45 * pulse)
        radius = (3.0 if not occupied else 4.0) + pulse * 1.6
        cx, cy = (PANEL_WIDTH - 1) / 2.0, (PANEL_HEIGHT - 1) / 2.0
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(int(red * 255), int(green * 255), int(blue * 255)),
        )

    def _draw_light_ring(self, draw: ImageDraw.ImageDraw, home: HomeState, t: float, pulse: float) -> None:
        entity_ids = list(home.light_entity_ids)[:MAX_ORBS]
        if not entity_ids:
            return
        cx, cy = (PANEL_WIDTH - 1) / 2.0, (PANEL_HEIGHT - 1) / 2.0
        ring_radius = 22.0
        count = len(entity_ids)
        for index, entity_id in enumerate(entity_ids):
            angle = (index / count) * 2.0 * np.pi + t * 0.05
            x = cx + np.cos(angle) * ring_radius
            y = cy + np.sin(angle) * ring_radius * 0.92
            entity = home.get(entity_id)
            is_on = entity.is_on if entity is not None else False
            if is_on:
                brightness = 0.6 + 0.4 * pulse
                colour = (int(255 * brightness), int(196 * brightness), int(110 * brightness))
                size = 1.6
            else:
                colour = (28, 30, 44)
                size = 0.9
            draw.ellipse([x - size, y - size, x + size, y + size], fill=colour)


SCENE = HomePulse()
