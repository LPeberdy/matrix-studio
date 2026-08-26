#include <cassert>
#include <cstdint>
#include <vector>

#include "matrix_studio_protocol.h"
#include "ms_protocol.h"

namespace msp = matrix_studio_protocol;
using matrix_studio::Message;
using matrix_studio::ParseResult;

namespace {

void put_u32(std::vector<uint8_t>& out, uint32_t value) {
  out.push_back(static_cast<uint8_t>(value & 0xff));
  out.push_back(static_cast<uint8_t>((value >> 8) & 0xff));
  out.push_back(static_cast<uint8_t>((value >> 16) & 0xff));
  out.push_back(static_cast<uint8_t>((value >> 24) & 0xff));
}

std::vector<uint8_t> message(msp::MessageType type, const std::vector<uint8_t>& payload) {
  std::vector<uint8_t> out;
  out.reserve(msp::kHeaderSizeBytes + payload.size());
  out.push_back(msp::kMagic);
  out.push_back(msp::kProtocolVersion);
  out.push_back(static_cast<uint8_t>(type));
  out.push_back(0);
  put_u32(out, static_cast<uint32_t>(payload.size()));
  out.insert(out.end(), payload.begin(), payload.end());
  return out;
}

}  // namespace

int main() {
  {
    std::vector<uint8_t> payload;
    put_u32(payload, 1234567);
    const auto bytes = message(msp::MessageType::kOtaBegin, payload);
    Message parsed;
    assert(matrix_studio::parse_message(bytes.data(), bytes.size(), parsed) == ParseResult::kOk);
    assert(parsed.type == msp::MessageType::kOtaBegin);
    assert(parsed.ota_image_size == 1234567u);
  }

  {
    std::vector<uint8_t> payload;
    put_u32(payload, 8192);
    payload.push_back(0x11);
    payload.push_back(0x22);
    payload.push_back(0x33);
    const auto bytes = message(msp::MessageType::kOtaData, payload);
    Message parsed;
    assert(matrix_studio::parse_message(bytes.data(), bytes.size(), parsed) == ParseResult::kOk);
    assert(parsed.type == msp::MessageType::kOtaData);
    assert(parsed.ota_data.offset == 8192u);
    assert(parsed.ota_data.data_len == 3u);
    assert(parsed.ota_data.data[0] == 0x11);
    assert(parsed.ota_data.data[2] == 0x33);
  }

  {
    const auto bytes = message(msp::MessageType::kOtaCommit, {});
    Message parsed;
    assert(matrix_studio::parse_message(bytes.data(), bytes.size(), parsed) == ParseResult::kOk);
    assert(parsed.type == msp::MessageType::kOtaCommit);
  }

  {
    std::vector<uint8_t> payload;
    put_u32(payload, 0);
    const auto bytes = message(msp::MessageType::kOtaBegin, payload);
    Message parsed;
    assert(matrix_studio::parse_message(bytes.data(), bytes.size(), parsed) == ParseResult::kMalformedPayload);
  }

  {
    std::vector<uint8_t> payload;
    put_u32(payload, 0);
    const auto bytes = message(msp::MessageType::kOtaData, payload);
    Message parsed;
    assert(matrix_studio::parse_message(bytes.data(), bytes.size(), parsed) == ParseResult::kMalformedPayload);
  }

  return 0;
}
