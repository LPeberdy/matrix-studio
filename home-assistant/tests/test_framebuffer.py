"""Frame coercion and the latest-frame fan-out bus."""
from __future__ import annotations

import asyncio

import pytest
from PIL import Image

from matrix_studio.framebuffer import (
    FrameBus,
    RenderedFrame,
    coerce_panel_image,
    image_to_rgb565,
    rgb565_to_image,
)


def frame(colour=(1, 2, 3), timestamp_ms: int = 0) -> RenderedFrame:
    image = Image.new("RGB", (64, 64), colour)
    return RenderedFrame(image_to_rgb565(image), timestamp_ms, "test", image)


# ------------------------------------------------------------------- coercion


@pytest.mark.parametrize("size", [(64, 64), (32, 32), (128, 96), (1, 1)])
def test_any_size_becomes_exactly_64x64(size):
    image = coerce_panel_image(Image.new("RGB", size, (10, 20, 30)))
    assert image.size == (64, 64)
    assert len(image_to_rgb565(image)) == 8192


@pytest.mark.parametrize("mode", ["L", "RGBA", "P", "1"])
def test_any_mode_becomes_rgb(mode):
    image = coerce_panel_image(Image.new(mode, (64, 64)))
    assert image.mode == "RGB"
    assert len(image_to_rgb565(image)) == 8192


def test_non_images_are_rejected_loudly():
    for value in (None, "an image, honest", 42, [[0]]):
        with pytest.raises(TypeError):
            coerce_panel_image(value)


def test_already_correct_images_are_passed_through_untouched():
    original = Image.new("RGB", (64, 64), (9, 9, 9))
    assert coerce_panel_image(original) is original


def test_rgb565_roundtrip_rejects_a_wrong_length_buffer():
    with pytest.raises(ValueError):
        rgb565_to_image(b"\x00" * 100)


# ------------------------------------------------------------------------ bus


def test_publishing_with_no_subscribers_is_a_no_op():
    bus = FrameBus()
    assert bus.subscriber_count == 0
    for index in range(10):
        bus.publish(frame(timestamp_ms=index))
    assert bus.latest.timestamp_ms == 9


async def test_a_subscriber_receives_published_frames():
    bus = FrameBus()
    with bus.subscribe() as subscription:
        assert bus.subscriber_count == 1
        bus.publish(frame(timestamp_ms=1))
        received = await asyncio.wait_for(subscription.get(), timeout=1)
        assert received.timestamp_ms == 1
    assert bus.subscriber_count == 0


async def test_a_slow_subscriber_skips_frames_instead_of_queueing():
    """docs/protocol.md §3 explicitly allows sequence gaps for this reason."""
    bus = FrameBus()
    with bus.subscribe() as subscription:
        for index in range(5):
            bus.publish(frame(timestamp_ms=index))
        received = await asyncio.wait_for(subscription.get(), timeout=1)
        assert received.timestamp_ms == 4, "the subscriber should get the newest frame"
        assert subscription.dropped == 4


async def test_wake_delivers_no_frame_but_releases_the_waiter():
    bus = FrameBus()
    with bus.subscribe() as subscription:
        bus.wake_all()
        assert await asyncio.wait_for(subscription.get(), timeout=1) is None


async def test_every_subscriber_gets_its_own_copy():
    bus = FrameBus()
    with bus.subscribe() as first, bus.subscribe() as second:
        bus.publish(frame(timestamp_ms=42))
        assert (await asyncio.wait_for(first.get(), timeout=1)).timestamp_ms == 42
        assert (await asyncio.wait_for(second.get(), timeout=1)).timestamp_ms == 42


async def test_next_after_waits_on_the_engine_clock_and_closes_the_publish_race():
    bus = FrameBus()
    bus.publish(frame(timestamp_ms=10))

    waiting = asyncio.create_task(bus.next_after(10, timeout=1))
    await asyncio.sleep(0)
    assert not waiting.done()

    bus.publish(frame(timestamp_ms=11))
    assert (await waiting).timestamp_ms == 11

    bus.publish(frame(timestamp_ms=12))
    assert (await bus.next_after(11, timeout=1)).timestamp_ms == 12


async def test_next_after_returns_promptly_when_controls_wake_the_bus():
    bus = FrameBus()
    current = frame(timestamp_ms=10)
    bus.publish(current)

    waiting = asyncio.create_task(bus.next_after(10, timeout=1))
    await asyncio.sleep(0)
    bus.wake_all()

    assert await waiting is current
