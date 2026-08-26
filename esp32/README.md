# Matrix Studio — ESP32-S3 firmware

Matrix Studio turns a 64x64 HUB75 LED panel into a Wi-Fi display driven by the
Home Assistant add-on. The ESP32-S3 joins Wi-Fi, opens a persistent WebSocket
to `ws://<host>:7887/matrix-studio`, performs the Protocol v1 handshake, and
renders incoming RGB565 frames with HUB75 DMA.

The authoritative wire contract is [`docs/protocol.md`](../docs/protocol.md).
Hardware facts and bring-up evidence live in
[`docs/hardware.md`](../docs/hardware.md).

> **Current physical target:** Hengantech-branded controller matching Seengreat
> `RGB Matrix HUB75 S3`, plus a Seengreat P3 64x64 / 1/32-scan panel. The GPIO
> map is vendor-documented and the physical panel photographs show FM6124EJ
> driver ICs. The complete combination has not yet been bench-verified.

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

Current panel settings:

```cpp
constexpr Hub75ScanWiring kScanWiring = Hub75ScanWiring::STANDARD_TWO_SCAN;
constexpr Hub75ShiftDriver kShiftDriver = Hub75ShiftDriver::FM6124;
constexpr Hub75ClockSpeed kClockSpeed = Hub75ClockSpeed::HZ_20M;
constexpr uint8_t kLatchBlanking = 1;
constexpr uint8_t kDefaultBrightness = 90;  // ~35%
```

The project pins `esp-hub75` to the upstream commit that routes FM6124 through
the library's FM6126A REG1/REG2 initialization sequence. This avoids the older
registry release's `ESP_ERR_NOT_SUPPORTED` path for FM6124 while keeping the
fix upstream rather than carrying a local driver fork.

The scan wiring, colour order, clock speed and latch blanking still require
physical confirmation.

## Requirements

| | |
|---|---|
| Controller | Hengantech / Seengreat-compatible RGB Matrix HUB75 S3 |
| Panel | P3 64x64 HUB75, 1/32 scan |
| Runtime power | Regulated 5 V USB-C source suitable for controller/panel load |
| Toolchain | ESP-IDF **v5.5.2 or newer** |
| Flash host | macOS, Linux or Windows |

Do not use Arduino or PlatformIO.

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

## Build and first flash

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

Exit monitor with **Ctrl-]**.

If automatic download mode fails:

1. hold **BOOT**
2. tap **EN / RESET**
3. release **BOOT**
4. retry `idf.py -p <PORT> flash`

The first wired image already contains Matrix Studio's OTA partition layout and
OTA receiver. USB remains available as a recovery path, but routine later
firmware updates do not require another cable flash.

### OTA-capable partition layout

The documented controller has 16 MB flash. `partitions.csv` provides:

```text
factory   3 MB
ota_0     3 MB
ota_1     3 MB
otadata
nvs
phy_init
```

Bootloader application rollback is enabled. A firmware image installed over
Wi-Fi is selected only after ESP-IDF validates it, then remains pending until
Matrix Studio successfully initializes its core subsystems on the next boot.
If that startup path fails, ESP-IDF rolls back to the previous image.

OTA uses ordinary Protocol v1 messages on the same WebSocket connection:
`OTA_BEGIN`, sequential `OTA_DATA` chunks, then `OTA_COMMIT`. See
[`docs/protocol.md`](../docs/protocol.md) for the exact contract.

### Routine OTA update

Build a new application image normally:

```sh
. ~/esp/esp-idf/export.sh
cd matrix-studio/esp32
idf.py build
```

Then open the Matrix Studio Home Assistant ingress UI. Under **Firmware**, pick
the connected device, select `esp32/build/matrix_studio.bin`, and press
**Install firmware**. The server pauses display frames during transfer, waits
for a device STATUS acknowledgement after each chunk, commits the image and
then lets the ESP32 reboot into the inactive OTA slot.

The reconnected device should report the new firmware version in HELLO. OTA
updates replace the application image; bootloader or partition-table changes
remain USB/recovery operations.

## Normal deployed wiring and power

```text
5 V USB-C supply
      |
      v
ESP32-S3 HUB75 controller
      |-- HUB75 ribbon --> panel J1 / IN
      `-- VH-4P power ---> panel +5V / GND

Home Assistant Pi <-- Wi-Fi --> ESP32-S3
```

The controller distributes panel power through its VH-4P output. The HUB75
ribbon carries signals and ground, not the LED operating supply.

Seengreat rates the controller VH-4P output to 5 V / 4 A and specifies the P3
64x64 panel at 5 V / 4 A. Start at the default ~35% brightness; do not begin
physical bring-up with sustained full-white/full-brightness output.

## First physical bring-up

After firmware has flashed successfully:

1. power the controller down;
2. connect the HUB75 ribbon from either controller HUB75 connector to panel **J1 / IN**;
3. connect the controller VH-4P lead to the panel power input;
4. apply the controller's 5 V USB-C power;
5. open the serial monitor;
6. use low-brightness diagnostics before relying on streamed artwork.

Expected boot configuration:

```text
driver=FM6124
R1=5 G1=4 B1=6 R2=15 G2=7 B2=17
A=8 B=18 C=10 D=9 E=16 CLK=12 LAT=11 OE=13
initial brightness 90/255
```

## Serial diagnostics

Commands are single keypresses; Enter is not required.

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

## Systematic panel diagnosis

| Symptom | First checks / change |
|---|---|
| completely black | power path, J1/IN, ribbon seating, OE |
| doubled/interleaved vertically | E line and 1/32 scan wiring |
| repeated horizontal bands | A-E mapping / scan wiring |
| wrong colours | R/G/B mapping or panel colour order |
| top/bottom halves swapped | R1/G1/B1 vs R2/G2/B2 |
| sparkles/noise/tearing | reduce `kClockSpeed` to `HZ_10M`, then `HZ_8M` |
| ghosting | raise `kLatchBlanking` from 1 to 2 |
| reset during bright white | power delivery / brightness limit until proven otherwise |

The physical panel is marked FM6124EJ, so FM6124 is the primary driver. All
hardware constants live in [`main/board_config.h`](main/board_config.h).

## Network bring-up

Once diagnostics render correctly, press `x` and verify the serial log reports:

- Wi-Fi IP acquired
- WebSocket connected
- `HELLO` sent
- `HELLO_ACK` received
- session established
- frame counters increasing

The endpoint is:

```text
ws://<HOME_ASSISTANT_LAN_IP>:7887/matrix-studio
```

Home Assistant's device count should move from zero to one.

## Sustained Wi-Fi / DMA acceptance test

A panel that displays one frame is not considered proven.

1. run real scenes at 24 FPS for at least **15-30 minutes**;
2. periodically press `i` and record reconnects, rejected frames, received /
   rendered frames and dropped frames;
3. watch for sparkles, tearing, freezes, colour corruption or resets;
4. check Home Assistant/add-on logs;
5. cautiously exercise brightness and establish a practical upper limit.

After streaming stability is established, perform one OTA update as a separate
acceptance test and verify that the device reconnects with the new
`HELLO.fw_version` and that rollback remains available.

Record final findings in [`docs/hardware.md`](../docs/hardware.md).

## What the firmware does

- connects to Wi-Fi as a station and reconnects indefinitely
- connects to the Matrix Studio WebSocket server
- performs Protocol v1 handshake and validation
- renders RGB565 frames through a latest-wins queue
- uses double-buffered HUB75 output
- separates display rendering and network work across ESP32-S3 cores
- detects PSRAM at runtime rather than assuming it
- exposes frame/reconnect/status counters
- accepts rollback-safe firmware updates over the existing Wi-Fi/WebSocket path

## Host tests

Protocol tests do not require ESP-IDF:

```sh
cd esp32/tests
cmake -B build
cmake --build build
ctest --test-dir build --output-on-failure
```

The real firmware build remains the authoritative compile check:

```sh
. ~/esp/esp-idf/export.sh
cd esp32
idf.py build
```

## Source layout

```text
esp32/
├── CMakeLists.txt
├── sdkconfig.defaults
├── partitions.csv             factory + dual OTA slots
├── main/
│   ├── board_config.h         panel GPIO/geometry/scan/driver settings
│   ├── ota_updater.*          ESP-IDF OTA write + rollback validation
│   ├── app_config.h
│   ├── wifi_secrets.h.example
│   ├── display/               HUB75 driver, queue, render task, diagnostics
│   ├── net/                   Wi-Fi + WebSocket client
│   └── protocol/              Protocol v1 parser/encoder
└── tests/                     host-side firmware protocol tests
```

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

Check that the Home Assistant add-on is running, port 7887 is published, the
firmware server host is the Pi's LAN IP, and both devices are on the same LAN.
