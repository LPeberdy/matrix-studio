# Matrix Studio Protocol v1

**Status: Protocol v1 frozen.**

This document is the single source of truth for how the Home Assistant side
("server") and the ESP32-S3 side ("device") of Matrix Studio talk to each
other. Once frozen, implementation agents on either side MUST NOT change
message semantics, field layouts, or lifecycle rules described here. If a
genuine contract problem is discovered, it must be reported back rather than
patched unilaterally — see `docs/architecture.md` for the change process.

Machine-readable copies of the constants in this document live in
[`protocol/matrix_studio_protocol.py`](../protocol/matrix_studio_protocol.py)
(Python, used by the Home Assistant side and to generate fixtures) and
[`protocol/matrix_studio_protocol.h`](../protocol/matrix_studio_protocol.h)
(C++ header, used by the ESP32 firmware). Golden binary test vectors live in
[`protocol/fixtures/`](../protocol/fixtures/) with a manifest describing the
expected parse of each one — both sides must pass tests against these
fixtures.

## 1. Transport

- The connection is a single persistent **WebSocket** connection carrying
  **binary** frames, one WebSocket message per protocol message.
- **The ESP32 device is the WebSocket client.** It connects out to
  `ws://<home-assistant-host>:7887/matrix-studio` (host, port, and path are
  configurable on the device side; 7887 is the default). The Home Assistant
  add-on is the WebSocket **server** and listens on that port.
- Rationale: a device-initiates-outbound model needs no discovery/mDNS in the
  MVP, works through typical home NAT/AP setups without inbound firewall
  rules on the HA host, and lets the device apply a simple reconnect loop
  against a fixed address. The server can serve multiple device connections
  concurrently in the future without protocol changes.
- Each WebSocket binary message is exactly one protocol message: an 8-byte
  header (§2) followed by that message's payload. The header's `length`
  field is redundant with the WebSocket frame length but is kept so that
  fixtures and validation logic are transport-agnostic (a future raw-TCP or
  file-replay transport could reuse the same parser).

## 2. Message header

Every message, in both directions, starts with this fixed 8-byte header:

| Offset | Field     | Type   | Notes                                        |
|-------:|-----------|--------|-----------------------------------------------|
| 0      | `magic`   | u8     | Always `0xA5`                                  |
| 1      | `version` | u8     | Protocol version. `1` for this document.       |
| 2      | `type`    | u8     | Message type, see §4                           |
| 3      | `flags`   | u8     | Reserved, must be `0x00` in v1                  |
| 4      | `length`  | u32 LE | Payload length in bytes, **not** including header |

**Byte ordering: all multi-byte integer fields in this protocol, in the
header and in every payload, are little-endian.** This matches the native
endianness of both the ESP32-S3 (Xtensa LX7, little-endian) and the Raspberry
Pi (aarch64, little-endian), avoiding byte-swapping on both ends.

Total message size = 8 + `length` bytes. `length` MUST NOT exceed
`MAX_PAYLOAD_BYTES = 65535`. A frame payload for the 64x64 RGB565 MVP is
exactly 8192 bytes.

## 3. Connection lifecycle

1. Device opens the WebSocket connection to the server.
2. Device MUST send `HELLO` (§4.1) within `HELLO_TIMEOUT_MS = 5000` of the
   socket opening. If it does not, the server closes the connection.
3. Server validates `HELLO.protocol_version`:
   - If unsupported, server sends `STATUS` (§4.8) with
     `code = ERR_UNSUPPORTED_VERSION`, then closes the connection.
   - If supported, server replies with `HELLO_ACK` (§4.2).
4. After `HELLO_ACK`, the server may begin streaming `FRAME` (§4.3) messages,
   and either side may send `BRIGHTNESS` (§4.4), `BLANK` (§4.5), `PING`/`PONG`
   (§4.6/§4.7), or `STATUS` (§4.8) at any time.
5. Frame cadence is **not guaranteed** — the server sends frames as fast as
   the active scene renders (target 20-30 FPS), and may pause entirely (e.g.
   scene error, no active scene). The device must not treat a gap in frames
   as a protocol error by itself; see §3.2 (frame timeout).
6. `FRAME.sequence` starts at 0 for each new connection/session and
   increments by 1 for every frame sent on that connection. Gaps are
   possible if the server intentionally drops a stale frame (e.g. it is
   behind); the device does not need to detect gaps, only render what it
   receives.

### 3.1 Heartbeat

- Either side MAY send `PING` at any time (recommended: server every
  `PING_INTERVAL_MS = 10000` while idle, i.e. when not actively streaming
  frames — an in-flight frame stream is itself evidence of liveness).
- The recipient of a `PING` MUST reply with `PONG` carrying the same
  `nonce`, as soon as possible.
- If a `PING` is not answered within `PONG_TIMEOUT_MS = 10000`, the sender
  MUST treat the connection as dead: close it and (if it is the device)
  begin the reconnect procedure in §3.3.

### 3.2 Frame timeout / no-signal behaviour

- The device tracks the time since the last `FRAME` it received.
- If no `FRAME` arrives for `FRAME_TIMEOUT_MS = 5000` (configurable), the
  device enters a **local "no signal" state**: it should stop trying to
  interpret stale pixel data and show a quiet fallback (e.g. blank panel or
  a small idle indicator), without closing the WebSocket connection. Normal
  rendering resumes automatically on the next `FRAME`.
- This is deliberately decoupled from the heartbeat/connection-liveness
  logic in §3.1: a connection can be alive with no frames (idle scene), and
  the device should not reconnect just because frames paused.

### 3.3 Reconnection

- If the WebSocket connection closes or fails for any reason (explicit
  close, TCP error, failed heartbeat, Wi-Fi loss), the device reconnects
  using exponential backoff: `1s, 2s, 4s, 8s, 16s, 30s, 30s, ...` (capped at
  `RECONNECT_MAX_BACKOFF_S = 30`).
- On Wi-Fi loss specifically, the device first re-establishes Wi-Fi, then
  applies the same backoff to the WebSocket connection.
- Every new connection is a new session: `FRAME.sequence` resets to 0, and
  the device must send a fresh `HELLO`.
- The server does not need to persist any per-device session state across
  reconnects in v1; each connection is handshaked from scratch.

### 3.4 Server restart / disappearance

- From the device's point of view, a server restart looks like a dropped
  connection; the device applies §3.3 unconditionally, with no special case.
- The Home Assistant add-on must survive having zero connected devices
  indefinitely (i.e. the device being unplugged, unflashed, or never
  connected is a normal, healthy state — not an error state for the server).

### 3.5 Malformed-message handling

On receipt of any message, the receiver validates, in order:

1. `magic == 0xA5` — else discard the message, log it, and close the
   connection (a bad magic byte means the stream is desynchronized and
   cannot be trusted further).
2. `version` matches the version negotiated at `HELLO`/`HELLO_ACK` — else
   send `STATUS(ERR_UNSUPPORTED_VERSION)` and close.
3. `type` is a known message type (or within the reserved extension range,
   §5) — else send `STATUS(ERR_UNKNOWN_TYPE)` and continue (do not close;
   unknown types in the extension range are expected to be safely ignorable
   in the future).
4. `length` does not exceed `MAX_PAYLOAD_BYTES` — else close the connection
   without attempting to read the declared payload.
5. For message types with a fixed or computable payload size (e.g. `FRAME`
   implies `width * height * bytes_per_pixel`), the actual payload size
   must match — else send `STATUS(ERR_MALFORMED_PAYLOAD)` and discard just
   that message (do not close the connection; a single bad frame should not
   drop an otherwise-healthy session).

In short: corruption of the framing itself (magic/version) is fatal to the
connection; a malformed but well-framed individual message is logged and
discarded. Implementations must never crash on malformed input — this is
covered by the "malformed header" / "truncated frame" fixtures.

## 4. Message types

`type` values:

| Value  | Name          | Direction        |
|--------|---------------|------------------|
| `0x01` | `HELLO`       | device -> server |
| `0x02` | `HELLO_ACK`   | server -> device |
| `0x03` | `FRAME`       | server -> device |
| `0x04` | `BRIGHTNESS`  | server -> device |
| `0x05` | `BLANK`       | server -> device |
| `0x06` | `PING`        | either           |
| `0x07` | `PONG`        | either           |
| `0x08` | `STATUS`      | either           |

### 4.1 `HELLO` (0x01, device -> server)

| Offset | Field              | Type      | Notes                              |
|-------:|--------------------|-----------|-------------------------------------|
| 0      | `protocol_version` | u8        | `1`                                  |
| 1      | `width`            | u16 LE    | Panel width in pixels, `64` for MVP  |
| 3      | `height`           | u16 LE    | Panel height in pixels, `64` for MVP |
| 5      | `pixel_format`     | u8        | `0x01 = RGB565` (only defined value) |
| 6      | `device_id`        | 16 bytes  | Stable device identifier (e.g. derived from MAC address), ASCII/UTF-8, NUL-padded |
| 22     | `fw_version`       | 16 bytes  | Firmware version string, UTF-8, NUL-padded |

Payload length: 38 bytes.

### 4.2 `HELLO_ACK` (0x02, server -> device)

| Offset | Field              | Type   | Notes                                     |
|-------:|--------------------|--------|--------------------------------------------|
| 0      | `protocol_version` | u8     | Echoes the accepted version                |
| 1      | `frame_interval_hint_ms` | u16 LE | Advisory only; device is not required to enforce it |
| 3      | `server_time_unix` | u32 LE | Seconds since epoch, for device RTC sync (best-effort; `0` if unknown) |

Payload length: 7 bytes.

### 4.3 `FRAME` (0x03, server -> device)

| Offset | Field          | Type     | Notes                                  |
|-------:|----------------|----------|------------------------------------------|
| 0      | `sequence`     | u32 LE   | Monotonically increasing per session      |
| 4      | `timestamp_ms` | u32 LE   | Optional; milliseconds since server start, `0` if unused |
| 8      | `width`        | u16 LE   | Must match `HELLO.width`                  |
| 10     | `height`       | u16 LE   | Must match `HELLO.height`                 |
| 12     | `pixel_format` | u8       | `0x01 = RGB565`                           |
| 13     | `reserved`     | u8       | `0x00`                                    |
| 14     | `pixels`       | bytes    | `width * height * 2` bytes, row-major, top-left origin, each pixel a little-endian RGB565 u16 |

Payload length: `14 + width*height*2`. For the 64x64 MVP: `14 + 8192 = 8206`
bytes.

RGB565 packing per pixel (16 bits): `RRRRR GGGGGG BBBBB` (5 red / 6 green / 5
blue bits), stored as a little-endian `u16`.

### 4.4 `BRIGHTNESS` (0x04, server -> device)

| Offset | Field        | Type | Notes                       |
|-------:|--------------|------|------------------------------|
| 0      | `brightness` | u8   | `0` (off) .. `255` (max)     |

Payload length: 1 byte. The device applies this as a global panel brightness
scaler; it does not affect the pixel values it receives.

### 4.5 `BLANK` (0x05, server -> device)

| Offset | Field   | Type | Notes                                    |
|-------:|---------|------|--------------------------------------------|
| 0      | `blank` | u8   | `1` = force display off/blank, `0` = resume normal rendering |

Payload length: 1 byte. Distinct from §3.2's no-signal state: `BLANK` is an
explicit server command (e.g. "night mode"), while no-signal is the device's
own fallback when frames stop arriving.

### 4.6 `PING` (0x06, either direction)

| Offset | Field   | Type   | Notes                          |
|-------:|---------|--------|----------------------------------|
| 0      | `nonce` | u32 LE | Arbitrary value, echoed in `PONG` |

Payload length: 4 bytes.

### 4.7 `PONG` (0x07, either direction)

| Offset | Field   | Type   | Notes                        |
|-------:|---------|--------|--------------------------------|
| 0      | `nonce` | u32 LE | Must equal the `PING` it answers |

Payload length: 4 bytes.

### 4.8 `STATUS` (0x08, either direction)

| Offset | Field     | Type   | Notes                                   |
|-------:|-----------|--------|--------------------------------------------|
| 0      | `code`    | u16 LE | See status codes table below               |
| 2      | `message` | bytes  | UTF-8, remainder of payload, not NUL-terminated |

Payload length: `2 + len(message)`. `message` may be zero-length.

Status codes:

| Code     | Name                      | Meaning                                  |
|---------:|---------------------------|--------------------------------------------|
| `0x0000` | `OK`                      | Informational, non-error status            |
| `0x0001` | `ERR_UNSUPPORTED_VERSION` | Sender's protocol version is not supported |
| `0x0002` | `ERR_UNKNOWN_TYPE`        | Message type not recognized                |
| `0x0003` | `ERR_MALFORMED_PAYLOAD`   | Payload length/content invalid for its type|
| `0x0004` | `ERR_DIMENSION_MISMATCH`  | `FRAME` dimensions don't match `HELLO`     |
| `0x0005` | `ERR_INTERNAL`            | Sender-side internal error, connection may continue |

## 5. Extension mechanism

- `type` values `0x80`-`0xFE` are reserved for future/vendor extensions.
  Receivers that do not recognize a type in this range MUST ignore that
  single message (per §3.5 rule 3) rather than treating it as fatal.
- `type` value `0xFF` is reserved (do not use).
- `flags` (header byte 3) is reserved and must be `0` in v1; future versions
  may define bits here without changing the header layout.
- New fields are always added at the **end** of an existing message's
  payload in future protocol versions; existing field offsets never change
  within a version. A receiver on an older version simply has a shorter
  `length` and ignores fields it doesn't know about, as governed by that
  future version's own compatibility notes.
- Adding an entirely new message type does not require a version bump;
  changing the meaning or layout of an existing message type does.

## 6. Limits summary

| Constant                     | Value    |
|-------------------------------|---------|
| `PROTOCOL_VERSION`             | 1       |
| `MAGIC`                        | `0xA5`  |
| `HEADER_SIZE_BYTES`            | 8       |
| `MAX_PAYLOAD_BYTES`            | 65535   |
| `HELLO_TIMEOUT_MS`             | 5000    |
| `PING_INTERVAL_MS`             | 10000   |
| `PONG_TIMEOUT_MS`              | 10000   |
| `FRAME_TIMEOUT_MS` (default)   | 5000    |
| `RECONNECT_MAX_BACKOFF_S`      | 30      |
| Default WebSocket port         | 7887    |
| Default WebSocket path         | `/matrix-studio` |

## 7. Known operational risk: Wi-Fi/DMA interference

Hardware research (`docs/hardware.md`) found a documented risk that the
HUB75 DMA engine's high-frequency output can disturb the ESP32-S3's Wi-Fi
radio on some boards, surfacing as connection stalls. This protocol was
deliberately **not** redesigned around UDP to pre-empt that risk: the
existing heartbeat/timeout/reconnect rules in §3.1 and §3.3 already turn a
stalled connection into an automatic reconnect rather than a wedged device,
which is an adequate mitigation for v1. If real-hardware testing shows this
is insufficient, that is a candidate reason for a deliberate, parent-agent-
reviewed protocol revision later — not something either side should route
around unilaterally.

## 8. Golden fixtures

See [`protocol/fixtures/`](../protocol/fixtures/) and its `manifest.json` for
byte-exact test vectors covering: valid `HELLO`, valid 64x64 `FRAME`, valid
`BRIGHTNESS`, valid `PING`, a malformed header (bad magic), an unsupported
protocol version, and a truncated frame payload. Both implementations'
protocol test suites must load and assert against these fixtures so that a
change to one side's parser that silently diverges from the other is caught
immediately.
