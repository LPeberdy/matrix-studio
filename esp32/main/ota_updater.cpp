#include "ota_updater.h"

#include "esp_err.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "matrix_studio_protocol.h"

namespace matrix_studio {
namespace ota {
namespace {

const char* TAG = "ms.ota";

esp_ota_handle_t g_handle = 0;
const esp_partition_t* g_partition = nullptr;
uint32_t g_expected_size = 0;
uint32_t g_written = 0;
bool g_active = false;

void clear_state() {
  g_handle = 0;
  g_partition = nullptr;
  g_expected_size = 0;
  g_written = 0;
  g_active = false;
}

}  // namespace

const char* result_name(Result result) {
  switch (result) {
    case Result::kOk: return "ok";
    case Result::kAlreadyActive: return "already_active";
    case Result::kNotActive: return "not_active";
    case Result::kInvalidSize: return "invalid_size";
    case Result::kNoUpdatePartition: return "no_update_partition";
    case Result::kBeginFailed: return "begin_failed";
    case Result::kOffsetMismatch: return "offset_mismatch";
    case Result::kChunkTooLarge: return "chunk_too_large";
    case Result::kWriteFailed: return "write_failed";
    case Result::kIncomplete: return "incomplete";
    case Result::kEndFailed: return "end_failed";
    case Result::kSetBootFailed: return "set_boot_failed";
  }
  return "unknown";
}

Result begin(uint32_t image_size) {
  if (g_active) return Result::kAlreadyActive;
  if (image_size == 0) return Result::kInvalidSize;

  const esp_partition_t* partition = esp_ota_get_next_update_partition(nullptr);
  if (partition == nullptr) return Result::kNoUpdatePartition;
  if (image_size > partition->size) return Result::kInvalidSize;

  esp_ota_handle_t handle = 0;
  const esp_err_t err = esp_ota_begin(partition, image_size, &handle);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "esp_ota_begin failed: %s", esp_err_to_name(err));
    return Result::kBeginFailed;
  }

  g_handle = handle;
  g_partition = partition;
  g_expected_size = image_size;
  g_written = 0;
  g_active = true;
  ESP_LOGI(TAG, "OTA begin: %lu bytes -> %s at 0x%lx", static_cast<unsigned long>(image_size),
           partition->label, static_cast<unsigned long>(partition->address));
  return Result::kOk;
}

Result write(uint32_t offset, const uint8_t* data, size_t len) {
  if (!g_active) return Result::kNotActive;
  if (data == nullptr || len == 0) return Result::kInvalidSize;
  if (len > matrix_studio_protocol::kOtaMaxChunkBytes) return Result::kChunkTooLarge;
  if (offset != g_written) return Result::kOffsetMismatch;
  if (len > static_cast<size_t>(g_expected_size - g_written)) return Result::kInvalidSize;

  const esp_err_t err = esp_ota_write(g_handle, data, len);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "esp_ota_write failed at offset %lu: %s", static_cast<unsigned long>(offset),
             esp_err_to_name(err));
    return Result::kWriteFailed;
  }

  g_written += static_cast<uint32_t>(len);
  if ((g_written % (64u * 1024u)) < len || g_written == g_expected_size) {
    ESP_LOGI(TAG, "OTA progress: %lu/%lu bytes", static_cast<unsigned long>(g_written),
             static_cast<unsigned long>(g_expected_size));
  }
  return Result::kOk;
}

Result commit() {
  if (!g_active) return Result::kNotActive;
  if (g_written != g_expected_size) return Result::kIncomplete;

  const esp_ota_handle_t handle = g_handle;
  const esp_partition_t* partition = g_partition;

  const esp_err_t end_err = esp_ota_end(handle);
  if (end_err != ESP_OK) {
    ESP_LOGE(TAG, "esp_ota_end failed: %s", esp_err_to_name(end_err));
    clear_state();
    return Result::kEndFailed;
  }

  const esp_err_t boot_err = esp_ota_set_boot_partition(partition);
  clear_state();
  if (boot_err != ESP_OK) {
    ESP_LOGE(TAG, "esp_ota_set_boot_partition failed: %s", esp_err_to_name(boot_err));
    return Result::kSetBootFailed;
  }

  ESP_LOGI(TAG, "OTA image validated and selected for next boot");
  return Result::kOk;
}

void abort() {
  if (!g_active) return;
  const esp_err_t err = esp_ota_abort(g_handle);
  if (err != ESP_OK) ESP_LOGW(TAG, "esp_ota_abort failed: %s", esp_err_to_name(err));
  ESP_LOGW(TAG, "aborted incomplete OTA at %lu/%lu bytes", static_cast<unsigned long>(g_written),
           static_cast<unsigned long>(g_expected_size));
  clear_state();
}

bool active() { return g_active; }
uint32_t bytes_written() { return g_written; }
uint32_t expected_size() { return g_expected_size; }

void finish_boot_validation(bool startup_ok) {
  const esp_partition_t* running = esp_ota_get_running_partition();
  if (running == nullptr) return;

  esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
  if (esp_ota_get_state_partition(running, &state) != ESP_OK || state != ESP_OTA_IMG_PENDING_VERIFY) return;

  if (startup_ok) {
    const esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    if (err == ESP_OK) {
      ESP_LOGI(TAG, "OTA boot validation passed; image marked valid");
    } else {
      ESP_LOGE(TAG, "could not mark OTA image valid: %s", esp_err_to_name(err));
    }
    return;
  }

  ESP_LOGE(TAG, "OTA boot validation failed; rolling back to previous image");
  const esp_err_t err = esp_ota_mark_app_invalid_rollback_and_reboot();
  ESP_LOGE(TAG, "rollback request returned unexpectedly: %s", esp_err_to_name(err));
}

}  // namespace ota
}  // namespace matrix_studio
