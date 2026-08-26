"""Parent-owned end-to-end integration test for Matrix Studio.

This is deliberately independent of both implementation subagents' own test
suites. It starts the *real* Home Assistant add-on (`home-assistant/`) and
drives it over an actual WebSocket connection using the *canonical*
`protocol/matrix_studio_protocol.py` codec — not the vendored copy the add-on
carries internally (`home-assistant/matrix_studio/vendor/...`). If the add-on
ever silently drifted from the frozen contract, this test would fail even
though the add-on's own tests (which use its own vendored copy for both
sides) would not necessarily catch it.

This exercises exactly the scenario in docs/architecture.md and the project
brief's Phase 5: run the renderer, connect a simulated ESP32 receiver over
the wire, verify handshake, receive multiple real frames, decode RGB565,
validate dimensions, verify sequence progression, issue a brightness change,
simulate a disconnect, and verify a clean reconnect — all without any
physical ESP32 hardware. The ESP32 C++ firmware is exercised separately by
its own host-side tests (esp32/tests/) against the same golden fixtures;
reproducing its parser in Python here would not add coverage, so this file
stays focused on the transport/session behaviour any correctly-behaving
client (C++ or Python) depends on.
"""
from __future__ import annotations

import pathlib
import sys

import aiohttp
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
HA_DIR = REPO_ROOT / "home-assistant"
PROTOCOL_DIR = REPO_ROOT / "protocol"
for path in (HA_DIR, PROTOCOL_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import matrix_studio_protocol as proto  # noqa: E402  (canonical codec, not the vendored copy)
from matrix_studio.app import MatrixStudioApp  # noqa: E402
from matrix_studio.options import Options  # noqa: E402
from matrix_studio.preview import _StaticStateAdapter, fake_home_state  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def running_app(tmp_path):
    options = Options.from_mapping(
        {
            "active_scene": "plasma",
            "target_fps": 30,
            "brightness": 200,
            "scenes_dir": str(tmp_path / "scenes"),
            "ws_port": 0,  # ephemeral: proves the add-on doesn't hardcode 7887 internally
            "ingress_port": 0,
            "hot_reload": False,
        }
    )
    app = MatrixStudioApp(options, state_adapter=_StaticStateAdapter(fake_home_state()))
    await app.start(with_web=False)
    try:
        yield app
    finally:
        await app.stop()


class SimulatedDevice:
    """A minimal ESP32 stand-in speaking Protocol v1 via the canonical codec."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse):
        self.ws = ws

    async def send_hello(self, device_id: str = "itest-sim-esp32") -> None:
        hello = proto.Hello(
            protocol_version=proto.PROTOCOL_VERSION,
            width=64,
            height=64,
            pixel_format=proto.PixelFormat.RGB565,
            device_id=device_id,
            fw_version="0.0.0-itest",
        )
        await self.ws.send_bytes(hello.encode())

    async def recv_message(self, timeout: float = 5.0):
        msg = await self.ws.receive(timeout=timeout)
        assert msg.type == aiohttp.WSMsgType.BINARY, f"expected binary frame, got {msg.type!r} ({msg.data!r})"
        header = proto.decode_header(msg.data)
        payload = msg.data[proto.HEADER_SIZE_BYTES:]
        assert len(payload) == header.length
        return header, payload

    async def expect(self, msg_type: proto.MessageType, timeout: float = 5.0, max_skip: int = 20):
        """Read messages until `msg_type` is seen, tolerating interleaved
        messages a real device would also just skip past (e.g. the server
        proactively pushing the current BRIGHTNESS right after HELLO_ACK,
        before the first FRAME)."""
        for _ in range(max_skip):
            header, payload = await self.recv_message(timeout=timeout)
            if header.type == msg_type:
                return header, payload
        raise AssertionError(f"never saw {msg_type!r} within {max_skip} messages")


async def _connect(session: aiohttp.ClientSession, app: MatrixStudioApp) -> aiohttp.ClientWebSocketResponse:
    url = f"ws://127.0.0.1:{app.server.port}{app.options.ws_path}"
    return await session.ws_connect(url)


async def test_handshake(running_app):
    app = running_app
    async with aiohttp.ClientSession() as session:
        ws = await _connect(session, app)
        try:
            device = SimulatedDevice(ws)
            await device.send_hello()
            header, payload = await device.expect(proto.MessageType.HELLO_ACK)
            ack = proto.HelloAck.decode_payload(payload)
            assert ack.protocol_version == proto.PROTOCOL_VERSION
        finally:
            await ws.close()


async def test_receives_valid_64x64_rgb565_frames_with_increasing_sequence(running_app):
    app = running_app
    async with aiohttp.ClientSession() as session:
        ws = await _connect(session, app)
        try:
            device = SimulatedDevice(ws)
            await device.send_hello()
            await device.expect(proto.MessageType.HELLO_ACK)

            sequences = []
            for _ in range(5):
                header, payload = await device.expect(proto.MessageType.FRAME)
                frame = proto.Frame.decode_payload(payload)
                assert (frame.width, frame.height) == (64, 64)
                assert frame.pixel_format == proto.PixelFormat.RGB565
                assert len(frame.pixels) == 64 * 64 * 2
                sequences.append(frame.sequence)

            assert sequences == sorted(sequences), "sequence must be non-decreasing"
            assert sequences[-1] > sequences[0], "sequence must actually progress, not stall"
            assert len(set(sequences)) == len(sequences), "no duplicate sequence numbers on one session"
        finally:
            await ws.close()


async def test_brightness_command_reaches_device(running_app):
    app = running_app
    async with aiohttp.ClientSession() as session:
        ws = await _connect(session, app)
        try:
            device = SimulatedDevice(ws)
            await device.send_hello()
            await device.expect(proto.MessageType.HELLO_ACK)

            # The server proactively syncs current controls right after the
            # handshake; drain that initial BRIGHTNESS (the fixture's
            # options set brightness=200) before triggering a real change.
            _, initial_payload = await device.expect(proto.MessageType.BRIGHTNESS)
            assert proto.Brightness.decode_payload(initial_payload).brightness == 200

            app.set_brightness(77)

            _, payload = await device.expect(proto.MessageType.BRIGHTNESS)
            assert proto.Brightness.decode_payload(payload).brightness == 77
        finally:
            await ws.close()


async def test_disconnect_then_reconnect_is_a_clean_new_session(running_app):
    app = running_app
    async with aiohttp.ClientSession() as session:
        # First session.
        ws1 = await _connect(session, app)
        device1 = SimulatedDevice(ws1)
        await device1.send_hello(device_id="itest-reconnect")
        await device1.expect(proto.MessageType.HELLO_ACK)
        _, payload = await device1.expect(proto.MessageType.FRAME)
        first_session_last_seq = proto.Frame.decode_payload(payload).sequence
        await ws1.close()

        # The add-on must not crash or wedge when a device disconnects: the
        # server stays bound and ready for the next connection.
        assert app.server.port is not None, "device server must survive a client disconnect"

        # Second session: a real device reconnecting after a drop.
        ws2 = await _connect(session, app)
        try:
            device2 = SimulatedDevice(ws2)
            await device2.send_hello(device_id="itest-reconnect")
            _, ack_payload = await device2.expect(proto.MessageType.HELLO_ACK)
            proto.HelloAck.decode_payload(ack_payload)  # must parse cleanly

            _, frame_payload = await device2.expect(proto.MessageType.FRAME)
            new_session_first_seq = proto.Frame.decode_payload(frame_payload).sequence
            assert new_session_first_seq == 0, "a new connection must start a fresh session (sequence resets, §3.3)"
            assert new_session_first_seq <= first_session_last_seq
        finally:
            await ws2.close()


async def test_malformed_bad_magic_from_device_closes_connection_not_the_server(running_app):
    """§3.5(1): a bad magic byte desynchronizes the stream and is fatal to
    *that* connection, but must never take down the add-on itself."""
    app = running_app
    async with aiohttp.ClientSession() as session:
        ws = await _connect(session, app)
        garbage = bytes([0xFF, proto.PROTOCOL_VERSION, proto.MessageType.PING, 0x00]) + (4).to_bytes(4, "little") + b"\x00\x00\x00\x00"
        await ws.send_bytes(garbage)

        msg = await ws.receive(timeout=5.0)
        assert msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR), (
            f"server must close the connection on bad magic, got {msg.type!r}"
        )
        if not ws.closed:
            await ws.close()

        # The add-on itself must still be healthy and accept a fresh, valid connection.
        ws2 = await _connect(session, app)
        try:
            device = SimulatedDevice(ws2)
            await device.send_hello()
            await device.expect(proto.MessageType.HELLO_ACK)
        finally:
            await ws2.close()


async def test_zero_connected_devices_is_healthy(running_app):
    """§3.4: no device connected is a normal state, not an error — the add-on
    must already be running (via the `running_app` fixture) without crashing
    or refusing to serve before any device ever connects."""
    app = running_app
    assert app.server.port is not None
    assert app.server.device_count == 0
