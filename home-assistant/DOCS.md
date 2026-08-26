# Matrix Studio

Streams generative artwork from Home Assistant to an ESP32-S3 driving a 64x64
HUB75 LED panel.

## Setup

1. **Install and start** the add-on.
2. Open the **Configuration** tab and fill in the entities you want scenes to
   react to (all optional — leave blank to skip that input):
   - `entities.lights` — specific lights, or leave empty to track every light
     Home Assistant knows about (`auto_discover_lights`).
   - `entities.indoor_temperature`, `entities.outdoor_temperature`
   - `entities.weather` — a `weather.` entity
   - `entities.occupancy` — a `binary_sensor.` that is `on` when someone's home
3. **Start** the add-on, then open it from the sidebar for the live preview,
   scene picker and brightness slider.
4. Flash the ESP32 firmware and point it at
   `ws://<this-machine>:7887/matrix-studio`.

Nothing shows on the panel until a device connects — that is normal. The
add-on happily runs with no device attached.

## The panel

- **Preview** — the most recent frame, exactly as the LED panel renders it
  (converted to RGB565 and back, so the colours are honest).
- **Status** — connected devices, active scene, measured vs target FPS, render
  time, Home Assistant state freshness, and the endpoint address to give the
  firmware.
- **Controls** — scene picker, brightness (0-255, sent to the device as a
  `BRIGHTNESS` command), **Blank** (turn the panel off without stopping the
  add-on), and **Reload scenes**.
- **Devices** — per-device id, firmware version, address, frames sent and
  round-trip time.

## Your own scenes

Scene files live in the add-on's config folder, visible on the host as
`/addon_configs/<slug>_matrix_studio/scenes/` (via the *File editor*, *Samba*
or *Terminal & SSH* add-ons). Two examples and a full authoring guide
(`README.txt`) are placed there the first time the add-on starts.

The shortest possible scene:

```python
from PIL import Image

def render(t, home, controls):
    level = int((t * 40) % 256)
    return Image.new("RGB", (64, 64), (level, 0, 255 - level))
```

Save it, and it appears in the scene picker within a couple of seconds — no
restart. If it crashes, the add-on logs the traceback, keeps running, and falls
back to another scene; the ingress page shows the error.

To check panel wiring, pick the **testcard** scene: colour bars, per-channel
ramps, a 1 px border and four distinct corner pixels (red = top-left,
green = top-right, blue = bottom-left, white = bottom-right).

## Troubleshooting

| Symptom | Check |
|---|---|
| Device never connects | The add-on must be reachable on port 7887 from the device's VLAN; check the *Network* panel and any firewall between them |
| "No Supervisor token" warning | `homeassistant_api: true` must be set (it is by default); restart the add-on |
| Home Assistant state shown as stale | See the add-on log for the API error; rendering continues with the last known values |
| Scene quarantined | Fix the file, then press **Reload scenes** |
| Low FPS | Lower `target_fps`, or simplify the scene; the status panel shows per-frame render time |
| Panel colours wrong / mirrored | Use the **testcard** scene and check the firmware's `board_config.h` |

## Ports

| Port | Purpose |
|---|---|
| 7887/tcp | Protocol v1 WebSocket endpoint the ESP32 connects to (published; remap the host side in *Network* if needed) |
| 8099/tcp | Ingress UI — internal only, reached through Home Assistant |
