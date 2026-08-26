// Hand-off of decoded frames from the network task to the render task.
//
// Three slots, latest-wins. At any moment at most one slot is being written
// (network task), one is published and waiting, and one is being read (render
// task) — so a writer can always find a free slot without blocking, and the
// render task never reads a buffer that is being written into. That is the
// second half of the tear-free guarantee: this queue keeps the network off the
// buffer being drawn, and esp-hub75's double buffering keeps drawing off the
// buffer being scanned out.
//
// Latest-wins rather than FIFO on purpose: if the render task falls behind,
// showing the newest frame is right and queueing stale ones is not
// (docs/protocol.md §3, "the device does not need to detect gaps").

#pragma once

#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

namespace matrix_studio {

class FrameQueue {
 public:
  static constexpr int kSlots = 3;

  // Allocates the slots. Prefers PSRAM when present (docs/hardware.md: the DMA
  // framebuffer stays in internal SRAM, PSRAM absorbs network jitter), and
  // falls back to internal SRAM otherwise.
  esp_err_t init(size_t frame_bytes);

  bool using_psram() const { return using_psram_; }
  size_t frame_bytes() const { return frame_bytes_; }

  // --- network side ---
  // Returns a writable slot of frame_bytes() bytes, or nullptr if the queue is
  // not initialised. Never blocks on the render task.
  uint8_t* begin_write();
  // Publishes the slot returned by the last begin_write(). Any previously
  // published-but-unrendered frame is dropped.
  void commit_write(uint32_t sequence);
  uint32_t dropped_frames() const { return dropped_; }

  // --- render side ---
  // Waits up to timeout_ms for a published frame. Returns nullptr on timeout.
  // Must be paired with release().
  const uint8_t* acquire(uint32_t timeout_ms, uint32_t* out_sequence);
  void release();

 private:
  SemaphoreHandle_t mutex_ = nullptr;
  SemaphoreHandle_t published_ = nullptr;  // binary: "a frame is waiting"
  uint8_t* slots_[kSlots] = {nullptr, nullptr, nullptr};
  size_t frame_bytes_ = 0;
  bool using_psram_ = false;

  int write_idx_ = -1;
  int ready_idx_ = -1;
  int render_idx_ = -1;
  uint32_t ready_sequence_ = 0;
  uint32_t dropped_ = 0;
};

}  // namespace matrix_studio
