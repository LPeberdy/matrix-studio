#include "ms_protocol.h"

#include <cstring>

namespace matrix_studio {
namespace {

class ByteReader {
 public:
  ByteReader(const uint8_t* data, size_t len) : data_(data), len_(len), pos_(0) {}

  size_t remaining() const { return len_ - pos_; }

  bool read_u8(uint8_t* out) {
    if (remaining() < 1) return false;
    *out = data_[pos_++];
    return true;
  }

  bool read_u16(uint16_t* out) {
    if (remaining() < 2) return false;
    *out = static_cast<uint16_t>(data_[pos_]) | static_cast<uint16_t>(data_[pos_ + 1] << 8);
    pos_ += 2;
    return true;
  }

  bool read_u32(uint32_t* out) {
    if (remaining() < 4) return false;
    *out = static_cast<uint32_t>(data_[pos_]) | (static_cast<uint32_t>(data_[pos_ + 1]) << 8) |
           (static_cast<uint32_t>(data_[pos_ + 2]) << 16) | (static_cast<uint32_t>(data_[pos_ + 3]) << 24);
    pos_ += 4;
    return true;
  }

  bool read_padded_string(char* out, size_t field_len) {
    if (remaining() < field_len) return false;
    std::memcpy(out, data_ + pos_, field_len);
    out[field_len] = '\0';
    pos_ += field_len;
    return true;
  }

  const uint8_t* cursor() const { return data_ + pos_; }

 private:
  const uint8_t* data_;
  size_t len_;
  size_t pos_;
};

class ByteWriter {
 public:
  ByteWriter(uint8_t* data, size_t cap) : data_(data), cap_(cap), pos_(0), ok_(true) {}

  void write_u8(uint8_t v) {
    if (!ok_ || cap_ - pos_ < 1) { ok_ = false; return; }
    data_[pos_++] = v;
  }

  void write_u16(uint16_t v) {
    if (!ok_ || cap_ - pos_ < 2) { ok_ = false; return; }
    data_[pos_++] = static_cast<uint8_t>(v & 0xFF);
    data_[pos_++] = static_cast<uint8_t>((v >> 8) & 0xFF);
  }

  void write_u32(uint32_t v) {
    if (!ok_ || cap_ - pos_ < 4) { ok_ = false; return; }
    data_[pos_++] = static_cast<uint8_t>(v & 0xFF);
    data_[pos_++] = static_cast<uint8_t>((v >> 8) & 0xFF);
    data_[pos_++] = static_cast<uint8_t>((v >> 16) & 0xFF);
    data_[pos_++] = static_cast<uint8_t>((v >> 24) & 0xFF);
  }

  void write_bytes(const uint8_t* src, size_t n) {
    if (!ok_ || cap_ - pos_ < n) { ok_ = false; return; }
    std::memcpy(data_ + pos_, src, n);
    pos_ += n;
  }

  void write_padded_string(const char* s, size_t field_len) {
    const size_t n = s ? std::strlen(s) : 0;
    if (n > field_len) { ok_ = false; return; }
    if (!ok_ || cap_ - pos_ < field_len) { ok_ = false; return; }
    std::memcpy(data_ + pos_, s, n);
    std::memset(data_ + pos_ + n, 0, field_len - n);
    pos_ += field_len;
  }

  size_t finish() const { return ok_ ? pos_ : 0; }

 private:
  uint8_t* data_;
  size_t cap_;
  size_t pos_;
  bool ok_;
};

void write_header(ByteWriter& w, msp::MessageType type, uint32_t payload_len) {
  w.write_u8(msp::kMagic);
  w.write_u8(msp::kProtocolVersion);
  w.write_u8(static_cast<uint8_t>(type));
  w.write_u8(0);
  w.write_u32(payload_len);
}

bool is_known_type(uint8_t t) {
  switch (t) {
    case static_cast<uint8_t>(msp::MessageType::kHello):
    case static_cast<uint8_t>(msp::MessageType::kHelloAck):
    case static_cast<uint8_t>(msp::MessageType::kFrame):
    case static_cast<uint8_t>(msp::MessageType::kBrightness):
    case static_cast<uint8_t>(msp::MessageType::kBlank):
    case static_cast<uint8_t>(msp::MessageType::kPing):
    case static_cast<uint8_t>(msp::MessageType::kPong):
    case static_cast<uint8_t>(msp::MessageType::kStatus):
    case static_cast<uint8_t>(msp::MessageType::kOtaBegin):
    case static_cast<uint8_t>(msp::MessageType::kOtaData):
    case static_cast<uint8_t>(msp::MessageType::kOtaCommit):
      return true;
    default:
      return false;
  }
}

}  // namespace

const char* parse_result_name(ParseResult r) {
  switch (r) {
    case ParseResult::kOk: return "ok";
    case ParseResult::kIncompleteHeader: return "incomplete_header";
    case ParseResult::kBadMagic: return "bad_magic";
    case ParseResult::kUnsupportedVersion: return "unsupported_version";
    case ParseResult::kLengthTooLarge: return "length_too_large";
    case ParseResult::kTruncatedPayload: return "truncated";
    case ParseResult::kUnknownType: return "unknown_type";
    case ParseResult::kExtensionType: return "extension_type";
    case ParseResult::kMalformedPayload: return "malformed_payload";
  }
  return "unknown";
}

bool is_fatal(ParseResult r) {
  switch (r) {
    case ParseResult::kBadMagic:
    case ParseResult::kUnsupportedVersion:
    case ParseResult::kLengthTooLarge:
      return true;
    default:
      return false;
  }
}

bool status_code_for(ParseResult r, msp::StatusCode* out_code) {
  switch (r) {
    case ParseResult::kUnsupportedVersion:
      *out_code = msp::StatusCode::kErrUnsupportedVersion;
      return true;
    case ParseResult::kUnknownType:
    case ParseResult::kExtensionType:
      *out_code = msp::StatusCode::kErrUnknownType;
      return true;
    case ParseResult::kTruncatedPayload:
    case ParseResult::kMalformedPayload:
      *out_code = msp::StatusCode::kErrMalformedPayload;
      return true;
    default:
      return false;
  }
}

ParseResult parse_message(const uint8_t* buf, size_t len, Message& out) {
  out = Message{};

  if (buf == nullptr || len < msp::kHeaderSizeBytes) return ParseResult::kIncompleteHeader;

  ByteReader header_reader(buf, len);
  (void)header_reader.read_u8(&out.header.magic);
  (void)header_reader.read_u8(&out.header.version);
  (void)header_reader.read_u8(&out.header.type);
  (void)header_reader.read_u8(&out.header.flags);
  (void)header_reader.read_u32(&out.header.length);

  if (out.header.magic != msp::kMagic) return ParseResult::kBadMagic;
  if (out.header.version != msp::kProtocolVersion) return ParseResult::kUnsupportedVersion;
  if (out.header.length > msp::kMaxPayloadBytes) return ParseResult::kLengthTooLarge;

  out.total_size = msp::kHeaderSizeBytes + static_cast<size_t>(out.header.length);
  if (len < out.total_size) return ParseResult::kTruncatedPayload;

  out.payload = buf + msp::kHeaderSizeBytes;
  out.payload_len = out.header.length;

  if (!is_known_type(out.header.type)) {
    return is_extension_type(out.header.type) ? ParseResult::kExtensionType : ParseResult::kUnknownType;
  }
  out.type = static_cast<msp::MessageType>(out.header.type);

  ByteReader r(out.payload, out.payload_len);

  switch (out.type) {
    case msp::MessageType::kHello: {
      if (out.payload_len != msp::kHelloPayloadLen) return ParseResult::kMalformedPayload;
      if (!r.read_u8(&out.hello.protocol_version)) return ParseResult::kMalformedPayload;
      if (!r.read_u16(&out.hello.width)) return ParseResult::kMalformedPayload;
      if (!r.read_u16(&out.hello.height)) return ParseResult::kMalformedPayload;
      if (!r.read_u8(&out.hello.pixel_format)) return ParseResult::kMalformedPayload;
      if (!r.read_padded_string(out.hello.device_id, msp::kDeviceIdFieldLen)) return ParseResult::kMalformedPayload;
      if (!r.read_padded_string(out.hello.fw_version, msp::kFwVersionFieldLen)) return ParseResult::kMalformedPayload;
      return ParseResult::kOk;
    }
    case msp::MessageType::kHelloAck: {
      if (out.payload_len != msp::kHelloAckPayloadLen) return ParseResult::kMalformedPayload;
      if (!r.read_u8(&out.hello_ack.protocol_version)) return ParseResult::kMalformedPayload;
      if (!r.read_u16(&out.hello_ack.frame_interval_hint_ms)) return ParseResult::kMalformedPayload;
      if (!r.read_u32(&out.hello_ack.server_time_unix)) return ParseResult::kMalformedPayload;
      return ParseResult::kOk;
    }
    case msp::MessageType::kFrame: {
      if (out.payload_len < msp::kFrameFixedFieldsLen) return ParseResult::kMalformedPayload;
      if (!r.read_u32(&out.frame.sequence)) return ParseResult::kMalformedPayload;
      if (!r.read_u32(&out.frame.timestamp_ms)) return ParseResult::kMalformedPayload;
      if (!r.read_u16(&out.frame.width)) return ParseResult::kMalformedPayload;
      if (!r.read_u16(&out.frame.height)) return ParseResult::kMalformedPayload;
      if (!r.read_u8(&out.frame.pixel_format)) return ParseResult::kMalformedPayload;
      if (!r.read_u8(&out.frame.reserved)) return ParseResult::kMalformedPayload;
      if (out.frame.pixel_format != static_cast<uint8_t>(msp::PixelFormat::kRgb565)) {
        return ParseResult::kMalformedPayload;
      }
      const size_t expected = static_cast<size_t>(out.frame.width) * static_cast<size_t>(out.frame.height) * 2u;
      if (r.remaining() != expected) return ParseResult::kMalformedPayload;
      out.frame.pixels = r.cursor();
      out.frame.pixel_bytes = expected;
      return ParseResult::kOk;
    }
    case msp::MessageType::kBrightness: {
      if (out.payload_len != msp::kBrightnessPayloadLen) return ParseResult::kMalformedPayload;
      if (!r.read_u8(&out.brightness)) return ParseResult::kMalformedPayload;
      return ParseResult::kOk;
    }
    case msp::MessageType::kBlank: {
      if (out.payload_len != msp::kBlankPayloadLen) return ParseResult::kMalformedPayload;
      uint8_t v = 0;
      if (!r.read_u8(&v)) return ParseResult::kMalformedPayload;
      out.blank = (v != 0);
      return ParseResult::kOk;
    }
    case msp::MessageType::kPing: {
      if (out.payload_len != msp::kPingPayloadLen) return ParseResult::kMalformedPayload;
      if (!r.read_u32(&out.nonce)) return ParseResult::kMalformedPayload;
      return ParseResult::kOk;
    }
    case msp::MessageType::kPong: {
      if (out.payload_len != msp::kPongPayloadLen) return ParseResult::kMalformedPayload;
      if (!r.read_u32(&out.nonce)) return ParseResult::kMalformedPayload;
      return ParseResult::kOk;
    }
    case msp::MessageType::kStatus: {
      if (out.payload_len < msp::kStatusFixedFieldsLen) return ParseResult::kMalformedPayload;
      if (!r.read_u16(&out.status.code)) return ParseResult::kMalformedPayload;
      out.status.text = reinterpret_cast<const char*>(r.cursor());
      out.status.text_len = r.remaining();
      return ParseResult::kOk;
    }
    case msp::MessageType::kOtaBegin: {
      if (out.payload_len != msp::kOtaBeginPayloadLen) return ParseResult::kMalformedPayload;
      if (!r.read_u32(&out.ota_image_size) || out.ota_image_size == 0) return ParseResult::kMalformedPayload;
      return ParseResult::kOk;
    }
    case msp::MessageType::kOtaData: {
      if (out.payload_len <= msp::kOtaDataFixedFieldsLen ||
          out.payload_len > msp::kOtaDataFixedFieldsLen + msp::kOtaMaxChunkBytes) {
        return ParseResult::kMalformedPayload;
      }
      if (!r.read_u32(&out.ota_data.offset)) return ParseResult::kMalformedPayload;
      out.ota_data.data = r.cursor();
      out.ota_data.data_len = r.remaining();
      return ParseResult::kOk;
    }
    case msp::MessageType::kOtaCommit:
      return out.payload_len == msp::kOtaCommitPayloadLen ? ParseResult::kOk : ParseResult::kMalformedPayload;
  }
  return ParseResult::kUnknownType;
}

size_t encode_hello(uint8_t* buf, size_t cap, uint16_t width, uint16_t height, uint8_t pixel_format,
                    const char* device_id, const char* fw_version) {
  ByteWriter w(buf, cap);
  write_header(w, msp::MessageType::kHello, msp::kHelloPayloadLen);
  w.write_u8(msp::kProtocolVersion);
  w.write_u16(width);
  w.write_u16(height);
  w.write_u8(pixel_format);
  w.write_padded_string(device_id, msp::kDeviceIdFieldLen);
  w.write_padded_string(fw_version, msp::kFwVersionFieldLen);
  return w.finish();
}

size_t encode_ping(uint8_t* buf, size_t cap, uint32_t nonce) {
  ByteWriter w(buf, cap);
  write_header(w, msp::MessageType::kPing, msp::kPingPayloadLen);
  w.write_u32(nonce);
  return w.finish();
}

size_t encode_pong(uint8_t* buf, size_t cap, uint32_t nonce) {
  ByteWriter w(buf, cap);
  write_header(w, msp::MessageType::kPong, msp::kPongPayloadLen);
  w.write_u32(nonce);
  return w.finish();
}

size_t encode_status(uint8_t* buf, size_t cap, msp::StatusCode code, const char* message) {
  size_t text_len = message ? std::strlen(message) : 0;
  if (text_len > kMaxStatusTextBytes) text_len = kMaxStatusTextBytes;
  ByteWriter w(buf, cap);
  write_header(w, msp::MessageType::kStatus,
               static_cast<uint32_t>(msp::kStatusFixedFieldsLen + text_len));
  w.write_u16(static_cast<uint16_t>(code));
  if (text_len > 0) w.write_bytes(reinterpret_cast<const uint8_t*>(message), text_len);
  return w.finish();
}

}  // namespace matrix_studio
