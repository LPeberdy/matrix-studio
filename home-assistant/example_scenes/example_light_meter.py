"""Example: a Home Assistant-reactive scene — a bar showing how lit the house is.

Shape 1 from README.txt: a class plus a module-level `SCENE` instance.

Which lights are counted comes from the add-on's `entities.lights` option (or,
if that list is empty, every `light.` entity Home Assistant knows about) — this
file hardcodes no entity ids.
"""
from PIL import Image, ImageDraw

from matrix_studio.scene_api import PANEL_HEIGHT, PANEL_WIDTH, Scene


class LightMeter(Scene):
    name = "example_light_meter"
    description = "Vertical bar: how many of your lights are on."

    def render(self, t, home, controls):
        image = Image.new("RGB", (PANEL_WIDTH, PANEL_HEIGHT), (4, 5, 10))
        draw = ImageDraw.Draw(image)

        fraction = home.lights_on_fraction
        filled = int(round(fraction * (PANEL_HEIGHT - 8)))
        for row in range(filled):
            y = PANEL_HEIGHT - 5 - row
            warmth = row / max(PANEL_HEIGHT - 8, 1)
            draw.line([(12, y), (PANEL_WIDTH - 13, y)], fill=(255, int(140 + 90 * warmth), int(40 + 60 * warmth)))

        draw.rectangle([11, 3, PANEL_WIDTH - 12, PANEL_HEIGHT - 4], outline=(40, 44, 60))
        if not home.available:
            # No Home Assistant data yet: show a dim, unmistakably idle marker.
            draw.line([(11, PANEL_HEIGHT // 2), (PANEL_WIDTH - 12, PANEL_HEIGHT // 2)], fill=(70, 50, 0))
        return image


SCENE = LightMeter()
