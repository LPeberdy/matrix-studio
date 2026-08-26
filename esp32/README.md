# Matrix Studio — ESP32-S3 firmware

Joins Wi-Fi, opens a persistent WebSocket to the Matrix Studio Home Assistant
add-on, and turns the `FRAME` messages it receives into light on a 64x64 HUB75
LED panel.

The wire contract is [`docs/protocol.md`](../docs/protocol.md) (frozen). The
hardware research this firmware is built on is
[`docs/hardware.md`](../docs/hardware.md). How the two halves fit together is
[`docs/architecture.md`](../docs/architecture.md).

> ⚠️ **Read [Power](#power) before you connect anything.** The HUB75 ribbon
> carries signal and ground only. The panel needs its own external 5V supply.
> Powering a 64x64 panel through the controller board is not a supported
> configuration.

> ⚠️ **The pin mapping and the panel's driver chip are unverified defaults**,
> not confirmed facts — see [Bring-up](#bring-up-when-the-panel-does-not-look-right).
> Everything you would need to change lives in
> [`main/board_config.h`](main/board_config.h).

---

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Toolchain setup](#toolchain-setup)
- [Configuration](#configuration)
- [Build, flash, monitor](#build-flash-monitor)
- [First boot](#first-boot)
- [Wiring](#wiring)
- [Power](#power)
- [Bring-up](#bring-up-when-the-panel-does-not-look-right)
- [Changing the pin mapping](#changing-the-pin-mapping)
- [Changing the scan pattern or driver chip](#changing-the-scan-pattern-or-driver-chip)
- [Serial commands](#serial-commands)
- [Host-side tests](#host-side-tests)
- [Source layout](#source-layout)
- [Design notes](#design-notes)
- [Troubleshooting](#troubleshooting)

---

## What it does

- Joins Wi-Fi as a station and reconnects indefinitely if the network drops.
- Connects out to `ws://<host>:7887/matrix-studio` as the WebSocket **client**,
  performs the `HELLO`/`HELLO_ACK` handshake, and streams frames.
- Validates every inbound message against `docs/protocol.md` §3.5 and never
  crashes on malformed input.
- Answers `PING` with `PONG`; sends its own `PING` when the link goes quiet and
  drops the connection if it goes unanswered (§3.1).
- Applies `BRIGHTNESS` and `BLANK` (§4.4, §4.5).
- Falls back to a quiet "no signal" indicator after 5s without a frame, without
  dropping the connection (§3.2).
- Reconnects with `1s → 2s → 4s → 8s → 16s → 30s` backoff on any disconnect
  (§3.3), re-establishing Wi-Fi first if that is what was lost.
- Ships diagnostic patterns for bench bring-up.

---

## Requirements

| | |
|---|---|
| Board | ESP32-S3 with a HUB75 connector — assumed Waveshare ESP32-S3-RGB-Matrix or a close clone |
| Panel | 64x64 P3 indoor HUB75, 1/32 scan (needs all five address lines A–E) |
| Panel power | External 5V, ≥5A, wired **directly to the panel** |
| Toolchain | ESP-IDF **v5.5** or newer (see below — v5.3 is *not* sufficient) |
| Host tests | CMake ≥3.16 and any C++17 compiler. No ESP-IDF needed. |

### Why ESP-IDF v5.5 and not v5.3

The HUB75 driver is [`esphome/esp-hub75`](https://github.com/esphome-libs/esp-hub75).
Its manifest claims ESP-IDF ≥4.4, but version 0.3.6's ESP32-S3 GDMA backend
uses two GDMA config fields that only exist from **ESP-IDF v5.4** onwards:

```
gdma_dma.cpp:158: 'gdma_channel_alloc_config_t' has no member named 'isr_cache_safe'
gdma_dma.cpp:184: 'gdma_strategy_config_t'      has no member named 'eof_till_data_popped'
```

Building against v5.3.2 fails with exactly those errors. CI pins v5.5.

This exact combination is verified to compile clean (zero warnings from
`main/`, 951KB binary, 38% of the app partition free):

| | Version |
|---|---|
| ESP-IDF | v5.5.2 |
| `esphome/esp-hub75` | 0.3.6 |
| `espressif/esp_websocket_client` | 1.8.0 |

Note that compiling is all that has been verified — **no part of this firmware
has been run on physical hardware yet.**

---

## Toolchain setup

Linux / macOS:

```sh
mkdir -p ~/esp && cd ~/esp
git clone -b v5.5.2 --depth 1 --recursive --shallow-submodules \
    https://github.com/espressif/esp-idf.git
~/esp/esp-idf/install.sh esp32s3
```

Then, **in every new shell** you want to build from:

```sh
. ~/esp/esp-idf/export.sh
```

Windows: use the [ESP-IDF Windows installer](https://dl.espressif.com/dl/esp-idf/)
and pick v5.5.x, then work inside the "ESP-IDF PowerShell" it creates.

Verify:

```sh
idf.py --version     # should print v5.5.2 or later
```

The first build downloads `esphome/esp-hub75` and
`espressif/esp_websocket_client` from the ESP Component Registry into
`managed_components/`, so it needs network access once. After that they are
cached and pinned by `dependencies.lock`.

---

## Configuration

Two settings you must supply: your Wi-Fi credentials, and the address of the
machine running the Home Assistant add-on. There are two ways to do it.

### Option A — `wifi_secrets.h` (recommended)

```sh
cp main/wifi_secrets.h.example main/wifi_secrets.h
$EDITOR main/wifi_secrets.h
```

`main/wifi_secrets.h` is gitignored. `sdkconfig` is not, which is why this is
the recommended route.

### Option B — menuconfig

```sh
idf.py menuconfig
#   -> Matrix Studio -> Wi-Fi              (SSID, password)
#   -> Matrix Studio -> Home Assistant server (host, port, path, frame timeout)
```

Values in `wifi_secrets.h` override the menuconfig equivalents. See
[`main/app_config.h`](main/app_config.h) for exactly how that resolution works.

Everything else under the `Matrix Studio` menu has a working default:

| Option | Default | |
|---|---|---|
| `MATRIX_STUDIO_SERVER_HOST` | `homeassistant.local` | An IP avoids depending on mDNS |
| `MATRIX_STUDIO_SERVER_PORT` | `7887` | Protocol v1 default |
| `MATRIX_STUDIO_SERVER_PATH` | `/matrix-studio` | Protocol v1 default |
| `MATRIX_STUDIO_FRAME_TIMEOUT_MS` | `5000` | §3.2 no-signal fallback |
| `MATRIX_STUDIO_SHOW_CONNECTION_PIXEL` | on | Tints pixel (0,0) with link state |
| `MATRIX_STUDIO_BOOT_INTO_DIAGNOSTICS` | off | Boot straight into test patterns |
| `MATRIX_STUDIO_SERIAL_COMMANDS` | on | Single-key console commands |
| `MATRIX_STUDIO_RENDER_CORE` | 1 | Panel task core |
| `MATRIX_STUDIO_NETWORK_CORE` | 0 | Wi-Fi + protocol task core |

**Panel pins, dimensions, scan pattern and driver chip are deliberately *not*
here.** They live in [`main/board_config.h`](main/board_config.h), so there is
exactly one place to look.

---

## Build, flash, monitor

```sh
. ~/esp/esp-idf/export.sh
cd esp32

idf.py set-target esp32s3      # once, per checkout
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

- `/dev/ttyUSB0` on Linux (often `/dev/ttyACM0` for native-USB boards),
  `/dev/cu.usbserial-*` on macOS, `COM3`-ish on Windows. `idf.py -p` can
  usually be omitted and autodetected.
- Exit the monitor with **Ctrl-]**.
- If the board does not enter download mode by itself: hold **BOOT**, tap
  **RESET**, release **BOOT**, then run `idf.py flash`.
  (Note that holding BOOT at a *normal* power-on puts this firmware into
  diagnostic mode — that is a different thing, and intentional.)

Useful extras:

```sh
idf.py monitor                 # attach without reflashing
idf.py erase-flash             # clear NVS and start clean
idf.py size-components         # where the flash went
idf.py fullclean               # nuke build/ (keeps managed_components/)
```

If the console is silent, the board is probably a native-USB-only one. Switch
`Component config → ESP System Settings → Channel for console output` to
**USB Serial/JTAG**; `serial_console.cpp` supports both.

---

## First boot

The exact sequence a user should expect, start to finish:

1. **Wire the panel** — see [Wiring](#wiring). Do not power anything yet.
2. **Connect the external 5V supply to the panel's own power terminals**, and
   bond its ground to the controller board's ground. See [Power](#power).
3. **Connect the controller board to your computer over USB.**
4. `cp main/wifi_secrets.h.example main/wifi_secrets.h` and fill it in.
5. `idf.py set-target esp32s3 && idf.py build`
6. `idf.py -p <port> flash monitor`
7. **Power on the panel supply.**
8. Watch the log. A healthy boot looks like this:

```
I ms.main : =====================================================
I ms.main :  Matrix Studio firmware 0.1.0 (Protocol v1)
I ms.main : =====================================================
I ms.main : chip: 2 core(s), silicon revision 0.2, 16MB embedded flash
I ms.psram: PSRAM detected: 8388608 bytes (8.0 MB), 8300000 bytes free
I ms.display: HUB75 panel 64x64, scan=STANDARD_TWO_SCAN, driver=FM6126A, ...
I ms.display: HUB75 pins R1=4 G1=5 B1=6 R2=7 G2=15 B2=16
I ms.display: HUB75 pins A=18 B=8 C=3 D=42 E=9 CLK=41 LAT=40 OE=2
W ms.display: Pin mapping and driver chip are UNVERIFIED defaults - see main/board_config.h
I ms.display: panel running, initial brightness 90/255 (~35%)
I ms.frames : 3 frame slots of 8192 bytes each in PSRAM
I ms.render : render task running on core 1
I ms.wifi   : device id ms-a0b1c2d3e4f5
I ms.wifi   : station started, connecting to "your-network"
I ms.wifi   : got IP 192.168.1.42
I ms.proto  : server ws://192.168.1.10:7887/matrix-studio
I ms.proto  : WebSocket connected to 192.168.1.10:7887/matrix-studio
I ms.proto  : sent HELLO (device_id=ms-a0b1c2d3e4f5, 64x64 RGB565)
I ms.proto  : HELLO_ACK: version 1, frame interval hint 40 ms, server time 1756...
I ms.proto  : session established
I ms.proto  : 1 frames received (seq 0, 0 dropped by the render queue)
```

9. If the panel shows nothing, or shows something wrong, go to
   [Bring-up](#bring-up-when-the-panel-does-not-look-right). **The most likely
   cause is the pin mapping or the driver chip, not a bug.**

While waiting for the server, the panel shows a dim dot in the centre plus a
single tinted pixel at the top-left:

| Pixel (0,0) | Meaning |
|---|---|
| red | Wi-Fi down |
| amber | Wi-Fi up, WebSocket down |
| blue | WebSocket open, waiting for `HELLO_ACK` |
| green | handshaked, no frames yet |
| untinted | frames streaming normally |

Turn this off with `MATRIX_STUDIO_SHOW_CONNECTION_PIXEL` if you want the whole
64x64 for artwork.

---

## Wiring

Defaults, from [`docs/hardware.md`](../docs/hardware.md) and mirrored in
[`main/board_config.h`](main/board_config.h). **Unverified against a physical
board.**

| Signal | GPIO | | Signal | GPIO |
|---|---|---|---|---|
| R1 | 4  | | R2  | 7  |
| G1 | 5  | | G2  | 15 |
| B1 | 6  | | B2  | 16 |
| A  | 18 | | B   | 8  |
| C  | 3  | | D   | 42 |
| E  | 9  | | CLK | 41 |
| LAT| 40 | | OE  | 2  |

If your board has a HUB75 header, the ribbon does all of this for you and there
is nothing to wire by hand — you only need this table if the defaults turn out
to be wrong for your board revision.

A 64x64 panel needs the **E** line. A board wired only for A–D is a 1/16-scan
(64x32-class) board and will not drive this panel correctly. The firmware logs
an error at startup if `kPinE` is `-1` while the panel is taller than 32 rows.

---

## Power

From [`docs/hardware.md`](../docs/hardware.md), and non-negotiable:

- **The HUB75 ribbon carries signal and ground only, never LED operating
  power.** Wire an external 5V supply directly to the panel's own power input
  (usually a spade/screw terminal or a 4-pin power lead), sharing only ground
  with the controller board.
- **Do not power the panel through the controller board.** Even where a board
  has a panel-power USB-C port rated 5V/4A, that is a ceiling with no margin,
  not an operating point.
- **Budget ~4A at 5V (~20W) at full white** for a 64x64 P3 panel. A 5V/5A (25W)
  supply is a sensible minimum. Size for peak, not average: an undersized
  supply causes **brownout resets** under bright content, not just dimming.
- **Add a 1000–2000µF capacitor** across the panel's 5V input, physically close
  to the panel, to absorb per-row switching spikes.
- The firmware ships at ~35% brightness (`board::kDefaultBrightness = 90`) for
  this reason. Current draw scales roughly linearly with brightness. The
  `m` serial command (brightness ramp) is the intended way to find where your
  supply gives out — if the board resets partway up the ramp, the supply is
  the problem.

---

## Bring-up: when the panel does not look right

Three things are genuinely unverified: the board revision, the panel's
shift-driver chip, and the pin mapping. The diagnostic patterns exist to turn
each into a quick check.

**Enter diagnostic mode** any of these ways:

- Hold the **BOOT** button (GPIO0) while powering on or resetting.
- Set `CONFIG_MATRIX_STUDIO_BOOT_INTO_DIAGNOSTICS=y` in menuconfig.
- Press `a` (or `r`/`g`/`b`/`w`/`q`/`c`/`m`) in `idf.py monitor` at any time.

Press `x` to return to normal rendering.

### Symptom table

| What you see | Most likely cause | What to change |
|---|---|---|
| **Completely black**, no flicker at all | Wrong shift-driver chip — FM6126A/ICN2038S panels are *black*, not dim, without their init sequence | `board::kShiftDriver` |
| Black, and you have already tried every driver chip | Power, or `OE` pin | Panel 5V supply; `board::kPinOe` |
| Image **doubled or interleaved** vertically | `E` address line wrong or missing | `board::kPinE` |
| **Horizontal bands**, image repeats in blocks | An address line (A–E) mapped wrong | `board::kPinA`..`kPinE` |
| **Colours swapped** (red shows as blue etc.) | R/G/B swapped within a half | `kPinR1`/`kPinG1`/`kPinB1` and the `2` set |
| **Top and bottom halves swapped** | The two halves' data pins are swapped | Swap `R1/G1/B1` with `R2/G2/B2` |
| **Sheared, torn, noisy, sparkling** | Clock too fast for the panel/ribbon | `board::kClockSpeed` → `HZ_10M` or `HZ_8M` |
| **Ghosting** of the row above/below | Latch blanking too short | `board::kLatchBlanking` → `2` |
| Image is **mirrored or rotated** | Panel orientation | `cfg.rotation` in `make_hub75_config()` |
| **Resets under bright content** | Undersized power supply | See [Power](#power) |

### Reading the patterns

- **Solid red / green / blue / white** (`r` `g` `b` `w`) — proves each colour
  channel independently. A missing channel means that colour's data pin.
- **Quadrants** (`q`) — top-left red, top-right green, bottom-left blue,
  bottom-right white. Instantly shows halves swapped, mirroring, or rotation.
- **Coordinate ramp** (`c`) — red ramps left→right, green ramps top→bottom,
  with a 1px white border and a single blue pixel just inside the top-left
  corner. A smooth gradient means the address lines are right; bands or
  repeated blocks mean they are not. The blue pixel is an unambiguous origin
  marker.
- **Brightness ramp** (`m`) — full white swept 0→255→0. Finds the point where
  your power supply gives out.
- **Cycle all** (`a`) — the above in rotation, 2.5s each. This is what the
  BOOT-button trigger starts.

---

## Changing the pin mapping

Every panel GPIO is a `constexpr` in
[`main/board_config.h`](main/board_config.h). No other file in `main/` refers
to a panel pin number — that is enforced by convention and stated at the top of
the file, so changing a board revision is a single-file edit:

```cpp
// main/board_config.h
constexpr int8_t kPinR1 = 4;    // <- change these
constexpr int8_t kPinG1 = 5;
...
constexpr int8_t kPinE  = 9;    // -1 only for a 1/16-scan panel
```

Then `idf.py build flash monitor`. The boot log prints the pin table that was
actually compiled in, so you can confirm the change took.

Panel size lives in the same file:

```cpp
constexpr uint16_t kPanelWidth  = 64;
constexpr uint16_t kPanelHeight = 64;
```

Changing these changes what the device advertises in `HELLO` and what
dimensions it accepts in `FRAME`, automatically — but note the Home Assistant
add-on renders 64x64 for the MVP, so both sides need to agree.

---

## Changing the scan pattern or driver chip

Same file:

```cpp
// main/board_config.h
constexpr Hub75ScanWiring  kScanWiring   = Hub75ScanWiring::STANDARD_TWO_SCAN;
constexpr Hub75ShiftDriver kShiftDriver  = Hub75ShiftDriver::FM6126A;
constexpr bool             kClockPhaseInverted = false;
```

**Driver chip.** Unknown without reading the ICs on the back of the panel.
FM6126A is the default because its failure mode (completely black) is the one
most often misdiagnosed as a wiring or power fault. If the panel stays black
through every diagnostic pattern, work down this list, reflashing between each:

```
FM6126A   (default; also covers ICN2038S)
GENERIC   (plain shift registers, no init sequence)
FM6124
MBI5124   (also set kClockPhaseInverted = true)
DP3246
```

**Scan wiring.** `STANDARD_TWO_SCAN` is correct for a normal 64x64 1/32-scan
panel; the driver derives the scan rate from `kPanelHeight`. The other
`Hub75ScanWiring` values are for panels with non-standard internal
shift-register wiring, which usually looks like the image appearing in
interleaved horizontal bands even though the address pins are right.

The boot log prints both values by name, so `i` on the serial console tells you
what is running without guessing.

---

## Serial commands

Available in `idf.py monitor` when `CONFIG_MATRIX_STUDIO_SERIAL_COMMANDS` is on
(default). Single keypress, no Enter needed.

| Key | Action |
|---|---|
| `r` `g` `b` `w` | Solid red / green / blue / white |
| `q` | Quadrant test |
| `c` | Coordinate ramp |
| `m` | Brightness ramp |
| `a` | Cycle all patterns |
| `x` | Leave diagnostics, resume normal rendering |
| `i` | Status: Wi-Fi, IP, WebSocket, frame counts, PSRAM, device id |
| `?` | Key map |

---

## Host-side tests

The protocol parser is plain C++17 over plain buffers, with no ESP-IDF types,
so it is tested on the host — no board and no toolchain required:

```sh
cd esp32/tests
cmake -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

Expected:

```
    Start 1: ms_protocol
1/1 Test #1: ms_protocol ......................   Passed    0.02 sec

100% tests passed, 0 tests failed out of 1
```

Run the binary directly for per-test detail:

```sh
./build/test_ms_protocol
```

Every golden fixture in [`protocol/fixtures/`](../protocol/fixtures/) is loaded
from disk and asserted against that directory's `manifest.json` — the valid
ones must parse with the documented field values, the malformed ones must be
rejected the way `docs/protocol.md` §3.5 says. The suite also checks that our
`HELLO` and `PING` encoders reproduce the golden fixtures **byte for byte**,
which is the strongest available guarantee that this parser and the Python
reference implementation have not drifted apart.

Fixtures are parsed out of exactly-sized heap allocations and the build enables
ASan+UBSan where the host supports running them, so an out-of-bounds read on
the truncated-frame fixture aborts the run rather than returning plausible
garbage. On hosts where AddressSanitizer cannot run — 39-bit-VA aarch64 kernels
such as a stock Raspberry Pi — CMake detects this at configure time, prints a
warning and falls back to UBSan only. CI runs on x86_64 with ASan enabled.

---

## Source layout

```
esp32/
├── CMakeLists.txt              ESP-IDF project
├── sdkconfig.defaults          committed baseline config (sdkconfig is gitignored)
├── PROTOCOL_ISSUES.md          ambiguities found in the frozen spec
├── main/
│   ├── board_config.h          ← ALL panel pins, size, scan, driver chip, brightness
│   ├── board_config.cpp
│   ├── app_config.h            resolves Kconfig / wifi_secrets.h into constants
│   ├── wifi_secrets.h.example  template; the real file is gitignored
│   ├── Kconfig.projbuild       the "Matrix Studio" menuconfig menu
│   ├── app_main.cpp            startup order
│   ├── psram_info.{h,cpp}      runtime PSRAM detection — never assumed
│   ├── serial_console.{h,cpp}  single-key commands
│   ├── display/
│   │   ├── display.{h,cpp}     the ONLY code that calls esp-hub75
│   │   ├── frame_queue.{h,cpp} 3-slot latest-wins network→render hand-off
│   │   ├── render_task.{h,cpp} the only task that touches the panel
│   │   └── diagnostics.{h,cpp} bring-up patterns
│   ├── net/
│   │   ├── wifi_station.{h,cpp}  station + indefinite reconnect
│   │   └── ms_client.{h,cpp}     Protocol v1 client: handshake, heartbeat,
│   │                             §3.5 validation, §3.3 backoff
│   └── protocol/
│       ├── ms_protocol.{h,cpp}   parser/encoder — no ESP-IDF types, host-testable
│       └── reconnect_backoff.h   §3.3 schedule, shared with the tests
└── tests/
    ├── CMakeLists.txt          standalone; no ESP-IDF required
    └── test_ms_protocol.cpp    golden-fixture tests
```

`protocol/matrix_studio_protocol.h` at the repo root is **included directly**
by both the firmware and the tests via an include path — never copied — so the
firmware cannot silently drift from the frozen contract.

---

## Design notes

**Two cores, on purpose.** `docs/hardware.md` documents an unresolved Wi-Fi/DMA
interference risk on ESP32-S3 HUB75 boards: high-frequency panel DMA can disturb
the radio. The mitigations here are concrete, not decorative:

- the render task is pinned to core 1 and the Wi-Fi + protocol tasks to core 0
  (`CONFIG_MATRIX_STUDIO_RENDER_CORE` / `_NETWORK_CORE`);
- Wi-Fi power save is off (`esp_wifi_set_ps(WIFI_PS_NONE)`);
- `esp-hub75` was chosen over the more common Arduino-oriented library
  specifically because its static, interrupt-free circular DMA design is what
  Waveshare's own ESP-IDF BSP uses;
- and `docs/protocol.md` §7 explains why the heartbeat/reconnect rules are
  considered an adequate v1 mitigation rather than redesigning the transport.

*Caveat:* `esp_websocket_client` creates its own FreeRTOS task with no core
affinity option, so that one task is not pinned. Its event handler is kept
cheap — it reassembles bytes and hands frames off — and all panel work happens
on the pinned render task.

**Tear-free in two stages.** The network task writes a whole frame into a free
`FrameQueue` slot and publishes it; the render task copies that into the HUB75
back buffer and flips. So the network never writes to the buffer being drawn,
and drawing never touches the buffer being scanned out.

**Latest-wins, not FIFO.** If the render task falls behind, showing the newest
frame is right and queueing stale ones is not. Dropped frames are counted and
logged.

**PSRAM is never assumed.** `esp_psram_get_size()` is called at runtime and the
result is logged. The DMA framebuffer stays in internal SRAM regardless — at
64x64 it needs only tens of KB and belongs on the timing-critical path. PSRAM,
when present, is used only for the frame staging slots and the WebSocket
reassembly buffer. Every allocation falls back to internal SRAM.

**Wi-Fi provisioning is build-time only.** menuconfig or `wifi_secrets.h`. A
captive portal is a nice-to-have that would be additive — nothing in
`wifi_station.h` would need to change — and was deliberately not allowed to
delay a working connection.

---

## Troubleshooting

**`idf.py: command not found`** — you have not sourced `export.sh` in this
shell. `. ~/esp/esp-idf/export.sh`.

**Build fails in `esphome__esp-hub75` with `no member named 'isr_cache_safe'`**
— your ESP-IDF is older than v5.4. See
[Why ESP-IDF v5.5](#why-esp-idf-v55-and-not-v53).

**First build fails downloading components** — the component manager needs
network access once. Behind a proxy, set `IDF_COMPONENT_REGISTRY_URL` or
vendor `managed_components/` from a machine that has access.

**`no Wi-Fi SSID configured`** — you skipped [Configuration](#configuration).
The panel still runs diagnostics.

**`connection timed out` / `connection refused` every 30s** — the ESP32 is
fine; it cannot reach the add-on. Check `MATRIX_STUDIO_SERVER_HOST` (try a raw
IP rather than `.local`), that port 7887 is open on the HA host, and that the
add-on is running. The backoff caps at 30s and retries forever, as specified.

**Connects, then drops every ~10s** — the server is not answering `PING` with
`PONG`, so the §3.1 heartbeat declares the link dead. Check the add-on side.

**`rejected message: ...` in the log** — the parser refused something. The log
names the reason (`bad_magic`, `unsupported_version`, `truncated`,
`malformed_payload`, `unknown_type`) plus the declared and actual lengths.
`bad_magic` and `unsupported_version` also close the connection, by design.

**Panel dark but logs look perfect** — this is the shift-driver-chip case. Go
to [Bring-up](#bring-up-when-the-panel-does-not-look-right).

**Brownout resets** — power. See [Power](#power).
