import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import matrix_studio_ota as ota  # noqa: E402
import matrix_studio_protocol as core  # noqa: E402


def payload(encoded: bytes) -> bytes:
    header = core.decode_header(encoded)
    body = encoded[core.HEADER_SIZE_BYTES :]
    assert len(body) == header.length
    return body


def test_begin_roundtrip():
    encoded = ota.encode_begin(1_234_567)
    header = core.decode_header(encoded)
    assert header.type == ota.OtaMessageType.BEGIN
    assert ota.decode_begin(payload(encoded)) == 1_234_567


def test_data_roundtrip():
    chunk = bytes(range(256)) * 16
    encoded = ota.encode_data(8192, chunk)
    header = core.decode_header(encoded)
    assert header.type == ota.OtaMessageType.DATA
    offset, decoded = ota.decode_data(payload(encoded))
    assert offset == 8192
    assert decoded == chunk


def test_commit_is_empty():
    encoded = ota.encode_commit()
    header = core.decode_header(encoded)
    assert header.type == ota.OtaMessageType.COMMIT
    assert header.length == 0
    assert ota.decode_commit(payload(encoded)) is None


def test_data_rejects_oversized_chunk():
    with pytest.raises(ValueError):
        ota.encode_data(0, b"x" * (ota.MAX_CHUNK_BYTES + 1))


def test_data_rejects_empty_chunk():
    with pytest.raises(ValueError):
        ota.encode_data(0, b"")


def test_begin_rejects_zero_size():
    with pytest.raises(ValueError):
        ota.encode_begin(0)
