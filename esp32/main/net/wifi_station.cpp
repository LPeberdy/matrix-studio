#include "wifi_station.h"

#include <cstdio>
#include <cstring>

#include "app_config.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs_flash.h"

namespace matrix_studio {
namespace wifi {
namespace {

const char* TAG = "ms.wifi";

constexpr EventBits_t kConnectedBit = BIT0;

EventGroupHandle_t g_events = nullptr;
char g_ip[16] = "0.0.0.0";
char g_device_id[17] = {};
int g_retries = 0;
bool g_started = false;
bool g_using_stored_credentials = false;

void on_wifi_event(void*, esp_event_base_t base, int32_t id, void* data) {
  if (base != WIFI_EVENT) return;

  switch (id) {
    case WIFI_EVENT_STA_START:
      if (g_using_stored_credentials) {
        ESP_LOGI(TAG, "station started, connecting with stored Wi-Fi credentials");
      } else {
        ESP_LOGI(TAG, "station started, connecting to \"%s\"", config::kWifiSsid);
      }
      esp_wifi_connect();
      break;

    case WIFI_EVENT_STA_CONNECTED:
      ESP_LOGI(TAG, "associated, waiting for IP");
      break;

    case WIFI_EVENT_STA_DISCONNECTED: {
      const auto* d = static_cast<wifi_event_sta_disconnected_t*>(data);
      xEventGroupClearBits(g_events, kConnectedBit);
      std::snprintf(g_ip, sizeof(g_ip), "0.0.0.0");
      ++g_retries;
      // Retrying forever is the correct behaviour for an appliance: a router
      // reboot must not need a power cycle here. The log is throttled so a
      // permanently-wrong SSID does not flood the console.
      if (g_retries == 1 || (g_retries % config::kWifiRetryLogInterval) == 0) {
        ESP_LOGW(TAG, "disconnected (reason %d), retrying (attempt %d)", d ? d->reason : -1, g_retries);
      }
      vTaskDelay(pdMS_TO_TICKS(1000));
      esp_wifi_connect();
      break;
    }

    default:
      break;
  }
}

void on_ip_event(void*, esp_event_base_t base, int32_t id, void* data) {
  if (base != IP_EVENT || id != IP_EVENT_STA_GOT_IP) return;
  const auto* event = static_cast<ip_event_got_ip_t*>(data);
  std::snprintf(g_ip, sizeof(g_ip), IPSTR, IP2STR(&event->ip_info.ip));
  g_retries = 0;
  ESP_LOGI(TAG, "got IP %s", g_ip);
  xEventGroupSetBits(g_events, kConnectedBit);
}

void compute_device_id() {
  uint8_t mac[6] = {};
  if (esp_read_mac(mac, ESP_MAC_WIFI_STA) != ESP_OK) {
    std::snprintf(g_device_id, sizeof(g_device_id), "ms-unknown");
    return;
  }
  // 14 characters, comfortably inside HELLO's 16-byte device_id field.
  std::snprintf(g_device_id, sizeof(g_device_id), "ms-%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3],
                mac[4], mac[5]);
}

}  // namespace

esp_err_t start() {
  if (g_started) return ESP_OK;

  esp_err_t err = nvs_flash_init();
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_LOGW(TAG, "NVS needs erasing, doing that now");
    ESP_ERROR_CHECK(nvs_flash_erase());
    err = nvs_flash_init();
  }
  if (err != ESP_OK) return err;

  g_events = xEventGroupCreate();
  if (g_events == nullptr) return ESP_ERR_NO_MEM;

  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());
  esp_netif_create_default_wifi_sta();

  wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&init_cfg));

  ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &on_wifi_event, nullptr,
                                                      nullptr));
  ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &on_ip_event, nullptr,
                                                      nullptr));

  compute_device_id();
  ESP_LOGI(TAG, "device id %s", g_device_id);

  wifi_config_t sta_cfg = {};
  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
  if (config::kWifiSsid[0] != '\0') {
    std::strncpy(reinterpret_cast<char*>(sta_cfg.sta.ssid), config::kWifiSsid, sizeof(sta_cfg.sta.ssid) - 1);
    std::strncpy(reinterpret_cast<char*>(sta_cfg.sta.password), config::kWifiPassword,
                 sizeof(sta_cfg.sta.password) - 1);
    sta_cfg.sta.threshold.authmode = (config::kWifiPassword[0] == '\0') ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_cfg));
  } else {
    // OTA release images deliberately carry no network secrets. ESP-IDF's
    // default WIFI_STORAGE_FLASH retains the configuration written by the
    // previous firmware, so leave it intact and reuse it. A first wired flash
    // still needs menuconfig or wifi_secrets.h as documented.
    ESP_ERROR_CHECK(esp_wifi_get_config(WIFI_IF_STA, &sta_cfg));
    g_using_stored_credentials = sta_cfg.sta.ssid[0] != '\0';
    if (!g_using_stored_credentials) {
      ESP_LOGE(TAG, "no compiled or stored Wi-Fi SSID. Set it with `idf.py menuconfig` under "
                    "\"Matrix Studio\" -> Wi-Fi, or copy main/wifi_secrets.h.example to "
                    "main/wifi_secrets.h. The panel will keep running diagnostics but cannot connect.");
    }
  }

  // Power save off. docs/hardware.md calls this out as one of the specific
  // knobs ESP-IDF gives us to mitigate the Wi-Fi/DMA interference risk: modem
  // sleep adds latency spikes to an already-contended radio, and this device is
  // mains-powered, so there is nothing to save power for.
  ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

  ESP_ERROR_CHECK(esp_wifi_start());
  g_started = true;
  return ESP_OK;
}

bool wait_connected(uint32_t timeout_ms) {
  if (g_events == nullptr) return false;
  const TickType_t ticks = (timeout_ms == UINT32_MAX) ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms);
  const EventBits_t bits = xEventGroupWaitBits(g_events, kConnectedBit, pdFALSE, pdTRUE, ticks);
  return (bits & kConnectedBit) != 0;
}

bool is_connected() {
  if (g_events == nullptr) return false;
  return (xEventGroupGetBits(g_events) & kConnectedBit) != 0;
}

bool has_network_config() { return config::kWifiSsid[0] != '\0' || g_using_stored_credentials; }

const char* ip_address() { return g_ip; }

const char* device_id() {
  if (g_device_id[0] == '\0') compute_device_id();
  return g_device_id;
}

}  // namespace wifi
}  // namespace matrix_studio
