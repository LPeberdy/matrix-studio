"""Composition root: wires options, scenes, HA state, the engine, the device
server and the ingress UI into one supervised process.

Everything below is deliberately assembled here rather than reaching for each
other directly, so the same pieces can be reused with a fake HA state adapter
(tests) or with no device server at all (standalone preview).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import pathlib
import shutil
import signal
import time
from typing import Any

from .engine import SceneEngine
from .ha_state import HaStateAdapter
from .loader import SceneRegistry
from .options import Options
from .scene_api import Controls
from .server import DeviceServer
from .vendor import matrix_studio_protocol as protocol
from .web import IngressWeb

_LOGGER = logging.getLogger(__name__)

#: Bundled user-scene examples. Missing files are copied into the configured
#: scenes directory on startup; existing user files are never overwritten.
EXAMPLE_SCENES_DIR = pathlib.Path(__file__).resolve().parents[1] / "example_scenes"


def configure_logging(level: int) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


class MatrixStudioApp:
    def __init__(self, options: Options, *, state_adapter: HaStateAdapter | None = None) -> None:
        self.options = options
        self.controls = Controls(
            brightness=options.brightness,
            blank=options.blank,
            active_scene=options.active_scene,
        )
        self.registry = SceneRegistry(options.scenes_dir)
        self.registry.load()
        self.state = state_adapter if state_adapter is not None else HaStateAdapter(options)
        self.engine = SceneEngine(
            self.registry,
            self.controls,
            target_fps=options.target_fps,
            home_state_provider=self.state.snapshot,
            hot_reload=options.hot_reload,
        )
        self.server = DeviceServer(
            self.engine.bus,
            self.controls,
            path=options.ws_path,
            frame_interval_hint_ms=options.frame_interval_hint_ms,
        )
        self.web = IngressWeb(self)
        self._started_at = time.time()
        self._resolve_initial_scene()

    def _resolve_initial_scene(self) -> None:
        available = self.registry.names()
        if self.controls.active_scene in available:
            return
        fallback = available[0] if available else "plasma"
        if available:
            _LOGGER.warning(
                "configured scene %r is not available (have: %s); starting with %r instead",
                self.controls.active_scene,
                ", ".join(available),
                fallback,
            )
        else:
            _LOGGER.error("no scenes loaded at all; the engine will emit black frames")
        self.controls.active_scene = fallback

    # ------------------------------------------------------------------ actions

    def set_scene(self, name: str) -> bool:
        if not self.engine.select_scene(name):
            return False
        _LOGGER.info("active scene set to %r", name)
        return True

    def set_brightness(self, value: int) -> int:
        self.controls.brightness = max(0, min(255, int(value)))
        self.server.notify_controls_changed()
        _LOGGER.info("brightness set to %d", self.controls.brightness)
        return self.controls.brightness

    def set_blank(self, blank: bool) -> bool:
        self.controls.blank = bool(blank)
        self.server.notify_controls_changed()
        _LOGGER.info("display %s", "blanked" if blank else "resumed")
        return self.controls.blank

    def reload_scenes(self) -> None:
        self.engine.reload_scenes()
        self._resolve_initial_scene()

    # ------------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        return {
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "protocol_version": protocol.PROTOCOL_VERSION,
            "panel": {"width": self.controls.width, "height": self.controls.height, "pixel_format": "RGB565"},
            "controls": {
                "brightness": self.controls.clamped_brightness(),
                "blank": self.controls.blank,
                "active_scene": self.controls.active_scene,
            },
            "engine": {
                **self.engine.stats.as_dict(),
                "target_fps": self.engine.target_fps,
            },
            "scenes": [
                {
                    "name": entry.name,
                    "source": entry.source,
                    "description": entry.description,
                    "ok": entry.ok,
                    "error": entry.error,
                }
                for entry in self.registry.entries()
            ],
            "scenes_dir": self.options.scenes_dir,
            "devices": self.server.connections(),
            "device_count": self.server.device_count,
            "server": {
                "ws_port": self.server.port if self.server.port is not None else self.options.ws_port,
                "ws_path": self.options.ws_path,
                "total_connections": self.server.total_connections,
                "rejected_connections": self.server.rejected_connections,
            },
            "home_assistant": self.state.status(),
        }

    # ---------------------------------------------------------------- lifecycle

    def ensure_scenes_dir(self) -> None:
        """Create the user scene directory and add any missing bundled examples."""
        try:
            directory = pathlib.Path(self.options.scenes_dir)
            directory.mkdir(parents=True, exist_ok=True)

            copied: list[str] = []
            if EXAMPLE_SCENES_DIR.is_dir():
                for source in EXAMPLE_SCENES_DIR.iterdir():
                    if not source.is_file():
                        continue
                    destination = directory / source.name
                    if destination.exists():
                        continue
                    shutil.copy2(source, destination)
                    copied.append(source.name)

            if copied:
                _LOGGER.info("added %d missing example file(s) to %s: %s", len(copied), directory, ", ".join(copied))
                self.registry.load()
        except OSError as exc:
            _LOGGER.warning("could not prepare scenes directory %s: %s", self.options.scenes_dir, exc)

    async def start(self, *, with_device_server: bool = True, with_web: bool = True) -> None:
        self.ensure_scenes_dir()
        await self.state.start()
        await self.engine.start()
        if with_device_server:
            await self.server.start(port=self.options.ws_port)
            if self.options.ws_port != protocol.DEFAULT_WS_PORT:
                _LOGGER.warning(
                    "ws_port is %d, but the add-on's published container port is %d. "
                    "Change the port mapping in the add-on's Network panel to match, "
                    "or the device will not be able to reach the server.",
                    self.options.ws_port,
                    protocol.DEFAULT_WS_PORT,
                )
        if with_web:
            await self.web.start(port=self.options.ingress_port)

    async def stop(self) -> None:
        await self.web.stop()
        await self.server.stop()
        await self.engine.stop()
        await self.state.stop()


async def run(options: Options | None = None) -> None:
    """Start everything and block until SIGTERM/SIGINT."""
    options = options or Options.load()
    configure_logging(options.python_log_level)
    _LOGGER.info(
        "Matrix Studio starting: scene=%r fps=%d brightness=%d scenes_dir=%s",
        options.active_scene,
        options.target_fps,
        options.brightness,
        options.scenes_dir,
    )
    studio = MatrixStudioApp(options)
    await studio.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGTERM", "SIGINT"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(signal, signal_name), stop_event.set)
    try:
        await stop_event.wait()
    finally:
        _LOGGER.info("shutting down")
        await studio.stop()
