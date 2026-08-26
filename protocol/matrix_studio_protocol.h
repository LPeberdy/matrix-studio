// Matrix Studio Protocol v1 — canonical constants (C++ side).
//
// This header is the C++ half of the contract described in docs/protocol.md.
// protocol/matrix_studio_protocol.py is the Python half. Do not change wire
// semantics here without updating docs/protocol.md first.
//
// This header defines constants and POD-friendly struct layouts only; it
// intentionally has no encode/decode logic so it can be included from either
// a hosted unit test build or the ESP32 firmware build without dragging in
// Arduino/ESP-IDF dependencies.

#pragma once

#include <cstdint>
#include <cstddef>

namespace matrix_studio_protocol {

constexpr uint8_t kProtocolVersion = 1;
constexpr uint8_t kMagic = 0xA5;
constexpr size_t kHeaderSizeBytes = 8;
constexpr size_t kMaxPayloadBytes = 65535;

constexpr uint32_t kHelloTimeoutMs = 5000;
constexpr uint32_t kPingIntervalMs = 10000;
constexpr uint32_t kPongTimeoutMs = 10000;
constexpr uint32_t kFrameTimeoutMsDefault = 5000;
constexpr uint32_t kReconnectMaxBackoffS = 30;

constexpr uint16_t kDefaultWsPort = 7887;
constexpr const char* kDefaultWsPath = "/matrix-studio";

constexpr size_t kDeviceIdFieldLen = 16;
constexpr size_t kFwVersionFieldLen = 16;

constexpr size_t kHelloPayloadLen = 38;
constexpr size_t kHelloAckPayloadLen = 7;
constexpr size_t kFrameFixedFieldsLen = 14;
constexpr size_t kBrightnessPayloadLen = 1;
constexpr size_t kBlankPayloadLen = 1;
constexpr size_t kPingPayloadLen = 4;
constexpr size_t kPongPayloadLen = 4;
constexpr size_t kStatusFixedFieldsLen = 2;
constexpr size_t kOtaBeginPayloadLen = 4;
constexpr size_t kOtaDataFixedFieldsLen = 4;
constexpr size_t kOtaMaxChunkBytes = 4096;
constexpr size_t kOtaCommitPayloadLen = 0;

enum class MessageType : uint8_t {
  kHello = 0x01,
  kHelloAck = 0x02,
  kFrame = 0x03,
  kBrightness = 0x04,
  kBlank = 0x05,
  kPing = 0x06,
  kPong = 0x07,
  kStatus = 0x08,
  kOtaBegin = 0x09,
  kOtaData = 0x0A,
  kOtaCommit = 0x0B,
};

enum class PixelFormat : uint8_t {
  kRgb565 = 0x01,
};

enum class StatusCode : uint16_t {
  kOk = 0x0000,
  kErrUnsupportedVersion = 0x0001,
  kErrUnknownType = 0x0002,
  kErrMalformedPayload = 0x0003,
  kErrDimensionMismatch = 0x0004,
  kErrInternal = 0x0005,
  kErrOtaState = 0x0006,
  kErrOtaImage = 0x0007,
};

#pragma pack(push, 1)

// Wire layout of the 8-byte header. Field order matches docs/protocol.md
// exactly; do not reorder. `length` is little-endian on the wire, which
// matches native byte order on ESP32-S3 (Xtensa LX7) and does not need
// swapping when memcpy'd from a little-endian-ordered buffer.
struct WireHeader {
  uint8_t magic;
  uint8_t version;
  uint8_t type;
  uint8_t flags;
  uint32_t length;
};

struct WireHelloFixed {
  uint8_t protocol_version;
  uint16_t width;
  uint16_t height;
  uint8_t pixel_format;
  char device_id[kDeviceIdFieldLen];
  char fw_version[kFwVersionFieldLen];
};

struct WireHelloAck {
  uint8_t protocol_version;
  uint16_t frame_interval_hint_ms;
  uint32_t server_time_unix;
};

struct WireFrameFixed {
  uint32_t sequence;
  uint32_t timestamp_ms;
  uint16_t width;
  uint16_t height;
  uint8_t pixel_format;
  uint8_t reserved;
  // followed by width*height*2 bytes of RGB565 pixel data
};

struct WireBrightness {
  uint8_t brightness;
};

struct WireBlank {
  uint8_t blank;
};

struct WirePing {
  uint32_t nonce;
};

struct WirePong {
  uint32_t nonce;
};

struct WireStatusFixed {
  uint16_t code;
  // followed by a UTF-8 message of (header.length - 2) bytes
};

struct WireOtaBegin {
  uint32_t image_size;
};

struct WireOtaDataFixed {
  uint32_t offset;
  // followed by 1..kOtaMaxChunkBytes firmware bytes
};

#pragma pack(pop)

static_assert(sizeof(WireHeader) == kHeaderSizeBytes, "WireHeader must be exactly 8 bytes on the wire");
static_assert(sizeof(WireOtaBegin) == kOtaBeginPayloadLen, "WireOtaBegin must be exactly 4 bytes");
static_assert(sizeof(WireOtaDataFixed) == kOtaDataFixedFieldsLen, "WireOtaDataFixed must be exactly 4 bytes");

}  // namespace matrix_studio_protocol
