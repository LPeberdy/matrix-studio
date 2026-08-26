"""The scene engine: a fixed-cadence render loop that cannot be killed by a scene.

Guarantees this module exists to provide:

* **Cadence** — ticks at `target_fps` using a drift-corrected schedule, and
  skips (rather than accumulates) missed ticks if a render runs long.
* **Isolation** — `render()` runs in a worker thread, so a slow scene delays
  frames but never blocks the WebSocket server, the ingress UI or the HA poller.
* **Survivability** — any exception from a scene is caught and logged; after
  `MAX_CONSECUTIVE_FAILURES` the scene is quarantined and the engine switches
  to a fallback scene (and, if even that fails, to a plain black frame). The
  add-on stays up and keeps streaming.
* **Output shape** — whatever a scene returns, what leaves here is always
  exactly 64x64 RGB565 (`framebuffer.coerce_panel_image` + `image_to_rgb565`).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

from .framebuffer import FrameBus, RenderedFrame, coerce_panel_image, image_to_rgb565
from .loader import SceneRegistry
from .scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls, HomeState

_LOGGER = logging.getLogger(__name__)

#: How many consecutive render failures before a scene is quarantined.
MAX_CONSECUTIVE_FAILURES = 3
#: Engine-level fallback order once the active scene is quarantined.
FALLBACK_SCENES = ("plasma", "starfield")

_BLACK_FRAME = bytes(PANEL_WIDTH * PANEL_HEIGHT * 2)


@dataclass
class EngineStats:
    frames_rendered: int = 0
    frames_failed: int = 0
    fps: float = 0.0
    last_render_ms: float = 0.0
    active_scene: str = ""
    rendering_scene: str = ""
    fallback_active: bool = False
    quarantined: dict[str, str] = field(default_factory=dict)
    reloads: int = 0
    blank: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "frames_rendered": self.frames_rendered,
            "frames_failed": self.frames_failed,
            "fps": round(self.fps, 2),
            "last_render_ms": round(self.last_render_ms, 2),
            "active_scene": self.active_scene,
            "rendering_scene": self.rendering_scene,
            "fallback_active": self.fallback_active,
            "quarantined": dict(self.quarantined),
            "reloads": self.reloads,
            "blank": self.blank,
        }


class SceneEngine:
    def __init__(
        self,
        registry: SceneRegistry,
        controls: Controls,
        *,
        target_fps: int = 24,
        home_state_provider: Callable[[], HomeState] | None = None,
        hot_reload: bool = True,
        frame_bus: FrameBus | None = None,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.controls = controls
        self.bus = frame_bus or FrameBus()
        self.stats = EngineStats(active_scene=controls.active_scene)
        self._target_fps = max(1, int(target_fps))
        self._home_state_provider = home_state_provider or (lambda: HomeState())
        self._hot_reload = hot_reload
        self._time = time_source
        self._started_at = self._time()
        self._failures: dict[str, int] = {}
        self._resized_warned: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._fps_window: list[float] = []
        self._last_reload_check = 0.0

    # ---------------------------------------------------------------- controls

    @property
    def target_fps(self) -> int:
        return self._target_fps

    @property
    def frame_interval(self) -> float:
        return 1.0 / self._target_fps

    def select_scene(self, name: str) -> bool:
        """Make `name` active. Returns False if it isn't a loadable scene."""
        entry = self.registry.get(name)
        if entry is None or not entry.ok:
            return False
        self.controls.active_scene = name
        # Selecting a scene explicitly is also a request to give it another go.
        self._failures.pop(name, None)
        self.stats.quarantined.pop(name, None)
        self.stats.fallback_active = False
        return True

    def reload_scenes(self) -> None:
        """Force a reload and un-quarantine everything (the UI's 'reload' button)."""
        self.registry.load()
        self._failures.clear()
        self.stats.quarantined.clear()
        self.stats.fallback_active = False
        self.stats.reloads += 1

    # --------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._task is None:
            self._started_at = self._time()
            self._task = asyncio.create_task(self._run(), name="matrix-studio-engine")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            next_tick += self.frame_interval
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop itself must never die
                _LOGGER.exception("unexpected engine error; continuing")
            now = loop.time()
            if next_tick <= now:
                # Behind schedule: drop the missed ticks instead of sprinting.
                next_tick = now
            await asyncio.sleep(max(0.0, next_tick - now))

    # ------------------------------------------------------------------ render

    async def tick(self) -> RenderedFrame | None:
        """Render and publish exactly one frame. Returns None when blanked."""
        self._maybe_hot_reload()
        self.stats.blank = self.controls.blank
        self.stats.active_scene = self.controls.active_scene
        if self.controls.blank:
            # Blanking is an explicit "display off": stop burning CPU and let
            # the device hold its BLANK state (docs/protocol.md §4.5).
            return None

        started = time.perf_counter()
        t = self._time() - self._started_at
        home = self._safe_home_state()
        loop = asyncio.get_running_loop()
        name, image = await loop.run_in_executor(None, self._render_with_fallback, t, home)
        pixels = image_to_rgb565(image)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        frame = RenderedFrame(
            pixels=pixels,
            timestamp_ms=int(t * 1000) & 0xFFFFFFFF,
            scene=name,
            image=image,
        )
        self.bus.publish(frame)
        self.stats.frames_rendered += 1
        self.stats.rendering_scene = name
        self.stats.last_render_ms = elapsed_ms
        self._note_fps()
        return frame

    def _safe_home_state(self) -> HomeState:
        try:
            return self._home_state_provider()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("home state provider failed; using an empty snapshot")
            return HomeState()

    def _render_with_fallback(self, t: float, home: HomeState) -> tuple[str, Image.Image]:
        """Runs in a worker thread. Always returns a usable 64x64 RGB image."""
        for candidate in self._candidate_scenes():
            image = self._try_render(candidate, t, home)
            if image is not None:
                self.stats.fallback_active = candidate != self.controls.active_scene
                return candidate, image
        # Everything failed (or nothing is loaded at all): black is still a
        # perfectly valid frame, and keeps the stream and the device alive.
        self.stats.fallback_active = True
        return "blank", Image.new("RGB", (PANEL_WIDTH, PANEL_HEIGHT), (0, 0, 0))

    def _candidate_scenes(self) -> list[str]:
        candidates = [self.controls.active_scene]
        candidates.extend(FALLBACK_SCENES)
        candidates.extend(self.registry.names())
        seen: set[str] = set()
        ordered = []
        for name in candidates:
            if name and name not in seen and name not in self.stats.quarantined:
                seen.add(name)
                ordered.append(name)
        return ordered

    def _try_render(self, name: str, t: float, home: HomeState) -> Image.Image | None:
        entry = self.registry.get(name)
        if entry is None or not entry.ok or entry.scene is None:
            return None
        try:
            raw = entry.scene.render(t, home, self.controls)
            image = coerce_panel_image(raw)
        except Exception as exc:  # noqa: BLE001 - the whole point of this class
            self._record_failure(name, exc)
            return None
        if raw is not image and getattr(raw, "size", None) not in ((PANEL_WIDTH, PANEL_HEIGHT), None):
            if name not in self._resized_warned:
                self._resized_warned.add(name)
                _LOGGER.warning(
                    "scene %r returned a %s image; resizing to %dx%d (this warning is logged once)",
                    name,
                    getattr(raw, "size", "?"),
                    PANEL_WIDTH,
                    PANEL_HEIGHT,
                )
        if self._failures.pop(name, 0):
            _LOGGER.info("scene %r recovered", name)
        return image

    def _record_failure(self, name: str, exc: BaseException) -> None:
        count = self._failures.get(name, 0) + 1
        self._failures[name] = count
        self.stats.frames_failed += 1
        # Rate-limited: a scene failing at 24 FPS must not flood the add-on log.
        # In practice quarantine stops the retries after MAX_CONSECUTIVE_FAILURES.
        if count <= MAX_CONSECUTIVE_FAILURES or count % 200 == 0:
            _LOGGER.error(
                "scene %r raised %s: %s (failure %d)",
                name,
                type(exc).__name__,
                exc,
                count,
                exc_info=exc if count == 1 else None,
            )
        if count >= MAX_CONSECUTIVE_FAILURES:
            reason = f"{type(exc).__name__}: {exc}"
            self.stats.quarantined[name] = reason
            _LOGGER.error(
                "scene %r quarantined after %d consecutive failures; falling back. "
                "Fix the scene and press Reload scenes (or restart the add-on).",
                name,
                count,
            )

    def _note_fps(self) -> None:
        now = self._time()
        self._fps_window.append(now)
        cutoff = now - 2.0
        while self._fps_window and self._fps_window[0] < cutoff:
            self._fps_window.pop(0)
        if len(self._fps_window) >= 2:
            span = self._fps_window[-1] - self._fps_window[0]
            self.stats.fps = (len(self._fps_window) - 1) / span if span > 0 else 0.0

    def _maybe_hot_reload(self) -> None:
        if not self._hot_reload:
            return
        now = self._time()
        if now - self._last_reload_check < 2.0:
            return
        self._last_reload_check = now
        try:
            if self.registry.reload_if_changed():
                self.stats.reloads += 1
                self._failures.clear()
                self.stats.quarantined.clear()
                self.stats.fallback_active = False
        except Exception:  # noqa: BLE001
            _LOGGER.exception("scene hot-reload failed; keeping the previous scenes")

    # -------------------------------------------------------------- accessors

    def latest_frame(self) -> RenderedFrame | None:
        return self.bus.latest

    def black_frame_pixels(self) -> bytes:
        return _BLACK_FRAME
