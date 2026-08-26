// Single-keypress serial console.
//
// Runs on whatever port `idf.py monitor` is already attached to, so triggering
// a diagnostic pattern needs no extra hardware, no button and no reflash —
// which matters because docs/hardware.md leaves the pin mapping and the panel's
// driver chip unverified, and those are exactly what the patterns test.
//
// Press '?' for the key map.

#pragma once

#include "esp_err.h"

namespace matrix_studio {

esp_err_t serial_console_start();

}  // namespace matrix_studio
