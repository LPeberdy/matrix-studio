"""Server-driven OTA transfer over a real Protocol v1 WebSocket session."""
from __future__ import annotations

import asyncio

import aiohttp
import pytest
from PIL import Image

from matrix_studio.framebuffer import FrameBus, RenderedFrame, image_to_rgb565
from matrix_studio.scene_api import Controls
from matrix_studio.server import DeviceServer, OtaUpdateError
from matrix_studio.vendor import matrix_studio_protocol as proto

HELLO = proto.Hello(1, 64, 64, proto.PixelFormat.RGB565, "ota-test", "0.1.0").encode()


async def next_type(ws, message_type: int, *, forbid=(), timeout: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for type {message_type:#x}")
        message = await asyncio.wait_for(ws.receive(), timeout=remaining)
        assert message.type is aiohttp.WSMsgType.BINARY, f"unexpected websocket message {message.type}"
        header = proto.decode_header(message.data)
        assert header.type not in forbid, f"server unexpectedly sent type {header.type:#x}"
        if header.type == message_type:
            return header, message.data[proto.HEADER_SIZE_BYTES :]


async def connect_device(server: DeviceServer, session: aiohttp.ClientSession):
    ws = await session.ws_connect(f"http://127.0.0.1:{server.port}{server.path}")
    await ws.send_bytes(HELLO)
    _, payload = await next_type(ws, proto.MessageType.HELLO_ACK)
    assert proto.HelloAck.decode_payload(payload).protocol_version == proto.PROTOCOL_VERSION
    await asyncio.sleep(0.05)
    assert server.device_count == 1
    return ws, server.connections()[0]["id"]


async def test_ota_sender_streams_sequential_chunks_and_pauses_frames():
    bus = FrameBus()
    server = DeviceServer(bus, Controls(brightness=90, blank=False, active_scene="plasma"), ping_interval=10)
    await server.start(host="127.0.0.1", port=0)
    session = aiohttp.ClientSession()
    try:
        ws, connection_id = await connect_device(server, session)
        image = b"\xe9" + bytes(4999)
        update = asyncio.create_task(server.ota_update(connection_id, image))

        _, payload = await next_type(ws, proto.MessageType.OTA_BEGIN)
        assert proto.OtaBegin.decode_payload(payload).image_size == len(image)
        await ws.send_bytes(proto.Status(proto.StatusCode.OK, "OTA_BEGIN").encode())

        _, payload = await next_type(ws, proto.MessageType.OTA_DATA)
        first = proto.OtaData.decode_payload(payload)
        assert first.offset == 0
        assert first.data == image[: proto.OTA_MAX_CHUNK_BYTES]
        await ws.send_bytes(proto.Status(proto.StatusCode.OK, "OTA_DATA").encode())

        # A fresh render while OTA is active must not interleave a FRAME with
        # the firmware transaction.
        panel = Image.new("RGB", (64, 64), (10, 20, 30))
        bus.publish(RenderedFrame(image_to_rgb565(panel), 1, "test", panel))

        _, payload = await next_type(ws, proto.MessageType.OTA_DATA, forbid=(proto.MessageType.FRAME,))
        second = proto.OtaData.decode_payload(payload)
        assert second.offset == proto.OTA_MAX_CHUNK_BYTES
        assert second.data == image[proto.OTA_MAX_CHUNK_BYTES :]
        await ws.send_bytes(proto.Status(proto.StatusCode.OK, "OTA_DATA").encode())

        _, payload = await next_type(ws, proto.MessageType.OTA_COMMIT, forbid=(proto.MessageType.FRAME,))
        proto.OtaCommit.decode_payload(payload)
        await ws.send_bytes(proto.Status(proto.StatusCode.OK, "ota committed; rebooting").encode())

        await asyncio.wait_for(update, timeout=5)
        ota = server.connections()[0]["ota"]
        assert ota["active"] is True
        assert ota["bytes_sent"] == len(image)
        assert ota["total_bytes"] == len(image)
        await ws.close()
    finally:
        await session.close()
        await server.stop()


async def test_ota_sender_surfaces_device_rejection():
    bus = FrameBus()
    server = DeviceServer(bus, Controls(brightness=90, blank=False, active_scene="plasma"), ping_interval=10)
    await server.start(host="127.0.0.1", port=0)
    session = aiohttp.ClientSession()
    try:
        ws, connection_id = await connect_device(server, session)
        update = asyncio.create_task(server.ota_update(connection_id, b"\xe9" + bytes(127)))

        await next_type(ws, proto.MessageType.OTA_BEGIN)
        await ws.send_bytes(proto.Status(proto.StatusCode.ERR_OTA_IMAGE, "invalid image").encode())

        with pytest.raises(OtaUpdateError, match="invalid image"):
            await asyncio.wait_for(update, timeout=5)
        ota = server.connections()[0]["ota"]
        assert ota["active"] is False
        assert "invalid image" in ota["last_error"]
        await ws.close()
    finally:
        await session.close()
        await server.stop()
