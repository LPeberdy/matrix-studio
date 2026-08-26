# Matrix Studio Protocol v1

**Status: Protocol v1 frozen.**

This document is the single source of truth for how the Home Assistant side
("server") and ESP32-S3 side ("device") of Matrix Studio communicate. The
machine-readable equivalents are
[`protocol/matrix_studio_protocol.py`](../protocol/matrix_studio_protocol.py)
and [`protocol/matrix_studio_protocol.h`](../protocol/matrix_studio_protocol.h).
Implementations MUST NOT change message semantics, field layouts, lifecycle
rules, or message allocations unilaterally.

## 1. Transport

- One persistent **binary WebSocket** connection.
- The ESP32 is the WebSocket client and connects to
  `ws://<home-assistant-host>:7887/matrix-studio` by default.
- The Home Assistant add-on is the server.
- Each WebSocket binary message contains exactly one Matrix Studio protocol
  message: an 8-byte header followed by its payload.
- The device initiates the connection so no inbound connection, discovery or
  mDNS is required on the ESP32.

## 2. Message header

Every message starts with:

| Offset | Field | Type | Notes |
|---:|---|---|---|
| 0 | `magic` | `u8` | Always `0xA5` |
| 1 | `version` | `u8` | `1` |
| 2 | `type` | `u8` | Message type from §4 |
| 3 | `flags` | `u8` | Reserved, `0x00` in v1 |
| 4 | `length` | `u32 LE` | Payload bytes, excluding header |

All multi-byte integers are little-endian. `length` MUST NOT exceed
`MAX_PAYLOAD_BYTES = 65535`.

## 3. Connection lifecycle

1. Device opens the WebSocket connection.
2. Device sends `HELLO` within `HELLO_TIMEOUT_MS = 5000`.
3. Server validates the protocol version, panel dimensions and pixel format.
4. Server replies with `HELLO_ACK` when accepted.
5. The session is then established. Normal frame/control traffic and OTA
   traffic may use the same connection according to the rules below.
6. `FRAME.sequence` begins at zero for every new session and increments by one
   for every frame sent. Gaps are legal.

### 3.1 Heartbeat

- Either side MAY send `PING`.
- A recipient MUST reply with `PONG` carrying the same nonce.
- At most one `PING` may be outstanding per sender.
- An unanswered ping older than `PONG_TIMEOUT_MS = 10000` makes the connection
  dead.
- Active frame or OTA traffic is itself evidence of liveness.

### 3.2 Frame timeout / no-signal

If the device receives no `FRAME` for `FRAME_TIMEOUT_MS = 5000`, it enters a
local no-signal state without disconnecting. A later frame resumes rendering.
An explicit `BLANK(1)` takes precedence over the no-signal indicator.

No-signal behaviour is suspended during an active OTA transaction because the
server intentionally pauses frame streaming while updating firmware.

### 3.3 Reconnection

On WebSocket failure or Wi-Fi loss the device reconnects with exponential
backoff:

`1s, 2s, 4s, 8s, 16s, 30s, 30s, ...`

The maximum is `RECONNECT_MAX_BACKOFF_S = 30`. A new connection always begins
with a fresh `HELLO` and a new session.

### 3.4 Server restart / zero devices

A server restart is just a dropped connection to the device. The Home
Assistant add-on MUST remain healthy indefinitely with zero connected devices.

### 3.5 Malformed-message handling

Receivers validate messages in this order:

1. bad `magic`: close;
2. unsupported `version`: send `STATUS(ERR_UNSUPPORTED_VERSION)`, then close;
3. unknown non-extension type: send `STATUS(ERR_UNKNOWN_TYPE)`, continue;
4. `length > MAX_PAYLOAD_BYTES`: close without trusting the payload;
5. incorrect payload size/content: send `STATUS(ERR_MALFORMED_PAYLOAD)`, discard
   that message and continue;
6. well-formed message in the wrong direction/context: log and ignore.

Implementations must never read past the received WebSocket message or crash on
malformed input.

### 3.6 OTA lifecycle

OTA is a first-class Protocol v1 operation over the same WebSocket connection.
It is deliberately transactional rather than a remote shell or general-purpose
management channel.

1. Server pauses `FRAME` streaming for that device.
2. Server sends `OTA_BEGIN(image_size)`.
3. Device selects the inactive OTA partition and replies `STATUS(OK)` or an
   OTA error.
4. Server sends sequential `OTA_DATA(offset, data)` messages. Chunks are at
   most `OTA_MAX_CHUNK_BYTES = 4096` bytes. `offset` must equal the number of
   firmware bytes already accepted.
5. Device writes each chunk directly using ESP-IDF OTA APIs and replies with
   `STATUS(OK)` or an OTA error.
6. After exactly `image_size` bytes, server sends `OTA_COMMIT`.
7. Device finalizes and validates the ESP-IDF application image, selects the
   completed inactive partition for next boot, replies `STATUS(OK)`, then
   reboots.
8. A newly OTA-booted image remains pending until Matrix Studio completes its
   core startup path and marks it valid. ESP-IDF rollback returns to the prior
   image if validation fails.
9. If the connection disappears before commit, the device aborts the pending
   OTA transaction and keeps the current running firmware selected.

A device MUST NOT accept a second `OTA_BEGIN` while a transaction is active.
Normal `FRAME` messages received during OTA may be ignored. Heartbeats and
`STATUS` remain valid during the transaction.

## 4. Message types

| Value | Name | Direction |
|---:|---|---|
| `0x01` | `HELLO` | device -> server |
| `0x02` | `HELLO_ACK` | server -> device |
| `0x03` | `FRAME` | server -> device |
| `0x04` | `BRIGHTNESS` | server -> device |
| `0x05` | `BLANK` | server -> device |
| `0x06` | `PING` | either |
| `0x07` | `PONG` | either |
| `0x08` | `STATUS` | either |
| `0x09` | `OTA_BEGIN` | server -> device |
| `0x0A` | `OTA_DATA` | server -> device |
| `0x0B` | `OTA_COMMIT` | server -> device |

### 4.1 `HELLO` (`0x01`)

| Offset | Field | Type | Notes |
|---:|---|---|---|
| 0 | `protocol_version` | `u8` | `1` |
| 1 | `width` | `u16 LE` | `64` for current panel |
| 3 | `height` | `u16 LE` | `64` for current panel |
| 5 | `pixel_format` | `u8` | `0x01 = RGB565` |
| 6 | `device_id` | 16 bytes | UTF-8/ASCII, NUL-padded |
| 22 | `fw_version` | 16 bytes | UTF-8, NUL-padded |

Payload: 38 bytes.

### 4.2 `HELLO_ACK` (`0x02`)

| Offset | Field | Type |
|---:|---|---|
| 0 | `protocol_version` | `u8` |
| 1 | `frame_interval_hint_ms` | `u16 LE` |
| 3 | `server_time_unix` | `u32 LE` |

Payload: 7 bytes.

### 4.3 `FRAME` (`0x03`)

| Offset | Field | Type |
|---:|---|---|
| 0 | `sequence` | `u32 LE` |
| 4 | `timestamp_ms` | `u32 LE` |
| 8 | `width` | `u16 LE` |
| 10 | `height` | `u16 LE` |
| 12 | `pixel_format` | `u8` |
| 13 | `reserved` | `u8` |
| 14 | `pixels` | bytes |

Pixels are row-major, top-left origin, little-endian RGB565. Payload length is
`14 + width*height*2`; for 64×64, 8206 bytes.

### 4.4 `BRIGHTNESS` (`0x04`)

One `u8`, `0..255`.

### 4.5 `BLANK` (`0x05`)

One `u8`: `1` forces the panel dark, `0` resumes normal rendering.

### 4.6 `PING` (`0x06`)

One `u32 LE` nonce.

### 4.7 `PONG` (`0x07`)

One `u32 LE` nonce matching the `PING` being answered.

### 4.8 `STATUS` (`0x08`)

| Offset | Field | Type |
|---:|---|---|
| 0 | `code` | `u16 LE` |
| 2 | `message` | UTF-8 bytes to end of payload |

Status codes:

| Code | Name | Meaning |
|---:|---|---|
| `0x0000` | `OK` | Operation accepted/completed |
| `0x0001` | `ERR_UNSUPPORTED_VERSION` | Protocol version unsupported |
| `0x0002` | `ERR_UNKNOWN_TYPE` | Message type unknown |
| `0x0003` | `ERR_MALFORMED_PAYLOAD` | Payload invalid |
| `0x0004` | `ERR_DIMENSION_MISMATCH` | Frame/panel dimensions disagree |
| `0x0005` | `ERR_INTERNAL` | Internal implementation failure |
| `0x0006` | `ERR_OTA_STATE` | OTA transaction is in an invalid state/order |
| `0x0007` | `ERR_OTA_IMAGE` | OTA partition/image/write/finalization failure |

### 4.9 `OTA_BEGIN` (`0x09`)

| Offset | Field | Type |
|---:|---|---|
| 0 | `image_size` | `u32 LE` |

Payload: 4 bytes. `image_size` must be greater than zero and fit the inactive
OTA application partition.

### 4.10 `OTA_DATA` (`0x0A`)

| Offset | Field | Type |
|---:|---|---|
| 0 | `offset` | `u32 LE` |
| 4 | `data` | 1..4096 bytes |

Payload: `4 + len(data)`. Chunks are sequential and may not extend beyond the
size declared by `OTA_BEGIN`.

### 4.11 `OTA_COMMIT` (`0x0B`)

Empty payload. Valid only when the device has received exactly the declared
image size.

## 5. Extension mechanism

- `0x80..0xFE` remain reserved for future/vendor extensions.
- Unknown extension-range messages are non-fatal: reply
  `STATUS(ERR_UNKNOWN_TYPE)` and continue.
- `0xFF` is reserved and must not be allocated.
- Header `flags` remain reserved and zero in v1. A receiver may log unknown
  non-zero flags but must not close solely because of them.
- Changing an existing message's semantics/layout requires a deliberate
  protocol revision. New message types require an explicit allocation in this
  document before implementation.

## 6. Limits summary

| Constant | Value |
|---|---:|
| `PROTOCOL_VERSION` | `1` |
| `MAGIC` | `0xA5` |
| `HEADER_SIZE_BYTES` | `8` |
| `MAX_PAYLOAD_BYTES` | `65535` |
| `HELLO_TIMEOUT_MS` | `5000` |
| `PING_INTERVAL_MS` | `10000` |
| `PONG_TIMEOUT_MS` | `10000` |
| `FRAME_TIMEOUT_MS` | `5000` default |
| `RECONNECT_MAX_BACKOFF_S` | `30` |
| `OTA_MAX_CHUNK_BYTES` | `4096` |
| Default WebSocket port | `7887` |
| Default WebSocket path | `/matrix-studio` |

## 7. Firmware/partition requirement

A production Matrix Studio ESP32 image uses an OTA-capable partition layout
from the first wired flash: factory application + `ota_0` + `ota_1` +
`otadata`. Bootloader application rollback is enabled. This is required for
the OTA lifecycle in §3.6 to be real rather than merely representable on the
wire.

The current Hengantech/Seengreat ESP32-S3 controller is documented as the
ESP32-S3-WROOM-1-N16R8 variant (16 MB flash / 8 MB PSRAM), and the committed
partition table is sized for that hardware.

## 8. Wi-Fi/DMA operational risk

HUB75 DMA activity may interfere with ESP32-S3 Wi-Fi on some hardware. Protocol
v1 relies on heartbeat, timeout and reconnect behaviour rather than changing
transport pre-emptively. Real-hardware bring-up must validate sustained
24 FPS streaming and reconnect counters.

## 9. Golden fixtures and tests

`protocol/fixtures/` contains byte-exact fixtures for the primary connection
and rendering messages plus malformed cases. Protocol tests additionally
round-trip the OTA messages. Both the Python reference implementation and the
ESP32 host parser must agree on the same constants and layouts.
