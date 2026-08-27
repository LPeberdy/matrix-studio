"""Scene lifecycle: discovery, loading, rendering, and surviving bad scenes."""
from __future__ import annotations

import asyncio
import textwrap

import numpy as np
import pytest
from PIL import Image

from matrix_studio.engine import MAX_CONSECUTIVE_FAILURES, SceneEngine
from matrix_studio.loader import SceneRegistry
from matrix_studio.scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState

BUILTIN_SCENES = {"plasma", "starfield", "landscape", "home_pulse", "testcard", "motion_test"}


def write_scene(directory, name: str, body: str) -> None:
    (directory / f"{name}.py").write_text(textwrap.dedent(body))


def make_engine(registry: SceneRegistry, scene: str, **kwargs) -> SceneEngine:
    controls = Controls(active_scene=scene)
    return SceneEngine(registry, controls, target_fps=kwargs.pop("target_fps", 50), hot_reload=False, **kwargs)


# ------------------------------------------------------------------ discovery


def test_all_builtin_scenes_load():
    registry = SceneRegistry(None)
    registry.load()
    assert BUILTIN_SCENES.issubset(set(registry.names()))
    assert registry.errors() == {}


@pytest.mark.parametrize("name", sorted(BUILTIN_SCENES))
def test_builtin_scene_renders_a_64x64_rgb_image(name, home_state):
    registry = SceneRegistry(None)
    registry.load()
    scene = registry.get(name).scene
    image = scene.render(3.5, home_state, Controls(active_scene=name))
    assert isinstance(image, Image.Image)
    assert image.size == (PANEL_WIDTH, PANEL_HEIGHT)
    assert image.convert("RGB").size == (64, 64)


def test_builtin_scenes_are_stable_across_many_frames(home_state):
    """Statefulness (starfield trails, landscape scroll) must not drift or blow up."""
    registry = SceneRegistry(None)
    registry.load()
    controls = Controls()
    for name in sorted(BUILTIN_SCENES):
        scene = registry.get(name).scene
        for step in range(60):
            image = scene.render(step * 0.04, home_state, controls)
            assert image.size == (64, 64)


def test_plasma_moves_as_one_field_at_a_constant_velocity(home_state):
    """One second of plasma motion is a rigid 6px/3px translation, not a beat."""
    registry = SceneRegistry(None)
    registry.load()
    scene = registry.get("plasma").scene
    controls = Controls(active_scene="plasma")

    start = np.asarray(scene.render(0.0, home_state, controls), dtype=np.int16)
    after_one_second = np.asarray(scene.render(1.0, home_state, controls), dtype=np.int16)
    expected = np.roll(start, shift=(3, 6), axis=(0, 1))

    assert np.abs(after_one_second - expected).max() <= 1


def test_motion_test_is_a_constant_velocity_reference(home_state):
    registry = SceneRegistry(None)
    registry.load()
    scene = registry.get("motion_test").scene
    controls = Controls(active_scene="motion_test")

    start = np.asarray(scene.render(2.0, home_state, controls))
    half_second_later = np.asarray(scene.render(2.5, home_state, controls))

    assert np.array_equal(half_second_later, np.roll(start, shift=4, axis=1))


# ------------------------------------------------------------- user scenes


def test_user_scene_shapes_all_load(scenes_dir):
    write_scene(
        scenes_dir,
        "as_function",
        """
        from PIL import Image
        def render(t, home, controls):
            return Image.new("RGB", (64, 64), (1, 2, 3))
        """,
    )
    write_scene(
        scenes_dir,
        "as_scene_object",
        """
        from PIL import Image
        from matrix_studio.scene_api import Scene
        class Mine(Scene):
            description = "class form"
            def render(self, t, home, controls):
                return Image.new("RGB", (64, 64), (4, 5, 6))
        SCENE = Mine()
        """,
    )
    write_scene(
        scenes_dir,
        "as_scene_subclass",
        """
        from PIL import Image
        from matrix_studio.scene_api import Scene
        class Implicit(Scene):
            def render(self, t, home, controls):
                return Image.new("RGB", (64, 64), (7, 8, 9))
        """,
    )
    registry = SceneRegistry(scenes_dir)
    registry.load()

    assert registry.errors() == {}
    for name, colour in (("as_function", (1, 2, 3)), ("as_scene_object", (4, 5, 6)), ("as_scene_subclass", (7, 8, 9))):
        entry = registry.get(name)
        assert entry.ok and entry.source == "user"
        assert entry.scene.render(0.0, HomeState(), Controls()).getpixel((0, 0)) == colour


def test_user_scene_can_shadow_a_builtin(scenes_dir):
    write_scene(
        scenes_dir,
        "plasma",
        """
        from PIL import Image
        def render(t, home, controls):
            return Image.new("RGB", (64, 64), (11, 22, 33))
        """,
    )
    registry = SceneRegistry(scenes_dir)
    registry.load()
    entry = registry.get("plasma")
    assert entry.source == "user"
    assert entry.scene.render(0.0, HomeState(), Controls()).getpixel((0, 0)) == (11, 22, 33)


def test_a_scene_that_explodes_on_import_does_not_break_the_others(scenes_dir):
    write_scene(scenes_dir, "broken_import", "raise RuntimeError('deliberately broken at import time')\n")
    write_scene(scenes_dir, "no_render", "VALUE = 1\n")
    write_scene(
        scenes_dir,
        "healthy",
        """
        from PIL import Image
        def render(t, home, controls):
            return Image.new("RGB", (64, 64), (0, 128, 0))
        """,
    )
    registry = SceneRegistry(scenes_dir)
    registry.load()

    assert "healthy" in registry.names()
    assert BUILTIN_SCENES.issubset(set(registry.names()))
    errors = registry.errors()
    assert "deliberately broken at import time" in errors["broken_import"]
    assert "no SCENE" in errors["no_render"]
    assert "broken_import" not in registry.names()


def test_hot_reload_notices_new_changed_and_removed_files(scenes_dir):
    registry = SceneRegistry(scenes_dir)
    registry.load()
    assert not registry.reload_if_changed()

    write_scene(
        scenes_dir,
        "late_arrival",
        """
        from PIL import Image
        def render(t, home, controls):
            return Image.new("RGB", (64, 64), (9, 9, 9))
        """,
    )
    assert registry.reload_if_changed()
    assert "late_arrival" in registry.names()

    (scenes_dir / "late_arrival.py").unlink()
    assert registry.reload_if_changed()
    assert "late_arrival" not in registry.names()


# ----------------------------------------------------------------- rendering


async def test_tick_publishes_exactly_one_64x64_rgb565_frame():
    registry = SceneRegistry(None)
    registry.load()
    engine = make_engine(registry, "plasma")
    frame = await engine.tick()
    assert frame is not None
    assert len(frame.pixels) == PANEL_WIDTH * PANEL_HEIGHT * 2 == 8192
    assert frame.image.size == (64, 64)
    assert engine.bus.latest is frame
    assert engine.stats.frames_rendered == 1


async def test_scene_returning_the_wrong_size_is_coerced_not_rejected(scenes_dir):
    write_scene(
        scenes_dir,
        "too_small",
        """
        from PIL import Image
        def render(t, home, controls):
            return Image.new("RGB", (16, 16), (255, 0, 0))
        """,
    )
    registry = SceneRegistry(scenes_dir)
    registry.load()
    engine = make_engine(registry, "too_small")
    frame = await engine.tick()
    assert len(frame.pixels) == 8192
    assert frame.scene == "too_small"
    assert not engine.stats.fallback_active


async def test_scene_returning_a_non_image_falls_back(scenes_dir):
    write_scene(scenes_dir, "not_an_image", "def render(t, home, controls):\n    return 'nope'\n")
    registry = SceneRegistry(scenes_dir)
    registry.load()
    engine = make_engine(registry, "not_an_image")
    frame = await engine.tick()
    assert frame.scene != "not_an_image"
    assert len(frame.pixels) == 8192
    assert engine.stats.fallback_active


async def test_blanking_pauses_rendering_without_stopping_the_engine():
    registry = SceneRegistry(None)
    registry.load()
    engine = make_engine(registry, "plasma")
    engine.controls.blank = True
    assert await engine.tick() is None
    engine.controls.blank = False
    assert await engine.tick() is not None


async def test_slow_home_state_provider_never_breaks_a_tick():
    registry = SceneRegistry(None)
    registry.load()

    def exploding_provider():
        raise RuntimeError("HA adapter is on fire")

    engine = make_engine(registry, "plasma", home_state_provider=exploding_provider)
    frame = await engine.tick()
    assert frame is not None and len(frame.pixels) == 8192


# ------------------------------------------------------- broken-scene fallback


async def test_broken_scene_is_quarantined_and_the_engine_keeps_serving_frames(scenes_dir):
    """The headline guarantee: a deliberately broken scene must not take the
    service down, and frames must keep flowing from a fallback."""
    write_scene(
        scenes_dir,
        "exploding",
        """
        def render(t, home, controls):
            raise ZeroDivisionError("this scene is deliberately broken")
        """,
    )
    registry = SceneRegistry(scenes_dir)
    registry.load()
    assert "exploding" in registry.names()  # it imports fine; it fails at render

    engine = make_engine(registry, "exploding")
    frames = [await engine.tick() for _ in range(MAX_CONSECUTIVE_FAILURES + 3)]

    assert all(frame is not None and len(frame.pixels) == 8192 for frame in frames)
    assert all(frame.scene != "exploding" for frame in frames)
    assert engine.stats.fallback_active
    assert "exploding" in engine.stats.quarantined
    assert "ZeroDivisionError" in engine.stats.quarantined["exploding"]
    # Only the failures before quarantine count: after that it is not retried.
    assert engine.stats.frames_failed == MAX_CONSECUTIVE_FAILURES
    assert engine.stats.frames_rendered == len(frames)


async def test_engine_task_stays_alive_with_a_broken_scene(scenes_dir):
    """Same scenario, but through the real run loop: the task must not die."""
    write_scene(scenes_dir, "exploding", "def render(t, home, controls):\n    raise RuntimeError('boom')\n")
    registry = SceneRegistry(scenes_dir)
    registry.load()
    engine = make_engine(registry, "exploding", target_fps=60)

    await engine.start()
    try:
        await asyncio.sleep(0.4)
        first = engine.stats.frames_rendered
        assert first > 0, "engine produced no frames at all"
        await asyncio.sleep(0.2)
        assert engine.stats.frames_rendered > first, "engine stopped producing frames"
        assert engine.bus.latest is not None and len(engine.bus.latest.pixels) == 8192
        assert engine._task is not None and not engine._task.done()
    finally:
        await engine.stop()


async def test_every_scene_broken_still_yields_black_frames(scenes_dir, monkeypatch):
    """Worst case: nothing renders. The stream must degrade to black, not stop."""
    registry = SceneRegistry(scenes_dir)
    registry.load()
    for entry in registry.entries():
        if entry.scene is not None:
            monkeypatch.setattr(
                entry.scene,
                "render",
                lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("all broken")),
                raising=False,
            )

    engine = make_engine(registry, "plasma")
    frame = await engine.tick()
    assert frame.scene == "blank"
    assert frame.pixels == bytes(8192)
    assert engine.stats.fallback_active


async def test_selecting_a_scene_clears_its_quarantine(scenes_dir):
    write_scene(scenes_dir, "flaky", "def render(t, home, controls):\n    raise RuntimeError('nope')\n")
    registry = SceneRegistry(scenes_dir)
    registry.load()
    engine = make_engine(registry, "flaky")
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        await engine.tick()
    assert "flaky" in engine.stats.quarantined

    assert engine.select_scene("flaky") is True
    assert "flaky" not in engine.stats.quarantined
    assert engine.select_scene("does_not_exist") is False


async def test_reload_scenes_recovers_a_fixed_scene(scenes_dir):
    write_scene(scenes_dir, "fixme", "def render(t, home, controls):\n    raise RuntimeError('broken')\n")
    registry = SceneRegistry(scenes_dir)
    registry.load()
    engine = make_engine(registry, "fixme")
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        await engine.tick()
    assert "fixme" in engine.stats.quarantined

    write_scene(
        scenes_dir,
        "fixme",
        """
        from PIL import Image
        def render(t, home, controls):
            return Image.new("RGB", (64, 64), (3, 3, 3))
        """,
    )
    engine.reload_scenes()
    frame = await engine.tick()
    assert frame.scene == "fixme"
    assert not engine.stats.fallback_active
