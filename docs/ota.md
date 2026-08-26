# OTA firmware extension

This document defines the Matrix Studio OTA bootstrap carried inside the
extension range reserved by `docs/protocol.md` §5. The core Protocol v1
contract remains frozen and unchanged.

The purpose of this extension is deliberately narrow: after the first wired
flash, routine firmware updates can be delivered over the device's existing
Matrix Studio WebSocket connection. It is not a remote shell or a generic
management protocol.

## Transport and trust model

OTA messages use the same persistent binary WebSocket connection as normal
Matrix Studio traffic. The ESP32 remains the client and only accepts OTA data
from the Home Assistant server it was configured to connect to.

The current `ws://` transport does not provide cryptographic server identity.
This OTA bootstrap therefore has the same LAN trust boundary as the existing
frame/control connection. ESP-IDF validates the completed application image
before it is selected for boot, and rollback protects against an image that
cannot complete startup. Signed firmware / secure boot would be a separate,
explicit hardening project because enabling those features can involve
irreversible eFuse changes.

## Partition requirement

The ESP32 must be flashed initially with an OTA-capable partition table:

- `factory` — the first USB-flashed Matrix Studio image
- `ota_0` — first OTA slot
- `ota_1` — second OTA slot
- `otadata` — boot-selection state

Matrix Studio targets the documented 16 MB flash on the Seengreat/Hengantech
ESP32-S3-WROOM-1-N16R8 controller. Each app slot is 3 MB, comfortably above
the current firmware size while leaving substantial flash unused for future
storage needs.

`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` is enabled. A newly selected OTA image
boots in pending-verification state and is marked valid only after Matrix
Studio completes its core startup path. If that startup fails or the device
reboots before validation, ESP-IDF rolls back to the previous working image.

## Message types

All messages use the normal 8-byte Protocol v1 header. Multi-byte integers are
little-endian, as in the core protocol.

| Value | Name | Direction |
|---:|---|---|
| `0x80` | `OTA_BEGIN` | server -> device |
| `0x81` | `OTA_DATA` | server -> device |
| `0x82` | `OTA_COMMIT` | server -> device |

Older Protocol v1 implementations already ignore unknown values in this
extension range, so these messages do not alter the frozen core layouts.

### OTA_BEGIN (`0x80`)

Payload:

| Offset | Field | Type |
|---:|---|---|
| 0 | `image_size` | `u32 LE` |

`image_size` must be greater than zero and fit in the selected inactive OTA
partition. The device calls `esp_ota_begin()` and replies with `STATUS(OK)` on
success. Starting another update while one is active is rejected.

### OTA_DATA (`0x81`)

Payload:

| Offset | Field | Type |
|---:|---|---|
| 0 | `offset` | `u32 LE` |
| 4 | `data` | 1..4096 bytes |

Chunks must be sequential. `offset` must equal the number of bytes already
written. A chunk that exceeds the declared image size, arrives out of order,
or exceeds 4096 bytes is rejected. TCP/WebSocket ordering means no reassembly
or retransmission layer is added here.

The server should pause normal `FRAME` streaming while an OTA transfer is in
progress; OTA is an administrative operation, not a concurrent rendering
workload.

### OTA_COMMIT (`0x82`)

Payload length is zero.

The device requires exactly `image_size` bytes to have been written. It then:

1. calls `esp_ota_end()` to validate/finalize the image;
2. calls `esp_ota_set_boot_partition()` for the completed inactive slot;
3. queues `STATUS(OK)`;
4. reboots after giving the outbound status a short opportunity to leave.

If the connection is lost before commit, the in-progress OTA handle is aborted
and the current running firmware remains selected.

## Recovery

OTA is a convenience path, not the only recovery path. The controller's USB
programming port and ESP-IDF flashing flow remain available if both OTA slots
are unusable or the firmware cannot reach Wi-Fi.
