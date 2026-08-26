// Host-side tests for the ESP32 device's Protocol v1 parser/encoder.
//
// Every fixture in protocol/fixtures/ is loaded from disk and asserted against
// the expectations recorded in that directory's manifest.json, so a divergence
// between this parser and the Python reference implementation fails here.
//
// Fixtures are copied into exactly-sized heap buffers before parsing. Combined
// with the ASan build enabled by CMakeLists.txt, any read past the end of a
// message (the "truncated frame" case in particular) aborts the test run
// instead of quietly returning plausible garbage.

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "ms_protocol.h"
#include "reconnect_backoff.h"

namespace msp = matrix_studio_protocol;
using matrix_studio::Message;
using matrix_studio::ParseResult;

namespace {

int g_failures = 0;
int g_checks = 0;
const char* g_current_test = "";

void report_failure(const char* file, int line, const std::string& what) {
  ++g_failures;
  std::fprintf(stderr, "  FAIL [%s] %s:%d: %s\n", g_current_test, file, line, what.c_str());
}

template <typename A, typename B>
void check_eq(const A& actual, const B& expected, const char* expr, const char* file, int line) {
  ++g_checks;
  if (!(actual == static_cast<A>(expected))) {
    report_failure(file, line,
                   std::string(expr) + ": got " + std::to_string(static_cast<long long>(actual)) +
                       ", expected " + std::to_string(static_cast<long long>(expected)));
  }
}

void check_true(bool cond, const char* expr, const char* file, int line) {
  ++g_checks;
  if (!cond) report_failure(file, line, std::string(expr) + " was false");
}

void check_str_eq(const char* actual, const char* expected, const char* expr, const char* file, int line) {
  ++g_checks;
  if (std::strcmp(actual, expected) != 0) {
    report_failure(file, line, std::string(expr) + ": got \"" + actual + "\", expected \"" + expected + "\"");
  }
}

#define CHECK_EQ(actual, expected) check_eq((actual), (expected), #actual, __FILE__, __LINE__)
#define CHECK(cond) check_true((cond), #cond, __FILE__, __LINE__)
#define CHECK_STR_EQ(actual, expected) check_str_eq((actual), (expected), #actual, __FILE__, __LINE__)
#define CHECK_PARSE(result, expected) \
  check_str_eq(matrix_studio::parse_result_name(result), matrix_studio::parse_result_name(expected), \
               #result, __FILE__, __LINE__)

// Loads a fixture into an exactly-sized allocation, so ASan's redzone sits
// immediately after the last legitimate byte.
std::vector<uint8_t> load_fixture(const char* name) {
  std::string path = std::string(MATRIX_STUDIO_FIXTURE_DIR) + "/" + name;
  std::FILE* f = std::fopen(path.c_str(), "rb");
  if (!f) {
    ++g_failures;
    std::fprintf(stderr, "  FAIL [%s] cannot open fixture %s\n", g_current_test, path.c_str());
    return {};
  }
  std::vector<uint8_t> data;
  uint8_t chunk[4096];
  size_t n;
  while ((n = std::fread(chunk, 1, sizeof(chunk), f)) > 0) data.insert(data.end(), chunk, chunk + n);
  std::fclose(f);
  data.shrink_to_fit();
  return data;
}

// Mirrors protocol/fixtures/generate_fixtures.py::rgb888_to_rgb565 and
// make_test_frame_pixels so the frame fixture's pixel bytes are validated
// against an independently-derived expectation, not just their length.
uint16_t rgb888_to_rgb565(int r, int g, int b) {
  return static_cast<uint16_t>(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

// ---------------------------------------------------------------------------

void test_valid_hello() {
  auto buf = load_fixture("valid_hello.bin");
  CHECK(!buf.empty());
  if (buf.empty()) return;

  Message m;
  ParseResult r = matrix_studio::parse_message(buf.data(), buf.size(), m);
  CHECK_PARSE(r, ParseResult::kOk);
  CHECK_EQ(static_cast<int>(m.type), static_cast<int>(msp::MessageType::kHello));
  CHECK_EQ(m.header.magic, msp::kMagic);
  CHECK_EQ(m.header.version, msp::kProtocolVersion);
  CHECK_EQ(m.header.flags, 0);
  CHECK_EQ(m.header.length, msp::kHelloPayloadLen);
  CHECK_EQ(m.total_size, buf.size());
  // Expectations from protocol/fixtures/manifest.json.
  CHECK_EQ(m.hello.protocol_version, 1);
  CHECK_EQ(m.hello.width, 64);
  CHECK_EQ(m.hello.height, 64);
  CHECK_EQ(m.hello.pixel_format, static_cast<uint8_t>(msp::PixelFormat::kRgb565));
  CHECK_STR_EQ(m.hello.device_id, "matrix-esp32-01");
  CHECK_STR_EQ(m.hello.fw_version, "0.1.0");
  CHECK(!matrix_studio::has_reserved_flags(m));
  CHECK(!matrix_studio::is_fatal(r));
}

// The device is the side that *sends* HELLO, so byte-equality with the golden
// fixture is the strongest available check that our encoder matches the
// reference Python encoder.
void test_hello_encoder_matches_fixture() {
  auto expected = load_fixture("valid_hello.bin");
  CHECK(!expected.empty());
  if (expected.empty()) return;

  uint8_t out[matrix_studio::kMaxEncodedTxBytes];
  size_t n = matrix_studio::encode_hello(out, sizeof(out), 64, 64,
                                         static_cast<uint8_t>(msp::PixelFormat::kRgb565),
                                         "matrix-esp32-01", "0.1.0");
  CHECK_EQ(n, expected.size());
  if (n == expected.size()) CHECK(std::memcmp(out, expected.data(), n) == 0);

  // Too-small destination must fail cleanly rather than write partially.
  uint8_t tiny[8];
  CHECK_EQ(matrix_studio::encode_hello(tiny, sizeof(tiny), 64, 64, 1, "d", "f"), 0u);
  // Over-long device_id must be rejected, not truncated (matches Python _pad()).
  CHECK_EQ(matrix_studio::encode_hello(out, sizeof(out), 64, 64, 1, "0123456789abcdefX", "f"), 0u);
}

void test_valid_frame() {
  auto buf = load_fixture("valid_frame_64x64.bin");
  CHECK(!buf.empty());
  if (buf.empty()) return;

  Message m;
  ParseResult r = matrix_studio::parse_message(buf.data(), buf.size(), m);
  CHECK_PARSE(r, ParseResult::kOk);
  CHECK_EQ(static_cast<int>(m.type), static_cast<int>(msp::MessageType::kFrame));
  CHECK_EQ(m.header.length, 8206u);
  CHECK_EQ(m.total_size, buf.size());
  CHECK_EQ(m.frame.sequence, 42u);
  CHECK_EQ(m.frame.timestamp_ms, 123456u);
  CHECK_EQ(m.frame.width, 64);
  CHECK_EQ(m.frame.height, 64);
  CHECK_EQ(m.frame.pixel_format, 1);
  CHECK_EQ(m.frame.reserved, 0);
  CHECK_EQ(m.frame.pixel_bytes, 8192u);
  CHECK(m.frame.pixels != nullptr);
  // Pixel data must start immediately after the 14 fixed FRAME fields.
  CHECK(m.frame.pixels == buf.data() + msp::kHeaderSizeBytes + msp::kFrameFixedFieldsLen);

  // Full pixel-by-pixel comparison against the generator's documented pattern,
  // decoding each pixel as a little-endian u16 (docs/protocol.md §4.3).
  int mismatches = 0;
  for (int y = 0; y < 64; ++y) {
    for (int x = 0; x < 64; ++x) {
      const size_t i = (static_cast<size_t>(y) * 64u + static_cast<size_t>(x)) * 2u;
      const uint16_t got =
          static_cast<uint16_t>(m.frame.pixels[i]) | static_cast<uint16_t>(m.frame.pixels[i + 1] << 8);
      const uint16_t want = rgb888_to_rgb565((x * 255) / 63, (y * 255) / 63, ((x + y) * 255) / 126);
      if (got != want) ++mismatches;
    }
  }
  CHECK_EQ(mismatches, 0);
}

void test_valid_brightness() {
  auto buf = load_fixture("brightness.bin");
  CHECK(!buf.empty());
  if (buf.empty()) return;

  Message m;
  ParseResult r = matrix_studio::parse_message(buf.data(), buf.size(), m);
  CHECK_PARSE(r, ParseResult::kOk);
  CHECK_EQ(static_cast<int>(m.type), static_cast<int>(msp::MessageType::kBrightness));
  CHECK_EQ(m.brightness, 128);
}

void test_valid_ping_and_pong_reply() {
  auto buf = load_fixture("heartbeat_ping.bin");
  CHECK(!buf.empty());
  if (buf.empty()) return;

  Message m;
  ParseResult r = matrix_studio::parse_message(buf.data(), buf.size(), m);
  CHECK_PARSE(r, ParseResult::kOk);
  CHECK_EQ(static_cast<int>(m.type), static_cast<int>(msp::MessageType::kPing));
  CHECK_EQ(m.nonce, 0xDEADBEEFu);

  // Our PING encoder must reproduce the fixture byte-for-byte...
  uint8_t out[matrix_studio::kMaxEncodedTxBytes];
  size_t n = matrix_studio::encode_ping(out, sizeof(out), 0xDEADBEEFu);
  CHECK_EQ(n, buf.size());
  if (n == buf.size()) CHECK(std::memcmp(out, buf.data(), n) == 0);

  // ...and the PONG we answer with must echo the nonce (§3.1).
  n = matrix_studio::encode_pong(out, sizeof(out), m.nonce);
  CHECK_EQ(n, msp::kHeaderSizeBytes + msp::kPongPayloadLen);
  Message pong;
  CHECK_PARSE(matrix_studio::parse_message(out, n, pong), ParseResult::kOk);
  CHECK_EQ(static_cast<int>(pong.type), static_cast<int>(msp::MessageType::kPong));
  CHECK_EQ(pong.nonce, 0xDEADBEEFu);
}

// §3.5(1): bad magic means the stream is desynchronised — close the connection.
void test_malformed_header_bad_magic() {
  auto buf = load_fixture("malformed_header.bin");
  CHECK(!buf.empty());
  if (buf.empty()) return;

  Message m;
  ParseResult r = matrix_studio::parse_message(buf.data(), buf.size(), m);
  CHECK_PARSE(r, ParseResult::kBadMagic);
  CHECK(matrix_studio::is_fatal(r));
  msp::StatusCode code;
  CHECK(!matrix_studio::status_code_for(r, &code));  // nothing to say; just close
  // Nothing beyond the magic byte may have been trusted: no payload view.
  CHECK(m.payload == nullptr);
  CHECK_EQ(m.payload_len, 0u);
}

// §3.5(2): unsupported version — STATUS(ERR_UNSUPPORTED_VERSION) then close.
void test_unsupported_version() {
  auto buf = load_fixture("unsupported_version.bin");
  CHECK(!buf.empty());
  if (buf.empty()) return;

  Message m;
  ParseResult r = matrix_studio::parse_message(buf.data(), buf.size(), m);
  CHECK_PARSE(r, ParseResult::kUnsupportedVersion);
  CHECK_EQ(m.header.version, 99);
  CHECK_EQ(m.header.type, static_cast<uint8_t>(msp::MessageType::kHello));
  CHECK(matrix_studio::is_fatal(r));
  msp::StatusCode code = msp::StatusCode::kOk;
  CHECK(matrix_studio::status_code_for(r, &code));
  CHECK_EQ(static_cast<int>(code), static_cast<int>(msp::StatusCode::kErrUnsupportedVersion));
  // The payload must not have been decoded on a version we do not understand.
  CHECK(m.payload == nullptr);
}

// The truncated-frame fixture: header declares 8206 payload bytes, only 100
// are present. Must be detected, must not over-read, must not close the
// connection (§3.5 "a single bad frame should not drop a healthy session").
void test_truncated_frame() {
  auto buf = load_fixture("truncated_frame.bin");
  CHECK(!buf.empty());
  if (buf.empty()) return;
  CHECK_EQ(buf.size(), msp::kHeaderSizeBytes + 100u);

  Message m;
  ParseResult r = matrix_studio::parse_message(buf.data(), buf.size(), m);
  CHECK_PARSE(r, ParseResult::kTruncatedPayload);
  CHECK_EQ(m.header.type, static_cast<uint8_t>(msp::MessageType::kFrame));
  CHECK_EQ(m.header.length, 8206u);
  CHECK_EQ(m.total_size, 8214u);
  CHECK(!matrix_studio::is_fatal(r));
  msp::StatusCode code = msp::StatusCode::kOk;
  CHECK(matrix_studio::status_code_for(r, &code));
  CHECK_EQ(static_cast<int>(code), static_cast<int>(msp::StatusCode::kErrMalformedPayload));
  // No pixel pointer may be handed out from a payload we never received.
  CHECK(m.frame.pixels == nullptr);
  CHECK_EQ(m.frame.pixel_bytes, 0u);
}

// Every proper prefix of every fixture must be handled without reading out of
// bounds and without ever being reported as a good message. Under ASan this is
// the real out-of-bounds test; the return-value checks are the visible half.
void test_all_prefixes_are_safe() {
  const char* names[] = {"valid_hello.bin", "valid_frame_64x64.bin", "brightness.bin",
                         "heartbeat_ping.bin", "unsupported_version.bin", "truncated_frame.bin"};
  for (const char* name : names) {
    auto full = load_fixture(name);
    if (full.empty()) continue;
    for (size_t n = 0; n < full.size(); ++n) {
      std::vector<uint8_t> prefix(full.begin(), full.begin() + static_cast<long>(n));
      prefix.shrink_to_fit();
      Message m;
      ParseResult r = matrix_studio::parse_message(prefix.empty() ? nullptr : prefix.data(), n, m);
      ++g_checks;
      if (r == ParseResult::kOk) {
        report_failure(__FILE__, __LINE__,
                       std::string("prefix of ") + name + " of length " + std::to_string(n) +
                           " was accepted as a complete message");
      }
    }
  }
}

// §3.5(3) / §5: extension-range types are ignorable, never fatal.
void test_extension_type_is_not_fatal() {
  uint8_t msg[msp::kHeaderSizeBytes + 3] = {msp::kMagic, msp::kProtocolVersion, 0x90, 0x00,
                                            0x03, 0x00, 0x00, 0x00, 0xAA, 0xBB, 0xCC};
  Message m;
  ParseResult r = matrix_studio::parse_message(msg, sizeof(msg), m);
  CHECK_PARSE(r, ParseResult::kExtensionType);
  CHECK(!matrix_studio::is_fatal(r));
  CHECK(matrix_studio::is_extension_type(0x80));
  CHECK(matrix_studio::is_extension_type(0xFE));
  CHECK(!matrix_studio::is_extension_type(0xFF));  // §5: 0xFF is reserved, not an extension
  CHECK(!matrix_studio::is_extension_type(0x7F));
}

// A type outside both the known set and the extension range: STATUS, continue.
void test_unknown_type_is_not_fatal() {
  uint8_t msg[msp::kHeaderSizeBytes] = {msp::kMagic, msp::kProtocolVersion, 0x42, 0x00, 0x00, 0x00, 0x00, 0x00};
  Message m;
  ParseResult r = matrix_studio::parse_message(msg, sizeof(msg), m);
  CHECK_PARSE(r, ParseResult::kUnknownType);
  CHECK(!matrix_studio::is_fatal(r));
  msp::StatusCode code = msp::StatusCode::kOk;
  CHECK(matrix_studio::status_code_for(r, &code));
  CHECK_EQ(static_cast<int>(code), static_cast<int>(msp::StatusCode::kErrUnknownType));

  // 0xFF is reserved (§5) and is treated as an unknown type, not an extension.
  msg[2] = 0xFF;
  CHECK_PARSE(matrix_studio::parse_message(msg, sizeof(msg), m), ParseResult::kUnknownType);
}

// §3.5(4): an oversized declared length is fatal and must be rejected before
// any attempt to address the payload. The buffer here is header-only, so an
// implementation that computed a payload pointer first would fault under ASan.
void test_length_too_large() {
  uint8_t msg[msp::kHeaderSizeBytes] = {msp::kMagic, msp::kProtocolVersion,
                                        static_cast<uint8_t>(msp::MessageType::kFrame), 0x00,
                                        0x00, 0x00, 0x01, 0x00};  // length = 65536 > 65535
  Message m;
  ParseResult r = matrix_studio::parse_message(msg, sizeof(msg), m);
  CHECK_PARSE(r, ParseResult::kLengthTooLarge);
  CHECK(matrix_studio::is_fatal(r));
  CHECK(m.payload == nullptr);

  // Exactly at the limit is a length problem no longer — it becomes truncation.
  uint8_t at_limit[msp::kHeaderSizeBytes] = {msp::kMagic, msp::kProtocolVersion,
                                             static_cast<uint8_t>(msp::MessageType::kFrame), 0x00,
                                             0xFF, 0xFF, 0x00, 0x00};  // length = 65535
  CHECK_PARSE(matrix_studio::parse_message(at_limit, sizeof(at_limit), m), ParseResult::kTruncatedPayload);
}

// §3.5(5): well-framed but wrong payload size for its type — discard the
// message, keep the connection.
void test_malformed_payloads_are_discardable() {
  Message m;

  // BRIGHTNESS with a 2-byte payload.
  uint8_t bad_brightness[msp::kHeaderSizeBytes + 2] = {
      msp::kMagic, msp::kProtocolVersion, static_cast<uint8_t>(msp::MessageType::kBrightness), 0x00,
      0x02, 0x00, 0x00, 0x00, 0x10, 0x20};
  ParseResult r = matrix_studio::parse_message(bad_brightness, sizeof(bad_brightness), m);
  CHECK_PARSE(r, ParseResult::kMalformedPayload);
  CHECK(!matrix_studio::is_fatal(r));
  msp::StatusCode code = msp::StatusCode::kOk;
  CHECK(matrix_studio::status_code_for(r, &code));
  CHECK_EQ(static_cast<int>(code), static_cast<int>(msp::StatusCode::kErrMalformedPayload));

  // FRAME whose declared dimensions disagree with the pixel bytes present:
  // a complete 8206-byte payload that claims to be 32x32 (2048 pixel bytes).
  std::vector<uint8_t> bad_frame(msp::kHeaderSizeBytes + 8206u, 0);
  bad_frame[0] = msp::kMagic;
  bad_frame[1] = msp::kProtocolVersion;
  bad_frame[2] = static_cast<uint8_t>(msp::MessageType::kFrame);
  bad_frame[4] = 0x0E;  // 8206 = 0x200E, little-endian
  bad_frame[5] = 0x20;
  bad_frame[8 + 8] = 32;   // width  = 32
  bad_frame[8 + 10] = 32;  // height = 32
  bad_frame[8 + 12] = 1;   // pixel_format = RGB565
  CHECK_PARSE(matrix_studio::parse_message(bad_frame.data(), bad_frame.size(), m),
              ParseResult::kMalformedPayload);

  // FRAME with an undefined pixel format: stride is unknown, so refuse it
  // rather than guessing.
  bad_frame[8 + 8] = 64;
  bad_frame[8 + 10] = 64;
  bad_frame[8 + 12] = 0x07;
  CHECK_PARSE(matrix_studio::parse_message(bad_frame.data(), bad_frame.size(), m),
              ParseResult::kMalformedPayload);

  // A FRAME shorter than its own 14 fixed fields.
  uint8_t runt_frame[msp::kHeaderSizeBytes + 4] = {
      msp::kMagic, msp::kProtocolVersion, static_cast<uint8_t>(msp::MessageType::kFrame), 0x00,
      0x04, 0x00, 0x00, 0x00, 0, 0, 0, 0};
  CHECK_PARSE(matrix_studio::parse_message(runt_frame, sizeof(runt_frame), m), ParseResult::kMalformedPayload);
}

void test_blank_and_hello_ack_and_status() {
  Message m;

  uint8_t blank_on[msp::kHeaderSizeBytes + 1] = {
      msp::kMagic, msp::kProtocolVersion, static_cast<uint8_t>(msp::MessageType::kBlank), 0x00,
      0x01, 0x00, 0x00, 0x00, 0x01};
  CHECK_PARSE(matrix_studio::parse_message(blank_on, sizeof(blank_on), m), ParseResult::kOk);
  CHECK(m.blank);
  blank_on[msp::kHeaderSizeBytes] = 0x00;
  CHECK_PARSE(matrix_studio::parse_message(blank_on, sizeof(blank_on), m), ParseResult::kOk);
  CHECK(!m.blank);

  // HELLO_ACK: version=1, frame_interval_hint_ms=40, server_time_unix=0x5F5E100
  uint8_t ack[msp::kHeaderSizeBytes + msp::kHelloAckPayloadLen] = {
      msp::kMagic, msp::kProtocolVersion, static_cast<uint8_t>(msp::MessageType::kHelloAck), 0x00,
      0x07, 0x00, 0x00, 0x00,
      0x01, 0x28, 0x00, 0x00, 0xE1, 0xF5, 0x05};
  CHECK_PARSE(matrix_studio::parse_message(ack, sizeof(ack), m), ParseResult::kOk);
  CHECK_EQ(static_cast<int>(m.type), static_cast<int>(msp::MessageType::kHelloAck));
  CHECK_EQ(m.hello_ack.protocol_version, 1);
  CHECK_EQ(m.hello_ack.frame_interval_hint_ms, 40);
  CHECK_EQ(m.hello_ack.server_time_unix, 0x05F5E100u);

  // STATUS round-trip through our own encoder, including the empty-text case.
  uint8_t out[matrix_studio::kMaxEncodedTxBytes];
  size_t n = matrix_studio::encode_status(out, sizeof(out), msp::StatusCode::kErrMalformedPayload, "bad frame");
  CHECK_EQ(n, msp::kHeaderSizeBytes + msp::kStatusFixedFieldsLen + 9u);
  CHECK_PARSE(matrix_studio::parse_message(out, n, m), ParseResult::kOk);
  CHECK_EQ(static_cast<int>(m.type), static_cast<int>(msp::MessageType::kStatus));
  CHECK_EQ(m.status.code, static_cast<uint16_t>(msp::StatusCode::kErrMalformedPayload));
  CHECK_EQ(m.status.text_len, 9u);
  CHECK(m.status.text != nullptr && std::memcmp(m.status.text, "bad frame", 9) == 0);

  n = matrix_studio::encode_status(out, sizeof(out), msp::StatusCode::kOk, nullptr);
  CHECK_EQ(n, msp::kHeaderSizeBytes + msp::kStatusFixedFieldsLen);
  CHECK_PARSE(matrix_studio::parse_message(out, n, m), ParseResult::kOk);
  CHECK_EQ(m.status.text_len, 0u);

  // An over-long STATUS text is truncated, never a failure: the error path
  // must not be able to fail.
  std::string long_text(500, 'x');
  n = matrix_studio::encode_status(out, sizeof(out), msp::StatusCode::kErrInternal, long_text.c_str());
  CHECK_EQ(n, msp::kHeaderSizeBytes + msp::kStatusFixedFieldsLen + matrix_studio::kMaxStatusTextBytes);
}

// §2 says flags must be 0 in v1, but §3.5 does not make a non-zero value fatal
// and §5 reserves the bits for future use — so record, do not reject.
void test_reserved_flags_are_recorded_not_rejected() {
  uint8_t ping[msp::kHeaderSizeBytes + 4] = {
      msp::kMagic, msp::kProtocolVersion, static_cast<uint8_t>(msp::MessageType::kPing), 0x80,
      0x04, 0x00, 0x00, 0x00, 0xEF, 0xBE, 0xAD, 0xDE};
  Message m;
  CHECK_PARSE(matrix_studio::parse_message(ping, sizeof(ping), m), ParseResult::kOk);
  CHECK(matrix_studio::has_reserved_flags(m));
  CHECK_EQ(m.nonce, 0xDEADBEEFu);
}

// §3.3: 1s, 2s, 4s, 8s, 16s, 30s, capped at RECONNECT_MAX_BACKOFF_S.
void test_reconnect_backoff_schedule() {
  matrix_studio::ReconnectBackoff backoff;
  const uint32_t expected[] = {1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000, 30000};
  for (uint32_t want : expected) {
    CHECK_EQ(backoff.peek_delay_ms(), want);
    CHECK_EQ(backoff.next_delay_ms(), want);
  }
  CHECK_EQ(matrix_studio::ReconnectBackoff{}.peek_delay_ms(), 1000u);
  backoff.reset();
  CHECK_EQ(backoff.next_delay_ms(), 1000u);
  CHECK_EQ(30000u, msp::kReconnectMaxBackoffS * 1000u);
}

// Cheap guard against the shared contract header drifting from what the
// firmware assumes about the wire.
void test_contract_header_constants() {
  CHECK_EQ(msp::kMagic, 0xA5);
  CHECK_EQ(msp::kProtocolVersion, 1);
  CHECK_EQ(msp::kHeaderSizeBytes, 8u);
  CHECK_EQ(msp::kMaxPayloadBytes, 65535u);
  CHECK_EQ(sizeof(msp::WireHeader), 8u);
  CHECK_EQ(sizeof(msp::WireHelloFixed), msp::kHelloPayloadLen);
  CHECK_EQ(sizeof(msp::WireHelloAck), msp::kHelloAckPayloadLen);
  CHECK_EQ(sizeof(msp::WireFrameFixed), msp::kFrameFixedFieldsLen);
  CHECK_EQ(msp::kDefaultWsPort, 7887);
  CHECK_EQ(msp::kFrameTimeoutMsDefault, 5000u);
  CHECK(std::strcmp(msp::kDefaultWsPath, "/matrix-studio") == 0);
}

struct TestCase {
  const char* name;
  void (*fn)();
};

const TestCase kTests[] = {
    {"valid_hello", test_valid_hello},
    {"hello_encoder_matches_fixture", test_hello_encoder_matches_fixture},
    {"valid_frame", test_valid_frame},
    {"valid_brightness", test_valid_brightness},
    {"valid_ping_and_pong_reply", test_valid_ping_and_pong_reply},
    {"malformed_header_bad_magic", test_malformed_header_bad_magic},
    {"unsupported_version", test_unsupported_version},
    {"truncated_frame", test_truncated_frame},
    {"all_prefixes_are_safe", test_all_prefixes_are_safe},
    {"extension_type_is_not_fatal", test_extension_type_is_not_fatal},
    {"unknown_type_is_not_fatal", test_unknown_type_is_not_fatal},
    {"length_too_large", test_length_too_large},
    {"malformed_payloads_are_discardable", test_malformed_payloads_are_discardable},
    {"blank_and_hello_ack_and_status", test_blank_and_hello_ack_and_status},
    {"reserved_flags_are_recorded_not_rejected", test_reserved_flags_are_recorded_not_rejected},
    {"reconnect_backoff_schedule", test_reconnect_backoff_schedule},
    {"contract_header_constants", test_contract_header_constants},
};

}  // namespace

int main() {
  std::printf("Matrix Studio ESP32 protocol tests (fixtures: %s)\n", MATRIX_STUDIO_FIXTURE_DIR);
  int failed_tests = 0;
  for (const TestCase& t : kTests) {
    g_current_test = t.name;
    const int before = g_failures;
    t.fn();
    const bool ok = (g_failures == before);
    if (!ok) ++failed_tests;
    std::printf("  %-45s %s\n", t.name, ok ? "ok" : "FAILED");
  }
  const int total = static_cast<int>(sizeof(kTests) / sizeof(kTests[0]));
  std::printf("\n%d/%d tests passed, %d assertions, %d failures\n", total - failed_tests, total, g_checks,
              g_failures);
  return g_failures == 0 ? 0 : 1;
}
