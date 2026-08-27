// Wi-Fi station with an indefinite reconnect loop.
//
// docs/protocol.md §3.3: "On Wi-Fi loss specifically, the device first
// re-establishes Wi-Fi, then applies the same backoff to the WebSocket
// connection." This module owns the first half of that; the protocol client
// waits on wait_connected() before every connection attempt.
//
// Provisioning is deliberately build-time only for the MVP (menuconfig or
// main/wifi_secrets.h). A captive-portal flow is additive and does not require
// changes here — see esp32/README.md.

#pragma once

#include <cstdint>

#include "esp_err.h"

namespace matrix_studio {
namespace wifi {

// Brings up NVS, netif, the default event loop and the station, and starts
// connecting. Returns as soon as the connect attempt is under way; use
// wait_connected() to block on the result.
esp_err_t start();

// Blocks until the station has an IP address, or the timeout expires.
// timeout_ms == UINT32_MAX waits forever.
bool wait_connected(uint32_t timeout_ms);

bool is_connected();

// True when the station has either build-time credentials or a non-empty
// configuration retained in NVS from the previous firmware. This deliberately
// does not require association: a router outage must not reject a healthy OTA
// image, but a secret-free image with erased NVS must roll back.
bool has_network_config();

// Dotted-quad address of the station, or "0.0.0.0" when not connected.
const char* ip_address();

// Stable 16-byte-safe device identifier derived from the station MAC, for
// HELLO.device_id (docs/protocol.md §4.1). Format: "ms-aabbccddeeff" (14
// chars), which fits the 16-byte field.
const char* device_id();

}  // namespace wifi
}  // namespace matrix_studio
