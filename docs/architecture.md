# Architecture

Matrix Studio is an ambient generative-art surface, not a dashboard. Home
Assistant renders artwork and provides context; the ESP32 reliably turns
frames into light.

```
┌─────────────────────────────┐        WebSocket (binary, Protocol v1)       ┌──────────────────────────────┐
│  Raspberry Pi 5 / HAOS       │  ──────────────────────────────────────────▶ │  ESP32-S3 HUB75 controller   │
│                              │  ◀────────────────────────────────────────── │                               │
│  ┌────────────────────────┐  │                                              │  ┌─────────────────────────┐ │
│  │ Matrix Studio add-on    │  │                                              │  │ Wi-Fi + WS client        │ │
│  │  - Scene engine (Python)│  │                                              │  │ Protocol v1 parser       │ │
│  │  - HA state adapter     │◀─┼── HA Core WebSocket API (supervisor token)   │  │ Double-buffered          │ │
│  │  - RGB565 encoder       │  │                                              │  │ HUB75 DMA driver         │ │
│  │  - WS server :7887      │  │                                              │  │ Brightness / blank /     │ │
│  │  - Ingress UI (preview) │  │                                              │  │ no-signal fallback       │ │
│  └────────────────────────┘  │                                              │  └───────────┬─────────────┘ │
└─────────────────────────────┘                                              │              │ HUB75 ribbon   │
                                                                               │              ▼               │
                                                                               │   64x64 P3 RGB LED panel      │
                                                                               │   (external 5V supply)        │
                                                                               └──────────────────────────────┘
```

## Components

### Home Assistant add-on (`home-assistant/`)

Runs as a Supervisor add-on (Docker container) on the user's Home Assistant
OS install. Responsibilities:

- **Scene engine**: loads user-authored scene files, calls each active
  scene's `render(time_seconds, home_state, controls) -> Image` at a fixed
  target FPS, independent of network/render hiccups in any one scene.
- **HA state adapter**: polls/subscribes to Home Assistant state via the
  Supervisor-provided HA Core API and exposes a small cached snapshot to
  scenes, decoupled from render cadence so a slow state fetch never stalls
  rendering.
- **RGB565 encoder + WebSocket server**: converts each rendered frame to
  RGB565 and serves it to connected devices per `docs/protocol.md`.
- **Ingress UI**: a minimal status/control page (connection state, active
  scene, brightness, FPS, scene picker, live preview) served through Home
  Assistant's ingress proxy — never delays the renderer itself.
- **Emulator/preview mode**: scenes can be previewed as images/browser
  frames without any physical ESP32 attached.

A broken scene is caught and logged; the engine falls back to a safe default
(blank or a built-in scene) rather than crashing the whole add-on.

### ESP32-S3 firmware (`esp32/`)

A "reliable display appliance": joins Wi-Fi, opens the Protocol v1 WebSocket
connection to the Home Assistant add-on, and drives the HUB75 panel from
whatever frame it most recently received. Network I/O and HUB75 refresh
timing are kept on separate concerns (a double-buffered framebuffer) so a
slow network doesn't tear the display, and a busy display refresh doesn't
stall the network stack. All board-specific pin/scan configuration is
isolated in one file (`esp32/src/board_config.h`) — see `docs/hardware.md`.

### Protocol (`protocol/`)

The frozen wire contract, plus a reference Python codec, a C++ constants
header, and golden binary fixtures both sides test against. See
`docs/protocol.md`. This directory has no HA- or ESP32-specific logic; it is
the shared boundary both implementations depend on and neither owns.

## Data flow

1. A scene's `render()` produces a 64x64 image once per engine tick.
2. The engine converts it to RGB565 and hands it to the WebSocket server.
3. The server pushes a `FRAME` message to every connected device.
4. The device decodes the header, validates dimensions/format, copies pixels
   into its back framebuffer, and swaps buffers on the HUB75 driver's own
   refresh cadence (not synchronized to network arrival — the panel keeps
   refreshing at its own rate regardless of frame timing jitter).

## Extensibility (not built in the MVP, but not blocked by it)

- Multiple ESP32 devices: the server already accepts arbitrary connections;
  a future version would key scenes/brightness per-device instead of
  broadcasting identically to all.
- ESP32-native scenes / SD assets / audio-reactive modes: the device's
  network and rendering paths are already separate, so a future firmware
  version could substitute a local scene source for the network path without
  protocol changes.
- OTA updates, mDNS discovery, captive portal provisioning: additive, do not
  require protocol or architecture changes.

## Why not X

- **No MQTT broker / Redis / database**: a single persistent WebSocket
  connection is sufficient for one (or a handful of) always-on display
  devices: no fan-out, retained-message, or persistence needs justify the
  operational cost of a broker.
- **No custom Lovelace framework or React app**: the ingress UI is a small
  static/served page; a full frontend framework would be scope creep for a
  status-and-picker page.
- **UDP was considered and rejected for v1**: a 64x64 panel expects fully
  intact frames; on a well-behaved home LAN, a reliable connection (TCP via
  WebSocket) avoids having to reinvent retransmit/ordering logic for a
  modest (8KB/frame @ ~20-30 FPS ⇒ ~160-240 KB/s) bandwidth budget.
