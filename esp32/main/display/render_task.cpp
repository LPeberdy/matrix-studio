#include "render_task.h"

#include <atomic>

#include "app_config.h"
#include "esp_log.h"
#include "freertos/task.h"
#include "net/ms_client.h"

namespace matrix_studio {
namespace {

const char* TAG = "ms.render";

// How long to block waiting for a frame before looping round to service the
// command queue. Short enough that a BRIGHTNESS or BLANK command applies
// promptly even with no frames arriving.
constexpr uint32_t kFrameWaitMs = 20;

Display* g_display = nullptr;
FrameQueue* g_frames = nullptr;
QueueHandle_t g_commands = nullptr;
QueueHandle_t g_pattern_requests = nullptr;

// Written by the render task on one core, read by the console and the status
// log on the other.
std::atomic<diag::Pattern> g_pattern{diag::Pattern::kOff};
std::atomic<uint32_t> g_frames_drawn{0};

// `diagnostics_active` means a bring-up pattern owns the panel: commands that
// would repaint or re-dim it are swallowed, but connection state is still
// tracked so leaving diagnostic mode shows the right indicator immediately.
void apply_command(const DisplayCommand& cmd, bool diagnostics_active) {
  switch (cmd.kind) {
    case DisplayCommand::Kind::kBrightness:
      if (!diagnostics_active) g_display->set_brightness(cmd.brightness);
      break;
    case DisplayCommand::Kind::kBlank:
      if (!diagnostics_active) g_display->set_blank(cmd.blank);
      break;
    case DisplayCommand::Kind::kConnectionState:
      g_display->set_connection_state(cmd.state);
      break;
    case DisplayCommand::Kind::kNoSignal:
      if (!diagnostics_active) g_display->show_no_signal();
      break;
  }
}

void drain_commands(bool diagnostics_active) {
  DisplayCommand cmd;
  while (xQueueReceive(g_commands, &cmd, 0) == pdTRUE) apply_command(cmd, diagnostics_active);
}

// Returns true if the pattern changed.
bool drain_pattern_requests(diag::Pattern* pattern) {
  bool changed = false;
  diag::Pattern requested;
  while (xQueueReceive(g_pattern_requests, &requested, 0) == pdTRUE) {
    if (requested != *pattern) {
      *pattern = requested;
      changed = true;
    }
  }
  return changed;
}

void render_task(void*) {
  ESP_LOGI(TAG, "render task running on core %d", xPortGetCoreID());

  diag::Pattern pattern = diag::Pattern::kOff;
  uint32_t diag_step = 0;

  for (;;) {
    if (drain_pattern_requests(&pattern)) {
      diag_step = 0;
      g_pattern.store(pattern, std::memory_order_relaxed);
      ESP_LOGI(TAG, "diagnostic pattern: %s", diag::pattern_name(pattern));
      if (pattern == diag::Pattern::kOff) {
        // Hand the panel back to the network path in a known state.
        g_display->set_brightness(board::kDefaultBrightness);
        g_display->clear_now();
        g_display->show_no_signal();
      }
    }

    if (pattern != diag::Pattern::kOff) {
      // Diagnostic mode owns the panel; incoming frames are ignored (but still
      // consumed, so the network side never blocks waiting for a free slot).
      uint32_t seq = 0;
      if (const uint8_t* stale = g_frames->acquire(0, &seq); stale != nullptr) g_frames->release();
      drain_commands(/*diagnostics_active=*/true);
      const uint32_t wait_ms = diag::render_step(*g_display, pattern, diag_step++);
      vTaskDelay(pdMS_TO_TICKS(wait_ms));
      continue;
    }

    drain_commands(/*diagnostics_active=*/false);

    uint32_t sequence = 0;
    const uint8_t* pixels = g_frames->acquire(kFrameWaitMs, &sequence);
    if (pixels == nullptr) continue;

    g_display->draw_frame_rgb565(pixels);
    g_frames->release();
    g_frames_drawn.fetch_add(1, std::memory_order_relaxed);
  }
}

}  // namespace

esp_err_t render_task_start(Display* display, FrameQueue* frames, QueueHandle_t commands) {
  g_display = display;
  g_frames = frames;
  g_commands = commands;

  g_pattern_requests = xQueueCreate(4, sizeof(diag::Pattern));
  if (g_pattern_requests == nullptr) return ESP_ERR_NO_MEM;

  // Priority above the protocol client: the panel must keep being fed even
  // when the network side is busy, and this task is short-lived per iteration.
  const BaseType_t ok =
      xTaskCreatePinnedToCore(&render_task, "ms_render", 4096, nullptr, 6, nullptr, config::kRenderCore);
  return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

void render_request_pattern(diag::Pattern pattern) {
  if (g_pattern_requests == nullptr) return;
  xQueueSend(g_pattern_requests, &pattern, 0);
}

diag::Pattern render_current_pattern() { return g_pattern.load(std::memory_order_relaxed); }

uint32_t render_frames_drawn() { return g_frames_drawn.load(std::memory_order_relaxed); }

}  // namespace matrix_studio
