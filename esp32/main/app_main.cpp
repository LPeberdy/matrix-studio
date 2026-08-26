// Matrix Studio — ESP32-S3 HUB75 firmware entry point.
//
// Bring-up order matters here:
//   1. panel first, so a wiring or driver-chip problem is visible before
//      anything can block on the network (docs/hardware.md leaves both
//      unverified);
//   2. render task next, so the panel has an owner;
//   3. Wi-Fi and the Protocol v1 client last, on the other core.
//
// See docs/architecture.md for how this half fits with the Home Assistant
// add-on, and docs/protocol.md for the wire contract.

#include <cstdio>

#include "app_config.h"
#include "board_config.h"
#include "display/diagnostics.h"
#include "display/display.h"
#include "display/frame_queue.h"
#include "display/render_task.h"
#include "esp_app_desc.h"
#include "esp_chip_info.h"
#include "esp_err.h"
#include "esp_flash.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "net/ms_client.h"
#include "net/wifi_station.h"
#include "protocol/ms_protocol.h"
#include "psram_info.h"
#include "serial_console.h"

namespace {

const char* TAG = "ms.main";

// Statics rather than heap: these live for the lifetime of the firmware and
// are shared by tasks that must not race on an allocation failure at runtime.
matrix_studio::Display g_display;
matrix_studio::FrameQueue g_frames;
QueueHandle_t g_commands = nullptr;

void log_banner() {
  esp_chip_info_t chip{};
  esp_chip_info(&chip);

  ESP_LOGI(TAG, "=====================================================");
  ESP_LOGI(TAG, " Matrix Studio firmware %s (Protocol v%u)", matrix_studio::config::kFirmwareVersion,
           matrix_studio_protocol::kProtocolVersion);
  ESP_LOGI(TAG, "=====================================================");
  uint32_t flash_bytes = 0;
  if (esp_flash_get_size(nullptr, &flash_bytes) != ESP_OK) flash_bytes = 0;

  ESP_LOGI(TAG, "chip: %d core(s), silicon revision %d.%d, %luMB %s flash", chip.cores,
           chip.revision / 100, chip.revision % 100,
           static_cast<unsigned long>(flash_bytes / (1024u * 1024u)),
           (chip.features & CHIP_FEATURE_EMB_FLASH) ? "embedded" : "external");
  ESP_LOGI(TAG, "idf: %s", esp_get_idf_version());
  ESP_LOGI(TAG, "reset reason: %d", static_cast<int>(esp_reset_reason()));
}

}  // namespace

extern "C" void app_main(void) {
  log_banner();

  // Never assume PSRAM: detect and log before anything sizes a buffer.
  matrix_studio::psram::probe_and_log();

  // 1. Panel.
  if (g_display.begin() != ESP_OK) {
    ESP_LOGE(TAG, "the panel failed to start. The firmware keeps running so the serial log stays "
                  "usable, but nothing will be displayed. Check main/board_config.h and the "
                  "wiring table in docs/hardware.md.");
  }

  // 2. Frame plumbing and the render task.
  ESP_ERROR_CHECK(g_frames.init(matrix_studio::board::kFrameBytes));

  g_commands = xQueueCreate(8, sizeof(matrix_studio::DisplayCommand));
  if (g_commands == nullptr) {
    ESP_LOGE(TAG, "cannot create the display command queue");
    return;
  }

  ESP_ERROR_CHECK(matrix_studio::render_task_start(&g_display, &g_frames, g_commands));
  ESP_ERROR_CHECK(matrix_studio::serial_console_start());

  // 3. Diagnostic mode, if asked for. Deliberately checked after the render
  // task exists so patterns can be driven the same way at boot and at runtime.
  const bool boot_button = matrix_studio::diag::boot_button_held();
  if (boot_button || matrix_studio::config::kBootIntoDiagnostics) {
    ESP_LOGW(TAG, "entering diagnostic mode (%s)",
             boot_button ? "BOOT button held" : "CONFIG_MATRIX_STUDIO_BOOT_INTO_DIAGNOSTICS");
    ESP_LOGW(TAG, "press 'x' on the serial console to return to normal rendering");
    matrix_studio::render_request_pattern(matrix_studio::diag::Pattern::kCycleAll);
  } else {
    // Something on the panel immediately, rather than a dark panel that looks
    // like a dead board while Wi-Fi associates.
    matrix_studio::render_request_pattern(matrix_studio::diag::Pattern::kOff);
  }

  // 4. Network, on the other core.
  ESP_ERROR_CHECK(matrix_studio::wifi::start());
  ESP_ERROR_CHECK(matrix_studio::ms_client_start(&g_frames, g_commands));

  ESP_LOGI(TAG, "startup complete; render core %d, network core %d", matrix_studio::config::kRenderCore,
           matrix_studio::config::kNetworkCore);

  // Periodic heartbeat in the log so a silent console means "wedged", not
  // "idle". Cheap, and the first thing to look at in a bug report.
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(60000));
    const matrix_studio::ClientStats s = matrix_studio::ms_client_stats();
    ESP_LOGI(TAG, "alive: wifi=%s ws=%s frames=%u drawn=%u rejected=%u reconnects=%u heap=%u",
             matrix_studio::wifi::is_connected() ? "up" : "down", s.socket_connected ? "up" : "down",
             static_cast<unsigned>(s.frames_received), static_cast<unsigned>(matrix_studio::render_frames_drawn()),
             static_cast<unsigned>(s.frames_rejected), static_cast<unsigned>(s.reconnects),
             static_cast<unsigned>(esp_get_free_heap_size()));
  }
}
