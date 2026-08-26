#pragma once

#include <cstddef>
#include <cstdint>

namespace matrix_studio {
namespace ota {

enum class Result : uint8_t {
  kOk = 0,
  kAlreadyActive,
  kNotActive,
  kInvalidSize,
  kNoUpdatePartition,
  kBeginFailed,
  kOffsetMismatch,
  kChunkTooLarge,
  kWriteFailed,
  kIncomplete,
  kEndFailed,
  kSetBootFailed,
};

const char* result_name(Result result);

Result begin(uint32_t image_size);
Result write(uint32_t offset, const uint8_t* data, size_t len);
Result commit();
void abort();

bool active();
uint32_t bytes_written();
uint32_t expected_size();

// Called once the new application has completed Matrix Studio's core startup
// path. When rollback is enabled this confirms a pending OTA image; if
// `startup_ok` is false, a pending image is marked invalid and the device
// immediately boots the previous working image.
void finish_boot_validation(bool startup_ok);

}  // namespace ota
}  // namespace matrix_studio
