#include "display.h"

#include <new>

#include "app_config.h"
#include "esp_log.h"
#include "hub75.h"

namespace matrix_studio {
namespace {

const char* TAG = "ms.display";

struct Rgb {
  uint8_t r, g, b;
};

Rgb connection_colour(ConnectionState s) {
  switch (s) {
    case ConnectionState::kWifiDown: return {40, 0, 0};     // red
    case ConnectionState::kWifiUp: return {40, 20, 0};      // amber
    case ConnectionState::kSocketOpen: return {0, 0, 40};   // blue
    case ConnectionState::kHandshaked: return {0, 40, 0};   // green
    case ConnectionState::kStreaming: return {0, 0, 0};     // no tint
  }
  return {0, 0, 0};
}

}  // namespace

esp_err_t Display::begin() {
  if (driver_ != nullptr) return ESP_OK;

  const Hub75Config cfg = board::make_hub75_config();

  ESP_LOGI(TAG, "HUB75 panel %ux%u, scan=%s, driver=%s, clock=%luHz, double-buffered",
           static_cast<unsigned>(cfg.panel_width), static_cast<unsigned>(cfg.panel_height),
           board::scan_wiring_name(), board::shift_driver_name(),
           static_cast<unsigned long>(cfg.output_clock_speed));
  ESP_LOGI(TAG, "HUB75 pins R1=%d G1=%d B1=%d R2=%d G2=%d B2=%d", cfg.pins.r1, cfg.pins.g1, cfg.pins.b1,
           cfg.pins.r2, cfg.pins.g2, cfg.pins.b2);
  ESP_LOGI(TAG, "HUB75 pins A=%d B=%d C=%d D=%d E=%d CLK=%d LAT=%d OE=%d", cfg.pins.a, cfg.pins.b, cfg.pins.c,
           cfg.pins.d, cfg.pins.e, cfg.pins.clk, cfg.pins.lat, cfg.pins.oe);
  ESP_LOGW(TAG, "Pin mapping and driver chip are UNVERIFIED defaults - see main/board_config.h");

  if (cfg.panel_height > 32 && cfg.pins.e < 0) {
    // Catching this here saves a confusing "image is interleaved" bring-up
    // session; docs/hardware.md calls it the single most common failure.
    ESP_LOGE(TAG, "Panel is %u rows tall but no E address line is configured - a 1/32-scan "
                  "panel needs all of A-E. Check kPinE in main/board_config.h.",
             static_cast<unsigned>(cfg.panel_height));
  }

  driver_ = new (std::nothrow) Hub75Driver(cfg);
  if (driver_ == nullptr) {
    ESP_LOGE(TAG, "out of memory allocating the HUB75 driver");
    return ESP_ERR_NO_MEM;
  }

  if (!driver_->begin()) {
    ESP_LOGE(TAG, "Hub75Driver::begin() failed - check pins, and that no other peripheral "
                  "claims them. See main/board_config.h.");
    delete driver_;
    driver_ = nullptr;
    return ESP_FAIL;
  }

  brightness_ = board::kDefaultBrightness;
  driver_->set_brightness(brightness_);
  driver_->clear();
  driver_->flip_buffer();
  driver_->clear();
  driver_->flip_buffer();

  ESP_LOGI(TAG, "panel running, initial brightness %u/255 (~%u%%)", brightness_,
           static_cast<unsigned>(brightness_ * 100u / 255u));
  return ESP_OK;
}

void Display::draw_frame_rgb565(const uint8_t* pixels) {
  if (driver_ == nullptr || pixels == nullptr) return;

  if (showing_no_signal_) {
    // Leaving the idle state: put back the brightness show_no_signal() dimmed.
    showing_no_signal_ = false;
    if (!blanked_) driver_->set_brightness(brightness_);
  }
  if (blanked_) return;  // BLANK is an explicit server command; honour it over frames

  // esp-hub75 consumes little-endian RGB565 directly, which is exactly what
  // Protocol v1 puts on the wire, so this is a bulk copy with no per-pixel
  // conversion on the hot path.
  driver_->draw_pixels(0, 0, board::kPanelWidth, board::kPanelHeight, pixels, Hub75PixelFormat::RGB565,
                       Hub75ColorOrder::RGB, /*big_endian=*/false);
  stamp_connection_pixel();
  driver_->flip_buffer();
}

void Display::set_brightness(uint8_t brightness) {
  brightness_ = brightness;
  if (driver_ != nullptr && !blanked_ && !showing_no_signal_) driver_->set_brightness(brightness_);
}

void Display::set_blank(bool blank) {
  if (driver_ == nullptr || blank == blanked_) return;
  blanked_ = blank;

  if (blanked_) {
    ESP_LOGI(TAG, "BLANK on");
    driver_->set_brightness(0);
    // Clear both buffers so nothing is retained behind the darkened panel.
    driver_->clear();
    driver_->flip_buffer();
    driver_->clear();
    driver_->flip_buffer();
  } else {
    ESP_LOGI(TAG, "BLANK off, restoring brightness %u", brightness_);
    driver_->set_brightness(brightness_);
  }
}

// Draws the whole idle frame from scratch. It has to be the whole frame rather
// than a touch-up: after flip_buffer() the back buffer holds whatever was
// showing two frames ago, so stamping a single pixel and flipping would
// resurrect stale content.
void Display::render_no_signal_frame() {
  driver_->set_brightness(board::kNoSignalBrightness);
  driver_->clear();
  // Deliberately quiet (docs/protocol.md §3.2): a dim 2x2 dot in the centre,
  // rather than stale pixels or a bright pattern.
  const uint16_t cx = board::kPanelWidth / 2;
  const uint16_t cy = board::kPanelHeight / 2;
  for (uint16_t dy = 0; dy < 2; ++dy) {
    for (uint16_t dx = 0; dx < 2; ++dx) {
      driver_->set_pixel(static_cast<uint16_t>(cx - 1 + dx), static_cast<uint16_t>(cy - 1 + dy), 90, 90, 90);
    }
  }
  stamp_connection_pixel();
  driver_->flip_buffer();
}

void Display::show_no_signal() {
  if (driver_ == nullptr || blanked_ || showing_no_signal_) return;
  showing_no_signal_ = true;
  ESP_LOGI(TAG, "no signal - showing idle indicator");
  render_no_signal_frame();
}

void Display::set_connection_state(ConnectionState state) {
  if (state_ == state) return;
  state_ = state;
  // While idle there is no frame arriving to carry the new tint, so redraw.
  if (driver_ != nullptr && showing_no_signal_ && !blanked_) render_no_signal_frame();
}

void Display::stamp_connection_pixel() {
  if (!config::kShowConnectionPixel || driver_ == nullptr) return;
  if (state_ == ConnectionState::kStreaming) return;
  const Rgb c = connection_colour(state_);
  driver_->set_pixel(0, 0, c.r, c.g, c.b);
}

// --- diagnostics support ---------------------------------------------------
// These bypass the blank/no-signal bookkeeping on purpose: diagnostic mode owns
// the panel outright while it is running.

void Display::fill_solid(uint8_t r, uint8_t g, uint8_t b) {
  if (driver_ == nullptr) return;
  driver_->fill(0, 0, board::kPanelWidth, board::kPanelHeight, r, g, b);
}

void Display::clear_now() {
  if (driver_ == nullptr) return;
  driver_->clear();
  driver_->flip_buffer();
  driver_->clear();
  driver_->flip_buffer();
  showing_no_signal_ = false;
}

void Display::set_pixel(uint16_t x, uint16_t y, uint8_t r, uint8_t g, uint8_t b) {
  if (driver_ == nullptr) return;
  driver_->set_pixel(x, y, r, g, b);
}

void Display::flip() {
  if (driver_ == nullptr) return;
  driver_->flip_buffer();
}

void Display::draw_quadrants() {
  if (driver_ == nullptr) return;
  const uint16_t hw = board::kPanelWidth / 2;
  const uint16_t hh = board::kPanelHeight / 2;
  driver_->fill(0, 0, hw, hh, 180, 0, 0);          // top-left     red
  driver_->fill(hw, 0, hw, hh, 0, 180, 0);         // top-right    green
  driver_->fill(0, hh, hw, hh, 0, 0, 180);         // bottom-left  blue
  driver_->fill(hw, hh, hw, hh, 160, 160, 160);    // bottom-right white
}

void Display::draw_coordinate_pattern() {
  if (driver_ == nullptr) return;
  const uint16_t w = board::kPanelWidth;
  const uint16_t h = board::kPanelHeight;

  // X ramps red, Y ramps green: any address-line fault shows up as bands or
  // repeated blocks rather than a smooth gradient.
  for (uint16_t y = 0; y < h; ++y) {
    for (uint16_t x = 0; x < w; ++x) {
      const uint8_t r = static_cast<uint8_t>((x * 255u) / (w - 1));
      const uint8_t g = static_cast<uint8_t>((y * 255u) / (h - 1));
      driver_->set_pixel(x, y, r, g, 0);
    }
  }

  // 1px white border proves the last row/column are actually addressable.
  for (uint16_t x = 0; x < w; ++x) {
    driver_->set_pixel(x, 0, 255, 255, 255);
    driver_->set_pixel(x, static_cast<uint16_t>(h - 1), 255, 255, 255);
  }
  for (uint16_t y = 0; y < h; ++y) {
    driver_->set_pixel(0, y, 255, 255, 255);
    driver_->set_pixel(static_cast<uint16_t>(w - 1), y, 255, 255, 255);
  }

  // Single blue pixel just inside the top-left corner: unambiguous origin, so
  // a rotated or mirrored panel is obvious at a glance.
  driver_->set_pixel(1, 1, 0, 0, 255);
}

}  // namespace matrix_studio
