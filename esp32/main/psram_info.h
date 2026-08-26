// Runtime PSRAM detection.
//
// docs/hardware.md is explicit that PSRAM size and presence must never be
// hardcoded: the two candidate board variants disagree (8MB octal vs 16MB) and
// cheap generic ESP32-S3 boards often have 2MB quad or none at all. Every
// PSRAM decision in this firmware goes through here.
//
// What PSRAM is and is not used for:
//   - NOT the HUB75 DMA framebuffer. At 64x64 that needs only tens of KB and
//     belongs in internal SRAM, on the timing-critical DMA path. esp-hub75
//     allocates it that way itself; nothing here overrides that.
//   - YES the incoming-frame staging buffers and the WebSocket reassembly
//     buffer, where extra headroom absorbs network jitter and costs nothing.

#pragma once

#include <cstddef>

namespace matrix_studio {
namespace psram {

// Detects PSRAM once and logs what was actually found. Call early in app_main.
void probe_and_log();

// True only if PSRAM was detected at runtime AND this build has PSRAM support
// compiled in.
bool available();

// Detected size in bytes, 0 if none.
size_t size_bytes();

}  // namespace psram
}  // namespace matrix_studio
