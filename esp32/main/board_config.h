// ============================================================================
// Matrix Studio — board configuration.
//
// THIS IS THE ONLY FILE IN THE FIRMWARE THAT NAMES A HUB75 GPIO NUMBER.
//
// If the physical board turns out not to be the one assumed in
// docs/hardware.md, or the panel needs a different scan/driver configuration,
// editing this one file is the entire fix. Nothing else in esp32/main/ may
// reference a panel pin, the panel dimensions, the scan pattern or the shift
// driver chip directly — always go through the constants below.
//
// ---------------------------------------------------------------------------
// BOARD IDENTIFICATION — UNVERIFIED
// ---------------------------------------------------------------------------
// Assumed board: Waveshare ESP32-S3-RGB-Matrix (or a close OEM/clone of the
// same reference design, e.g. Seengreat "RGB Matrix HUB75 S3").
//
// NONE of the values in this file have been bench-verified against a physical
// board. They are the defaults that Waveshare's documentation and the
// ESP32-HUB75-MatrixPanel-DMA library's ESP32-S3 pin file agree on. Confirm
// the silkscreen part number on your board before trusting them, and use the
// firmware's diagnostic mode (hold the BOOT button at power-on, or set
// CONFIG_MATRIX_STUDIO_BOOT_INTO_DIAGNOSTICS) to verify wiring on the bench.
//
// See docs/hardware.md for the full research write-up and confidence markers.
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
// ❓ UNVERIFIED against the physical board. If the panel shows nothing, shows
// garbage, or shows the right image with wrong colours, this table is the
// first thing to check.
//
//   Symptom                                  Most likely cause
//   ---------------------------------------  --------------------------------
//   Completely black, no flicker at all      shift driver (see below), or OE
//   Image is doubled/interleaved vertically  E line wrong or missing
//   Colours swapped                          R/G/B pins swapped within a half
//   Top and bottom halves swapped            R1/G1/B1 <-> R2/G2/B2 swapped
//   Sheared / torn / noisy                   CLK, LAT, or clock speed
//
constexpr int8_t kPinR1 = 4;
constexpr int8_t kPinG1 = 5;
constexpr int8_t kPinB1 = 6;
constexpr int8_t kPinR2 = 7;
constexpr int8_t kPinG2 = 15;
constexpr int8_t kPinB2 = 16;

// Address lines. A 64x64 1/32-scan panel needs all five, including E. A board
// wired only for A-D is a 1/16-scan (64x32-class) board, not this one; set
// kPinE to -1 only for such a panel.
constexpr int8_t kPinA = 18;
constexpr int8_t kPinB = 8;
constexpr int8_t kPinC = 3;
constexpr int8_t kPinD = 42;
constexpr int8_t kPinE = 9;

constexpr int8_t kPinClk = 41;
constexpr int8_t kPinLat = 40;
constexpr int8_t kPinOe = 2;

// ---------------------------------------------------------------------------
// Scan pattern
// ---------------------------------------------------------------------------
// A 64x64 P3 panel is expected to be 1/32 ("two") scan, which esp-hub75 derives
// automatically from panel_height for STANDARD_TWO_SCAN. Change this only if
// the panel turns out to have non-standard internal shift-register wiring
// (typically visible as the image appearing in interleaved horizontal bands).
constexpr Hub75ScanWiring kScanWiring = Hub75ScanWiring::STANDARD_TWO_SCAN;

// ---------------------------------------------------------------------------
// Shift driver chip  — ❓ UNKNOWN without reading the panel's own ICs
// ---------------------------------------------------------------------------
// FM6126A/ICN2038S are the most common chips on recent 64x64 P3 panels and
// need a specific power-on init sequence; without it the panel is completely
// black (not dim, not garbled), which is easy to misdiagnose as a wiring or
// power fault. That failure mode is why FM6126A is the default here rather
// than GENERIC.
//
// If the panel stays black with every diagnostic pattern, work down this list,
// reflashing between each:
//     Hub75ShiftDriver::FM6126A   (default — also covers ICN2038S)
//     Hub75ShiftDriver::GENERIC   (plain shift registers, no init sequence)
//     Hub75ShiftDriver::FM6124
//     Hub75ShiftDriver::MBI5124   (also set kClockPhaseInverted = true)
//     Hub75ShiftDriver::DP3246
constexpr Hub75ShiftDriver kShiftDriver = Hub75ShiftDriver::FM6126A;

// MBI5124 latches on the positive clock edge and needs this set to true.
constexpr bool kClockPhaseInverted = false;

// ---------------------------------------------------------------------------
// Timing / quality
// ---------------------------------------------------------------------------
// Panel and ribbon-cable quality, not the ESP32, is usually the limiting
// factor. Drop to HZ_10M or HZ_8M if the image is noisy or unstable.
constexpr Hub75ClockSpeed kClockSpeed = Hub75ClockSpeed::HZ_20M;

// Minimum panel refresh rate; the driver sizes its DMA descriptors from this.
constexpr uint16_t kMinRefreshRateHz = 60;

// OE blanking cycles around the latch pulse. Raise to 2 if the panel shows
// faint ghosting of the row above/below.
constexpr uint8_t kLatchBlanking = 1;

// ---------------------------------------------------------------------------
// Brightness
// ---------------------------------------------------------------------------
// Deliberately conservative (~35% of 255). Panel current draw scales roughly
// linearly with brightness, and a 64x64 P3 panel can pull ~4A at 5V at full
// white — see the power section of docs/hardware.md before raising this.
// The server can override this at runtime with a BRIGHTNESS message.
constexpr uint8_t kDefaultBrightness = 90;

// Brightness used for the "no signal" idle indicator, so a disconnected panel
// is visibly alive without being a light source in a dark room.
constexpr uint8_t kNoSignalBrightness = 24;

// ---------------------------------------------------------------------------
// Board GPIOs that are not part of the HUB75 ribbon
// ---------------------------------------------------------------------------
// The BOOT button, held at power-on, enters diagnostic mode. GPIO0 is the
// standard ESP32-S3 BOOT/strapping pin and is active-low with a pull-up.
// Set kPinBootButton to -1 if this board has no usable button.
constexpr int8_t kPinBootButton = 0;
constexpr bool kBootButtonActiveLow = true;

// ---------------------------------------------------------------------------
// Assembled esp-hub75 configuration
// ---------------------------------------------------------------------------
// The single point where the constants above become driver configuration.
// Double buffering is mandatory for this project, not optional: the network
// path writes a whole frame and then flips, so the panel is never scanning a
// half-written buffer (docs/architecture.md, "Data flow").
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

// Human-readable names for the two settings a user is most likely to change
// during bring-up, so the boot log states what was actually compiled in.
const char* shift_driver_name();
const char* scan_wiring_name();

}  // namespace board
}  // namespace matrix_studio
