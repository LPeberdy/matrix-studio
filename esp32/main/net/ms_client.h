// Matrix Studio Protocol v1 client (device side).
//
// Owns one persistent outbound WebSocket connection to the Home Assistant
// add-on and implements the whole of docs/protocol.md §3: the HELLO/HELLO_ACK
// handshake, the PING/PONG heartbeat, the malformed-message rules in §3.5, and
// the exponential-backoff reconnect in §3.3.
//
// It never touches the panel. Decoded frames go into a FrameQueue and display
// commands into a queue the render task drains, so all HUB75 work stays on the
// render core.

#pragma once

#include <cstdint>

#include "display/display.h"
#include "display/frame_queue.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

namespace matrix_studio {

// Commands the network side asks the render task to apply. Passing these
// through a queue rather than calling Display directly is what keeps every
// esp-hub75 call on a single task.
struct DisplayCommand {
  enum class Kind : uint8_t {
    kBrightness,       // BRIGHTNESS message (docs/protocol.md §4.4)
    kBlank,            // BLANK message (§4.5)
    kConnectionState,  // status-pixel tint only
    kNoSignal,         // §3.2 fallback: stop showing stale pixels
  };
  Kind kind;
  uint8_t brightness;
  bool blank;
  ConnectionState state;
};

// Starts the client task. `frames` and `commands` must outlive it.
esp_err_t ms_client_start(FrameQueue* frames, QueueHandle_t commands);

// Snapshot of client state, for logging and the serial console.
struct ClientStats {
  bool socket_connected;
  bool handshaked;
  uint32_t frames_received;
  uint32_t frames_rejected;
  uint32_t reconnects;
  uint32_t last_sequence;
};
ClientStats ms_client_stats();

}  // namespace matrix_studio
