#include "frame_queue.h"

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "psram_info.h"

namespace matrix_studio {
namespace {
const char* TAG = "ms.frames";
}

esp_err_t FrameQueue::init(size_t frame_bytes) {
  frame_bytes_ = frame_bytes;

  mutex_ = xSemaphoreCreateMutex();
  published_ = xSemaphoreCreateBinary();
  if (mutex_ == nullptr || published_ == nullptr) return ESP_ERR_NO_MEM;

  // PSRAM is opportunistic, never assumed: docs/hardware.md documents that the
  // two candidate board variants disagree about its size and that cheap clones
  // may have none at all.
  using_psram_ = psram::available();
  const uint32_t caps = using_psram_ ? (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) : MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;

  for (int i = 0; i < kSlots; ++i) {
    slots_[i] = static_cast<uint8_t*>(heap_caps_calloc(1, frame_bytes_, caps));
    if (slots_[i] == nullptr && using_psram_) {
      ESP_LOGW(TAG, "PSRAM allocation failed, falling back to internal SRAM");
      using_psram_ = false;
      slots_[i] = static_cast<uint8_t*>(heap_caps_calloc(1, frame_bytes_, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    }
    if (slots_[i] == nullptr) {
      ESP_LOGE(TAG, "cannot allocate %d frame slots of %u bytes", kSlots, static_cast<unsigned>(frame_bytes_));
      return ESP_ERR_NO_MEM;
    }
  }

  ESP_LOGI(TAG, "%d frame slots of %u bytes each in %s", kSlots, static_cast<unsigned>(frame_bytes_),
           using_psram_ ? "PSRAM" : "internal SRAM");
  return ESP_OK;
}

uint8_t* FrameQueue::begin_write() {
  if (mutex_ == nullptr) return nullptr;
  uint8_t* slot = nullptr;
  xSemaphoreTake(mutex_, portMAX_DELAY);
  for (int i = 0; i < kSlots; ++i) {
    if (i != ready_idx_ && i != render_idx_) {
      write_idx_ = i;
      slot = slots_[i];
      break;
    }
  }
  xSemaphoreGive(mutex_);
  // With three slots and at most one reader plus one writer, a free slot always
  // exists; if this ever fires the invariant above has been broken.
  if (slot == nullptr) ESP_LOGE(TAG, "no free frame slot (ready=%d render=%d)", ready_idx_, render_idx_);
  return slot;
}

void FrameQueue::commit_write(uint32_t sequence) {
  if (mutex_ == nullptr) return;
  bool replaced = false;
  xSemaphoreTake(mutex_, portMAX_DELAY);
  if (write_idx_ >= 0) {
    replaced = (ready_idx_ >= 0);
    ready_idx_ = write_idx_;
    ready_sequence_ = sequence;
    write_idx_ = -1;
    if (replaced) ++dropped_;
  }
  xSemaphoreGive(mutex_);
  xSemaphoreGive(published_);
}

const uint8_t* FrameQueue::acquire(uint32_t timeout_ms, uint32_t* out_sequence) {
  if (mutex_ == nullptr) return nullptr;
  if (xSemaphoreTake(published_, pdMS_TO_TICKS(timeout_ms)) != pdTRUE) return nullptr;

  const uint8_t* buf = nullptr;
  xSemaphoreTake(mutex_, portMAX_DELAY);
  if (ready_idx_ >= 0) {
    render_idx_ = ready_idx_;
    ready_idx_ = -1;
    buf = slots_[render_idx_];
    if (out_sequence != nullptr) *out_sequence = ready_sequence_;
  }
  xSemaphoreGive(mutex_);
  return buf;
}

void FrameQueue::release() {
  if (mutex_ == nullptr) return;
  xSemaphoreTake(mutex_, portMAX_DELAY);
  render_idx_ = -1;
  xSemaphoreGive(mutex_);
}

}  // namespace matrix_studio
