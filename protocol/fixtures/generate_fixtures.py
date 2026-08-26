#!/usr/bin/env python3
"""Generates the golden binary test vectors for Matrix Studio Protocol v1.

Run this from the repo root: `python3 protocol/fixtures/generate_fixtures.py`

Both the Home Assistant-side and ESP32-side protocol test suites load these
fixtures and assert against manifest.json rather than hand-rolling their own
byte strings, so a change that silently diverges from docs/protocol.md is
caught on both sides at once.
"""
import json
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from matrix_studio_protocol import (  # noqa: E402
    Hello,
    Frame,
    Brightness,
    Ping,
    encode_header,
    MessageType,
    rgb888_to_rgb565,
)

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent
manifest = {}


def write_fixture(name: str, data: bytes, description: str, expect: dict):
    path = FIXTURES_DIR / name
    path.write_bytes(data)
    manifest[name] = {"description": description, "expect": expect}


def make_test_frame_pixels(width: int, height: int) -> bytes:
    """A deterministic diagonal-gradient test pattern, not visually meaningful."""
    out = bytearray(width * height * 2)
    i = 0
    for y in range(height):
        for x in range(width):
            r = (x * 255) // max(width - 1, 1)
            g = (y * 255) // max(height - 1, 1)
            b = ((x + y) * 255) // max(width + height - 2, 1)
            pixel = rgb888_to_rgb565(r, g, b)
            struct.pack_into("<H", out, i, pixel)
            i += 2
    return bytes(out)


def main():
    # 1. valid_hello.bin
    hello = Hello(
        protocol_version=1,
        width=64,
        height=64,
        pixel_format=1,
        device_id="matrix-esp32-01",
        fw_version="0.1.0",
    )
    write_fixture(
        "valid_hello.bin",
        hello.encode(),
        "A well-formed HELLO from a 64x64 RGB565 device.",
        {"type": "HELLO", "protocol_version": 1, "width": 64, "height": 64,
         "pixel_format": 1, "device_id": "matrix-esp32-01", "fw_version": "0.1.0"},
    )

    # 2. valid_frame_64x64.bin
    pixels = make_test_frame_pixels(64, 64)
    frame = Frame(sequence=42, timestamp_ms=123456, width=64, height=64, pixel_format=1, pixels=pixels)
    write_fixture(
        "valid_frame_64x64.bin",
        frame.encode(),
        "A well-formed 64x64 RGB565 FRAME with sequence=42.",
        {"type": "FRAME", "sequence": 42, "timestamp_ms": 123456, "width": 64,
         "height": 64, "pixel_format": 1, "pixel_bytes": len(pixels)},
    )

    # 3. brightness.bin
    brightness = Brightness(brightness=128)
    write_fixture(
        "brightness.bin",
        brightness.encode(),
        "A well-formed BRIGHTNESS command set to 128.",
        {"type": "BRIGHTNESS", "brightness": 128},
    )

    # 4. heartbeat_ping.bin
    ping = Ping(nonce=0xDEADBEEF)
    write_fixture(
        "heartbeat_ping.bin",
        ping.encode(),
        "A well-formed PING with nonce 0xDEADBEEF.",
        {"type": "PING", "nonce": 0xDEADBEEF},
    )

    # 5. malformed_header.bin — bad magic byte
    good_header = encode_header(MessageType.PING, 4)
    bad_header = bytes([0xFF]) + good_header[1:]  # corrupt magic
    write_fixture(
        "malformed_header.bin",
        bad_header + struct.pack("<I", 1),
        "A message whose magic byte is 0xFF instead of 0xA5; must be rejected before the header is otherwise trusted.",
        {"expect_error": "bad_magic"},
    )

    # 6. unsupported_version.bin — HELLO with version 99
    bad_version_hello = Hello(
        protocol_version=99,
        width=64,
        height=64,
        pixel_format=1,
        device_id="matrix-esp32-01",
        fw_version="0.1.0",
    )
    encoded = bad_version_hello.encode()
    # encode_header always stamps PROTOCOL_VERSION in the header; force header.version=99 too,
    # since a real nonconformant/future device would set both consistently.
    encoded = bytes([encoded[0], 99]) + encoded[2:]
    write_fixture(
        "unsupported_version.bin",
        encoded,
        "A HELLO declaring protocol_version=99 in both the header and payload; server must reply STATUS(ERR_UNSUPPORTED_VERSION) and close.",
        {"type": "HELLO", "header_version": 99, "expect_error": "unsupported_version"},
    )

    # 7. truncated_frame.bin — valid header claiming a full frame, but payload cut short
    full_frame = frame.encode()
    header = full_frame[:8]
    truncated_payload = full_frame[8:8 + 100]  # far short of the 8206-byte payload
    write_fixture(
        "truncated_frame.bin",
        header + truncated_payload,
        "A FRAME header declaring the full 8206-byte payload, but only 100 bytes actually follow (e.g. a dropped TCP segment); must be detected as malformed, not read out of bounds.",
        {"type": "FRAME", "declared_length": 8206, "actual_payload_bytes": 100, "expect_error": "truncated"},
    )

    (FIXTURES_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(manifest)} fixtures + manifest.json to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
