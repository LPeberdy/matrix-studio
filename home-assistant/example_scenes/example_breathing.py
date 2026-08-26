"""Example: the smallest possible scene — a slowly breathing colour wash.

Shape 3 from README.txt: just a module-level `render` function.
Copy this file, rename it, and edit.
"""
import colorsys
import math

from PIL import Image

from matrix_studio.scene_api import PANEL_HEIGHT, PANEL_WIDTH


def render(t, home, controls):
    # Hue drifts once every ~60 s; brightness breathes every ~8 s.
    hue = (t / 60.0) % 1.0
    value = 0.35 + 0.3 * (0.5 + 0.5 * math.sin(t * 0.785))
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.7, value)
    return Image.new("RGB", (PANEL_WIDTH, PANEL_HEIGHT), (int(red * 255), int(green * 255), int(blue * 255)))
