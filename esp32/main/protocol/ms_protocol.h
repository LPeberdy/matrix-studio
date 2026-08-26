// Matrix Studio Protocol v1 — device-side parser/encoder.
//
// This file is deliberately free of ESP-IDF (and any other platform) types so
// that it compiles both inside the firmware and in the host-side test suite
// under esp32/tests/. It works on plain `const uint8_t*` buffers and never
// allocates.
//
// The wire constants and struct layouts come from protocol/matrix_studio_protocol.h.
// Behaviour is defined by docs/protocol.md.

#pragma once

#include <cstddef>
#include <cstdint>

#include "matrix_studio_protocol.h"

namespace matrix_studio {

namespace msp = matrix_studio_protocol;

enum class ParseResult : uint8_t {
  kOk = 0,
  kIncompleteHeader,
  kBadMagic,
  kUnsupportedVersion,
  kLengthTooLarge,
  kTruncatedPayload,
  kUnknownType,
  kExtensionType,
  kMalformedPayload,
};

const char* parse_result_name(ParseResult r);
bool is_fatal(ParseResult r);
bool status_code_for(ParseResult r, msp::StatusCode* out_code);
constexpr bool is_extension_type(uint8_t type) { return type >= 0x80 && type <= 0xFE; }

struct Header {
  uint8_t magic = 0;
  uint8_t version = 0;
  uint8_t type = 0;
  uint8_t flags = 0;
  uint32_t length = 0;
};

struct HelloFields {
  uint8_t protocol_version = 0;
  uint16_t width = 0;
  uint16_t height = 0;
  uint8_t pixel_format = 0;
  char device_id[msp::kDeviceIdFieldLen + 1] = {};
  char fw_version[msp::kFwVersionFieldLen + 1] = {};
};

struct HelloAckFields {
  uint8_t protocol_version = 0;
  uint16_t frame_interval_hint_ms = 0;
  uint32_t server_time_unix = 0;
};

struct FrameFields {
  uint32_t sequence = 0;
  uint32_t timestamp_ms = 0;
  uint16_t width = 0;
  uint16_t height = 0;
  uint8_t pixel_format = 0;
  uint8_t reserved = 0;
  const uint8_t* pixels = nullptr;
  size_t pixel_bytes = 0;
};

struct StatusFields {
  uint16_t code = 0;
  const char* text = nullptr;
  size_t text_len = 0;
};

struct OtaDataFields {
  uint32_t offset = 0;
  const uint8_t* data = nullptr;
  size_t data_len = 0;
};

struct Message {
  Header header{};
  msp::MessageType type{};
  size_t total_size = 0;
  const uint8_t* payload = nullptr;
  size_t payload_len = 0;

  HelloFields hello{};
  HelloAckFields hello_ack{};
  FrameFields frame{};
  StatusFields status{};
  OtaDataFields ota_data{};
  uint8_t brightness = 0;
  bool blank = false;
  uint32_t nonce = 0;
  uint32_t ota_image_size = 0;
};

ParseResult parse_message(const uint8_t* buf, size_t len, Message& out);
inline bool has_reserved_flags(const Message& m) { return m.header.flags != 0; }

size_t encode_hello(uint8_t* buf, size_t cap, uint16_t width, uint16_t height, uint8_t pixel_format,
                    const char* device_id, const char* fw_version);
size_t encode_ping(uint8_t* buf, size_t cap, uint32_t nonce);
size_t encode_pong(uint8_t* buf, size_t cap, uint32_t nonce);

constexpr size_t kMaxStatusTextBytes = 64;
size_t encode_status(uint8_t* buf, size_t cap, msp::StatusCode code, const char* message);

constexpr size_t kMaxEncodedTxBytes =
    msp::kHeaderSizeBytes + msp::kStatusFixedFieldsLen + kMaxStatusTextBytes;
static_assert(kMaxEncodedTxBytes >= msp::kHeaderSizeBytes + msp::kHelloPayloadLen,
              "tx scratch buffer must also fit a HELLO");

}  // namespace matrix_studio
