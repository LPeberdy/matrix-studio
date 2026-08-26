// Bring-up / diagnostic patterns.
//
// docs/hardware.md leaves three things genuinely unverified: the board
// revision, the panel's shift-driver chip, and the pin mapping. These patterns
// are how a user turns each of those into a five-minute check instead of a
// support dead-end, so they must be reachable without a working network.
//
// Triggers (any one of):
//   - hold the BOOT button (board::kPinBootButton) while powering on / resetting
//   - CONFIG_MATRIX_STUDIO_BOOT_INTO_DIAGNOSTICS=y
//   - press a key on the serial console (CONFIG_MATRIX_STUDIO_SERIAL_COMMANDS)

#pragma once

#include <cstdint>

#include "display.h"

namespace matrix_studio {
namespace diag {

enum class Pattern : uint8_t {
  kOff = 0,
  kSolidRed,
  kSolidGreen,
  kSolidBlue,
  kSolidWhite,
  kQuadrants,
  kCoordinates,
  kBrightnessRamp,
  kCycleAll,
};

const char* pattern_name(Pattern p);

// True if the BOOT button is held down right now. Called once at startup.
bool boot_button_held();

// Prints the serial key map.
void print_help();

// Maps a console keypress to a pattern. Returns kOff for keys that mean
// "leave diagnostic mode"; returns false via `handled` for unknown keys.
Pattern pattern_for_key(char key, bool* handled);

// Renders one step of `pattern`. `step` increments on each call, letting the
// animated patterns (brightness ramp, cycle) advance. Returns the number of
// milliseconds the caller should wait before the next step.
uint32_t render_step(Display& display, Pattern pattern, uint32_t step);

}  // namespace diag
}  // namespace matrix_studio
