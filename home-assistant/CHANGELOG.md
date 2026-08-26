# Changelog

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
