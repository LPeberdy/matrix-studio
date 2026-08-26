// Matrix Studio Protocol v1 — OTA extension.
//
// This is a backwards-compatible extension using the 0x80..0xFE range
// reserved by docs/protocol.md §5. It deliberately does not modify the frozen
// core Protocol v1 message layouts.

#pragma once

#include <cstddef>
#include <cstdint>

namespace matrix_studio_ota {

enum class MessageType : uint8_t {
  kBegin = 0x80,
  kData = 0x81,
  kCommit = 0x82,
};

constexpr size_t kBeginPayloadLen = 4;       // image_size: u32 LE
constexpr size_t kDataOffsetLen = 4;         // offset: u32 LE, then bytes
constexpr size_t kMaxChunkBytes = 4096;
constexpr size_t kCommitPayloadLen = 0;

constexpr bool is_ota_type(uint8_t type) {
  return type == static_cast<uint8_t>(MessageType::kBegin) ||
         type == static_cast<uint8_t>(MessageType::kData) ||
         type == static_cast<uint8_t>(MessageType::kCommit);
}

}  // namespace matrix_studio_ota
