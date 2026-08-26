// Exponential reconnect backoff, per docs/protocol.md §3.3:
//
//   1s, 2s, 4s, 8s, 16s, 30s, 30s, ...  (capped at RECONNECT_MAX_BACKOFF_S)
//
// Header-only and platform-free so the firmware and the host-side test suite
// share exactly the same schedule. The firmware must never open-code a delay.

#pragma once

#include <cstdint>

#include "matrix_studio_protocol.h"

namespace matrix_studio {

class ReconnectBackoff {
 public:
  // Returns the delay to wait before the next connection attempt, then
  // advances the schedule. Call reset() after a successful handshake.
  uint32_t next_delay_ms() {
    const uint32_t delay_s = current_delay_s_;
    if (current_delay_s_ < matrix_studio_protocol::kReconnectMaxBackoffS) {
      const uint32_t doubled = current_delay_s_ * 2u;
      current_delay_s_ = (doubled >= matrix_studio_protocol::kReconnectMaxBackoffS)
                             ? matrix_studio_protocol::kReconnectMaxBackoffS
                             : doubled;
    }
    return delay_s * 1000u;
  }

  // Peek at the delay the next call to next_delay_ms() would return.
  uint32_t peek_delay_ms() const { return current_delay_s_ * 1000u; }

  void reset() { current_delay_s_ = 1; }

 private:
  uint32_t current_delay_s_ = 1;
};

}  // namespace matrix_studio
