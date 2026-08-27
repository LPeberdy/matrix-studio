# Matrix Studio — Home Assistant add-on

The Home Assistant half of Matrix Studio. It renders generative scenes at a
fixed cadence, converts each frame to RGB565, and streams it to one or more
ESP32-S3 HUB75 controllers over a WebSocket using
[Protocol v1](../docs/protocol.md).

This is a **Supervisor add-on** (a container Supervisor builds and runs), not a
custom integration and not a HACS component.

```
options ──▶ SceneEngine ──▶ FrameBus ──▶ DeviceServer :7887  ──▶ ESP32
              ▲                  │        (Protocol v1 WebSocket)
              │                  └──────▶ IngressWeb :8099  ──▶ browser preview
        HaStateAdapter
    (http://supervisor/core/api)
```

## Layout

| Path | What it is |
|---|---|
| `config.yaml` | Add-on manifest: options, schema, ports, ingress |
| `build.yaml` | Per-architecture base images |
| `Dockerfile` / `run.sh` | Image build and entrypoint |
| `matrix_studio/app.py` | Composition root; wires everything together |
| `matrix_studio/engine.py` | Fixed-cadence render loop + broken-scene fallback |
| `matrix_studio/server.py` | Protocol v1 WebSocket server, including OTA sender |
| `matrix_studio/ha_state.py` | Home Assistant state adapter (Supervisor proxy) |
| `matrix_studio/loader.py` | Scene discovery, loading, hot reload |
| `matrix_studio/framebuffer.py` | RGB565 conversion + latest-frame fan-out |
| `matrix_studio/web.py` + `static/` | Ingress UI, preview and firmware upload endpoints |
| `matrix_studio/preview.py` | Emulator / preview CLI (no hardware needed) |
| `matrix_studio/scenes/` | Built-in scenes |
| `matrix_studio/vendor/` | Verbatim copy of the frozen protocol codec |
| `example_scenes/` | Seeded into the user's scenes directory on first run |
| `tests/` | pytest suite |

### Why the protocol codec is vendored

Supervisor builds an add-on with **the add-on directory as the Docker build
context**, so `../protocol/matrix_studio_protocol.py` does not exist at image
build time. `matrix_studio/vendor/matrix_studio_protocol.py` is a byte-for-byte
copy, and `tests/test_protocol_contract.py` fails if it ever drifts. Re-sync
after any (rare) protocol change:

```sh
python3 home-assistant/tools/sync_protocol.py          # copy
python3 home-assistant/tools/sync_protocol.py --check  # verify only
```

No module outside `vendor/` is allowed to know the wire format — a test
enforces that too.

## Installing on Home Assistant OS

Supervisor can only install add-ons from a directory it can see. Two options:

### A. Local add-on (fastest, no publishing)

1. Copy this directory onto the HA host as `/addons/matrix_studio` — i.e. the
   share exposed by the *Samba share* add-on as `\\<host>\addons\`, or via the
   *Terminal & SSH* add-on / `ha` CLI. The folder must contain `config.yaml`
   at its top level.
2. **Settings → Add-ons → Add-on store → ⋮ → Check for updates.**
3. "Matrix Studio" appears under **Local add-ons**. Open it → **Install**
   (Supervisor builds the image from `Dockerfile`; a few minutes the first time).
4. **Configuration** tab: set your entity ids (see below), then **Start**.
5. Enable **Show in sidebar** to reach the ingress UI.

### B. Add-on repository

Push a repo containing a `repository.yaml` plus this directory, then
**Add-on store → ⋮ → Repositories → Add** the URL. Supervisor still builds
locally because `config.yaml` declares no prebuilt `image:`.

### Configuration

| Option | Default | Notes |
|---|---|---|
| `active_scene` | `plasma` | Scene to start on; also settable from the UI |
| `target_fps` | `24` | 1-60. ~24 uses ≈7 ms/frame on a Pi 5 |
| `brightness` | `90` | 0-255, sent to the device as `BRIGHTNESS`; ~35% is the conservative bring-up default |
| `blank` | `false` | Start with the panel blanked |
| `scenes_dir` | `/config/scenes` | Inside the container. On the host this is `/addon_configs/<slug>_matrix_studio/scenes` |
| `ws_port` | `7887` | Container-internal listen port — see the caveat below |
| `ws_path` | `/matrix-studio` | Must match the firmware |
| `state_poll_interval` | `5` | Seconds between Home Assistant state polls |
| `hot_reload` | `true` | Watch `scenes_dir` and reload changed files |
| `auto_discover_lights` | `true` | When `entities.lights` is empty, track every `light.` entity |
| `log_level` | `info` | |
| `entities.lights` | `[]` | Explicit list of light entity ids |
| `entities.indoor_temperature` | – | e.g. `sensor.living_room_temperature` |
| `entities.outdoor_temperature` | – | e.g. `sensor.outside_temperature` |
| `entities.weather` | – | e.g. `weather.home` |
| `entities.occupancy` | – | e.g. `binary_sensor.someone_home` |

No entity ids are hardcoded anywhere in this add-on; scenes only ever see what
you configure (plus auto-discovered lights, if enabled).

> **`ws_port` caveat.** `config.yaml` publishes container port `7887`. Changing
> `ws_port` changes what the *container* listens on, which then no longer
> matches the published port. To use a different port on the LAN, leave
> `ws_port` at `7887` and remap the **host** side in the add-on's *Network*
> panel instead. The add-on logs a warning if you change it.

### Point the device at it

The ESP32 connects out to
`ws://<home-assistant-host>:7887/matrix-studio`. The add-on never opens a
separate management connection to the device: display traffic and firmware
updates both use the device's existing Protocol v1 WebSocket session. Zero
connected devices is a normal, healthy state — the add-on keeps rendering
regardless.

### Firmware updates

Once the ESP32 has received the first wired OTA-capable image, later application
firmware can be installed from the Matrix Studio ingress UI:

1. build the firmware from `esp32/` with `idf.py build`;
2. open Matrix Studio in Home Assistant and find **Firmware**;
3. select the connected device and choose `esp32/build/matrix_studio.bin`;
4. press **Install firmware**.

The server pauses FRAME traffic, sends `OTA_BEGIN`, sequential 4096-byte
`OTA_DATA` chunks with a STATUS acknowledgement after every chunk, then
`OTA_COMMIT`. The device validates the image, selects the inactive OTA slot and
reboots. The UI reports transfer progress from the live device session; after
reboot the Devices table should show the new `HELLO.fw_version`.

The upload endpoint accepts only an ESP-IDF application image up to the 3 MiB
OTA-slot size. Bootloader and partition-table changes remain recovery/USB work,
not routine OTA updates.

## Writing scenes

A scene is anything with `render(t, home, controls) -> PIL.Image.Image`
returning a 64x64 RGB image. Drop `.py` files into `scenes_dir`; they are
picked up without restarting the add-on. Full authoring notes, including the
`HomeState` API, are in [`example_scenes/README.txt`](example_scenes/README.txt),
which is copied into your scenes directory on first run.

If a scene raises, the engine logs it and renders a fallback scene instead;
after three consecutive failures the scene is quarantined until you fix it and
press **Reload scenes**. The add-on itself never goes down because of a scene.

Built-in scenes: `plasma` (coherently drifting colour test), `starfield`,
`landscape`, `home_pulse` (reacts to Home Assistant), `testcard` (panel
wiring check), and `motion_test` (constant 8 px/s cadence reference).

## Animation cadence

The default remains 24 FPS. Testing the original plasma at 24, 30, 40, 48,
and 60 FPS produced the same periodic slow/fast motion, so renderer frequency
was not the cause. Its independently timed interference waves were beating
against each other; the built-in plasma now translates the whole field at one
constant velocity.

The repeatable renderer-only check sampled ten seconds at each rate and
compared the busiest and quietest rolling one-second windows of mean absolute
RGB change. It isolates artwork motion from scheduling and transport:

| Target FPS | Original plasma | Coherent plasma |
|---:|---:|---:|
| 24 | 1.157× | 1.000× |
| 30 | 1.157× | 1.000× |
| 40 | 1.157× | 1.000× |
| 48 | 1.157× | 1.000× |
| 60 | 1.157× | 1.000× |

A value of 1.000× means visual activity remains uniform across the sampled
windows. The result explains why increasing the original scene to 60 FPS did
not remove its slow/fast rhythm. The default stays at 24 FPS because higher
rates add bandwidth without fixing artwork-level cadence.

Use `motion_test` when checking a physical installation. The cyan bar moves at
exactly 8 pixels/second with fractional edge coverage. If it moves uniformly,
the renderer/transport/display cadence is healthy and any uneven motion is in
the selected artwork. If it bunches or jumps, the Devices table shows recent
send FPS, interval jitter, longest recent gap, and skipped frames.

All configured rates remain supported. A raw 64×64 RGB565 stream uses roughly:

| Target FPS | Payload rate |
|---:|---:|
| 24 | 1.57 Mbit/s |
| 30 | 1.97 Mbit/s |
| 40 | 2.62 Mbit/s |
| 48 | 3.15 Mbit/s |
| 60 | 3.93 Mbit/s |

Raise `target_fps` only when the Devices table stays close to target with zero
skips and low jitter. Latest-frame semantics are retained: a slow connection
skips stale frames instead of building latency.

## Development

```sh
cd home-assistant
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

Preview scenes with no hardware and no Home Assistant:

```sh
.venv/bin/python -m matrix_studio.preview --list
.venv/bin/python -m matrix_studio.preview --scene landscape --out /tmp/frame.png
.venv/bin/python -m matrix_studio.preview --scene starfield --frames 90 --gif /tmp/out.gif
.venv/bin/python -m matrix_studio.preview --serve                  # browser preview on :8099
.venv/bin/python -m matrix_studio.preview --serve --device-server  # + real :7887 endpoint
```

`--serve --device-server` runs the genuine Protocol v1 endpoint, so firmware
can be developed against a laptop before the add-on is installed. Feed scenes
real data with `--home-state states.json` (a dump of `/api/states`).

Preview output is round-tripped through RGB565, so what you see is what the
panel shows, quantisation included. The browser requests native 64×64 PNGs at
up to 24 FPS and scales them with nearest-neighbour rendering; each request is
started only after the previous one finishes.
