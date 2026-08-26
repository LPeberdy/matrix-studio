// Matrix Studio Protocol v1 — device-side parser/encoder.
//
// This file is deliberately free of ESP-IDF (and any other platform) types so
// that it compiles both inside the firmware and in the host-side test suite
// under esp32/tests/. It works on plain `const uint8_t*` buffers and never
// allocates.
//
// The wire constants and struct layouts come from the frozen contract header
// protocol/matrix_studio_protocol.h — this file must not redefine any of them.
// Behaviour is defined by docs/protocol.md; section references below point at
// that document.

#pragma once

#include <cstddef>
#include <cstdint>

#include "matrix_studio_protocol.h"

namespace matrix_studio {

namespace msp = matrix_studio_protocol;

// Result of attempting to parse one complete protocol message out of a buffer.
//
// The mapping from these values to connection behaviour is docs/protocol.md
// §3.5 and is expressed by is_fatal() / status_code_for() below, so that the
// policy lives in exactly one place.
enum class ParseResult : uint8_t {
  kOk = 0,
  // Fewer than kHeaderSizeBytes bytes available. Not an error on a streaming
  // transport; over WebSocket (one message per protocol message) it means the
  // peer sent a runt message.
  kIncompleteHeader,
  // §3.5(1): magic != 0xA5. Stream is desynchronised — fatal.
  kBadMagic,
  // §3.5(2): header version is not kProtocolVersion — STATUS then close.
  kUnsupportedVersion,
  // §3.5(4): declared length > kMaxPayloadBytes. Close *without* attempting to
  // read the declared payload.
  kLengthTooLarge,
  // Declared length is sane but fewer bytes are actually present. Over
  // WebSocket this is the "truncated frame" fixture case: the message is
  // malformed, but the connection is still usable (§3.5(5) treatment).
  kTruncatedPayload,
  // §3.5(3): type is not a known v1 type and is outside the extension range.
  kUnknownType,
  // §5: type is in the reserved extension range 0x80..0xFE. Ignore this single
  // message; never fatal.
  kExtensionType,
  // §3.5(5): well-framed but the payload size is wrong for its type.
  kMalformedPayload,
};

const char* parse_result_name(ParseResult r);

// True if this result requires closing the connection (§3.5).
bool is_fatal(ParseResult r);

// If the receiver should answer with a STATUS message, writes the code and
// returns true. (kOk and kIncompleteHeader produce no STATUS.)
bool status_code_for(ParseResult r, msp::StatusCode* out_code);

// True for a type byte inside the reserved extension range (§5).
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
  // NUL-terminated copies of the fixed-width, NUL-padded wire fields.
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
  // Points into the caller's buffer; valid only as long as that buffer is.
  const uint8_t* pixels = nullptr;
  size_t pixel_bytes = 0;
};

struct StatusFields {
  uint16_t code = 0;
  const char* text = nullptr;  // not NUL-terminated; points into caller buffer
  size_t text_len = 0;
};

// A decoded message. Only the member matching `type` is meaningful.
struct Message {
  Header header{};
  msp::MessageType type{};
  size_t total_size = 0;  // kHeaderSizeBytes + header.length
  const uint8_t* payload = nullptr;
  size_t payload_len = 0;

  HelloFields hello{};
  HelloAckFields hello_ack{};
  FrameFields frame{};
  StatusFields status{};
  uint8_t brightness = 0;
  bool blank = false;
  uint32_t nonce = 0;
};

// Parse exactly one message from `buf[0 .. len)`.
//
// Never reads past `len` regardless of what the header claims — the
// "truncated frame" fixture exercises precisely this. `out` is always at least
// partially populated (header fields whenever they were readable and valid) so
// callers can log meaningfully even on failure.
ParseResult parse_message(const uint8_t* buf, size_t len, Message& out);

// True if the message carried reserved header flag bits. v1 requires flags==0
// (docs/protocol.md §2) but §3.5 does not make a non-zero value fatal, and §5
// reserves those bits for future use, so the parser records rather than
// rejects. See esp32/PROTOCOL_ISSUES.md.
inline bool has_reserved_flags(const Message& m) { return m.header.flags != 0; }

// ---------------------------------------------------------------------------
// Encoders. Each returns the number of bytes written, or 0 if `cap` is too
// small or an argument does not fit its wire field.
// ---------------------------------------------------------------------------

size_t encode_hello(uint8_t* buf, size_t cap, uint16_t width, uint16_t height, uint8_t pixel_format,
                    const char* device_id, const char* fw_version);
size_t encode_ping(uint8_t* buf, size_t cap, uint32_t nonce);
size_t encode_pong(uint8_t* buf, size_t cap, uint32_t nonce);

// Longest STATUS text this device will ever transmit; longer strings are
// truncated rather than rejected so an error path can never itself fail.
constexpr size_t kMaxStatusTextBytes = 64;

// `message` may be nullptr for a zero-length STATUS message.
size_t encode_status(uint8_t* buf, size_t cap, msp::StatusCode code, const char* message);

// Largest buffer any encoder here can need, so callers can size a stack buffer
// once instead of guessing per message type.
constexpr size_t kMaxEncodedTxBytes =
    msp::kHeaderSizeBytes + msp::kStatusFixedFieldsLen + kMaxStatusTextBytes;
static_assert(kMaxEncodedTxBytes >= msp::kHeaderSizeBytes + msp::kHelloPayloadLen,
              "tx scratch buffer must also fit a HELLO");

}  // namespace matrix_studio
