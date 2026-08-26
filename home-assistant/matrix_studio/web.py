"""Ingress UI and preview/emulator HTTP surface.

Deliberately tiny: static HTML/CSS/JS plus a handful of JSON endpoints and a
PNG of the most recent frame. No frontend framework, no build step.

Home Assistant's ingress proxy mounts this app under
`/api/hassio_ingress/<token>/` and passes the prefix in the `X-Ingress-Path`
header, so `index.html` is served with a matching `<base href>` and every asset
and fetch in the page is a relative URL. The same app runs unchanged (base
`./`) when started standalone by `python -m matrix_studio.preview --serve`.

Nothing in here is on the render or streaming path: the UI only reads
already-computed state and pokes `Controls`.
"""
from __future__ import annotations

import io
import logging
import pathlib
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .framebuffer import rgb565_to_image
from .server import MAX_OTA_IMAGE_BYTES, OtaUpdateError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .app import MatrixStudioApp

_LOGGER = logging.getLogger(__name__)

STATIC_DIR = pathlib.Path(__file__).parent / "static"
MAX_PREVIEW_SCALE = 8


class IngressWeb:
    """The ingress/preview aiohttp application."""

    def __init__(self, studio: "MatrixStudioApp") -> None:
        self.studio = studio
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None

    # ------------------------------------------------------------------- setup

    def make_app(self) -> web.Application:
        # The largest valid firmware fits a 3 MiB OTA slot. Keep a small margin
        # for HTTP framing while still rejecting accidental giant uploads.
        app = web.Application(client_max_size=MAX_OTA_IMAGE_BYTES + 64 * 1024)
        app.router.add_get("/", self.index)
        app.router.add_get("/index.html", self.index)
        app.router.add_get("/api/status", self.status)
        app.router.add_get("/api/preview.png", self.preview_png)
        app.router.add_post("/api/scene", self.set_scene)
        app.router.add_post("/api/brightness", self.set_brightness)
        app.router.add_post("/api/blank", self.set_blank)
        app.router.add_post("/api/reload", self.reload_scenes)
        app.router.add_post("/api/ota", self.ota_update)
        app.router.add_static("/static/", STATIC_DIR, name="static")
        return app

    async def start(self, host: str = "0.0.0.0", port: int = 8099) -> int:
        self._runner = web.AppRunner(self.make_app(), access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        self._port = port
        if port == 0:
            for socket in getattr(self._site._server, "sockets", None) or []:  # noqa: SLF001
                self._port = int(socket.getsockname()[1])
                break
        _LOGGER.info("ingress/preview UI listening on http://%s:%s/", host, self._port)
        return self._port

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    @property
    def port(self) -> int | None:
        return self._port

    # ---------------------------------------------------------------- handlers

    async def index(self, request: web.Request) -> web.Response:
        prefix = request.headers.get("X-Ingress-Path", "").rstrip("/")
        base = f"{prefix}/" if prefix else "./"
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return web.Response(text=html.replace("{{BASE}}", base), content_type="text/html")

    async def status(self, request: web.Request) -> web.Response:
        return web.json_response(self.studio.status())

    async def preview_png(self, request: web.Request) -> web.StreamResponse:
        try:
            scale = int(request.query.get("scale", "4"))
        except ValueError:
            scale = 4
        scale = max(1, min(MAX_PREVIEW_SCALE, scale))

        frame = self.studio.engine.latest_frame()
        if frame is None:
            image = rgb565_to_image(self.studio.engine.black_frame_pixels())
        else:
            # Round-trip through RGB565 so the preview shows what the panel
            # will actually display, quantisation included.
            image = rgb565_to_image(frame.pixels)
        if scale > 1:
            image = image.resize((image.width * scale, image.height * scale), resample=0)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return web.Response(
            body=buffer.getvalue(),
            content_type="image/png",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    async def set_scene(self, request: web.Request) -> web.Response:
        body = await self._json(request)
        name = str(body.get("name", ""))
        if not self.studio.set_scene(name):
            return web.json_response({"ok": False, "error": f"unknown scene {name!r}"}, status=400)
        return web.json_response({"ok": True, "active_scene": self.studio.controls.active_scene})

    async def set_brightness(self, request: web.Request) -> web.Response:
        body = await self._json(request)
        try:
            value = int(body.get("value"))
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "error": "value must be an integer 0-255"}, status=400)
        self.studio.set_brightness(value)
        return web.json_response({"ok": True, "brightness": self.studio.controls.clamped_brightness()})

    async def set_blank(self, request: web.Request) -> web.Response:
        body = await self._json(request)
        self.studio.set_blank(bool(body.get("blank")))
        return web.json_response({"ok": True, "blank": self.studio.controls.blank})

    async def reload_scenes(self, request: web.Request) -> web.Response:
        self.studio.reload_scenes()
        return web.json_response({"ok": True, "scenes": self.studio.engine.registry.names()})

    async def ota_update(self, request: web.Request) -> web.Response:
        try:
            connection_id = int(request.query.get("connection_id", ""))
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "error": "connection_id must be an integer"}, status=400)

        image = await request.read()
        if not image:
            return web.json_response({"ok": False, "error": "firmware image is empty"}, status=400)

        try:
            await self.studio.server.ota_update(connection_id, image)
        except OtaUpdateError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=409)

        return web.json_response(
            {
                "ok": True,
                "connection_id": connection_id,
                "bytes": len(image),
                "message": "firmware committed; device rebooting",
            }
        )

    @staticmethod
    async def _json(request: web.Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - tolerate empty/garbage bodies
            return {}
        return body if isinstance(body, dict) else {}
