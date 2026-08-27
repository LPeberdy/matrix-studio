// Matrix Studio — ESP32-S3 HUB75 firmware entry point.
//
// Bring-up order matters here:
//   1. panel first, so a wiring or driver-chip problem is visible before
//      anything can block on the network;
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
#include "ota_updater.h"
#include "protocol/ms_protocol.h"
#include "psram_info.h"
#include "serial_console.h"

namespace {

const char* TAG = "ms.main";

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

  matrix_studio::psram::probe_and_log();

  const bool display_ok = g_display.begin() == ESP_OK;
  if (!display_ok) {
    ESP_LOGE(TAG, "the panel failed to start. The firmware keeps running so the serial log stays "
                  "usable on a factory/USB boot, but a just-installed OTA image will not be "
                  "accepted. Check main/board_config.h and docs/hardware.md.");
  }

  ESP_ERROR_CHECK(g_frames.init(matrix_studio::board::kFrameBytes));

  g_commands = xQueueCreate(8, sizeof(matrix_studio::DisplayCommand));
  if (g_commands == nullptr) {
    ESP_LOGE(TAG, "cannot create the display command queue");
    matrix_studio::ota::finish_boot_validation(false);
    return;
  }

  esp_err_t startup_err = matrix_studio::render_task_start(&g_display, &g_frames, g_commands);
  if (startup_err != ESP_OK) {
    ESP_LOGE(TAG, "render task failed to start: %s", esp_err_to_name(startup_err));
    matrix_studio::ota::finish_boot_validation(false);
    return;
  }

  startup_err = matrix_studio::serial_console_start();
  if (startup_err != ESP_OK) {
    ESP_LOGE(TAG, "serial console failed to start: %s", esp_err_to_name(startup_err));
    matrix_studio::ota::finish_boot_validation(false);
    return;
  }

  const bool boot_button = matrix_studio::diag::boot_button_held();
  if (boot_button || matrix_studio::config::kBootIntoDiagnostics) {
    ESP_LOGW(TAG, "entering diagnostic mode (%s)",
             boot_button ? "BOOT button held" : "CONFIG_MATRIX_STUDIO_BOOT_INTO_DIAGNOSTICS");
    ESP_LOGW(TAG, "press 'x' on the serial console to return to normal rendering");
    matrix_studio::render_request_pattern(matrix_studio::diag::Pattern::kCycleAll);
  } else {
    matrix_studio::render_request_pattern(matrix_studio::diag::Pattern::kOff);
  }

  startup_err = matrix_studio::wifi::start();
  if (startup_err != ESP_OK) {
    ESP_LOGE(TAG, "Wi-Fi subsystem failed to start: %s", esp_err_to_name(startup_err));
    matrix_studio::ota::finish_boot_validation(false);
    return;
  }

  startup_err = matrix_studio::ms_client_start(&g_frames, g_commands);
  if (startup_err != ESP_OK) {
    ESP_LOGE(TAG, "Matrix Studio client failed to start: %s", esp_err_to_name(startup_err));
    matrix_studio::ota::finish_boot_validation(false);
    return;
  }

  // An OTA image is accepted only if every core local subsystem initialized,
  // including HUB75, and the image has usable network configuration. Actual
  // association is deliberately excluded: a router outage must not roll back
  // healthy firmware. The configuration check prevents a secret-free release
  // image with erased NVS from stranding a panel that could roll back to an
  // older image containing build-time credentials.
  matrix_studio::ota::finish_boot_validation(display_ok && matrix_studio::wifi::has_network_config());

  ESP_LOGI(TAG, "startup complete; render core %d, network core %d", matrix_studio::config::kRenderCore,
           matrix_studio::config::kNetworkCore);

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
