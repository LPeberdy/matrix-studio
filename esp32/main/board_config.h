// ============================================================================
// Matrix Studio — board configuration.
//
// THIS IS THE ONLY FILE IN THE FIRMWARE THAT NAMES A HUB75 GPIO NUMBER.
//
// If a future controller or panel revision needs a different pin map, geometry,
// scan pattern, or shift driver, change it here rather than scattering hardware
// assumptions through the firmware.
//
// ---------------------------------------------------------------------------
// BOARD IDENTIFICATION — VENDOR-DOCUMENTED, PHYSICAL OUTPUT NOT YET VERIFIED
// ---------------------------------------------------------------------------
// Controller: Hengantech-branded RGB LED Matrix HUB75 Controller Board for
// ESP32-S3 with Audio, matching the Seengreat "RGB Matrix HUB75 S3" design.
// Seengreat documents ESP32-S3-WROOM-1-N16R8 and the HUB75 GPIO map below:
// https://seengreat.com/wiki/214
//
// Panel: Seengreat-compatible P3 64x64, 192x192 mm, 1/32 scan. Photographs of
// the user's physical panel show FM6124EJ LED driver ICs and the expected HUB75
// IN/OUT + 5V/GND connectors. The exact optical result still needs bench
// verification with the firmware diagnostics.
//
// See docs/hardware.md for evidence and remaining bring-up unknowns.
// ============================================================================

#pragma once

#include <cstddef>
#include <cstdint>

#include "hub75_types.h"  // esphome/esp-hub75: Hub75Config and friends

namespace matrix_studio {
namespace board {

// ---------------------------------------------------------------------------
// Panel geometry
// ---------------------------------------------------------------------------
// 64x64 P3 indoor panel, single module, no chaining. These must agree with the
// dimensions advertised in the Protocol v1 HELLO message; the WebSocket client
// rejects FRAMEs that do not match (STATUS(ERR_DIMENSION_MISMATCH)).
constexpr uint16_t kPanelWidth = 64;
constexpr uint16_t kPanelHeight = 64;
constexpr uint16_t kLayoutRows = 1;
constexpr uint16_t kLayoutCols = 1;

// Bytes of RGB565 pixel data in one full frame (docs/protocol.md §4.3).
constexpr size_t kFrameBytes = static_cast<size_t>(kPanelWidth) * kPanelHeight * 2u;

// ---------------------------------------------------------------------------
// HUB75 GPIO pin mapping  (docs/hardware.md "HUB75 GPIO pin mapping")
// ---------------------------------------------------------------------------
// ✅ Documented by Seengreat for RGB Matrix HUB75 S3. The physical Hengantech
// controller has not yet driven the panel, so diagnostics still need to verify
// that this branded board is electrically identical to the documented design.
//
//   Symptom                                  Most likely cause
//   ---------------------------------------  --------------------------------
//   Completely black, no flicker at all      power, OE, or shift driver
//   Image is doubled/interleaved vertically  E line / scan wiring
//   Colours swapped                          R/G/B mapping or panel colour order
//   Top and bottom halves swapped            R1/G1/B1 <-> R2/G2/B2
//   Sheared / torn / noisy                   CLK, LAT, or clock speed
//
constexpr int8_t kPinR1 = 5;
constexpr int8_t kPinG1 = 4;
constexpr int8_t kPinB1 = 6;
constexpr int8_t kPinR2 = 15;
constexpr int8_t kPinG2 = 7;
constexpr int8_t kPinB2 = 17;

// Address lines. The physical panel is documented as 1/32 scan and therefore
// needs all five address lines A-E.
constexpr int8_t kPinA = 8;
constexpr int8_t kPinB = 18;
constexpr int8_t kPinC = 10;
constexpr int8_t kPinD = 9;
constexpr int8_t kPinE = 16;

constexpr int8_t kPinClk = 12;
constexpr int8_t kPinLat = 11;
constexpr int8_t kPinOe = 13;

// ---------------------------------------------------------------------------
// Scan pattern
// ---------------------------------------------------------------------------
// Seengreat documents this 64x64 P3 panel as 1/32 scan. STANDARD_TWO_SCAN is
// the normal esp-hub75 wiring for that geometry; physical diagnostics will
// confirm that the panel does not use a non-standard internal row layout.
constexpr Hub75ScanWiring kScanWiring = Hub75ScanWiring::STANDARD_TWO_SCAN;

// ---------------------------------------------------------------------------
// Shift driver chip
// ---------------------------------------------------------------------------
// ✅ The user's panel photographs show FM6124EJ markings on the LED driver
// ICs, so use esp-hub75's FM6124 driver rather than the earlier FM6126A guess.
// If the panel is unexpectedly black after power, ribbon orientation, and OE
// have been verified, re-read the IC markings before trying alternate drivers.
constexpr Hub75ShiftDriver kShiftDriver = Hub75ShiftDriver::FM6124;

// MBI5124 latches on the positive clock edge and needs this set to true. The
// physical panel is FM6124EJ, so keep the normal phase.
constexpr bool kClockPhaseInverted = false;

// ---------------------------------------------------------------------------
// Timing / quality
// ---------------------------------------------------------------------------
// 20 MHz remains the initial target. Drop to HZ_10M or HZ_8M if physical
// testing shows sparkles, tearing, or marginal signal integrity.
constexpr Hub75ClockSpeed kClockSpeed = Hub75ClockSpeed::HZ_20M;

// Minimum panel refresh rate; the driver sizes its DMA descriptors from this.
constexpr uint16_t kMinRefreshRateHz = 60;

// OE blanking cycles around the latch pulse. Raise to 2 if the panel shows
// faint ghosting of the row above/below.
constexpr uint8_t kLatchBlanking = 1;

// ---------------------------------------------------------------------------
// Brightness
// ---------------------------------------------------------------------------
// Deliberately conservative (~35% of 255) for first physical bring-up. The
// panel is specified at 5V/4A and the controller's VH-4P output is rated to a
// maximum of 5V/4A, so do not start with a full-white / full-brightness load.
// The server can override this at runtime with a BRIGHTNESS message.
constexpr uint8_t kDefaultBrightness = 90;

// Brightness used for the "no signal" idle indicator.
constexpr uint8_t kNoSignalBrightness = 24;

// ---------------------------------------------------------------------------
// Board GPIOs that are not part of the HUB75 ribbon
// ---------------------------------------------------------------------------
// The Seengreat board exposes the ESP32-S3 BOOT button. GPIO0 is the standard
// active-low download/strapping input; holding it during a normal firmware
// reset also requests Matrix Studio diagnostic mode.
constexpr int8_t kPinBootButton = 0;
constexpr bool kBootButtonActiveLow = true;

// ---------------------------------------------------------------------------
// Assembled esp-hub75 configuration
// ---------------------------------------------------------------------------
// Double buffering is mandatory: the network path writes a whole frame and
// then flips, so the panel is never scanning a half-written buffer.
inline Hub75Config make_hub75_config() {
  Hub75Config cfg{};

  cfg.panel_width = kPanelWidth;
  cfg.panel_height = kPanelHeight;
  cfg.scan_wiring = kScanWiring;
  cfg.shift_driver = kShiftDriver;

  cfg.layout_rows = kLayoutRows;
  cfg.layout_cols = kLayoutCols;
  cfg.layout = Hub75PanelLayout::HORIZONTAL;
  cfg.rotation = Hub75Rotation::ROTATE_0;

  cfg.pins.r1 = kPinR1;
  cfg.pins.g1 = kPinG1;
  cfg.pins.b1 = kPinB1;
  cfg.pins.r2 = kPinR2;
  cfg.pins.g2 = kPinG2;
  cfg.pins.b2 = kPinB2;
  cfg.pins.a = kPinA;
  cfg.pins.b = kPinB;
  cfg.pins.c = kPinC;
  cfg.pins.d = kPinD;
  cfg.pins.e = kPinE;
  cfg.pins.lat = kPinLat;
  cfg.pins.oe = kPinOe;
  cfg.pins.clk = kPinClk;

  cfg.output_clock_speed = kClockSpeed;
  cfg.min_refresh_rate = kMinRefreshRateHz;
  cfg.latch_blanking = kLatchBlanking;

  cfg.double_buffer = true;
  cfg.clk_phase_inverted = kClockPhaseInverted;

  cfg.brightness = kDefaultBrightness;

  return cfg;
}

const char* shift_driver_name();
const char* scan_wiring_name();

}  // namespace board
}  // namespace matrix_studio
