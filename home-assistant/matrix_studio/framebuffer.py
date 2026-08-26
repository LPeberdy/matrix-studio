"""Pixel conversion (RGB888 -> RGB565) and the latest-frame fan-out bus.

Everything that turns a rendered image into wire bytes lives here so there is
exactly one place where the 64x64 / RGB565 output guarantee is enforced. The
packing itself is checked against `protocol.rgb888_to_rgb565` (the frozen
reference implementation) by the test suite; the numpy path here is purely a
vectorised restatement of it.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Iterator

import numpy as np
from PIL import Image

from .scene_api import PANEL_HEIGHT, PANEL_WIDTH

__all__ = [
    "coerce_panel_image",
    "image_to_rgb565",
    "rgb565_to_image",
    "RenderedFrame",
    "FrameBus",
]


def coerce_panel_image(image: object, width: int = PANEL_WIDTH, height: int = PANEL_HEIGHT) -> Image.Image:
    """Force whatever a scene returned into an exactly width x height RGB image.

    Raises TypeError if it isn't a PIL image at all — the engine treats that as
    a scene failure. Wrong size or mode is repaired (and reported by the caller)
    rather than being fatal, so a slightly sloppy user scene still displays.
    """
    if not isinstance(image, Image.Image):
        raise TypeError(f"scene render() must return a PIL.Image.Image, got {type(image).__name__}")
    if image.mode != "RGB":
        image = image.convert("RGB")
    if image.size != (width, height):
        image = image.resize((width, height), Image.NEAREST)
    return image


def image_to_rgb565(image: Image.Image) -> bytes:
    """Pack an RGB image into little-endian RGB565 bytes, row-major, top-left origin.

    Bit layout per pixel, matching docs/protocol.md §4.3: RRRRR GGGGGG BBBBB.
    """
    array = np.asarray(image.convert("RGB"), dtype=np.uint8)
    red = array[..., 0].astype(np.uint16)
    green = array[..., 1].astype(np.uint16)
    blue = array[..., 2].astype(np.uint16)
    packed = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
    return packed.astype("<u2").tobytes()


def rgb565_to_image(pixels: bytes, width: int = PANEL_WIDTH, height: int = PANEL_HEIGHT) -> Image.Image:
    """Inverse of `image_to_rgb565` — what the panel will actually show.

    Used by the emulator/preview so the preview reflects RGB565 quantisation
    instead of the pre-quantisation render.
    """
    expected = width * height * 2
    if len(pixels) != expected:
        raise ValueError(f"expected {expected} bytes for {width}x{height} RGB565, got {len(pixels)}")
    packed = np.frombuffer(pixels, dtype="<u2").reshape((height, width))
    red = ((packed >> 11) & 0x1F).astype(np.uint8)
    green = ((packed >> 5) & 0x3F).astype(np.uint8)
    blue = (packed & 0x1F).astype(np.uint8)
    # Replicate high bits into the low bits so full-scale stays full-scale.
    rgb = np.dstack(
        [
            (red << 3) | (red >> 2),
            (green << 2) | (green >> 4),
            (blue << 3) | (blue >> 2),
        ]
    )
    return Image.fromarray(rgb, mode="RGB")


@dataclass(frozen=True)
class RenderedFrame:
    """One rendered frame: both the wire bytes and the image, for preview reuse."""

    pixels: bytes
    timestamp_ms: int
    scene: str
    image: Image.Image


class _Subscription:
    """A single consumer's coalescing mailbox.

    Only the most recent frame is retained: a device that cannot keep up skips
    frames rather than building an unbounded backlog. docs/protocol.md §3 makes
    `FRAME.sequence` gaps explicitly legal for exactly this reason.
    """

    def __init__(self) -> None:
        self._pending: RenderedFrame | None = None
        self._event = asyncio.Event()
        self.dropped = 0

    def offer(self, frame: RenderedFrame) -> None:
        if self._pending is not None:
            self.dropped += 1
        self._pending = frame
        self._event.set()

    def wake(self) -> None:
        """Wake the consumer without a frame (used for out-of-band control sends)."""
        self._event.set()

    async def get(self) -> RenderedFrame | None:
        """The newest unseen frame, or None if this was a bare `wake()`."""
        await self._event.wait()
        self._event.clear()
        frame = self._pending
        self._pending = None
        return frame


class FrameBus:
    """Fan-out of the newest rendered frame to zero or more device connections.

    Zero subscribers is a normal, healthy state (docs/protocol.md §3.4): publish
    simply stores the frame for the preview endpoint and returns.
    """

    def __init__(self) -> None:
        self._subscriptions: set[_Subscription] = set()
        self._latest: RenderedFrame | None = None

    @property
    def latest(self) -> RenderedFrame | None:
        return self._latest

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    def publish(self, frame: RenderedFrame) -> None:
        self._latest = frame
        for subscription in self._subscriptions:
            subscription.offer(frame)

    def wake_all(self) -> None:
        """Nudge every subscriber even though there is no new frame."""
        for subscription in self._subscriptions:
            subscription.wake()

    @contextlib.contextmanager
    def subscribe(self) -> Iterator[_Subscription]:
        subscription = _Subscription()
        self._subscriptions.add(subscription)
        try:
            yield subscription
        finally:
            self._subscriptions.discard(subscription)
