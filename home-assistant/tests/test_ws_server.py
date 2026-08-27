"""End-to-end Protocol v1 server behaviour, over a real WebSocket connection.

Every test here drives the server the way the ESP32 firmware will: connect,
HELLO, read HELLO_ACK, exchange binary messages. Messages are built with the
frozen codec, and the HELLO bytes for the happy path come straight from the
golden fixture, so a divergence between this server and the firmware's view of
the wire format fails here.
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest
from PIL import Image

from matrix_studio.framebuffer import FrameBus, RenderedFrame, image_to_rgb565
from matrix_studio.scene_api import Controls
from matrix_studio.server import DeviceConnection, DeviceServer
from matrix_studio.vendor import matrix_studio_protocol as proto

HELLO = proto.Hello(1, 64, 64, proto.PixelFormat.RGB565, "test-device", "0.0.1").encode()


def make_frame(colour=(255, 0, 0), timestamp_ms: int = 7, scene: str = "test") -> RenderedFrame:
    image = Image.new("RGB", (64, 64), colour)
    return RenderedFrame(pixels=image_to_rgb565(image), timestamp_ms=timestamp_ms, scene=scene, image=image)


def test_device_connection_reports_recent_send_cadence_and_jitter():
    connection = DeviceConnection(id=1, remote="test")
    for sent_at in (10.000, 10.040, 10.081, 10.120, 10.160):
        connection.note_frame_sent(sent_at)

    status = connection.as_dict(monotonic_now=10.160)

    assert status["send_fps"] == pytest.approx(25.0, abs=0.1)
    assert status["send_jitter_ms"] == pytest.approx(0.7, abs=0.1)
    assert status["max_frame_gap_ms"] == pytest.approx(41.0, abs=0.1)
    assert status["cadence_stale"] is False


def test_device_connection_marks_stalled_cadence_and_reads_live_drops():
    dropped = 0
    connection = DeviceConnection(id=1, remote="test")
    connection.track_drops(lambda: dropped)
    connection.note_frame_sent(10.000)
    connection.note_frame_sent(10.040)

    dropped = 7
    status = connection.as_dict(monotonic_now=11.100)

    assert status["frames_dropped"] == 7
    assert status["cadence_stale"] is True
    assert status["send_fps"] is None
    assert status["send_jitter_ms"] is None
    assert status["max_frame_gap_ms"] == pytest.approx(1060.0, abs=0.1)


async def test_blank_resume_resets_cadence_before_detecting_a_new_stall():
    class StubWebSocket:
        closed = False

        async def send_bytes(self, data):
            pass

    controls = Controls(brightness=200, blank=False)
    server = DeviceServer(FrameBus(), controls, frame_interval_hint_ms=40)
    connection = DeviceConnection(id=1, remote="test", expected_frame_interval_s=0.04)
    connection.sent_brightness = 200
    connection.sent_blank = False
    connection.note_frame_sent(10.000)
    connection.note_frame_sent(10.040)

    controls.blank = True
    await server._flush_controls(StubWebSocket(), connection)
    controls.blank = False
    await server._flush_controls(StubWebSocket(), connection)
    connection.note_frame_sent(20.000)
    connection.note_frame_sent(20.040)

    resumed = connection.as_dict(monotonic_now=20.040)
    stalled_again = connection.as_dict(monotonic_now=21.100)
    assert resumed["send_fps"] == pytest.approx(25.0, abs=0.1)
    assert resumed["max_frame_gap_ms"] == pytest.approx(40.0, abs=0.1)
    assert stalled_again["cadence_stale"] is True


class Harness:
    """A running DeviceServer plus a client session pointed at it."""

    def __init__(self, server: DeviceServer, url: str, session: aiohttp.ClientSession) -> None:
        self.server = server
        self.url = url
        self.session = session

    def connect(self):
        return self.session.ws_connect(self.url)

    async def handshake(self, ws, hello: bytes = HELLO):
        await ws.send_bytes(hello)
        message = await asyncio.wait_for(ws.receive(), timeout=5)
        assert message.type is aiohttp.WSMsgType.BINARY
        header = proto.decode_header(message.data)
        assert header.type == proto.MessageType.HELLO_ACK
        return proto.HelloAck.decode_payload(message.data[8:])

    async def next_of_type(self, ws, message_type: int, *, timeout: float = 5.0, forbid=()):
        """Read until `message_type` arrives, ignoring other traffic.

        Anything in `forbid` fails the test immediately — that is how a test
        asserts "the server must NOT have replied with a STATUS here".
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for type {message_type:#x}")
            message = await asyncio.wait_for(ws.receive(), timeout=remaining)
            assert message.type is aiohttp.WSMsgType.BINARY, f"unexpected websocket message {message.type}"
            header = proto.decode_header(message.data)
            if header.type == message_type:
                return header, message.data[8:]
            assert header.type not in forbid, f"server unexpectedly sent type {header.type:#x}"


@pytest.fixture
async def harness():
    bus = FrameBus()
    controls = Controls(brightness=200, blank=False, active_scene="plasma")
    server = DeviceServer(bus, controls, frame_interval_hint_ms=40, ping_interval=0.15, pong_timeout=0.3)
    port = await server.start(host="127.0.0.1", port=0)
    session = aiohttp.ClientSession()
    harness = Harness(server, f"http://127.0.0.1:{port}{server.path}", session)
    harness.bus = bus
    harness.controls = controls
    try:
        yield harness
    finally:
        await session.close()
        await server.stop()


# -------------------------------------------------------------------- handshake


async def test_zero_devices_is_a_healthy_state(harness):
    """Publishing with nothing connected must be a no-op, not an error."""
    for _ in range(5):
        harness.bus.publish(make_frame())
    assert harness.server.device_count == 0
    assert harness.bus.latest is not None


async def test_hello_from_the_golden_fixture_is_accepted(harness, read_fixture):
    async with harness.connect() as ws:
        ack = await harness.handshake(ws, read_fixture("valid_hello.bin"))
        assert ack.protocol_version == proto.PROTOCOL_VERSION
        assert ack.frame_interval_hint_ms == 40
        assert ack.server_time_unix > 0
        await asyncio.sleep(0.05)
        devices = harness.server.connections()
        assert len(devices) == 1
        assert devices[0]["device_id"] == "matrix-esp32-01"
        assert devices[0]["resolution"] == "64x64"


async def test_unsupported_version_gets_status_then_close(harness, read_fixture):
    async with harness.connect() as ws:
        await ws.send_bytes(read_fixture("unsupported_version.bin"))
        message = await asyncio.wait_for(ws.receive(), timeout=5)
        status = proto.Status.decode_payload(message.data[8:])
        assert status.code == proto.StatusCode.ERR_UNSUPPORTED_VERSION
        assert (await asyncio.wait_for(ws.receive(), timeout=5)).type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.CLOSING,
        )
    assert harness.server.device_count == 0
    assert harness.server.rejected_connections == 1


async def test_bad_magic_closes_without_a_status(harness, read_fixture):
    async with harness.connect() as ws:
        await ws.send_bytes(read_fixture("malformed_header.bin"))
        message = await asyncio.wait_for(ws.receive(), timeout=5)
        assert message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
    assert harness.server.device_count == 0


async def test_dimension_mismatch_is_rejected(harness):
    hello = proto.Hello(1, 32, 32, proto.PixelFormat.RGB565, "small", "0.0.1").encode()
    async with harness.connect() as ws:
        await ws.send_bytes(hello)
        message = await asyncio.wait_for(ws.receive(), timeout=5)
        status = proto.Status.decode_payload(message.data[8:])
        assert status.code == proto.StatusCode.ERR_DIMENSION_MISMATCH
    assert harness.server.device_count == 0


async def test_frames_before_hello_are_refused(harness):
    async with harness.connect() as ws:
        await ws.send_bytes(proto.Ping(1).encode())
        message = await asyncio.wait_for(ws.receive(), timeout=5)
        status = proto.Status.decode_payload(message.data[8:])
        assert status.code == proto.StatusCode.ERR_MALFORMED_PAYLOAD
    assert harness.server.device_count == 0


async def test_a_silent_client_is_dropped_after_the_hello_timeout():
    bus = FrameBus()
    server = DeviceServer(bus, Controls(), hello_timeout=0.2)
    port = await server.start(host="127.0.0.1", port=0)
    session = aiohttp.ClientSession()
    try:
        async with session.ws_connect(f"http://127.0.0.1:{port}{server.path}") as ws:
            message = await asyncio.wait_for(ws.receive(), timeout=5)
            assert message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING)
        assert server.device_count == 0
    finally:
        await session.close()
        await server.stop()


# ---------------------------------------------------------------- frame streaming


async def test_frames_are_streamed_with_a_per_session_sequence(harness):
    async with harness.connect() as ws:
        await harness.handshake(ws)
        await asyncio.sleep(0.05)

        sequences = []
        for index in range(3):
            harness.bus.publish(make_frame(colour=(0, 0, 255), timestamp_ms=index))
            header, payload = await harness.next_of_type(ws, proto.MessageType.FRAME)
            frame = proto.Frame.decode_payload(payload)
            assert header.length == 14 + 8192
            assert (frame.width, frame.height) == (64, 64)
            assert frame.pixel_format == proto.PixelFormat.RGB565
            assert len(frame.pixels) == 8192
            assert frame.pixels == image_to_rgb565(Image.new("RGB", (64, 64), (0, 0, 255)))
            sequences.append(frame.sequence)
            await asyncio.sleep(0.02)

        assert sequences == [0, 1, 2], "sequence must start at 0 and increment per frame"


async def test_drop_count_stays_live_while_a_frame_send_is_backpressured(harness, monkeypatch):
    async with harness.connect() as ws:
        await harness.handshake(ws)
        await asyncio.sleep(0.05)

        original_send = harness.server._send
        send_started = asyncio.Event()
        release_send = asyncio.Event()

        async def block_frame_send(socket, data):
            if proto.decode_header(data).type == proto.MessageType.FRAME:
                send_started.set()
                await release_send.wait()
            await original_send(socket, data)

        monkeypatch.setattr(harness.server, "_send", block_frame_send)
        harness.bus.publish(make_frame(timestamp_ms=0))
        await asyncio.wait_for(send_started.wait(), timeout=1)

        for index in range(1, 7):
            harness.bus.publish(make_frame(timestamp_ms=index))

        assert harness.server.connections()[0]["frames_dropped"] == 5
        release_send.set()
        await harness.next_of_type(ws, proto.MessageType.FRAME)


async def test_a_reconnecting_device_gets_a_fresh_session(harness):
    """docs/protocol.md §3.3: every new connection restarts sequence at 0."""
    for attempt in range(2):
        async with harness.connect() as ws:
            await harness.handshake(ws)
            await asyncio.sleep(0.05)
            harness.bus.publish(make_frame(timestamp_ms=attempt))
            _, payload = await harness.next_of_type(ws, proto.MessageType.FRAME)
            assert proto.Frame.decode_payload(payload).sequence == 0
        await asyncio.sleep(0.05)
        assert harness.server.device_count == 0, "connection should be reaped on close"

    assert harness.server.total_connections == 2


async def test_two_devices_are_served_the_same_frame(harness):
    async with harness.connect() as first, harness.connect() as second:
        await harness.handshake(first)
        await harness.handshake(second)
        await asyncio.sleep(0.05)
        assert harness.server.device_count == 2

        harness.bus.publish(make_frame(colour=(0, 255, 0)))
        _, first_payload = await harness.next_of_type(first, proto.MessageType.FRAME)
        _, second_payload = await harness.next_of_type(second, proto.MessageType.FRAME)
        assert proto.Frame.decode_payload(first_payload).pixels == proto.Frame.decode_payload(second_payload).pixels


async def test_one_device_disconnecting_does_not_disturb_the_other(harness):
    async with harness.connect() as keeper:
        await harness.handshake(keeper)
        async with harness.connect() as leaver:
            await harness.handshake(leaver)
            await asyncio.sleep(0.05)
            assert harness.server.device_count == 2
        await asyncio.sleep(0.1)
        assert harness.server.device_count == 1

        harness.bus.publish(make_frame(colour=(1, 2, 3)))
        _, payload = await harness.next_of_type(keeper, proto.MessageType.FRAME)
        assert proto.Frame.decode_payload(payload).sequence == 0


# -------------------------------------------------------------------- controls


async def test_brightness_and_blank_are_pushed_on_connect_and_on_change(harness):
    async with harness.connect() as ws:
        await harness.handshake(ws)

        header, payload = await harness.next_of_type(ws, proto.MessageType.BRIGHTNESS)
        assert proto.Brightness.decode_payload(payload).brightness == 200
        header, payload = await harness.next_of_type(ws, proto.MessageType.BLANK)
        assert proto.Blank.decode_payload(payload).blank is False

        harness.controls.brightness = 42
        harness.server.notify_controls_changed()
        _, payload = await harness.next_of_type(ws, proto.MessageType.BRIGHTNESS)
        assert proto.Brightness.decode_payload(payload).brightness == 42

        harness.controls.blank = True
        harness.server.notify_controls_changed()
        _, payload = await harness.next_of_type(ws, proto.MessageType.BLANK)
        assert proto.Blank.decode_payload(payload).blank is True


async def test_no_frames_are_sent_while_blanked(harness):
    async with harness.connect() as ws:
        await harness.handshake(ws)
        harness.controls.blank = True
        harness.server.notify_controls_changed()
        await harness.next_of_type(ws, proto.MessageType.BLANK)

        harness.bus.publish(make_frame())
        with pytest.raises((asyncio.TimeoutError, TimeoutError, AssertionError)):
            await harness.next_of_type(ws, proto.MessageType.FRAME, timeout=0.4)


# ------------------------------------------------------------------- heartbeat


async def test_server_pings_while_idle_and_tracks_the_pong(harness):
    async with harness.connect() as ws:
        await harness.handshake(ws)
        _, payload = await harness.next_of_type(ws, proto.MessageType.PING)
        nonce = proto.Ping.decode_payload(payload).nonce
        await ws.send_bytes(proto.Pong(nonce).encode())
        await asyncio.sleep(0.1)
        assert harness.server.connections()[0]["rtt_ms"] is not None


async def test_an_unanswered_ping_closes_the_connection(harness):
    async with harness.connect() as ws:
        await harness.handshake(ws)
        # Never answer the PING; ping_interval=0.15s, pong_timeout=0.3s.
        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            message = await asyncio.wait_for(ws.receive(), timeout=3)
            if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                break
        else:
            pytest.fail("server never closed an unresponsive connection")
    await asyncio.sleep(0.05)
    assert harness.server.device_count == 0


async def test_device_ping_is_answered_with_a_matching_pong(harness):
    async with harness.connect() as ws:
        await harness.handshake(ws)
        await ws.send_bytes(proto.Ping(0xDEADBEEF).encode())
        _, payload = await harness.next_of_type(ws, proto.MessageType.PONG)
        assert proto.Pong.decode_payload(payload).nonce == 0xDEADBEEF


async def test_heartbeat_ping_fixture_is_answered(harness, read_fixture, fixture_manifest):
    async with harness.connect() as ws:
        await harness.handshake(ws)
        await ws.send_bytes(read_fixture("heartbeat_ping.bin"))
        _, payload = await harness.next_of_type(ws, proto.MessageType.PONG)
        expected = fixture_manifest["heartbeat_ping.bin"]["expect"]["nonce"]
        assert proto.Pong.decode_payload(payload).nonce == expected


# ------------------------------------------------------- malformed mid-session


async def test_a_malformed_message_is_discarded_without_dropping_the_session(harness):
    """docs/protocol.md §3.5 rule 5: bad message, good connection."""
    async with harness.connect() as ws:
        await harness.handshake(ws)
        # A PING header that lies about its payload length.
        await ws.send_bytes(proto.encode_header(proto.MessageType.PING, 4) + b"\x01\x02")
        _, payload = await harness.next_of_type(ws, proto.MessageType.STATUS)
        assert proto.Status.decode_payload(payload).code == proto.StatusCode.ERR_MALFORMED_PAYLOAD

        # The session must still work.
        await ws.send_bytes(proto.Ping(99).encode())
        _, payload = await harness.next_of_type(ws, proto.MessageType.PONG)
        assert proto.Pong.decode_payload(payload).nonce == 99
        assert harness.server.device_count == 1


async def test_an_unknown_type_gets_status_and_keeps_the_session(harness):
    async with harness.connect() as ws:
        await harness.handshake(ws)
        await ws.send_bytes(proto.encode_header(0x42, 0))
        _, payload = await harness.next_of_type(ws, proto.MessageType.STATUS)
        assert proto.Status.decode_payload(payload).code == proto.StatusCode.ERR_UNKNOWN_TYPE
        assert harness.server.device_count == 1


async def test_extension_range_types_are_ignored_silently(harness):
    """docs/protocol.md §5: 0x80-0xFE must be ignorable, not an error."""
    async with harness.connect() as ws:
        await harness.handshake(ws)
        await ws.send_bytes(proto.encode_header(0x90, 3) + b"abc")
        await ws.send_bytes(proto.Ping(7).encode())
        # A PONG and no STATUS at all proves the extension message was ignored.
        _, payload = await harness.next_of_type(
            ws, proto.MessageType.PONG, forbid=(proto.MessageType.STATUS,)
        )
        assert proto.Pong.decode_payload(payload).nonce == 7
        assert harness.server.device_count == 1


async def test_bad_magic_mid_session_closes_the_connection(harness):
    async with harness.connect() as ws:
        await harness.handshake(ws)
        await ws.send_bytes(b"\xff" + proto.Ping(1).encode()[1:])
        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            message = await asyncio.wait_for(ws.receive(), timeout=3)
            if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                break
        else:
            pytest.fail("server did not close on a desynchronised stream")
    await asyncio.sleep(0.05)
    assert harness.server.device_count == 0


async def test_a_wrong_direction_message_is_ignored(harness):
    async with harness.connect() as ws:
        await harness.handshake(ws)
        await ws.send_bytes(proto.Brightness(10).encode())  # server -> device only
        await ws.send_bytes(proto.Ping(5).encode())
        _, payload = await harness.next_of_type(ws, proto.MessageType.PONG)
        assert proto.Pong.decode_payload(payload).nonce == 5
        assert harness.server.device_count == 1
