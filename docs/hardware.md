# Hardware

This document records the hardware facts Matrix Studio is built against and the
remaining assumptions that must be checked during physical bring-up.

Confidence markers: ✅ confirmed by vendor documentation or the user's physical
hardware · ⚠️ plausible but not yet bench-verified · ❓ still unknown.

## Controller identification

✅ **Controller:** Hengantech-branded "RGB LED Matrix HUB75 Controller Board for
ESP32-S3 with Audio", using the Seengreat **RGB Matrix HUB75 S3** hardware
documentation supplied with the product:

- https://seengreat.com/wiki/214
- ESP32-S3-WROOM-1-N16R8
- 16 MB flash / 8 MB PSRAM
- ES8311 codec + ES7210 ADC
- dual microphones + speaker output
- PCF85063 RTC + battery connector
- microSD
- two HUB75 connector styles on the same bus
- two 5 V USB-C inputs
- VH-4P panel-power output, rated to a maximum of 5 V / 4 A

The earlier repository state treated this as a likely Waveshare-compatible
board and used the Waveshare GPIO defaults. That was the wrong hardware map for
this controller. The Seengreat vendor table below is now the firmware default.

## HUB75 GPIO pin mapping

✅ Vendor-documented for RGB Matrix HUB75 S3:

| Signal | GPIO | | Signal | GPIO |
|---|---:|---|---|---:|
| R1 | 5  | | R2  | 15 |
| G1 | 4  | | G2  | 7  |
| B1 | 6  | | B2  | 17 |
| A  | 8  | | B   | 18 |
| C  | 10 | | D   | 9  |
| E  | 16 | | CLK | 12 |
| LAT| 11 | | OE  | 13 |

The physical display has not yet been driven, so bring-up diagnostics still
need to confirm that the Hengantech-branded controller is electrically
identical to the documented Seengreat design. If it is not, all panel GPIOs
remain isolated in `esp32/main/board_config.h`.

For comparison, the previous defaults had only **one of the fourteen HUB75
signals correct** (`B1 = GPIO6`) for this board.

## Panel identification

✅ **Panel:** P3 64x64 HUB75, 192 x 192 mm, 1/32 scan, 5 V / 4 A.

Vendor documentation supplied with the panel:

- https://seengreat.com/wiki/74

Physical photographs additionally confirm:

- PCB marking `P3-64x64-2012-20B-1.2 QD`
- `J1` is the HUB75 **IN** connector
- `J2` is the HUB75 **OUT** connector
- panel power is explicitly marked `+5V` and `GND`
- the LED driver ICs are marked **FM6124EJ**

The firmware therefore uses:

```cpp
constexpr Hub75ScanWiring kScanWiring = Hub75ScanWiring::STANDARD_TWO_SCAN;
constexpr Hub75ShiftDriver kShiftDriver = Hub75ShiftDriver::FM6124;
```

⚠️ `STANDARD_TWO_SCAN` is the expected esp-hub75 layout for a normal 64x64
1/32-scan panel, but the first coordinate/quadrant diagnostics still need to
confirm the panel's internal row ordering and colour order.

## PSRAM

✅ The documented controller carries 8 MB PSRAM. Matrix Studio still detects
PSRAM at runtime rather than hardcoding its presence or size.

The timing-critical HUB75 DMA framebuffer remains in internal SRAM. PSRAM is
used for incoming-frame staging and WebSocket buffering when available, with an
internal-SRAM fallback.

## Flash and OTA layout

✅ The documented ESP32-S3-WROOM-1-N16R8 controller carries **16 MB flash**.
Matrix Studio uses that fact from the first wired flash rather than building a
single-app image that would require another cable flash to introduce OTA later.

The committed `esp32/partitions.csv` contains:

- `factory`: 3 MB first/wired application slot
- `ota_0`: 3 MB OTA application slot
- `ota_1`: 3 MB OTA application slot
- `otadata`: ESP-IDF boot-selection state
- `nvs` and `phy_init` data partitions

ESP-IDF application rollback is enabled. Firmware received through Protocol v1
`OTA_BEGIN` / `OTA_DATA` / `OTA_COMMIT` is written to the inactive slot. The
new image is marked valid only after Matrix Studio initializes its core display,
render, Wi-Fi and protocol subsystems; failure during that startup path rolls
back to the previous working image.

This does **not** enable secure boot or flash encryption. Those features can
involve irreversible eFuse configuration and are outside physical bring-up.

## Power topology

✅ The HUB75 ribbon carries **signals and ground**, not LED operating power.
Panel power reaches the display through the separate VH-4P power cable.

For this controller/panel pair, the normal supported topology is:

```text
5 V USB-C supply
      |
      v
RGB Matrix HUB75 S3 controller
      |-- HUB75 ribbon --> panel J1 / IN
      `-- VH-4P 5 V ----> panel power input

Home Assistant Pi <--Wi-Fi--> ESP32-S3
```

Seengreat documents the controller with two Type-C inputs: the normal USB-C is
for board power, matrix power, download and debug; the second `Power` Type-C is
a dedicated matrix-power input. The controller's VH-4P output is rated to a
maximum of 5 V / 4 A. The panel itself is specified at 5 V / 4 A.

Controller-distributed panel power is therefore an intended configuration.
Matrix Studio targets a **single external power cable in normal deployed use**,
with frame and firmware-update data arriving over Wi-Fi.

Because the panel's stated maximum load reaches the controller output rating,
first bring-up remains deliberately conservative:

- default brightness stays at 90/255 (~35%)
- establish correct diagnostics before using full-white high brightness
- increase brightness gradually while watching for resets or visual corruption
- if high-brightness operation proves marginal, use the dedicated matrix-power
  input or a higher-margin direct 5 V panel supply rather than treating a reset
  as a firmware problem

A large extra capacitor is not required for the first test. It remains a
possible power-integrity troubleshooting measure if real testing shows voltage
droop or row-switching instability.

## Toolchain and HUB75 driver

Matrix Studio uses **ESP-IDF v5.5+** and `esphome-libs/esp-hub75` rather than
Arduino/PlatformIO.

- native ESP-IDF gives direct control over Wi-Fi, task affinity, memory and OTA
- `esp-hub75` provides the ESP32-S3 DMA path and FM6124 driver mode
- render and network work remain separated so the sustained 24 FPS Wi-Fi/DMA
  test can expose any real contention

The driver swaps a completed network frame only on a HUB75 scan boundary. At
the original 60 Hz minimum, this panel's 64x64, 8-bit, 20 MHz configuration
selected a roughly 76 Hz scan cadence; a free-running 20-60 FPS producer could
therefore alternate between short and long frame holds. The firmware now asks
for a 240 Hz minimum, selecting the driver's roughly 287 Hz BCM timing tier and
reducing swap quantisation from about 13.1 ms to 3.5 ms.

⚠️ The faster tier uses fewer binary-code-modulation time slices per scan. That
can reduce the number of distinct low-brightness intensity steps even though
the RGB565 input format is unchanged. Smooth gradients and dim colours must be
checked on the physical panel after this timing change; if visible banding is a
worse tradeoff than the former cadence, choose a lower intermediate scan tier
from bench measurements rather than increasing the 20 MHz electrical clock.

The project must still prove this combination on the real board for 15-30
minutes; a successful firmware build is not evidence of radio/DMA stability.

## Physical bring-up facts still to establish

The following are **not** yet proven:

1. ⚠️ the Hengantech controller behaves electrically exactly like the Seengreat
   RGB Matrix HUB75 S3 mapping
2. ⚠️ `STANDARD_TWO_SCAN` produces the expected physical row order
3. ❓ whether this panel batch uses RGB, RBG, or another colour ordering
4. ❓ whether 20 MHz is clean with the supplied ribbon; 10 MHz or 8 MHz remains
   the fallback for noise/sparkles
5. ❓ whether latch blanking 1 is sufficient; use 2 if ghosting appears
6. ❓ the safe practical upper brightness limit using the chosen USB-C supply
7. ❓ sustained Wi-Fi + HUB75 DMA behaviour at 24 FPS over 15-30 minutes
8. ❓ a real end-to-end OTA update on this physical controller after initial
   streaming stability is established
9. ❓ low-brightness gradient quality at the approximately 287 Hz scan tier

## Bring-up evidence to record

When the physical test is complete, record here:

- controller silkscreen/revision if visible
- final GPIO map
- panel PCB revision and FM6124EJ marking
- scan wiring and colour order
- working clock speed and latch blanking
- chosen brightness limit and power source
- Wi-Fi IP + successful `HELLO` / `HELLO_ACK`
- reconnect count
- rejected frames
- dropped/rendered frame counts
- duration of sustained test
- OTA update/rollback result
- any visual corruption or brownouts

Only after those checks should this document describe the complete hardware
combination as bench-verified.
