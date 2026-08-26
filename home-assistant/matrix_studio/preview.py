"""Emulator / preview — render scenes with no ESP32 (and no Home Assistant).

    python -m matrix_studio.preview --list
    python -m matrix_studio.preview --scene landscape --out /tmp/frame.png
    python -m matrix_studio.preview --scene starfield --frames 90 --gif /tmp/out.gif
    python -m matrix_studio.preview --serve            # browser preview on :8099
    python -m matrix_studio.preview --serve --device-server   # + a real :7887 endpoint

Everything renders through the same `SceneEngine` and the same RGB565
conversion the device sees, and the still/GIF output is round-tripped back from
RGB565, so what you look at is what the panel would show — not a prettier
pre-quantisation version.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import pathlib
import sys
from typing import Sequence

from PIL import Image

from .app import MatrixStudioApp, configure_logging
from .framebuffer import coerce_panel_image, image_to_rgb565, rgb565_to_image
from .loader import SceneRegistry
from .options import Options
from .scene_api import Controls, EntityState, HomeState

_LOGGER = logging.getLogger("matrix_studio.preview")


class _StaticStateAdapter:
    """Stands in for `HaStateAdapter` when previewing without Home Assistant."""

    def __init__(self, snapshot: HomeState) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> HomeState:
        return self._snapshot

    def status(self) -> dict:
        return {
            "configured": False,
            "available": self._snapshot.available,
            "updated_at": self._snapshot.updated_at,
            "age_seconds": None,
            "poll_count": 0,
            "entity_count": len(self._snapshot.entities),
            "tracked_entities": len(self._snapshot.entities),
            "lights_on": self._snapshot.lights_on,
            "lights_total": self._snapshot.lights_total,
            "last_error": None,
        }

    async def start(self) -> None:  # pragma: no cover - trivial
        return None

    async def stop(self) -> None:  # pragma: no cover - trivial
        return None


def fake_home_state(path: str | None = None) -> HomeState:
    """A believable HomeState for previewing HA-reactive scenes offline.

    With `--home-state FILE`, the file is a JSON list in the same shape Home
    Assistant's `/api/states` returns, so a real dump can be replayed.
    """
    if path:
        payload = json.loads(pathlib.Path(path).read_text())
        entities = {
            item["entity_id"]: EntityState(item["entity_id"], str(item.get("state", "")), item.get("attributes") or {})
            for item in payload
            if isinstance(item, dict) and isinstance(item.get("entity_id"), str)
        }
        lights = tuple(sorted(eid for eid in entities if eid.startswith("light.")))
        roles = {
            role: eid
            for role, eid in (
                ("indoor_temperature", next((e for e in entities if "indoor" in e or "temperature" in e), "")),
                ("weather", next((e for e in entities if e.startswith("weather.")), "")),
            )
            if eid
        }
        return HomeState(entities=entities, roles=roles, light_entity_ids=lights, available=True, updated_at=0.0)

    demo = {
        "light.demo_1": "on",
        "light.demo_2": "off",
        "light.demo_3": "on",
        "light.demo_4": "off",
        "light.demo_5": "on",
        "light.demo_6": "on",
        "sensor.demo_indoor_temperature": "21.4",
        "sensor.demo_outdoor_temperature": "8.2",
        "weather.demo": "rainy",
        "binary_sensor.demo_occupancy": "on",
    }
    entities = {eid: EntityState(eid, state, {}) for eid, state in demo.items()}
    return HomeState(
        entities=entities,
        roles={
            "indoor_temperature": "sensor.demo_indoor_temperature",
            "outdoor_temperature": "sensor.demo_outdoor_temperature",
            "weather": "weather.demo",
            "occupancy": "binary_sensor.demo_occupancy",
        },
        light_entity_ids=tuple(eid for eid in demo if eid.startswith("light.")),
        available=True,
        updated_at=0.0,
    )


def render_frames(
    scene_name: str,
    *,
    frames: int = 1,
    fps: float = 24.0,
    start_time: float = 0.0,
    scenes_dir: str | None = None,
    home: HomeState | None = None,
    brightness: int = 160,
) -> list[Image.Image]:
    """Render `frames` frames of a scene, as the panel would show them.

    Used by the CLI and by tests; raises `KeyError` for an unknown scene and
    lets scene exceptions propagate (unlike the engine, which absorbs them) so
    that authoring mistakes are visible while developing.
    """
    registry = SceneRegistry(scenes_dir)
    registry.load()
    entry = registry.get(scene_name)
    if entry is None or not entry.ok or entry.scene is None:
        detail = entry.error if entry is not None else "no such scene"
        raise KeyError(f"scene {scene_name!r} is not available: {detail}")

    controls = Controls(brightness=brightness, active_scene=scene_name)
    home = home if home is not None else fake_home_state()
    output: list[Image.Image] = []
    for index in range(max(1, frames)):
        t = start_time + index / fps
        image = coerce_panel_image(entry.scene.render(t, home, controls))
        output.append(rgb565_to_image(image_to_rgb565(image)))
    return output


async def _serve(options: Options, *, device_server: bool, home: HomeState) -> None:
    studio = MatrixStudioApp(options, state_adapter=_StaticStateAdapter(home))  # type: ignore[arg-type]
    await studio.start(with_device_server=device_server, with_web=True)
    port = studio.web.port
    print(f"Matrix Studio preview: http://localhost:{port}/", flush=True)
    if device_server:
        print(f"Device endpoint:       ws://localhost:{studio.server.port}{options.ws_path}", flush=True)
    print("Press Ctrl-C to stop.", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await studio.stop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matrix_studio.preview", description=__doc__)
    parser.add_argument("--scene", default="plasma", help="scene name (default: plasma)")
    parser.add_argument("--scenes-dir", default=None, help="extra directory of user scenes to load")
    parser.add_argument("--list", action="store_true", help="list available scenes and exit")
    parser.add_argument("--frames", type=int, default=1, help="how many frames to render")
    parser.add_argument("--fps", type=float, default=24.0, help="time step between frames")
    parser.add_argument("--start", type=float, default=0.0, help="scene time of the first frame, in seconds")
    parser.add_argument("--scale", type=int, default=6, help="nearest-neighbour upscale for saved images")
    parser.add_argument("--out", default=None, help="write the last rendered frame here as PNG")
    parser.add_argument("--gif", default=None, help="write all rendered frames here as an animated GIF")
    parser.add_argument("--serve", action="store_true", help="serve the live browser preview instead")
    parser.add_argument("--port", type=int, default=8099, help="preview HTTP port (with --serve)")
    parser.add_argument(
        "--device-server",
        action="store_true",
        help="with --serve, also start the real Protocol v1 endpoint so firmware can connect",
    )
    parser.add_argument("--home-state", default=None, help="JSON file in /api/states shape to feed scenes")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    configure_logging(logging.DEBUG if args.verbose else logging.INFO)
    home = fake_home_state(args.home_state)

    if args.list:
        registry = SceneRegistry(args.scenes_dir)
        registry.load()
        for entry in registry.entries():
            status = "ok  " if entry.ok else "FAIL"
            print(f"{status} {entry.name:<16} [{entry.source}] {entry.description or ''}")
            if entry.error:
                print(f"       error: {entry.error.splitlines()[-1]}")
        return 0

    if args.serve:
        options = Options.from_mapping(
            {
                "active_scene": args.scene,
                "scenes_dir": args.scenes_dir or "",
                "ingress_port": args.port,
                "hot_reload": True,
            }
        )
        try:
            asyncio.run(_serve(options, device_server=args.device_server, home=home))
        except KeyboardInterrupt:
            pass
        return 0

    try:
        images = render_frames(
            args.scene,
            frames=args.frames,
            fps=args.fps,
            start_time=args.start,
            scenes_dir=args.scenes_dir,
            home=home,
        )
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    scale = max(1, min(16, args.scale))
    scaled = [image.resize((image.width * scale, image.height * scale), Image.NEAREST) for image in images]

    if args.gif:
        scaled[0].save(
            args.gif,
            save_all=True,
            append_images=scaled[1:],
            duration=int(1000 / max(args.fps, 1)),
            loop=0,
        )
        print(f"wrote {len(scaled)} frame(s) to {args.gif}")
    if args.out or not args.gif:
        out = args.out or "matrix-studio-preview.png"
        scaled[-1].save(out)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(main())
