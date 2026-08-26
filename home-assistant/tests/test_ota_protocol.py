from matrix_studio.vendor import matrix_studio_protocol as proto


def _payload(encoded: bytes) -> bytes:
    header = proto.decode_header(encoded)
    body = encoded[proto.HEADER_SIZE_BYTES :]
    assert len(body) == header.length
    return body


def test_ota_begin_is_first_class_protocol_v1():
    message = proto.OtaBegin(123456)
    encoded = message.encode()
    assert proto.decode_header(encoded).type == proto.MessageType.OTA_BEGIN
    assert proto.OtaBegin.decode_payload(_payload(encoded)) == message


def test_ota_data_is_first_class_protocol_v1():
    message = proto.OtaData(4096, b"firmware")
    encoded = message.encode()
    assert proto.decode_header(encoded).type == proto.MessageType.OTA_DATA
    assert proto.OtaData.decode_payload(_payload(encoded)) == message


def test_ota_commit_is_first_class_protocol_v1():
    encoded = proto.OtaCommit().encode()
    assert proto.decode_header(encoded).type == proto.MessageType.OTA_COMMIT
    assert proto.OtaCommit.decode_payload(_payload(encoded)) == proto.OtaCommit()
