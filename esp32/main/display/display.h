// The one seam between Matrix Studio and the HUB75 driver.
//
// Every call into esphome/esp-hub75 happens behind this class. docs/hardware.md
// asks for exactly this: if esp-hub75 ever has to be swapped for
// mrcodetastic/ESP32-HUB75-MatrixPanel-DMA, only display.cpp changes.
//
// THREADING: not thread-safe by design. Only the render task (see
// render_task.cpp) may call these methods. Other tasks ask for changes by
// posting commands, which keeps all panel work on one core and off the Wi-Fi
// core — the documented mitigation for the Wi-Fi/DMA interference risk in
// docs/hardware.md.

#pragma once

#include <cstdint>

#include "board_config.h"
#include "esp_err.h"

class Hub75Driver;

namespace matrix_studio {

// What the top-left status pixel is telling you, when
// CONFIG_MATRIX_STUDIO_SHOW_CONNECTION_PIXEL is enabled.
enum class ConnectionState : uint8_t {
  kWifiDown = 0,     // red
  kWifiUp,           // amber — associated, but no WebSocket
  kSocketOpen,       // blue  — WebSocket open, HELLO sent, awaiting HELLO_ACK
  kHandshaked,       // green — session established
  kStreaming,        // no tint — frames are flowing, stay out of the way
};

class Display {
 public:
  // Brings up the panel from board::make_hub75_config() and starts the DMA
  // refresh. Safe to call once, from any task, before the render task starts.
  esp_err_t begin();

  bool started() const { return driver_ != nullptr; }

  // Copies a full-panel RGB565 frame into the back buffer and flips. `pixels`
  // must be board::kFrameBytes bytes of little-endian RGB565 (docs/protocol.md
  // §4.3) — the same byte order esp-hub75 consumes natively, so there is no
  // per-pixel conversion on this path.
  void draw_frame_rgb565(const uint8_t* pixels);

  // Global panel brightness, 0-255 (docs/protocol.md §4.4). Remembered while
  // blanked, and reapplied on unblank.
  void set_brightness(uint8_t brightness);
  uint8_t brightness() const { return brightness_; }

  // BLANK (docs/protocol.md §4.5): an explicit server command to go dark.
  // Distinct from the no-signal fallback below.
  void set_blank(bool blank);
  bool blanked() const { return blanked_; }

  // The §3.2 no-signal fallback: stop showing stale pixel data, show a quiet
  // dim indicator instead. Idempotent.
  void show_no_signal();

  void set_connection_state(ConnectionState state);

  // Diagnostics (diagnostics.cpp drives these; see that file for the patterns).
  void fill_solid(uint8_t r, uint8_t g, uint8_t b);
  void clear_now();
  void draw_quadrants();
  void draw_coordinate_pattern();
  void set_pixel(uint16_t x, uint16_t y, uint8_t r, uint8_t g, uint8_t b);
  void flip();

 private:
  void stamp_connection_pixel();
  void render_no_signal_frame();

  Hub75Driver* driver_ = nullptr;
  uint8_t brightness_ = board::kDefaultBrightness;
  bool blanked_ = false;
  bool showing_no_signal_ = false;
  ConnectionState state_ = ConnectionState::kWifiDown;
};

}  // namespace matrix_studio
