"""Protocol v1 WebSocket server — the half of the wire contract HA owns.

The ESP32 is the client and connects out to us (docs/protocol.md §1), so this
is a plain aiohttp WebSocket endpoint on `ws_port` at `ws_path`. Every framing
decision here comes from `docs/protocol.md`; all encoding/decoding goes through
the frozen reference codec in `matrix_studio/vendor/matrix_studio_protocol.py`
so this module contains no wire-format knowledge of its own.

Operational rules this module implements:

* zero connected devices is healthy, not an error (§3.4);
* each connection is an independent session with its own `sequence` from 0 (§3.3);
* a slow device silently skips frames rather than backing up memory (§3, gaps legal);
* framing corruption (magic/version) closes the connection, a single malformed
  message does not (§3.5);
* PING every 10 s while idle, PONG within 10 s or the connection is dropped (§3.1).
"""
from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import itertools
import logging
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from aiohttp import WSMsgType, web

from .framebuffer import FrameBus
from .scene_api import PANEL_HEIGHT, PANEL_WIDTH, Controls
from .vendor import matrix_studio_protocol as protocol

_LOGGER = logging.getLogger(__name__)

HELLO_TIMEOUT_S = protocol.HELLO_TIMEOUT_MS / 1000.0
PING_INTERVAL_S = protocol.PING_INTERVAL_MS / 1000.0
PONG_TIMEOUT_S = protocol.PONG_TIMEOUT_MS / 1000.0
OTA_STATUS_TIMEOUT_S = 5.0
MAX_OTA_IMAGE_BYTES = 3 * 1024 * 1024

#: WebSocket close codes we use (RFC 6455).
_CLOSE_PROTOCOL_ERROR = 1002
_CLOSE_GOING_AWAY = 1001

_EXTENSION_TYPE_RANGE = range(0x80, 0xFF)


class OtaUpdateError(RuntimeError):
    """Raised when a device refuses or fails a firmware update."""


@dataclass
class DeviceConnection:
    """Bookkeeping for one connected device, surfaced by the ingress UI."""

    id: int
    remote: str
    expected_frame_interval_s: float = 0.04
    device_id: str = ""
    fw_version: str = ""
    width: int = 0
    height: int = 0
    connected_at: float = field(default_factory=time.time)
    sequence: int = 0
    frames_sent: int = 0
    frames_dropped: int = 0
    last_frame_at: float = 0.0
    last_rtt_ms: float | None = None
    last_pong_at: float = 0.0
    sent_brightness: int | None = None
    sent_blank: bool | None = None
    pending_ping: int | None = None
    pending_ping_at: float = 0.0
    ota_active: bool = False
    ota_bytes_sent: int = 0
    ota_total_bytes: int = 0
    ota_last_error: str = ""
    ota_image: bytes | None = field(default=None, repr=False)
    ota_done: asyncio.Future[None] | None = field(default=None, repr=False)
    ota_status: asyncio.Queue[protocol.Status] = field(default_factory=asyncio.Queue, repr=False)
    _last_send_at: float | None = field(default=None, repr=False)
    _send_intervals: deque[float] = field(default_factory=lambda: deque(maxlen=120), repr=False)
    _drop_counter: Callable[[], int] | None = field(default=None, repr=False)

    def note_frame_sent(self, sent_at: float) -> None:
        """Record send completion time for a bounded, recent cadence window."""
        if self._last_send_at is not None:
            self._send_intervals.append(sent_at - self._last_send_at)
        self._last_send_at = sent_at

    def track_drops(self, counter: Callable[[], int]) -> None:
        """Read the subscription's live drop count even while a send blocks."""
        self._drop_counter = counter

    def reset_cadence(self) -> None:
        """Start a fresh measurement window after an intentional stream pause."""
        self._last_send_at = None
        self._send_intervals.clear()

    def as_dict(self, *, monotonic_now: float | None = None) -> dict[str, Any]:
        now = time.time()
        monotonic_now = time.monotonic() if monotonic_now is None else monotonic_now
        intervals = tuple(self._send_intervals)
        interval_total = sum(intervals)
        unfinished_gap = monotonic_now - self._last_send_at if self._last_send_at is not None else None
        stale_after = max(1.0, self.expected_frame_interval_s * 3.0)
        cadence_stale = unfinished_gap is not None and unfinished_gap > stale_after
        send_fps = len(intervals) / interval_total if intervals and interval_total > 0 and not cadence_stale else None
        send_jitter_ms = (
            statistics.pstdev(intervals) * 1000.0 if len(intervals) >= 2 and not cadence_stale else None
        )
        observed_gaps = intervals + ((unfinished_gap,) if unfinished_gap is not None else ())
        max_frame_gap_ms = max(observed_gaps) * 1000.0 if observed_gaps else None
        frames_dropped = self._drop_counter() if self._drop_counter is not None else self.frames_dropped
        return {
            "id": self.id,
            "remote": self.remote,
            "device_id": self.device_id,
            "fw_version": self.fw_version,
            "resolution": f"{self.width}x{self.height}",
            "connected_seconds": round(now - self.connected_at, 1),
            "frames_sent": self.frames_sent,
            "frames_dropped": frames_dropped,
            "sequence": self.sequence,
            "last_frame_age": round(now - self.last_frame_at, 2) if self.last_frame_at else None,
            "rtt_ms": round(self.last_rtt_ms, 1) if self.last_rtt_ms is not None else None,
            "ota": {
                "active": self.ota_active,
                "bytes_sent": self.ota_bytes_sent,
                "total_bytes": self.ota_total_bytes,
                "last_error": self.ota_last_error,
            },
            "send_fps": round(send_fps, 1) if send_fps is not None else None,
            "send_jitter_ms": round(send_jitter_ms, 1) if send_jitter_ms is not None else None,
            "max_frame_gap_ms": round(max_frame_gap_ms, 1) if max_frame_gap_ms is not None else None,
            "cadence_stale": cadence_stale,
        }


class DeviceServer:
    """Serves `FrameBus` output to every connected Protocol v1 device."""

    def __init__(
        self,
        bus: FrameBus,
        controls: Controls,
        *,
        path: str = protocol.DEFAULT_WS_PATH,
        frame_interval_hint_ms: int = 40,
        width: int = PANEL_WIDTH,
        height: int = PANEL_HEIGHT,
        ping_interval: float = PING_INTERVAL_S,
        pong_timeout: float = PONG_TIMEOUT_S,
        hello_timeout: float = HELLO_TIMEOUT_S,
    ) -> None:
        self.bus = bus
        self.controls = controls
        self.path = path if path.startswith("/") else "/" + path
        self.frame_interval_hint_ms = frame_interval_hint_ms
        self.width = width
        self.height = height
        self.ping_interval = ping_interval
        self.pong_timeout = pong_timeout
        self.hello_timeout = hello_timeout

        self._connections: dict[int, DeviceConnection] = {}
        self._ids = itertools.count(1)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None
        self.total_connections = 0
        self.rejected_connections = 0

    # ------------------------------------------------------------------ status

    @property
    def device_count(self) -> int:
        return len(self._connections)

    @property
    def port(self) -> int | None:
        """The port actually bound (useful when starting on port 0 in tests)."""
        return self._port

    def connections(self) -> list[dict[str, Any]]:
        return [connection.as_dict() for connection in self._connections.values()]

    def notify_controls_changed(self) -> None:
        """Push brightness/blank to every device promptly, even while idle."""
        self.bus.wake_all()

    async def ota_update(self, connection_id: int, image: bytes) -> None:
        """Install one ESP-IDF application image over the existing device session.

        The writer task owns all OTA traffic, so FRAME streaming pauses cleanly
        for the update and resumes only if the update fails. Every chunk waits
        for the firmware's STATUS acknowledgement before the next is sent.
        """
        connection = self._connections.get(connection_id)
        if connection is None:
            raise OtaUpdateError(f"device connection {connection_id} is not connected")
        if connection.ota_active or connection.ota_image is not None:
            raise OtaUpdateError("an OTA update is already in progress for this device")

        image = bytes(image)
        if not image:
            raise OtaUpdateError("firmware image is empty")
        if len(image) > MAX_OTA_IMAGE_BYTES:
            raise OtaUpdateError(
                f"firmware image is {len(image)} bytes; OTA app partition is {MAX_OTA_IMAGE_BYTES} bytes"
            )
        # ESP-IDF application images begin with the standard ESP image magic.
        # esp_ota_end() performs the authoritative validation on-device; this is
        # just an early guard against uploading an obviously wrong file.
        if image[0] != 0xE9:
            raise OtaUpdateError("file is not an ESP-IDF application image (missing 0xE9 image magic)")

        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()
        connection.ota_image = image
        connection.ota_done = done
        connection.ota_bytes_sent = 0
        connection.ota_total_bytes = len(image)
        connection.ota_last_error = ""
        self.bus.wake_all()

        try:
            await done
        finally:
            if connection.ota_done is done:
                connection.ota_done = None

    # --------------------------------------------------------------- lifecycle

    def make_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get(self.path, self.handle_connection)
        return app

    async def start(self, host: str = "0.0.0.0", port: int = protocol.DEFAULT_WS_PORT) -> int:
        self._runner = web.AppRunner(self.make_app(), access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        self._port = self._resolve_port(port)
        _LOGGER.info(
            "Protocol v1 device server listening on ws://%s:%s%s (0 devices connected is normal)",
            host,
            self._port,
            self.path,
        )
        return self._port

    def _resolve_port(self, requested: int) -> int:
        if requested != 0 or self._site is None:
            return requested
        for server in getattr(self._site._server, "sockets", None) or []:  # noqa: SLF001
            return int(server.getsockname()[1])
        return requested

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    # ------------------------------------------------------------- connection

    async def handle_connection(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=protocol.HEADER_SIZE_BYTES + protocol.MAX_PAYLOAD_BYTES + 1024)
        await ws.prepare(request)
        remote = request.remote or "?"
        connection = DeviceConnection(
            id=next(self._ids),
            remote=remote,
            expected_frame_interval_s=self.frame_interval_hint_ms / 1000.0,
        )
        _LOGGER.info("device connected from %s (connection %d)", remote, connection.id)

        try:
            if not await self._handshake(ws, connection):
                return ws
            self._connections[connection.id] = connection
            self.total_connections += 1
            await self._session(ws, connection)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await ws.close(code=_CLOSE_GOING_AWAY)
            raise
        except Exception:  # noqa: BLE001 - one bad device must not kill the server
            _LOGGER.exception("connection %d failed unexpectedly", connection.id)
            with contextlib.suppress(Exception):
                await self._send(ws, protocol.Status(protocol.StatusCode.ERR_INTERNAL, "server error").encode())
        finally:
            self._connections.pop(connection.id, None)
            if connection.ota_done is not None and not connection.ota_done.done():
                connection.ota_done.set_exception(OtaUpdateError("device disconnected during OTA update"))
            connection.ota_active = False
            connection.ota_image = None
            with contextlib.suppress(Exception):
                await ws.close()
            _LOGGER.info(
                "device %s disconnected (connection %d, %d frames sent)",
                connection.device_id or remote,
                connection.id,
                connection.frames_sent,
            )
        return ws

    async def _reject(
        self,
        ws: web.WebSocketResponse,
        connection: DeviceConnection,
        reason: str,
        status_code: int | None = None,
        status_message: str = "",
    ) -> bool:
        """Refuse a connection: count it, optionally STATUS, then close.

        The counter is bumped *before* the close so that by the time the peer
        observes the close, `rejected_connections` already reflects it.
        """
        self.rejected_connections += 1
        _LOGGER.warning("connection %d rejected: %s", connection.id, reason)
        if status_code is not None:
            with contextlib.suppress(Exception):
                await self._send(ws, protocol.Status(status_code, status_message).encode())
        with contextlib.suppress(Exception):
            await ws.close(code=_CLOSE_PROTOCOL_ERROR, message=reason.encode("utf-8")[:120])
        return False

    async def _handshake(self, ws: web.WebSocketResponse, connection: DeviceConnection) -> bool:
        """HELLO -> HELLO_ACK, per docs/protocol.md §3. False means 'rejected'."""
        version_note = f"server speaks protocol v{protocol.PROTOCOL_VERSION}"
        try:
            message = await asyncio.wait_for(ws.receive(), timeout=self.hello_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return await self._reject(ws, connection, f"no HELLO within {self.hello_timeout:.0f}s")

        if message.type is not WSMsgType.BINARY:
            return await self._reject(ws, connection, f"first message was {message.type.name}, expected binary HELLO")

        data: bytes = message.data
        try:
            header = protocol.decode_header(data)
        except protocol.ProtocolError as exc:
            # §3.5 rule 1: bad magic desynchronises the stream; close, no STATUS.
            return await self._reject(ws, connection, str(exc))

        if header.version != protocol.PROTOCOL_VERSION:
            return await self._reject(
                ws,
                connection,
                f"unsupported protocol version {header.version}",
                protocol.StatusCode.ERR_UNSUPPORTED_VERSION,
                version_note,
            )

        payload = data[protocol.HEADER_SIZE_BYTES :]
        if header.type != protocol.MessageType.HELLO or len(payload) != header.length:
            return await self._reject(
                ws,
                connection,
                f"expected a well-formed HELLO, got type {header.type:#x}",
                protocol.StatusCode.ERR_MALFORMED_PAYLOAD,
                "expected a well-formed HELLO",
            )

        try:
            hello = protocol.Hello.decode_payload(payload)
        except protocol.ProtocolError as exc:
            return await self._reject(
                ws, connection, f"malformed HELLO: {exc}", protocol.StatusCode.ERR_MALFORMED_PAYLOAD, str(exc)
            )

        if hello.protocol_version != protocol.PROTOCOL_VERSION:
            return await self._reject(
                ws,
                connection,
                f"device speaks protocol v{hello.protocol_version}",
                protocol.StatusCode.ERR_UNSUPPORTED_VERSION,
                version_note,
            )

        if (hello.width, hello.height) != (self.width, self.height):
            return await self._reject(
                ws,
                connection,
                f"device is {hello.width}x{hello.height}, server renders {self.width}x{self.height}",
                protocol.StatusCode.ERR_DIMENSION_MISMATCH,
                f"server renders {self.width}x{self.height}",
            )

        if hello.pixel_format != protocol.PixelFormat.RGB565:
            return await self._reject(
                ws,
                connection,
                f"unsupported pixel format {hello.pixel_format:#x}",
                protocol.StatusCode.ERR_MALFORMED_PAYLOAD,
                "server only produces RGB565",
            )

        connection.device_id = hello.device_id
        connection.fw_version = hello.fw_version
        connection.width = hello.width
        connection.height = hello.height

        await self._send(
            ws,
            protocol.HelloAck(
                protocol_version=protocol.PROTOCOL_VERSION,
                frame_interval_hint_ms=self.frame_interval_hint_ms,
                server_time_unix=int(time.time()),
            ).encode(),
        )
        _LOGGER.info(
            "handshake ok: device_id=%r fw=%r %dx%d (connection %d)",
            hello.device_id,
            hello.fw_version,
            hello.width,
            hello.height,
            connection.id,
        )
        return True

    async def _session(self, ws: web.WebSocketResponse, connection: DeviceConnection) -> None:
        reader = asyncio.create_task(self._reader(ws, connection), name=f"ms-reader-{connection.id}")
        writer = asyncio.create_task(self._writer(ws, connection), name=f"ms-writer-{connection.id}")
        done, pending = await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                raise exc

    # ---------------------------------------------------------------- reading

    async def _reader(self, ws: web.WebSocketResponse, connection: DeviceConnection) -> None:
        async for message in ws:
            if message.type is WSMsgType.BINARY:
                if not await self._handle_message(ws, connection, message.data):
                    return
            elif message.type is WSMsgType.TEXT:
                # Protocol v1 is binary-only; a stray text frame is odd but not fatal.
                _LOGGER.warning("connection %d sent a text frame; ignoring", connection.id)
            elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                return
            elif message.type is WSMsgType.ERROR:
                _LOGGER.warning("connection %d websocket error: %s", connection.id, ws.exception())
                return

    async def _handle_message(self, ws: web.WebSocketResponse, connection: DeviceConnection, data: bytes) -> bool:
        """Handle one inbound message. Returns False when the connection must close."""
        try:
            header = protocol.decode_header(data)
        except protocol.ProtocolError as exc:
            _LOGGER.warning("connection %d: %s; closing (stream desynchronised)", connection.id, exc)
            await ws.close(code=_CLOSE_PROTOCOL_ERROR, message=b"bad magic")
            return False

        if header.version != protocol.PROTOCOL_VERSION:
            await self._send(
                ws, protocol.Status(protocol.StatusCode.ERR_UNSUPPORTED_VERSION, "version changed mid-session").encode()
            )
            await ws.close(code=_CLOSE_PROTOCOL_ERROR, message=b"unsupported version")
            return False

        if header.length > protocol.MAX_PAYLOAD_BYTES:
            _LOGGER.warning(
                "connection %d declared a %d-byte payload (max %d); closing",
                connection.id,
                header.length,
                protocol.MAX_PAYLOAD_BYTES,
            )
            await ws.close(code=_CLOSE_PROTOCOL_ERROR, message=b"payload too large")
            return False

        payload = data[protocol.HEADER_SIZE_BYTES :]
        if len(payload) != header.length:
            # §3.5 rule 5: discard just this message, keep the session.
            _LOGGER.warning(
                "connection %d: declared length %d but %d bytes present; discarding message",
                connection.id,
                header.length,
                len(payload),
            )
            await self._send(
                ws, protocol.Status(protocol.StatusCode.ERR_MALFORMED_PAYLOAD, "length mismatch").encode()
            )
            return True

        try:
            message_type = protocol.MessageType(header.type)
        except ValueError:
            if header.type in _EXTENSION_TYPE_RANGE:
                _LOGGER.debug("connection %d: ignoring extension-range type %#x", connection.id, header.type)
            else:
                _LOGGER.warning("connection %d: unknown message type %#x", connection.id, header.type)
                await self._send(
                    ws, protocol.Status(protocol.StatusCode.ERR_UNKNOWN_TYPE, f"type {header.type:#x}").encode()
                )
            return True

        try:
            return await self._dispatch(ws, connection, message_type, payload)
        except protocol.ProtocolError as exc:
            _LOGGER.warning("connection %d: malformed %s: %s", connection.id, message_type.name, exc)
            await self._send(ws, protocol.Status(protocol.StatusCode.ERR_MALFORMED_PAYLOAD, str(exc)).encode())
            return True

    async def _dispatch(
        self,
        ws: web.WebSocketResponse,
        connection: DeviceConnection,
        message_type: "protocol.MessageType",
        payload: bytes,
    ) -> bool:
        if message_type is protocol.MessageType.PING:
            ping = protocol.Ping.decode_payload(payload)
            await self._send(ws, protocol.Pong(ping.nonce).encode())
        elif message_type is protocol.MessageType.PONG:
            pong = protocol.Pong.decode_payload(payload)
            if connection.pending_ping is not None and pong.nonce == connection.pending_ping:
                connection.last_rtt_ms = (time.monotonic() - connection.pending_ping_at) * 1000.0
                connection.pending_ping = None
            connection.last_pong_at = time.time()
        elif message_type is protocol.MessageType.STATUS:
            status = protocol.Status.decode_payload(payload)
            level = logging.INFO if status.code == protocol.StatusCode.OK else logging.WARNING
            _LOGGER.log(level, "connection %d STATUS %#06x: %s", connection.id, status.code, status.message)
            if connection.ota_active:
                connection.ota_status.put_nowait(status)
        elif message_type is protocol.MessageType.HELLO:
            _LOGGER.warning("connection %d sent a second HELLO; ignoring (each connection is one session)", connection.id)
        else:
            # HELLO_ACK / FRAME / BRIGHTNESS / BLANK / OTA_* are server -> device only.
            _LOGGER.warning(
                "connection %d sent %s, which is server->device only; ignoring", connection.id, message_type.name
            )
        return True

    # ---------------------------------------------------------------- writing

    async def _writer(self, ws: web.WebSocketResponse, connection: DeviceConnection) -> None:
        with self.bus.subscribe() as subscription:
            connection.track_drops(lambda: subscription.dropped)
            # Bring the freshly connected device in line with current controls.
            await self._flush_controls(ws, connection)
            while not ws.closed:
                if connection.ota_image is not None:
                    await self._perform_ota(ws, connection)
                    continue
                if connection.ota_active:
                    # OTA_COMMIT has been acknowledged and the device is about
                    # to reboot. Do not race its outbound STATUS with frames.
                    await asyncio.sleep(0.05)
                    continue

                try:
                    frame = await asyncio.wait_for(subscription.get(), timeout=self.ping_interval)
                except (asyncio.TimeoutError, TimeoutError):
                    frame = None

                if ws.closed:
                    return
                if connection.ota_image is not None:
                    await self._perform_ota(ws, connection)
                    continue
                if connection.ota_active:
                    continue

                # A bare FrameBus wake is how control changes reach an idle
                # device. Flush controls before deciding whether this wake also
                # needs a heartbeat; otherwise a control-only wake can starve
                # behind PING/PONG handling.
                await self._flush_controls(ws, connection)
                if frame is None:
                    if not await self._heartbeat(ws, connection):
                        return
                    continue

                if self.controls.blank:
                    continue
                message = protocol.Frame(
                    sequence=connection.sequence,
                    timestamp_ms=frame.timestamp_ms,
                    width=self.width,
                    height=self.height,
                    pixel_format=protocol.PixelFormat.RGB565,
                    pixels=frame.pixels,
                ).encode()
                await self._send(ws, message)
                connection.sequence = (connection.sequence + 1) & 0xFFFFFFFF
                connection.frames_sent += 1
                connection.frames_dropped = subscription.dropped
                connection.last_frame_at = time.time()
                connection.note_frame_sent(time.monotonic())

    async def _perform_ota(self, ws: web.WebSocketResponse, connection: DeviceConnection) -> None:
        image = connection.ota_image
        if image is None:
            return

        connection.ota_active = True
        # No STATUS from before the OTA transaction may satisfy an OTA ack.
        while not connection.ota_status.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                connection.ota_status.get_nowait()

        began = False
        try:
            _LOGGER.info(
                "starting OTA for %s: %d bytes", connection.device_id or connection.remote, len(image)
            )
            await self._send(ws, protocol.OtaBegin(len(image)).encode())
            await self._expect_ota_status(connection, "OTA_BEGIN")
            began = True

            for offset in range(0, len(image), protocol.OTA_MAX_CHUNK_BYTES):
                chunk = image[offset : offset + protocol.OTA_MAX_CHUNK_BYTES]
                await self._send(ws, protocol.OtaData(offset, chunk).encode())
                await self._expect_ota_status(connection, "OTA_DATA")
                connection.ota_bytes_sent = offset + len(chunk)

            await self._send(ws, protocol.OtaCommit().encode())
            await self._expect_ota_status(connection, "ota committed; rebooting")
            connection.ota_bytes_sent = len(image)
            connection.ota_image = None
            _LOGGER.info("OTA committed for %s; waiting for device reboot", connection.device_id or connection.remote)
            if connection.ota_done is not None and not connection.ota_done.done():
                connection.ota_done.set_result(None)
            # The device has accepted the new boot slot and is about to reboot.
            # Close our side proactively: a reboot can leave the old TCP socket
            # half-open, and ota_active deliberately suppresses heartbeats. If
            # retained, that ghost session receives no frames but remains in
            # status forever alongside the device's fresh connection.
            await ws.close(code=_CLOSE_GOING_AWAY, message=b"ota committed")
        except OtaUpdateError as exc:
            connection.ota_last_error = str(exc)
            connection.ota_image = None
            connection.ota_active = False
            _LOGGER.warning("OTA failed for %s: %s", connection.device_id or connection.remote, exc)
            if connection.ota_done is not None and not connection.ota_done.done():
                connection.ota_done.set_exception(exc)
            if began and not ws.closed:
                # Protocol v1 has no OTA_ABORT message. Closing the session is
                # intentional: the firmware's connection teardown calls
                # esp_ota_abort(), then reconnects with a clean OTA state.
                await ws.close(code=_CLOSE_GOING_AWAY, message=b"ota transfer failed")
            else:
                self.bus.wake_all()

    async def _expect_ota_status(self, connection: DeviceConnection, expected_message: str) -> protocol.Status:
        try:
            status = await asyncio.wait_for(connection.ota_status.get(), timeout=OTA_STATUS_TIMEOUT_S)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise OtaUpdateError(f"timed out waiting for {expected_message} acknowledgement") from exc

        if status.code != protocol.StatusCode.OK:
            message = status.message or "device rejected the update"
            raise OtaUpdateError(f"{expected_message} failed with status {status.code:#06x}: {message}")
        if status.message != expected_message:
            raise OtaUpdateError(
                f"expected {expected_message!r} acknowledgement, received {status.message!r}"
            )
        return status

    async def _flush_controls(self, ws: web.WebSocketResponse, connection: DeviceConnection) -> None:
        brightness = self.controls.clamped_brightness()
        if connection.sent_brightness != brightness:
            await self._send(ws, protocol.Brightness(brightness).encode())
            connection.sent_brightness = brightness
        blank = bool(self.controls.blank)
        if connection.sent_blank != blank:
            await self._send(ws, protocol.Blank(blank).encode())
            connection.sent_blank = blank
            connection.reset_cadence()

    async def _heartbeat(self, ws: web.WebSocketResponse, connection: DeviceConnection) -> bool:
        """Send a PING while idle; close if the previous one was never answered."""
        now = time.monotonic()
        if connection.pending_ping is not None:
            if now - connection.pending_ping_at > self.pong_timeout:
                _LOGGER.warning(
                    "connection %d did not answer PING within %.0fs; closing", connection.id, self.pong_timeout
                )
                await ws.close(code=_CLOSE_GOING_AWAY, message=b"pong timeout")
                return False
            return True
        nonce = random.getrandbits(32)
        connection.pending_ping = nonce
        connection.pending_ping_at = now
        await self._send(ws, protocol.Ping(nonce).encode())
        return True

    @staticmethod
    async def _send(ws: web.WebSocketResponse, data: bytes) -> None:
        if not ws.closed:
            await ws.send_bytes(data)
