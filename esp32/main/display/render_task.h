// The render task: the only task in the firmware that touches the panel.
//
// It is pinned to CONFIG_MATRIX_STUDIO_RENDER_CORE, away from Wi-Fi and the
// protocol client on CONFIG_MATRIX_STUDIO_NETWORK_CORE. docs/hardware.md and
// docs/protocol.md §7 both call this out as the mitigation for the documented
// Wi-Fi/DMA interference risk on ESP32-S3 HUB75 boards, so the separation is a
// requirement rather than a stylistic choice.
//
// It owns a Display, drains the DisplayCommand queue the protocol client posts
// to, pulls finished frames out of the FrameQueue, and hosts diagnostic mode.

#pragma once

#include "diagnostics.h"
#include "display.h"
#include "esp_err.h"
#include "frame_queue.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

namespace matrix_studio {

// `display`, `frames` and `commands` must outlive the task.
esp_err_t render_task_start(Display* display, FrameQueue* frames, QueueHandle_t commands);

// Asks the render task to show a diagnostic pattern (diag::Pattern::kOff
// returns to normal frame rendering). Safe to call from any task.
void render_request_pattern(diag::Pattern pattern);

diag::Pattern render_current_pattern();

// Frames actually drawn to the panel, for the serial `i` command.
uint32_t render_frames_drawn();

}  // namespace matrix_studio
