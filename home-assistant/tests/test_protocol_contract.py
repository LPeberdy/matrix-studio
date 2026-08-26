"""The add-on's half of the frozen wire contract.

Two jobs:
  1. prove the vendored codec is byte-identical to protocol/matrix_studio_protocol.py
     (so shipping the add-on can never fork the protocol), and
  2. prove the add-on's own encode path produces bytes that match the golden
     fixtures in protocol/fixtures/ — not hand-rolled expectations.
"""
from __future__ import annotations

import pathlib
import struct

import pytest

from matrix_studio.framebuffer import image_to_rgb565, rgb565_to_image
from matrix_studio.vendor import matrix_studio_protocol as proto

HA_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = HA_DIR.parent
CANONICAL = REPO_ROOT / "protocol" / "matrix_studio_protocol.py"
VENDORED = HA_DIR / "matrix_studio" / "vendor" / "matrix_studio_protocol.py"


def test_vendored_codec_is_byte_identical_to_canonical():
    assert CANONICAL.is_file(), "canonical protocol module is missing from the repo"
    assert VENDORED.read_bytes() == CANONICAL.read_bytes(), (
        "the vendored protocol codec has drifted from protocol/matrix_studio_protocol.py; "
        "run python3 home-assistant/tools/sync_protocol.py"
    )


def test_addon_uses_the_shared_codec_not_its_own():
    """Guard against anyone re-implementing the wire format inside the add-on."""
    suspicious = []
    for path in (HA_DIR / "matrix_studio").rglob("*.py"):
        if "vendor" in path.parts:
            continue
        text = path.read_text()
        if "0xA5" in text or "0xa5" in text:
            suspicious.append(f"{path.name}: hardcodes the protocol magic byte")
        if 'struct.pack("<BBBBI"' in text:
            suspicious.append(f"{path.name}: re-implements the header layout")
    assert not suspicious, "; ".join(suspicious)


# --------------------------------------------------------------------- fixtures


def test_hello_fixture_parses_and_reencodes_identically(read_fixture, fixture_manifest):
    data = read_fixture("valid_hello.bin")
    expect = fixture_manifest["valid_hello.bin"]["expect"]
    header = proto.decode_header(data)
    assert header.type == proto.MessageType.HELLO
    hello = proto.Hello.decode_payload(data[proto.HEADER_SIZE_BYTES :])
    assert hello.device_id == expect["device_id"]
    assert (hello.width, hello.height) == (expect["width"], expect["height"])
    assert hello.encode() == data


def test_server_helloack_matches_the_documented_layout():
    ack = proto.HelloAck(protocol_version=1, frame_interval_hint_ms=40, server_time_unix=1_700_000_000)
    encoded = ack.encode()
    header = proto.decode_header(encoded)
    assert (header.magic, header.version, header.type, header.flags) == (
        0xA5,
        1,
        proto.MessageType.HELLO_ACK,
        0,
    )
    assert header.length == 7
    assert proto.HelloAck.decode_payload(encoded[8:]) == ack


def test_frame_encoding_matches_the_golden_frame_fixture(read_fixture, fixture_manifest):
    """Re-encode the fixture's own pixels and require byte equality."""
    data = read_fixture("valid_frame_64x64.bin")
    expect = fixture_manifest["valid_frame_64x64.bin"]["expect"]
    decoded = proto.Frame.decode_payload(data[proto.HEADER_SIZE_BYTES :])

    reencoded = proto.Frame(
        sequence=expect["sequence"],
        timestamp_ms=expect["timestamp_ms"],
        width=expect["width"],
        height=expect["height"],
        pixel_format=expect["pixel_format"],
        pixels=decoded.pixels,
    ).encode()
    assert reencoded == data
    assert len(decoded.pixels) == expect["pixel_bytes"] == 8192


def test_frame_pixels_survive_an_image_roundtrip(read_fixture):
    """The add-on's numpy pack/unpack must agree with the golden pixel bytes."""
    data = read_fixture("valid_frame_64x64.bin")
    pixels = proto.Frame.decode_payload(data[proto.HEADER_SIZE_BYTES :]).pixels
    image = rgb565_to_image(pixels)
    assert image.size == (64, 64)
    # Unpacking to RGB888 and repacking must be lossless: the 8-bit values
    # produced by rgb565_to_image are exactly representable in RGB565.
    assert image_to_rgb565(image) == pixels


@pytest.mark.parametrize(
    "name,message_type",
    [("brightness.bin", proto.MessageType.BRIGHTNESS), ("heartbeat_ping.bin", proto.MessageType.PING)],
)
def test_control_fixtures_reencode_identically(read_fixture, fixture_manifest, name, message_type):
    data = read_fixture(name)
    expect = fixture_manifest[name]["expect"]
    header = proto.decode_header(data)
    assert header.type == message_type
    if message_type is proto.MessageType.BRIGHTNESS:
        assert proto.Brightness(expect["brightness"]).encode() == data
    else:
        assert proto.Ping(expect["nonce"]).encode() == data


def test_malformed_and_truncated_fixtures_are_rejected_not_crashed(read_fixture):
    with pytest.raises(proto.ProtocolError):
        proto.decode_header(read_fixture("malformed_header.bin"))

    truncated = read_fixture("truncated_frame.bin")
    header = proto.decode_header(truncated)
    payload = truncated[proto.HEADER_SIZE_BYTES :]
    assert len(payload) < header.length
    with pytest.raises(proto.ProtocolError):
        proto.Frame.decode_payload(payload)


def test_unsupported_version_fixture_is_detected_from_the_header(read_fixture):
    header = proto.decode_header(read_fixture("unsupported_version.bin"))
    assert header.version == 99 != proto.PROTOCOL_VERSION


# ------------------------------------------------------------------- RGB565


@pytest.mark.parametrize(
    "rgb,expected",
    [
        ((0, 0, 0), 0x0000),
        ((255, 255, 255), 0xFFFF),
        ((255, 0, 0), 0xF800),
        ((0, 255, 0), 0x07E0),
        ((0, 0, 255), 0x001F),
        ((17, 34, 51), None),
        ((200, 100, 50), None),
    ],
)
def test_numpy_packing_agrees_with_the_reference_implementation(rgb, expected):
    from PIL import Image

    reference = proto.rgb888_to_rgb565(*rgb)
    if expected is not None:
        assert reference == expected
    packed = image_to_rgb565(Image.new("RGB", (1, 1), rgb))
    assert struct.unpack("<H", packed)[0] == reference


def test_packing_is_little_endian_and_row_major():
    from PIL import Image

    image = Image.new("RGB", (2, 2), (0, 0, 0))
    image.putpixel((0, 0), (255, 0, 0))  # 0xF800 -> bytes 00 F8
    image.putpixel((1, 0), (0, 0, 255))  # 0x001F -> bytes 1F 00
    packed = image_to_rgb565(image)
    assert packed[:4] == b"\x00\xf8\x1f\x00"
    assert len(packed) == 2 * 2 * 2
