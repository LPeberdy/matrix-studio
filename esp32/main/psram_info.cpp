#include "psram_info.h"

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "sdkconfig.h"

#if defined(CONFIG_SPIRAM)
#include "esp_psram.h"
#endif

namespace matrix_studio {
namespace psram {
namespace {

const char* TAG = "ms.psram";
bool g_probed = false;
size_t g_size = 0;

}  // namespace

void probe_and_log() {
  g_probed = true;

#if defined(CONFIG_SPIRAM)
  if (esp_psram_is_initialized()) {
    g_size = esp_psram_get_size();
    ESP_LOGI(TAG, "PSRAM detected: %u bytes (%.1f MB), %u bytes free",
             static_cast<unsigned>(g_size), static_cast<double>(g_size) / (1024.0 * 1024.0),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
  } else {
    g_size = 0;
    ESP_LOGW(TAG, "PSRAM support is compiled in but no PSRAM was initialised at boot - "
                  "continuing with internal SRAM only");
  }
#else
  g_size = 0;
  ESP_LOGI(TAG, "PSRAM support not compiled in - using internal SRAM only");
#endif

  ESP_LOGI(TAG, "internal SRAM free: %u bytes (largest block %u)",
           static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
           static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL)));
  ESP_LOGI(TAG, "DMA-capable free: %u bytes (largest block %u) - the HUB75 framebuffer comes from here",
           static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_DMA)),
           static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_DMA)));
}

bool available() {
  if (!g_probed) probe_and_log();
  return g_size > 0;
}

size_t size_bytes() {
  if (!g_probed) probe_and_log();
  return g_size;
}

}  // namespace psram
}  // namespace matrix_studio
