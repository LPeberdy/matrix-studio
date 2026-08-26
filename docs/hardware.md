# Hardware

This document records what we could determine about the ESP32-S3 HUB75
board and 64x64 P3 panel from documentation research, what remains an
assumption, and the architectural decisions that follow from it. **Nothing
here has been bench-verified against the user's actual physical board** —
treat pin/driver-chip values as defaults to try first, not facts.

Confidence markers: ✅ confirmed by vendor/library docs · ⚠️ strong inference
· ❓ uncertain, needs verification against the physical board.

## Board identification

⚠️ Most likely board: **Waveshare `ESP32-S3-RGB-Matrix`**, or a close
OEM/clone of the same reference design (e.g. Seengreat "RGB Matrix HUB75
S3", or Amazon-rebadged equivalents). This is the only board family found
that matches the full description — ESP32-S3, Wi-Fi, microSD, RTC, **and**
onboard audio codec — all together on one board with a HUB75 connector.

| Feature | Waveshare ESP32-S3-RGB-Matrix | Seengreat/OEM variant |
|---|---|---|
| MCU | ESP32-S3-N32R16 (32MB flash / 16MB PSRAM) | ESP32-S3-WROOM-1-N16R8 (16MB flash / 8MB octal PSRAM) |
| RTC | PCF85063(A) + battery | same |
| Audio | ES8311 codec + ES7210 ADC, mic + speaker header | same |
| Storage | microSD/TF | same |
| Power | Dual USB-C + VH-4P 5V terminal (panel-only USB-C rated max 5V/4A) | same |

**Do not hardcode a PSRAM size or type.** Detect it at runtime
(`esp_psram_get_size()`); the two documented variants disagree (8MB octal vs
16MB), and cheaper generic S3 boards in the wild are often 2MB **quad**
PSRAM, which this project's DMA path must not assume is present.

**Action for the user:** confirm the exact silkscreen model/part number on
the physical board before relying on the pin defaults below in anger. If it
turns out to be a different board, only `esp32/main/board_config.h` (or
equivalent Kconfig) needs to change — see "Isolation" below.

## HUB75 GPIO pin mapping (default, unverified against physical hardware)

❓ This mapping is what both the Waveshare vendor documentation and the
`ESP32-HUB75-MatrixPanel-DMA` library's own ESP32-S3 default pin file agree
on, which is reassuring but still not a substitute for testing against the
real board:

| Signal | GPIO | | Signal | GPIO |
|---|---|---|---|---|
| R1 | 4  | | R2  | 7  |
| G1 | 5  | | G2  | 15 |
| B1 | 6  | | B2  | 16 |
| A  | 18 | | B   | 8  |
| C  | 3  | | D   | 42 |
| E  | 9  | | CLK | 41 |
| LAT| 40 | | OE  | 2  |

The 64x64 panel needs the `E` line (see scan mode below) — a board wired
only for `A`-`D` is a 1/16-scan (64x32-class) board, not this one.

**Isolation requirement:** every one of these 14 pins, plus panel
width/height/chain-length, scan pattern, and shift-driver chip selection,
live in exactly one place (`esp32/main/board_config.h`). No other firmware
file references a GPIO number directly. If the physical board turns out to
use different pins, or the panel needs a different scan/driver
configuration, changing this one file is the entire fix.

## PSRAM

⚠️ Typically octal PSRAM, 8MB or 16MB depending on variant (see table
above). At 64x64 with 8-bit colour depth, the HUB75 DMA framebuffer itself
needs only ~32-64KB, which fits in the S3's internal 512KB SRAM —
**the DMA framebuffer should live in internal SRAM, not PSRAM.** PSRAM (when
present, size detected at runtime) is used instead for the incoming-frame
network buffer, giving headroom to absorb network jitter without touching
the timing-critical DMA path.

## Scan mode and driver chip for the 64x64 P3 panel

✅ A 64x64 indoor P3 panel is expected to be **1/32 scan** ("two-scan"),
which requires all five HUB75 address lines (`A`-`E`); this is standard for
this panel size/pitch across vendors (Adafruit's equivalent 64x64 3mm panel
is documented the same way). The single most common bring-up failure for
this class of panel is a missing or misconfigured `E` line, which typically
shows up as a scrambled/torn image rather than a clean failure.

❓ **Driver chip is unknown without reading the panel's own ICs** — common
candidates for recent 64x64 P3 panels are FM6126A/ICN2038S (most common),
FM6124, ICND2012, or MBI5124. FM6126A/ICN2038S need a specific power-on
init sequence; without it the panel can appear completely dead (not dim,
not garbled — black), which is easy to misdiagnose as a wiring or power
fault. The driver chip is therefore a **runtime-configurable** setting in
`board_config.h` (default: FM6126A), with the firmware's diagnostic mode
(see below) making it fast to try alternatives.

## Library and toolchain choice

**Chosen: ESP-IDF (not PlatformIO/Arduino), using the
[`esphome-libs/esp-hub75`](https://github.com/esphome-libs/esp-hub75)
component for the HUB75 DMA driver.**

This is a deliberate departure from "PlatformIO/Arduino is usually the
easier default" for one specific, well-documented reason:

- The more widely-known `mrcodetastic/ESP32-HUB75-MatrixPanel-DMA` library
  documents an **unresolved Wi-Fi/DMA interference issue specific to
  ESP32-S3 boards**: the high-frequency DMA traffic driving the panel can
  disturb the S3's Wi-Fi radio on boards without especially careful PCB
  layout, surfacing as WebSocket/HTTP stalls — precisely the failure mode
  Matrix Studio depends on not happening, since the whole point of the
  device is "reliably keep receiving frames over Wi-Fi."
- `esp-hub75` is a native ESP-IDF component built around a static,
  interrupt-free circular DMA design (rather than the older interrupt-driven
  refresh approach), and — significant precedent — **it is what Waveshare's
  own ESP-IDF reference BSP for this exact board uses**, not the
  Arduino-oriented library (their Arduino *examples* use the other library;
  their IDF *BSP* uses `esp-hub75`).
- ESP-IDF gives direct control over the specific knobs that mitigate the
  Wi-Fi/DMA risk: pinning the network task and the render/DMA task to
  different FreeRTOS cores, disabling Wi-Fi power-save
  (`WIFI_PS_NONE`), and tuning LWIP/radio buffers — control that Arduino's
  abstractions make harder to reach.
- This project's protocol already tolerates transient stalls from this risk
  without a design change: the heartbeat/timeout/reconnect behaviour in
  `docs/protocol.md` §3.1/§3.3 means a Wi-Fi hiccup surfaces as a dropped
  connection that reconnects automatically, not a wedged device. No UDP
  rewrite was judged necessary for v1; keep an eye on this in real testing.

Practical fallback: keep the HUB75 driver behind a small internal interface
so `mrcodetastic/ESP32-HUB75-MatrixPanel-DMA` (Arduino-compatible) could be
substituted later if `esp-hub75` proves harder to work with in practice —
but do this at that one seam only, not throughout the firmware.

## Power

✅ **The HUB75 ribbon carries signal and ground only — never LED operating
power.** The panel must be powered from its own external 5V supply wired
directly to the panel's own power input, sharing only ground with the
controller board.

- Budget for **~4A at 5V (~20W) at full white** on a 64x64 P3 panel; a
  5V/5A (25W) supply is a sensible minimum, more if additional panels are
  ever chained. Undersized supplies cause brownout resets under bright
  content, not just dimming — size for peak, not average.
- Even though this board's panel-power USB-C port is rated up to 5V/4A,
  treat that as a ceiling with no margin, not a comfortable operating
  point — an external bench/wall supply direct to the panel terminals is
  the documented-safe path, and is what firmware docs and the flashing
  guide should tell users to do rather than relying on the board to supply
  panel power.
- Add a 1000-2000µF capacitor across the panel's 5V input, close to the
  panel, to absorb per-row switching current spikes (standard HUB75
  guidance, not specific to this board).
- Ship a conservative default brightness (e.g. ~35%) rather than defaulting
  to full brightness, and document that current draw scales roughly
  linearly with brightness.

## What the firmware exposes as a result

- `board_config.h`: all 14 HUB75 pins, panel width/height, scan pattern,
  shift-driver chip, default brightness, PSRAM presence detected at runtime.
- A diagnostic/bring-up mode (solid R/G/B/white, per-quadrant test, a way to
  cycle shift-driver chip choice, pixel-coordinate test) so that the three
  unavoidable unknowns above (exact board revision, exact panel driver chip,
  exact pin wiring if this isn't quite the assumed board) become a
  five-minute guided bring-up procedure instead of a support dead-end.
