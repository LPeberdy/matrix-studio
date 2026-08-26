# Protocol v1 — observations from the Home Assistant implementation

Written by the Home Assistant-side implementation. Per the change process in
`docs/architecture.md` / `docs/protocol.md`, nothing under `docs/protocol.md`,
`protocol/matrix_studio_protocol.py`, `protocol/matrix_studio_protocol.h` or
`protocol/fixtures/` was modified. Everything below is implemented **as
documented**; this file records what had to be decided locally and what the
protocol owner may want to clarify.

**None of these blocked the implementation.** No defect was found in the codec
or the fixtures — the add-on passes against all seven golden vectors.

---

## 1. `BLANK` and the §3.2 "no signal" state overlap (interop ambiguity)

**Where:** §3.2 vs §4.5.

§4.5 defines `BLANK(1)` as an explicit server command to turn the display off.
§3.2 says the device enters its own local "no signal" state after
`FRAME_TIMEOUT_MS` (5 s) without a `FRAME`, showing "a quiet fallback (e.g.
blank panel or a small idle indicator)".

The natural server implementation of `BLANK(1)` is to stop rendering and stop
sending frames (there is no point burning CPU on invisible frames). But then,
5 s later, the device *also* enters no-signal state. If a firmware chooses "a
small idle indicator" as its no-signal fallback, an explicitly blanked panel
will light up an indicator 5 s after being told to go dark — the opposite of
what the user asked for.

**What this side does:** on `BLANK(1)` the engine pauses rendering and the
server sends no further `FRAME`s until unblanked, then sends `BLANK(0)` and
resumes. Frames are never sent while blanked.

**Suggested clarification (no wire change needed):** state that `BLANK(1)`
takes precedence over the no-signal fallback, and that a device must not show
an idle indicator while blanked. Alternatively state that the server must keep
streaming black frames while blanked — but that wastes ~200 KB/s to no end.

## 2. `PING_INTERVAL_MS` equals `PONG_TIMEOUT_MS` (boundary race)

**Where:** §3.1 and §6 — both are `10000`.

A straightforward reading ("ping every 10 s while idle; if a ping is not
answered within 10 s, the connection is dead") puts the deadline for ping *N*
at exactly the moment ping *N+1* is due. An implementation that sends on a
fixed 10 s timer and evaluates the timeout on the same tick can declare a
healthy-but-slightly-slow link dead, or send a second ping and then be unable
to tell which one a late `PONG` answers (nonces make the latter recoverable,
but only if both are tracked).

**What this side does:** at most one ping is outstanding at a time. On each
idle interval, if a ping is already outstanding, its age is checked against
`PONG_TIMEOUT_MS` and no second ping is sent; the connection is closed only
once the deadline is strictly exceeded. A `PONG` whose nonce doesn't match the
outstanding ping is ignored (but still counts as traffic).

**Suggested clarification:** either say explicitly that only one ping may be
outstanding, or make `PONG_TIMEOUT_MS` shorter than `PING_INTERVAL_MS`.

## 3. `MAX_PAYLOAD_BYTES` caps future panel sizes at ~64x128

**Where:** §2 / §6.

`MAX_PAYLOAD_BYTES = 65535` with a `FRAME` payload of `14 + width*height*2`
means the largest transmittable frame is 32760 pixels — 64x64 and 128x128 fit
comfortably, but e.g. 256x128 (65536 pixel bytes) does not. The header's
`length` field is a `u32`, so the limit is purely the stated constant, not the
layout.

This is not a v1 problem (the MVP is 64x64) and does not need fixing now, but
it is worth recording so that a future "chained panels" version knows it needs
a version bump or a tiling/multi-message scheme rather than just a bigger
number.

**What this side does:** nothing — the server renders 64x64 and rejects a
`HELLO` declaring any other size with `STATUS(ERR_DIMENSION_MISMATCH)`.

## 4. Minor: behaviour on a duplicate `HELLO` mid-session is unspecified

**Where:** §3.

§3.3 says "every new connection is a new session" and each connection is
handshaked from scratch, but nothing says what to do if a device sends a second
`HELLO` on an already-handshaked connection. It is neither an unknown type nor
a malformed payload, so §3.5 doesn't cover it.

**What this side does:** logs a warning and ignores it; the session continues
with the parameters from the first `HELLO`. Same for a `FRAME` / `BRIGHTNESS` /
`BLANK` / `HELLO_ACK` arriving device→server (wrong direction) — logged and
ignored, connection kept, since §3.5 reserves closing for framing corruption.

## 5. Note: `FRAME.timestamp_ms` wraps after ~49.7 days

**Where:** §4.3.

`timestamp_ms` is a `u32` of "milliseconds since server start", which wraps
after 2^32 ms ≈ 49.7 days — plausible uptime for an always-on Home Assistant
box. The field is explicitly advisory ("Optional; ... `0` if unused"), so this
is only a hazard for a device that computes deltas from it.

**What this side does:** masks the value to 32 bits (`& 0xFFFFFFFF`), i.e.
wraps rather than saturating or resetting. Firmware should not assume
monotonicity of this field; `sequence` is the reliable ordering signal.
