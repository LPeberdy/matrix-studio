"""Contract tests for Matrix Studio Protocol v1.

These tests exercise the reference Python codec against both round-tripped
values and the golden fixtures in protocol/fixtures/. The Home Assistant test
suite imports this same protocol module directly; the ESP32 side has its own
C++ tests against the same fixtures (esp32/tests/), so this file is the
canonical description of expected behaviour for the wire format itself.
"""
import json
import pathlib
import struct
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import matrix_studio_protocol as proto  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text())


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_valid_hello_roundtrip():
    hello = proto.Hello(1, 64, 64, proto.PixelFormat.RGB565, "dev-1", "1.2.3")
    encoded = hello.encode()
    header = proto.decode_header(encoded)
    assert header.type == proto.MessageType.HELLO
    decoded = proto.Hello.decode_payload(encoded[proto.HEADER_SIZE_BYTES:])
    assert decoded == hello


def test_valid_frame_roundtrip():
    pixels = bytes(range(256)) * 32  # 8192 bytes
    frame = proto.Frame(7, 1000, 64, 64, proto.PixelFormat.RGB565, pixels)
    encoded = frame.encode()
    header = proto.decode_header(encoded)
    assert header.type == proto.MessageType.FRAME
    assert header.length == 14 + 8192
    decoded = proto.Frame.decode_payload(encoded[proto.HEADER_SIZE_BYTES:])
    assert decoded == frame


def test_frame_rejects_wrong_pixel_length():
    with pytest.raises(ValueError):
        proto.Frame(0, 0, 64, 64, proto.PixelFormat.RGB565, b"\x00" * 10).encode()


@pytest.mark.parametrize("name", ["valid_hello.bin", "valid_frame_64x64.bin", "brightness.bin", "heartbeat_ping.bin"])
def test_fixture_decodes_as_expected(name):
    data = fixture_bytes(name)
    expect = MANIFEST[name]["expect"]
    header = proto.decode_header(data)
    payload = data[proto.HEADER_SIZE_BYTES:]
    assert len(payload) == header.length

    if expect["type"] == "HELLO":
        decoded = proto.Hello.decode_payload(payload)
        assert decoded.protocol_version == expect["protocol_version"]
        assert decoded.width == expect["width"]
        assert decoded.height == expect["height"]
        assert decoded.device_id == expect["device_id"]
        assert decoded.fw_version == expect["fw_version"]
    elif expect["type"] == "FRAME":
        decoded = proto.Frame.decode_payload(payload)
        assert decoded.sequence == expect["sequence"]
        assert decoded.width == expect["width"]
        assert decoded.height == expect["height"]
        assert len(decoded.pixels) == expect["pixel_bytes"]
    elif expect["type"] == "BRIGHTNESS":
        decoded = proto.Brightness.decode_payload(payload)
        assert decoded.brightness == expect["brightness"]
    elif expect["type"] == "PING":
        decoded = proto.Ping.decode_payload(payload)
        assert decoded.nonce == expect["nonce"]
    else:
        pytest.fail(f"unhandled fixture type {expect['type']}")


def test_malformed_header_bad_magic_is_rejected():
    data = fixture_bytes("malformed_header.bin")
    with pytest.raises(proto.ProtocolError):
        proto.decode_header(data)


def test_unsupported_version_is_detected_before_trusting_payload():
    data = fixture_bytes("unsupported_version.bin")
    header = proto.decode_header(data)
    assert header.version == 99
    assert header.version != proto.PROTOCOL_VERSION


def test_truncated_frame_payload_is_detected_not_overread():
    data = fixture_bytes("truncated_frame.bin")
    header = proto.decode_header(data)
    payload = data[proto.HEADER_SIZE_BYTES:]
    # The header lies about having a full frame; the actual bytes available
    # are short. A correct implementation must detect this via length
    # mismatch rather than reading past the end of the buffer.
    assert header.length == 14 + 8192
    assert len(payload) < header.length
    with pytest.raises(proto.ProtocolError):
        proto.Frame.decode_payload(payload)


def test_header_length_cannot_exceed_max_payload():
    with pytest.raises(ValueError):
        proto.encode_header(proto.MessageType.FRAME, proto.MAX_PAYLOAD_BYTES + 1)


def test_rgb888_to_rgb565_packs_expected_bits():
    assert proto.rgb888_to_rgb565(255, 255, 255) == 0xFFFF
    assert proto.rgb888_to_rgb565(0, 0, 0) == 0x0000
    assert proto.rgb888_to_rgb565(255, 0, 0) == 0xF800
    assert proto.rgb888_to_rgb565(0, 255, 0) == 0x07E0
    assert proto.rgb888_to_rgb565(0, 0, 255) == 0x001F
