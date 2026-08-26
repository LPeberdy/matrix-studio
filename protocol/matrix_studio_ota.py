"""Matrix Studio Protocol v1 OTA extension.

Uses the 0x80..0xFE extension range reserved by docs/protocol.md §5. The core
Protocol v1 codec remains frozen and unchanged.
"""
from __future__ import annotations

import struct
from enum import IntEnum

import matrix_studio_protocol as core


class OtaMessageType(IntEnum):
    BEGIN = 0x80
    DATA = 0x81
    COMMIT = 0x82


MAX_CHUNK_BYTES = 4096


def encode_begin(image_size: int) -> bytes:
    if image_size <= 0 or image_size > 0xFFFFFFFF:
        raise ValueError("image_size must fit u32 and be greater than zero")
    payload = struct.pack("<I", image_size)
    return core.encode_header(OtaMessageType.BEGIN, len(payload)) + payload


def encode_data(offset: int, chunk: bytes) -> bytes:
    if offset < 0 or offset > 0xFFFFFFFF:
        raise ValueError("offset must fit u32")
    if not chunk or len(chunk) > MAX_CHUNK_BYTES:
        raise ValueError(f"chunk must contain 1..{MAX_CHUNK_BYTES} bytes")
    payload = struct.pack("<I", offset) + bytes(chunk)
    return core.encode_header(OtaMessageType.DATA, len(payload)) + payload


def encode_commit() -> bytes:
    return core.encode_header(OtaMessageType.COMMIT, 0)


def decode_begin(payload: bytes) -> int:
    if len(payload) != 4:
        raise core.ProtocolError(f"OTA_BEGIN payload must be 4 bytes, got {len(payload)}")
    image_size = struct.unpack("<I", payload)[0]
    if image_size == 0:
        raise core.ProtocolError("OTA_BEGIN image_size must be greater than zero")
    return image_size


def decode_data(payload: bytes) -> tuple[int, bytes]:
    if len(payload) < 5 or len(payload) > 4 + MAX_CHUNK_BYTES:
        raise core.ProtocolError(
            f"OTA_DATA payload must contain 4-byte offset plus 1..{MAX_CHUNK_BYTES} data bytes"
        )
    offset = struct.unpack("<I", payload[:4])[0]
    return offset, payload[4:]


def decode_commit(payload: bytes) -> None:
    if payload:
        raise core.ProtocolError(f"OTA_COMMIT payload must be empty, got {len(payload)} bytes")
