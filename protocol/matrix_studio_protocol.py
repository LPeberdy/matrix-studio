"""Matrix Studio Protocol v1 — canonical constants and (de)serialization.

This module is the reference implementation of docs/protocol.md. It is used
by the Home Assistant side and by protocol/fixtures/generate_fixtures.py to
produce the golden test vectors that both sides must validate against.

Do not change wire semantics here without updating docs/protocol.md first —
see that document's header for the change process. This file is the
executable half of the frozen contract; esp32/lib/matrix_studio_protocol.h is
the C++ half.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

PROTOCOL_VERSION = 1
MAGIC = 0xA5
HEADER_SIZE_BYTES = 8
MAX_PAYLOAD_BYTES = 65535

HELLO_TIMEOUT_MS = 5000
PING_INTERVAL_MS = 10000
PONG_TIMEOUT_MS = 10000
FRAME_TIMEOUT_MS_DEFAULT = 5000
RECONNECT_MAX_BACKOFF_S = 30

DEFAULT_WS_PORT = 7887
DEFAULT_WS_PATH = "/matrix-studio"

DEVICE_ID_FIELD_LEN = 16
FW_VERSION_FIELD_LEN = 16


class MessageType(IntEnum):
    HELLO = 0x01
    HELLO_ACK = 0x02
    FRAME = 0x03
    BRIGHTNESS = 0x04
    BLANK = 0x05
    PING = 0x06
    PONG = 0x07
    STATUS = 0x08


class PixelFormat(IntEnum):
    RGB565 = 0x01


class StatusCode(IntEnum):
    OK = 0x0000
    ERR_UNSUPPORTED_VERSION = 0x0001
    ERR_UNKNOWN_TYPE = 0x0002
    ERR_MALFORMED_PAYLOAD = 0x0003
    ERR_DIMENSION_MISMATCH = 0x0004
    ERR_INTERNAL = 0x0005


class ProtocolError(ValueError):
    """Raised for any malformed message during decode."""


def _pad(s: str, length: int) -> bytes:
    b = s.encode("utf-8")
    if len(b) > length:
        raise ValueError(f"{s!r} exceeds {length} bytes")
    return b + b"\x00" * (length - len(b))


def _unpad(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def encode_header(msg_type: int, payload_len: int, version: int = PROTOCOL_VERSION, flags: int = 0) -> bytes:
    if payload_len > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload of {payload_len} bytes exceeds MAX_PAYLOAD_BYTES={MAX_PAYLOAD_BYTES}")
    return struct.pack("<BBBBI", MAGIC, version, msg_type, flags, payload_len)


@dataclass
class Header:
    magic: int
    version: int
    type: int
    flags: int
    length: int


def decode_header(buf: bytes) -> Header:
    if len(buf) < HEADER_SIZE_BYTES:
        raise ProtocolError("buffer shorter than header")
    magic, version, mtype, flags, length = struct.unpack("<BBBBI", buf[:HEADER_SIZE_BYTES])
    if magic != MAGIC:
        raise ProtocolError(f"bad magic byte: {magic:#x}")
    return Header(magic=magic, version=version, type=mtype, flags=flags, length=length)


@dataclass
class Hello:
    protocol_version: int
    width: int
    height: int
    pixel_format: int
    device_id: str
    fw_version: str

    def encode(self) -> bytes:
        payload = struct.pack(
            "<BHHB",
            self.protocol_version,
            self.width,
            self.height,
            self.pixel_format,
        ) + _pad(self.device_id, DEVICE_ID_FIELD_LEN) + _pad(self.fw_version, FW_VERSION_FIELD_LEN)
        return encode_header(MessageType.HELLO, len(payload)) + payload

    @staticmethod
    def decode_payload(payload: bytes) -> "Hello":
        if len(payload) != 38:
            raise ProtocolError(f"HELLO payload must be 38 bytes, got {len(payload)}")
        version, width, height, pf = struct.unpack("<BHHB", payload[:6])
        device_id = _unpad(payload[6:22])
        fw_version = _unpad(payload[22:38])
        return Hello(version, width, height, pf, device_id, fw_version)


@dataclass
class HelloAck:
    protocol_version: int
    frame_interval_hint_ms: int
    server_time_unix: int

    def encode(self) -> bytes:
        payload = struct.pack("<BHI", self.protocol_version, self.frame_interval_hint_ms, self.server_time_unix)
        return encode_header(MessageType.HELLO_ACK, len(payload)) + payload

    @staticmethod
    def decode_payload(payload: bytes) -> "HelloAck":
        if len(payload) != 7:
            raise ProtocolError(f"HELLO_ACK payload must be 7 bytes, got {len(payload)}")
        version, hint, t = struct.unpack("<BHI", payload)
        return HelloAck(version, hint, t)


@dataclass
class Frame:
    sequence: int
    timestamp_ms: int
    width: int
    height: int
    pixel_format: int
    pixels: bytes  # width*height*2 bytes, RGB565 LE, row-major

    def encode(self) -> bytes:
        expected = self.width * self.height * 2
        if len(self.pixels) != expected:
            raise ValueError(f"pixels must be {expected} bytes for {self.width}x{self.height}, got {len(self.pixels)}")
        payload = struct.pack(
            "<IIHHBB",
            self.sequence,
            self.timestamp_ms,
            self.width,
            self.height,
            self.pixel_format,
            0,
        ) + self.pixels
        return encode_header(MessageType.FRAME, len(payload)) + payload

    @staticmethod
    def decode_payload(payload: bytes) -> "Frame":
        if len(payload) < 14:
            raise ProtocolError("FRAME payload shorter than fixed fields")
        sequence, timestamp_ms, width, height, pf, _reserved = struct.unpack("<IIHHBB", payload[:14])
        pixels = payload[14:]
        expected = width * height * 2
        if len(pixels) != expected:
            raise ProtocolError(f"FRAME pixel payload is {len(pixels)} bytes, expected {expected} for {width}x{height}")
        return Frame(sequence, timestamp_ms, width, height, pf, pixels)


@dataclass
class Brightness:
    brightness: int

    def encode(self) -> bytes:
        payload = struct.pack("<B", self.brightness)
        return encode_header(MessageType.BRIGHTNESS, len(payload)) + payload

    @staticmethod
    def decode_payload(payload: bytes) -> "Brightness":
        if len(payload) != 1:
            raise ProtocolError(f"BRIGHTNESS payload must be 1 byte, got {len(payload)}")
        return Brightness(payload[0])


@dataclass
class Blank:
    blank: bool

    def encode(self) -> bytes:
        payload = struct.pack("<B", 1 if self.blank else 0)
        return encode_header(MessageType.BLANK, len(payload)) + payload

    @staticmethod
    def decode_payload(payload: bytes) -> "Blank":
        if len(payload) != 1:
            raise ProtocolError(f"BLANK payload must be 1 byte, got {len(payload)}")
        return Blank(bool(payload[0]))


@dataclass
class Ping:
    nonce: int

    def encode(self) -> bytes:
        payload = struct.pack("<I", self.nonce)
        return encode_header(MessageType.PING, len(payload)) + payload

    @staticmethod
    def decode_payload(payload: bytes) -> "Ping":
        if len(payload) != 4:
            raise ProtocolError(f"PING payload must be 4 bytes, got {len(payload)}")
        return Ping(struct.unpack("<I", payload)[0])


@dataclass
class Pong:
    nonce: int

    def encode(self) -> bytes:
        payload = struct.pack("<I", self.nonce)
        return encode_header(MessageType.PONG, len(payload)) + payload

    @staticmethod
    def decode_payload(payload: bytes) -> "Pong":
        if len(payload) != 4:
            raise ProtocolError(f"PONG payload must be 4 bytes, got {len(payload)}")
        return Pong(struct.unpack("<I", payload)[0])


@dataclass
class Status:
    code: int
    message: str = ""

    def encode(self) -> bytes:
        msg_bytes = self.message.encode("utf-8")
        payload = struct.pack("<H", self.code) + msg_bytes
        return encode_header(MessageType.STATUS, len(payload)) + payload

    @staticmethod
    def decode_payload(payload: bytes) -> "Status":
        if len(payload) < 2:
            raise ProtocolError("STATUS payload shorter than fixed fields")
        code = struct.unpack("<H", payload[:2])[0]
        message = payload[2:].decode("utf-8", errors="replace")
        return Status(code, message)


def rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
