# Matrix Studio — ESP32-S3 firmware

Matrix Studio turns a 64x64 HUB75 LED panel into a Wi-Fi display driven by the
Home Assistant add-on. The ESP32-S3 joins Wi-Fi, opens a persistent WebSocket
to `ws://<host>:7887/matrix-studio`, performs the Protocol v1 handshake, and
renders incoming RGB565 frames with HUB75 DMA.

The frozen wire contract is [`docs/protocol.md`](../docs/protocol.md). Hardware
facts and bring-up evidence live in [`docs/hardware.md`](../docs/hardware.md).

> **Current physical target:** Hengantech-branded controller matching Seengreat
> `RGB Matrix HUB75 S3`, plus a Seengreat P3 64x64 / 1/32-scan panel. The GPIO
> map is vendor-documented and the physical panel photographs show FM6124EJ
> driver ICs. The complete combination has not yet been bench-verified.

---

## Known hardware configuration

### Controller

Seengreat hardware guide: <https://seengreat.com/wiki/214>

- ESP32-S3-WROOM-1-N16R8
- 16 MB flash / 8 MB PSRAM
- two HUB75 connector styles on the same signal bus
- normal USB-C for board power / matrix power / download / debug
- second `Power` USB-C dedicated to matrix power
- VH-4P 5 V panel output, maximum 4 A

### Panel

Seengreat panel guide: <https://seengreat.com/wiki/74>

- P3, 64x64, 192 x 192 mm
- HUB75
- 1/32 scan
- 5 V / 4 A
- physical PCB: `P3-64x64-2012-20B-1.2 QD`
- HUB75 `J1` = **IN**
- HUB75 `J2` = **OUT**
- physical driver markings: **FM6124EJ**

### HUB75 GPIO map

These values come from the Seengreat RGB Matrix HUB75 S3 documentation and are
mirrored in [`main/board_config.h`](main/board_config.h):

| Signal | GPIO | | Signal | GPIO |
|---|---:|---|---|---:|
| R1 | 5  | | R2  | 15 |
| G1 | 4  | | G2  | 7  |
| B1 | 6  | | B2  | 17 |
| A  | 8  | | B   | 18 |
| C  | 10 | | D   | 9  |
| E  | 16 | | CLK | 12 |
| LAT| 11 | | OE  | 13 |

The old Waveshare-derived defaults were not suitable for this controller: only
`B1 = GPIO6` matched.

Current panel settings:

```cpp
constexpr Hub75ScanWiring kScanWiring = Hub75ScanWiring::STANDARD_TWO_SCAN;
constexpr Hub75ShiftDriver kShiftDriver = Hub75ShiftDriver::FM6124;
constexpr Hub75ClockSpeed kClockSpeed = Hub75ClockSpeed::HZ_20M;
constexpr uint8_t kLatchBlanking = 1;
constexpr uint8_t kDefaultBrightness = 90;  // ~35%
```

The scan wiring, colour order, clock speed and latch blanking still require
physical confirmation.

---

## Requirements

| | |
|---|---|
| Controller | Hengantech / Seengreat-compatible RGB Matrix HUB75 S3 |
| Panel | P3 64x64 HUB75, 1/32 scan |
| Runtime power | Regulated 5 V USB-C source suitable for the controller/panel load |
| Toolchain | ESP-IDF **v5.5.2 or newer** |
| Flash host | macOS, Linux or Windows |

Do not use Arduino or PlatformIO for Matrix Studio firmware.

---

## Toolchain setup

### macOS / Linux

If ESP-IDF is not already installed:

```sh
mkdir -p ~/esp
cd ~/esp
git clone -b v5.5.2 --depth 1 --recursive --shallow-submodules \
    https://github.com/espressif/esp-idf.git
~/esp/esp-idf/install.sh esp32s3
```

In every new shell used for firmware work:

```sh
. ~/esp/esp-idf/export.sh
idf.py --version
```

The version should be `v5.5.2` or newer.

The first build downloads the pinned `esp-hub75` and
`esp_websocket_client` components. Subsequent builds use the cached versions.

---

## Wi-Fi and server configuration

From `esp32/`:

```sh
cp main/wifi_secrets.h.example main/wifi_secrets.h
```

Edit `main/wifi_secrets.h` with:

- Wi-Fi SSID
- Wi-Fi password
- Home Assistant Pi LAN IP, preferably numeric
- server port `7887`

`main/wifi_secrets.h` is gitignored. **Never commit or paste its contents into
logs, issues, or pull requests.**

The equivalent settings are also available through `idf.py menuconfig`, but the
local secrets header is recommended because `sdkconfig` is not a secrets file.

---

## Build and flash

For the first flash, the panel does not need to be connected or powered.

```sh
. ~/esp/esp-idf/export.sh
cd matrix-studio/esp32
idf.py set-target esp32s3
idf.py build
```

Identify the serial port:

- macOS: `/dev/cu.usbmodem*` or `/dev/cu.usbserial*`
- Linux: `/dev/ttyACM0` or `/dev/ttyUSB0`
- Windows: the COM port shown in Device Manager

Then:

```sh
idf.py -p <PORT> flash monitor
```

Exit the monitor with **Ctrl-]**.

If automatic download mode fails:

1. hold **BOOT**
2. tap **EN / RESET**
3. release **BOOT**
4. retry `idf.py -p <PORT> flash`

A normal USB-C connection is used for flashing/debugging. The Mac/PC is not
part of the deployed system once firmware is installed.

---

## Normal deployed wiring and power

The normal setup is:

```text
5 V USB-C supply
      |
      v
ESP32-S3 HUB75 controller
      |-- HUB75 ribbon --> panel J1 / IN
      `-- VH-4P power ---> panel +5V / GND

Home Assistant Pi <-- Wi-Fi --> ESP32-S3
```

The HUB75 ribbon carries display signals and ground. **It does not carry the
panel's LED operating power.** The separate VH-4P cable supplies that power.

The controller is designed to distribute panel power: Seengreat rates its
VH-4P output to a maximum of 5 V / 4 A, and the P3 64x64 panel is itself
specified at 5 V / 4 A. Controller-powered operation is therefore the intended
baseline for this hardware pair.

For first bring-up, keep the firmware at the default ~35% brightness. Do not
start with sustained full-white / full-brightness output. If high-brightness
operation later produces resets or corruption, establish the practical limit
and use the controller's dedicated matrix-power input or a higher-margin direct
panel supply if needed.

---

## First physical bring-up

After firmware has flashed successfully:

1. Leave the controller powered down.
2. Connect the HUB75 ribbon from the controller's boxed HUB75 connector to the
   panel's **J1 / IN** connector.
3. Connect the controller's VH-4P panel-power lead to the panel power input.
   The physical panel PCB marks the rails `+5V` and `GND`; use the keyed cable
   supplied for this hardware and do not reverse it.
4. Apply the controller's 5 V USB-C power.
5. Open the serial monitor.
6. Run low-brightness diagnostics before relying on streamed artwork.

The expected boot configuration should include:

```text
driver=FM6124
R1=5 G1=4 B1=6 R2=15 G2=7 B2=17
A=8 B=18 C=10 D=9 E=16 CLK=12 LAT=11 OE=13
initial brightness 90/255
```

The firmware may join Wi-Fi immediately as well; panel diagnostics and network
bring-up can be checked independently.

---

## Serial diagnostics

When serial commands are enabled, commands are single keypresses; Enter is not
required.

| Key | Action |
|---|---|
| `r` | solid red |
| `g` | solid green |
| `b` | solid blue |
| `w` | solid white |
| `q` | quadrant pattern |
| `c` | coordinate ramp |
| `a` | cycle diagnostics |
| `m` | brightness ramp |
| `x` | exit diagnostics and resume normal rendering |
| `i` | status/counters |
| `?` | help |

Holding **BOOT** during a normal reset also requests diagnostic mode.

### What the patterns tell us

- `r/g/b`: verify logical colour channels and reveal swapped colour order.
- `q`: reveal swapped halves, rotation or mirroring.
- `c`: reveal address-line/scan problems through repeated or interleaved rows.
- `w`: useful only at conservative brightness during early bring-up.
- `m`: use only after low-brightness patterns are stable; establish the safe
  practical brightness limit rather than assuming 255 is usable.

---

## Systematic panel diagnosis

| Symptom | First checks / change |
|---|---|
| completely black | power path, J1/IN, ribbon seating, OE; re-read driver marking before changing driver |
| doubled/interleaved vertically | E line and 1/32 scan wiring |
| repeated horizontal bands | A-E mapping / scan wiring |
| wrong colours | R/G/B mapping or panel colour order |
| top/bottom halves swapped | R1/G1/B1 vs R2/G2/B2 |
| sparkles/noise/tearing | reduce `kClockSpeed` to `HZ_10M`, then `HZ_8M` |
| ghosting | raise `kLatchBlanking` from 1 to 2 |
| reset during bright white | power delivery / brightness limit until proven otherwise |

The physical panel is marked FM6124EJ, so **FM6124 is now the primary driver**,
not a fallback. Do not cycle through other shift drivers unless the physical
behaviour gives a reason to question the observed marking or library support.

All hardware constants live in [`main/board_config.h`](main/board_config.h).

---

## Network bring-up

Once diagnostics render correctly, press `x` to resume normal rendering and
verify the serial log reports, in order:

- Wi-Fi IP acquired
- WebSocket connected
- `HELLO` sent
- `HELLO_ACK` received
- session established
- frame counters increasing

The add-on endpoint is:

```text
ws://<HOME_ASSISTANT_LAN_IP>:7887/matrix-studio
```

In Home Assistant, the connected device count should move from zero to one.
Then confirm real Matrix Studio scenes have the correct orientation and colour
order.

---

## Sustained Wi-Fi / DMA acceptance test

A panel that displays one frame is **not** considered proven.

After normal streaming works:

1. run real scenes at the configured 24 FPS for at least **15-30 minutes**
2. periodically press `i` and record:
   - reconnect count
   - rejected frames
   - received/rendered frames
   - dropped frames
3. watch for sparkles, tearing, freezes, colour corruption or resets
4. check Home Assistant/add-on logs for new errors
5. cautiously exercise the brightness ramp and establish a practical upper
   limit for the chosen power source

Physical bring-up is complete only when diagnostics are correct **and** serial
counters plus Home Assistant connection state confirm sustained operation.

Record the final findings in [`docs/hardware.md`](../docs/hardware.md).

---

## What the firmware does

- connects to Wi-Fi as a station and reconnects indefinitely
- connects to the Matrix Studio WebSocket server
- performs the frozen Protocol v1 handshake
- validates inbound messages
- responds to heartbeat traffic
- renders RGB565 frames through a latest-wins queue
- uses double-buffered HUB75 output
- keeps display rendering and network work separated across the ESP32-S3 cores
- detects PSRAM at runtime rather than assuming it
- exposes frame/reconnect/status counters for physical validation

---

## Host tests

Protocol tests do not require ESP-IDF:

```sh
cd esp32/tests
cmake -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

The real firmware build remains the authoritative compile check for the
ESP32-S3/HUB75 component combination:

```sh
. ~/esp/esp-idf/export.sh
cd esp32
idf.py build
```

---

## Source layout

```text
esp32/
├── CMakeLists.txt
├── sdkconfig.defaults
├── main/
│   ├── board_config.h          all panel GPIO/geometry/scan/driver settings
│   ├── board_config.cpp
│   ├── app_config.h
│   ├── wifi_secrets.h.example
│   ├── display/                HUB75 driver, queue, render task, diagnostics
│   ├── net/                    Wi-Fi + WebSocket client
│   └── protocol/               Protocol v1 parser/encoder
└── tests/                      host-side firmware protocol tests
```

---

## Troubleshooting

**`idf.py: command not found`**

```sh
. ~/esp/esp-idf/export.sh
```

**No serial port appears on macOS**

Compare `ls /dev/cu.*` before and after connecting the controller. If necessary,
use BOOT + EN to enter download mode and check again.

**`no Wi-Fi SSID configured`**

Create and fill in `main/wifi_secrets.h` from the example file.

**WebSocket repeatedly times out or refuses the connection**

Use the Home Assistant Pi's numeric LAN IP and confirm the add-on is running on
port 7887.

**Panel diagnostics work but streaming does not**

Treat that as a network/protocol problem; inspect `i`, the serial log, and the
Home Assistant add-on logs rather than changing panel wiring.

**Streaming works but the image is visibly wrong**

Treat that as panel scan/colour/timing configuration; use `r/g/b/q/c` before
changing network code.
