# Protocol v1 — notes from the ESP32 implementation

Protocol v1 is frozen. Nothing in this directory patches around it, and this
file changes no wire behaviour — it records places where the device
implementation had to make a judgement call that `docs/protocol.md` does not
settle, so the parent agent can decide whether a future revision should say
something explicit.

**None of these blocked the implementation.** They are all "the spec permits
two readings and I picked one"; each entry says which one and why.

Ordered roughly by how likely they are to cause a real interop bug.

---

## 1. §3.5 does not name the "well-framed but short" case

**What the spec says.** §3.5 rule 5 covers a payload whose *actual size* does
not match the size implied by its type, and asks for
`STATUS(ERR_MALFORMED_PAYLOAD)` plus discarding that one message. Rule 4 covers
a `length` above `MAX_PAYLOAD_BYTES`, and asks for a connection close.

**The gap.** The `truncated_frame.bin` fixture is neither: `length` is a legal
8206, but only 100 payload bytes follow. On a byte-stream transport that just
means "read more". Over WebSocket, where §1 guarantees one WebSocket message
per protocol message, it means the message is genuinely malformed.

**What this implementation does.** Reports it as its own parse result
(`ParseResult::kTruncatedPayload`), and treats it exactly like rule 5:
`STATUS(ERR_MALFORMED_PAYLOAD)`, discard that message, keep the connection.
That follows §3.5's stated principle — "corruption of the framing itself is
fatal; a malformed but well-framed individual message is logged and discarded"
— and a short WebSocket message is not framing corruption.

**Suggested clarification.** A sentence in §3.5 saying that on a
message-oriented transport, `length` disagreeing with the received message size
is handled under rule 5.

---

## 2. Extension-range types: §3.5 rule 3 and §5 disagree about whether to reply

**What the spec says.**

- §3.5 rule 3: `type` is valid if it is a known type *or within the reserved
  extension range*; otherwise `STATUS(ERR_UNKNOWN_TYPE)` and continue. Read
  literally, an extension-range type *passes* validation and gets no STATUS.
- §5: "Receivers that do not recognize a type in this range MUST ignore that
  single message". Read literally, "ignore" means no reply at all.

So one section implies extension types are accepted silently and the other that
they are ignored silently — but neither clearly asks for a STATUS, while the
natural implementation of "unknown type" does send one.

**What this implementation does.** Sends `STATUS(ERR_UNKNOWN_TYPE)` and
continues, for both extension-range and out-of-range unknown types. The two
cases are distinguished internally (`kExtensionType` vs `kUnknownType`) and
logged differently, so switching to silent-ignore for the extension range is a
one-line change in `status_code_for()` if that is the intent.

**Why this way.** Sending an informational STATUS while still ignoring the
message satisfies "ignore that single message rather than treating it as
fatal", and it makes a misconfigured server visible rather than silent. But a
future server that legitimately broadcasts extension messages to a mixed fleet
will get a STATUS back from every v1 device, which may not be wanted.

**Suggested clarification.** State explicitly whether extension-range types
should be answered with STATUS or dropped silently.

---

## 3. `flags` must be zero (§2) but §3.5 never checks it

**What the spec says.** §2: `flags` is "Reserved, must be `0x00` in v1". §5:
"future versions may define bits here without changing the header layout". The
§3.5 validation sequence does not mention `flags` at all.

**What this implementation does.** Parses the message normally, records the
flags, and logs a warning if any bit is set. It does **not** reject the
message.

**Why this way.** Rejecting would defeat the forward-compatibility §5 is
setting up, and §3.5 is meant to be the exhaustive list of validation steps —
adding a rejection rule that is not in that list would make this device stricter
than the reference implementation for no stated reason.

---

## 4. `HELLO_ACK` is not required to echo a *supported* version

§4.2 says `HELLO_ACK.protocol_version` "echoes the accepted version", and §3
step 3 says an unsupported version gets `STATUS(ERR_UNSUPPORTED_VERSION)`
instead. There is no stated rule for what a device should do if `HELLO_ACK`
arrives carrying a version other than the one it sent.

Since the *header* version of that same message is already validated by §3.5
rule 2 (and would be fatal if wrong), the payload field is redundant in
practice. This implementation logs the value and does not act on it.

---

## 5. Nothing specifies a maximum WebSocket message the device must buffer

`MAX_PAYLOAD_BYTES` is 65535, so a conforming server may send a 65543-byte
message. A 64x64 device only ever needs 8214 bytes, and internal SRAM on an
ESP32-S3 is not free.

This implementation sizes its receive buffer to the full 65543 bytes when PSRAM
is present, and to panel-size-plus-slack (8726 bytes) when it is not. In the
latter case an over-large but legal message is dropped with
`STATUS(ERR_MALFORMED_PAYLOAD)` and a log line, rather than the connection being
dropped. This only matters if a future server sends large non-FRAME messages to
a device without PSRAM.

---

## 6. Observation, not a problem: `HELLO.device_id` has no uniqueness rule

§4.1 says "Stable device identifier (e.g. derived from MAC address)". This
implementation uses `ms-<12 hex digits of the station MAC>` (14 bytes, fits the
16-byte field). Worth noting only because a future multi-device server will
need to key on this, and the spec does not currently promise it is unique or
stable across a firmware reflash. As implemented here it is both.
