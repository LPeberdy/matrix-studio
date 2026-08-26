#include "serial_console.h"

#include "app_config.h"
#include "board_config.h"
#include "display/diagnostics.h"
#include "display/render_task.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "net/ms_client.h"
#include "net/wifi_station.h"
#include "psram_info.h"
#include "sdkconfig.h"

#if defined(CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG) || defined(CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG_ENABLED)
#define MS_CONSOLE_USB_SERIAL_JTAG 1
#include "driver/usb_serial_jtag.h"
#else
#include "driver/uart.h"
#endif

namespace matrix_studio {
namespace {

const char* TAG = "ms.console";

void print_info() {
  const ClientStats s = ms_client_stats();
  ESP_LOGI(TAG, "--- Matrix Studio status ---");
  ESP_LOGI(TAG, "panel      : %ux%u, scan %s, driver %s", board::kPanelWidth, board::kPanelHeight,
           board::scan_wiring_name(), board::shift_driver_name());
  ESP_LOGI(TAG, "wifi       : %s (%s)", wifi::is_connected() ? "connected" : "down", wifi::ip_address());
  ESP_LOGI(TAG, "server     : ws://%s:%d%s", config::kServerHost, config::kServerPort, config::kServerPath);
  ESP_LOGI(TAG, "websocket  : %s, handshaked=%s", s.socket_connected ? "open" : "closed",
           s.handshaked ? "yes" : "no");
  ESP_LOGI(TAG, "frames     : %u received, %u drawn, last sequence %u",
           static_cast<unsigned>(s.frames_received), static_cast<unsigned>(render_frames_drawn()),
           static_cast<unsigned>(s.last_sequence));
  ESP_LOGI(TAG, "rejected   : %u messages; reconnects: %u", static_cast<unsigned>(s.frames_rejected),
           static_cast<unsigned>(s.reconnects));
  ESP_LOGI(TAG, "psram      : %u bytes", static_cast<unsigned>(psram::size_bytes()));
  ESP_LOGI(TAG, "diagnostics: %s", diag::pattern_name(render_current_pattern()));
  ESP_LOGI(TAG, "device id  : %s", wifi::device_id());
}

void handle_key(char key) {
  if (key == '?' || key == 'h') {
    diag::print_help();
    return;
  }
  if (key == 'i') {
    print_info();
    return;
  }

  bool handled = false;
  const diag::Pattern pattern = diag::pattern_for_key(key, &handled);
  if (!handled) return;  // ignore stray bytes, newlines, terminal escapes
  render_request_pattern(pattern);
}

void console_task(void*) {
#if defined(MS_CONSOLE_USB_SERIAL_JTAG)
  usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
  if (usb_serial_jtag_driver_install(&cfg) != ESP_OK) {
    ESP_LOGW(TAG, "cannot install the USB Serial/JTAG driver; serial commands disabled");
    vTaskDelete(nullptr);
    return;
  }
#else
  // Field-by-field rather than a designated initializer: uart_config_t puts
  // source_clk inside an anonymous union, which C++ designated initializers
  // cannot portably target.
  uart_config_t uart_cfg = {};
  uart_cfg.baud_rate = CONFIG_ESP_CONSOLE_UART_BAUDRATE;
  uart_cfg.data_bits = UART_DATA_8_BITS;
  uart_cfg.parity = UART_PARITY_DISABLE;
  uart_cfg.stop_bits = UART_STOP_BITS_1;
  uart_cfg.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
  uart_cfg.rx_flow_ctrl_thresh = 0;
  uart_cfg.source_clk = UART_SCLK_DEFAULT;
  if (uart_driver_install(UART_NUM_0, 256, 0, 0, nullptr, 0) != ESP_OK ||
      uart_param_config(UART_NUM_0, &uart_cfg) != ESP_OK) {
    ESP_LOGW(TAG, "cannot install the UART0 driver; serial commands disabled");
    vTaskDelete(nullptr);
    return;
  }
#endif

  diag::print_help();

  uint8_t byte = 0;
  for (;;) {
#if defined(MS_CONSOLE_USB_SERIAL_JTAG)
    const int n = usb_serial_jtag_read_bytes(&byte, 1, pdMS_TO_TICKS(500));
#else
    const int n = uart_read_bytes(UART_NUM_0, &byte, 1, pdMS_TO_TICKS(500));
#endif
    if (n == 1) handle_key(static_cast<char>(byte));
  }
}

}  // namespace

esp_err_t serial_console_start() {
  if (!config::kSerialCommands) return ESP_OK;
  // Lowest priority in the system: a human pressing keys must never compete
  // with the panel or the network.
  const BaseType_t ok = xTaskCreate(&console_task, "ms_console", 3584, nullptr, 2, nullptr);
  return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

}  // namespace matrix_studio
