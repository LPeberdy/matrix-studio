#include "diagnostics.h"

#include "board_config.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace matrix_studio {
namespace diag {
namespace {

const char* TAG = "ms.diag";

// One step per 40ms in the brightness ramp; 0 -> 255 -> 0 in about 10 seconds,
// slow enough to see where the panel starts to misbehave.
constexpr uint32_t kRampStepMs = 40;
constexpr uint32_t kRampSteps = 128;

// How long each pattern is held in the cycle-all mode.
constexpr uint32_t kCycleHoldMs = 2500;

const Pattern kCycleOrder[] = {Pattern::kSolidRed,   Pattern::kSolidGreen, Pattern::kSolidBlue,
                               Pattern::kSolidWhite, Pattern::kQuadrants,  Pattern::kCoordinates};
constexpr size_t kCycleCount = sizeof(kCycleOrder) / sizeof(kCycleOrder[0]);

void render_static(Display& display, Pattern p) {
  switch (p) {
    case Pattern::kSolidRed: display.fill_solid(255, 0, 0); break;
    case Pattern::kSolidGreen: display.fill_solid(0, 255, 0); break;
    case Pattern::kSolidBlue: display.fill_solid(0, 0, 255); break;
    case Pattern::kSolidWhite: display.fill_solid(255, 255, 255); break;
    case Pattern::kQuadrants: display.draw_quadrants(); break;
    case Pattern::kCoordinates: display.draw_coordinate_pattern(); break;
    default: display.fill_solid(0, 0, 0); break;
  }
  display.flip();
}

}  // namespace

const char* pattern_name(Pattern p) {
  switch (p) {
    case Pattern::kOff: return "off";
    case Pattern::kSolidRed: return "solid red";
    case Pattern::kSolidGreen: return "solid green";
    case Pattern::kSolidBlue: return "solid blue";
    case Pattern::kSolidWhite: return "solid white";
    case Pattern::kQuadrants: return "quadrants";
    case Pattern::kCoordinates: return "coordinate ramp";
    case Pattern::kBrightnessRamp: return "brightness ramp";
    case Pattern::kCycleAll: return "cycle all";
  }
  return "?";
}

bool boot_button_held() {
  if (board::kPinBootButton < 0) return false;

  const gpio_num_t pin = static_cast<gpio_num_t>(board::kPinBootButton);
  gpio_config_t cfg = {};
  cfg.pin_bit_mask = 1ULL << board::kPinBootButton;
  cfg.mode = GPIO_MODE_INPUT;
  cfg.pull_up_en = board::kBootButtonActiveLow ? GPIO_PULLUP_ENABLE : GPIO_PULLUP_DISABLE;
  cfg.pull_down_en = board::kBootButtonActiveLow ? GPIO_PULLDOWN_DISABLE : GPIO_PULLDOWN_ENABLE;
  cfg.intr_type = GPIO_INTR_DISABLE;
  if (gpio_config(&cfg) != ESP_OK) return false;

  // Debounce by requiring the button to stay down across several reads, so a
  // floating pin or a strapping-pin glitch cannot put the panel into
  // diagnostic mode on its own.
  for (int i = 0; i < 5; ++i) {
    const int level = gpio_get_level(pin);
    const bool pressed = board::kBootButtonActiveLow ? (level == 0) : (level == 1);
    if (!pressed) return false;
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  ESP_LOGI(TAG, "BOOT button (GPIO%d) held at startup", board::kPinBootButton);
  return true;
}

void print_help() {
  ESP_LOGI(TAG,
           "serial commands: r/g/b/w = solid red/green/blue/white, q = quadrants, "
           "c = coordinate ramp, m = brightness ramp, a = cycle all, "
           "x = leave diagnostics, i = info, ? = this help");
}

Pattern pattern_for_key(char key, bool* handled) {
  *handled = true;
  switch (key) {
    case 'r': return Pattern::kSolidRed;
    case 'g': return Pattern::kSolidGreen;
    case 'b': return Pattern::kSolidBlue;
    case 'w': return Pattern::kSolidWhite;
    case 'q': return Pattern::kQuadrants;
    case 'c': return Pattern::kCoordinates;
    case 'm': return Pattern::kBrightnessRamp;
    case 'a': return Pattern::kCycleAll;
    case 'x': return Pattern::kOff;
    default: *handled = false; return Pattern::kOff;
  }
}

uint32_t render_step(Display& display, Pattern pattern, uint32_t step) {
  switch (pattern) {
    case Pattern::kOff:
      if (step == 0) display.clear_now();
      return 200;

    case Pattern::kBrightnessRamp: {
      // Full white at a triangular brightness sweep. The point at which the
      // panel flickers, browns out or resets is the point at which the power
      // supply is undersized - see the power section of docs/hardware.md.
      const uint32_t phase = step % (kRampSteps * 2);
      const uint32_t level = (phase < kRampSteps) ? phase : (kRampSteps * 2 - phase);
      const uint8_t brightness = static_cast<uint8_t>((level * 255u) / kRampSteps);
      if (step == 0) {
        display.fill_solid(255, 255, 255);
        display.flip();
      }
      display.set_brightness(brightness);
      if ((step % 32u) == 0u) ESP_LOGI(TAG, "brightness ramp at %u/255", brightness);
      return kRampStepMs;
    }

    case Pattern::kCycleAll: {
      const size_t index = static_cast<size_t>(step) % kCycleCount;
      const Pattern current = kCycleOrder[index];
      ESP_LOGI(TAG, "diagnostic pattern: %s", pattern_name(current));
      display.set_brightness(board::kDefaultBrightness);
      render_static(display, current);
      return kCycleHoldMs;
    }

    default:
      if (step == 0) {
        display.set_brightness(board::kDefaultBrightness);
        render_static(display, pattern);
      }
      return 200;
  }
}

}  // namespace diag
}  // namespace matrix_studio
