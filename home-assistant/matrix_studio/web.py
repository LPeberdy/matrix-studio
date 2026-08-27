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

import asyncio
import base64
import binascii
from concurrent.futures import ThreadPoolExecutor
import io
import logging
import pathlib
import re
import secrets
import time
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .framebuffer import rgb565_to_image
from .server import MAX_OTA_IMAGE_BYTES, OtaUpdateError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .app import MatrixStudioApp

_LOGGER = logging.getLogger(__name__)

STATIC_DIR = pathlib.Path(__file__).parent / "static"
MAX_PREVIEW_SCALE = 8
MAX_SCENE_SOURCE_BYTES = 128 * 1024
_SCENE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OTA_UPLOAD_TTL_SECONDS = 15 * 60


def _encode_preview_png(pixels: bytes, scale: int) -> bytes:
    """Encode outside the event loop so preview work cannot stall frame sends."""
    image = rgb565_to_image(pixels)
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale), resample=0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class IngressWeb:
    """The ingress/preview aiohttp application."""

    def __init__(self, studio: "MatrixStudioApp") -> None:
        self.studio = studio
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None
        self._preview_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="matrix-preview")
        self._preview_lock = asyncio.Lock()
        self._preview_cache_frame: object | None = None
        self._preview_cache_scale: int | None = None
        self._preview_cache_body: bytes | None = None
        self._ota_upload_id: str | None = None
        self._ota_upload = bytearray()
        self._ota_upload_started_at = 0.0
        self._ota_upload_committing = False
        self._ota_expiry_handle: asyncio.TimerHandle | None = None

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
        app.router.add_put("/api/scenes/{name}", self.install_scene)
        app.router.add_post("/api/scenes/{name}", self.install_scene)
        app.router.add_post("/api/brightness", self.set_brightness)
        app.router.add_post("/api/blank", self.set_blank)
        app.router.add_post("/api/reload", self.reload_scenes)
        app.router.add_post("/api/ota", self.ota_update)
        app.router.add_post("/api/ota/stage", self.ota_stage)
        app.router.add_post("/api/ota/commit", self.ota_commit)
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
        self._clear_ota_upload()
        self._preview_executor.shutdown(wait=True, cancel_futures=True)

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

        try:
            after = int(request.query["after"])
        except (KeyError, TypeError, ValueError):
            after = None

        # Long-poll the engine's actual frame boundary instead of making the
        # browser run an unrelated timer. Two free-running clocks periodically
        # phase past one another and manifest as the reported move/freeze beat.
        frame = self.studio.engine.latest_frame()
        if after is not None and not self.studio.controls.blank:
            frame = await self.studio.engine.bus.next_after(after)
        # Use one state snapshot for both pixels and response marker. A control
        # transition after this point wakes the next long-poll request.
        blank = self.studio.controls.blank
        if blank:
            frame = None

        # Coalesce all clients around one encode at a time. This both reuses a
        # frame for concurrent tabs and prevents preview requests from queuing
        # jobs in the renderer's default executor.
        async with self._preview_lock:
            if (
                self._preview_cache_body is not None
                and self._preview_cache_frame is frame
                and self._preview_cache_scale == scale
            ):
                body = self._preview_cache_body
            else:
                # RGB565 bytes are immutable, so the dedicated worker can
                # safely encode this snapshot while the engine moves on.
                pixels = self.studio.engine.black_frame_pixels() if frame is None else frame.pixels
                loop = asyncio.get_running_loop()
                body = await loop.run_in_executor(self._preview_executor, _encode_preview_png, pixels, scale)
                self._preview_cache_frame = frame
                self._preview_cache_scale = scale
                self._preview_cache_body = body
        marker = "blank" if blank or frame is None else str(frame.timestamp_ms)
        return web.Response(
            body=body,
            content_type="image/png",
            headers={"Cache-Control": "no-store, max-age=0", "X-Matrix-Frame": marker},
        )

    async def set_scene(self, request: web.Request) -> web.Response:
        body = await self._json(request)
        name = str(body.get("name", ""))
        if not self.studio.set_scene(name):
            return web.json_response({"ok": False, "error": f"unknown scene {name!r}"}, status=400)
        return web.json_response({"ok": True, "active_scene": self.studio.controls.active_scene})

    async def install_scene(self, request: web.Request) -> web.Response:
        """Create or replace one user scene in the add-on's persistent config.

        This is the stable agent-facing write surface. Callers provide scene
        source, not filesystem paths; Matrix Studio owns where the file lives,
        reloads it, validates that the loader accepts it, and can activate it.
        """
        name = request.match_info.get("name", "").strip()
        if not _SCENE_NAME.fullmatch(name):
            return web.json_response(
                {
                    "ok": False,
                    "error": "scene name must match ^[a-z][a-z0-9_]{0,63}$",
                },
                status=400,
            )

        body = await self._json(request)
        source = body.get("source")
        if not isinstance(source, str) or not source.strip():
            return web.json_response({"ok": False, "error": "source must be a non-empty string"}, status=400)
        if len(source.encode("utf-8")) > MAX_SCENE_SOURCE_BYTES:
            return web.json_response(
                {"ok": False, "error": f"scene source exceeds {MAX_SCENE_SOURCE_BYTES} bytes"},
                status=413,
            )

        # Catch syntax errors before touching the current file.
        try:
            compile(source, f"{name}.py", "exec")
        except SyntaxError as exc:
            return web.json_response(
                {"ok": False, "error": f"scene syntax error: {exc.msg} (line {exc.lineno})"},
                status=400,
            )

        directory = pathlib.Path(self.studio.options.scenes_dir)
        destination = directory / f"{name}.py"
        temporary = directory / f".{name}.py.tmp"
        previous = destination.read_bytes() if destination.exists() else None
        created = previous is None

        try:
            directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(source, encoding="utf-8")
            temporary.replace(destination)
            self.studio.reload_scenes()

            entry = self.studio.registry.get(name)
            if entry is None or not entry.ok:
                detail = entry.error if entry is not None else "scene was not discovered after reload"
                raise ValueError(detail)
        except Exception as exc:  # noqa: BLE001 - failed installs must roll back atomically
            temporary.unlink(missing_ok=True)
            try:
                if previous is None:
                    destination.unlink(missing_ok=True)
                else:
                    destination.write_bytes(previous)
                self.studio.reload_scenes()
            except OSError:
                _LOGGER.exception("failed to roll back scene %r after install error", name)
            return web.json_response({"ok": False, "error": f"scene failed to load: {exc}"}, status=400)

        activated = bool(body.get("activate", False))
        if activated and not self.studio.set_scene(name):
            return web.json_response(
                {"ok": False, "error": "scene loaded but could not be activated"},
                status=409,
            )

        entry = self.studio.registry.get(name)
        return web.json_response(
            {
                "ok": True,
                "name": name,
                "created": created,
                "source": "user",
                "description": entry.description if entry is not None else "",
                "activated": activated,
                "active_scene": self.studio.controls.active_scene,
            },
            status=201 if created else 200,
        )

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

    async def ota_stage(self, request: web.Request) -> web.Response:
        """Stage one bounded base64 chunk for clients that cannot send files.

        Home Assistant's ingress UI continues to use the raw `/api/ota`
        upload. This companion path gives API clients a JSON-only transport
        without ever placing an entire firmware image in one request.
        """
        self._expire_ota_upload()
        body = await self._json(request)
        offset = body.get("offset")
        if isinstance(offset, bool) or not isinstance(offset, int):
            return web.json_response({"ok": False, "error": "offset must be an integer"}, status=400)
        try:
            chunk = base64.b64decode(str(body.get("data", "")), validate=True)
        except (binascii.Error, ValueError):
            return web.json_response({"ok": False, "error": "data must be valid base64"}, status=400)
        if not chunk:
            return web.json_response({"ok": False, "error": "firmware chunk is empty"}, status=400)

        if self._ota_upload_committing:
            return web.json_response({"ok": False, "error": "firmware upload is being committed"}, status=409)
        if offset == 0:
            if self._ota_upload_id is not None and body.get("reset") is not True:
                return web.json_response(
                    {"ok": False, "error": "firmware upload already staged; pass reset=true to replace it"},
                    status=409,
                )
            self._clear_ota_upload()
            self._ota_upload_id = secrets.token_hex(12)
            self._ota_upload_started_at = time.monotonic()
            self._schedule_ota_expiry()
        elif body.get("upload_id") != self._ota_upload_id or self._ota_upload_id is None:
            return web.json_response({"ok": False, "error": "unknown firmware upload"}, status=400)
        if offset != len(self._ota_upload):
            return web.json_response(
                {"ok": False, "error": f"expected offset {len(self._ota_upload)}, got {offset}"}, status=400
            )
        if len(self._ota_upload) + len(chunk) > MAX_OTA_IMAGE_BYTES:
            return web.json_response({"ok": False, "error": "firmware image exceeds OTA partition"}, status=413)

        self._ota_upload.extend(chunk)
        return web.json_response({"ok": True, "upload_id": self._ota_upload_id, "bytes": len(self._ota_upload)})

    async def ota_commit(self, request: web.Request) -> web.Response:
        self._expire_ota_upload()
        body = await self._json(request)
        if body.get("upload_id") != self._ota_upload_id or self._ota_upload_id is None:
            return web.json_response({"ok": False, "error": "unknown firmware upload"}, status=400)
        try:
            connection_id = int(body.get("connection_id"))
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "error": "connection_id must be an integer"}, status=400)

        if self._ota_upload_committing:
            return web.json_response({"ok": False, "error": "firmware upload is already being committed"}, status=409)

        image = bytes(self._ota_upload)
        upload_id = self._ota_upload_id
        self._ota_upload_committing = True
        try:
            await self.studio.server.ota_update(connection_id, image)
        except OtaUpdateError as exc:
            # Retain the staged bytes so a transient disconnect or validation
            # failure can be retried without transferring the whole image again.
            self._ota_upload_started_at = time.monotonic()
            self._schedule_ota_expiry()
            return web.json_response({"ok": False, "error": str(exc)}, status=409)
        finally:
            self._ota_upload_committing = False
        if self._ota_upload_id == upload_id:
            self._clear_ota_upload()
        return web.json_response(
            {
                "ok": True,
                "connection_id": connection_id,
                "bytes": len(image),
                "message": "firmware committed; device rebooting",
            }
        )

    def _expire_ota_upload(self) -> None:
        self._ota_expiry_handle = None
        if self._ota_upload_id is None or self._ota_upload_committing:
            return
        remaining = _OTA_UPLOAD_TTL_SECONDS - (time.monotonic() - self._ota_upload_started_at)
        if remaining <= 0:
            self._clear_ota_upload()
        else:
            self._ota_expiry_handle = asyncio.get_running_loop().call_later(remaining, self._expire_ota_upload)

    def _schedule_ota_expiry(self) -> None:
        if self._ota_expiry_handle is not None:
            self._ota_expiry_handle.cancel()
        self._ota_expiry_handle = asyncio.get_running_loop().call_later(
            _OTA_UPLOAD_TTL_SECONDS, self._expire_ota_upload
        )

    def _clear_ota_upload(self) -> None:
        if self._ota_expiry_handle is not None:
            self._ota_expiry_handle.cancel()
            self._ota_expiry_handle = None
        self._ota_upload_id = None
        self._ota_upload.clear()
        self._ota_upload_started_at = 0.0

    @staticmethod
    async def _json(request: web.Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - tolerate empty/garbage bodies
            return {}
        return body if isinstance(body, dict) else {}
