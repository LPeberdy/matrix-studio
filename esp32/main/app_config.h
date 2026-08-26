// Resolves build-time configuration into plain constants.
//
// There are two ways to supply Wi-Fi credentials and the server address:
//
//   1. `idf.py menuconfig` -> "Matrix Studio" (values land in sdkconfig)
//   2. main/wifi_secrets.h, copied from main/wifi_secrets.h.example
//
// Option 2 wins when the file exists. It is gitignored; sdkconfig is not, so
// prefer it if there is any chance of the tree being pushed somewhere public.

#pragma once

#include <cstdint>

#include "sdkconfig.h"

#if defined(__has_include)
#if __has_include("wifi_secrets.h")
#include "wifi_secrets.h"
#define MATRIX_STUDIO_HAVE_SECRETS_HEADER 1
#endif
#endif

namespace matrix_studio {
namespace config {

#if defined(MATRIX_STUDIO_SECRET_WIFI_SSID)
constexpr const char* kWifiSsid = MATRIX_STUDIO_SECRET_WIFI_SSID;
#else
constexpr const char* kWifiSsid = CONFIG_MATRIX_STUDIO_WIFI_SSID;
#endif

#if defined(MATRIX_STUDIO_SECRET_WIFI_PASSWORD)
constexpr const char* kWifiPassword = MATRIX_STUDIO_SECRET_WIFI_PASSWORD;
#else
constexpr const char* kWifiPassword = CONFIG_MATRIX_STUDIO_WIFI_PASSWORD;
#endif

#if defined(MATRIX_STUDIO_SECRET_SERVER_HOST)
constexpr const char* kServerHost = MATRIX_STUDIO_SECRET_SERVER_HOST;
#else
constexpr const char* kServerHost = CONFIG_MATRIX_STUDIO_SERVER_HOST;
#endif

#if defined(MATRIX_STUDIO_SECRET_SERVER_PORT)
constexpr int kServerPort = MATRIX_STUDIO_SECRET_SERVER_PORT;
#else
constexpr int kServerPort = CONFIG_MATRIX_STUDIO_SERVER_PORT;
#endif

constexpr const char* kServerPath = CONFIG_MATRIX_STUDIO_SERVER_PATH;
constexpr uint32_t kFrameTimeoutMs = CONFIG_MATRIX_STUDIO_FRAME_TIMEOUT_MS;
constexpr int kWifiRetryLogInterval = CONFIG_MATRIX_STUDIO_WIFI_MAX_RETRY_LOG;

constexpr int kRenderCore = CONFIG_MATRIX_STUDIO_RENDER_CORE;
constexpr int kNetworkCore = CONFIG_MATRIX_STUDIO_NETWORK_CORE;

#if defined(CONFIG_MATRIX_STUDIO_SHOW_CONNECTION_PIXEL)
constexpr bool kShowConnectionPixel = true;
#else
constexpr bool kShowConnectionPixel = false;
#endif

#if defined(CONFIG_MATRIX_STUDIO_BOOT_INTO_DIAGNOSTICS)
constexpr bool kBootIntoDiagnostics = true;
#else
constexpr bool kBootIntoDiagnostics = false;
#endif

#if defined(CONFIG_MATRIX_STUDIO_SERIAL_COMMANDS)
constexpr bool kSerialCommands = true;
#else
constexpr bool kSerialCommands = false;
#endif

// Reported to the server in HELLO.fw_version (max 16 bytes, docs/protocol.md
// §4.1).
constexpr const char* kFirmwareVersion = "0.1.1";

}  // namespace config
}  // namespace matrix_studio
