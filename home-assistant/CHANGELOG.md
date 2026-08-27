# Changelog

## 0.1.5

- Synchronise ingress preview requests to actual engine frames instead of an independent 24 FPS browser timer.
- Raise ESP32 HUB75 scan cadence from about 76 Hz to about 287 Hz so network-frame swaps no longer produce perceptible periodic holds.
- Ship firmware 0.1.2 for the new panel scan cadence; the wire protocol remains v1.
- Preserve the panel's stored Wi-Fi credentials when installing a secret-free OTA release image.
- Add a bounded JSON-chunk OTA staging API for authenticated automation clients that cannot send multipart files.

## 0.1.4

- Remove plasma's periodic slow/fast beat by moving its interference field at one coherent velocity.
- Upgrade the untouched 0.1.2/0.1.3 starter plasma while preserving customized user scenes.
- Add a constant-speed `motion_test` scene for diagnosing end-to-end cadence.
- Show recent device send FPS, jitter, longest gap, and live skipped-frame counts.
- Raise the ingress preview to 24 FPS with native 64x64 images, serialized browser requests, and coalesced dedicated encoding.

## 0.1.3

- Add a Matrix Studio scene installation API for Home Assistant MCP and other ingress clients.
- Validate scene names and syntax, reload installed scenes immediately, and restore the previous working file when a replacement fails to import.
- Rewrite the Matrix Studio scene-authoring skill around the public scene/API contract rather than the implementation repository.
- Add this changelog so Home Assistant can show release notes in the app store.

## 0.1.2

- Add editable `plasma` and `glitch_life` starter scenes.
- Seed missing starter scene files into existing user scene directories without overwriting user files.
- Add tests for bundled user scenes and upgrade seeding.

## 0.1.1

- Add Home Assistant-to-ESP32 OTA firmware delivery through the existing Matrix Studio WebSocket connection.
- Fix FM6124 panel initialization by pinning the upstream driver fix.
- Lower first-bring-up brightness to 90/255.
- Require successful display initialization before accepting a newly installed OTA image.

## 0.1.0

- Initial Matrix Studio Home Assistant add-on and ESP32-S3 firmware.
- Stream 64x64 RGB565 generative scenes over Matrix Studio Protocol v1.
- Provide live preview, scene controls, Home Assistant state inputs, and user scene hot reload.
