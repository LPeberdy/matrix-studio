# Matrix Studio

Matrix Studio turns a Home Assistant install and a 64x64 HUB75 LED matrix
into a wireless generative-art display. Home Assistant renders scenes —
plasma, particles, generative landscapes, or things driven by real house
state like weather, occupancy, or who's home — and streams them over Wi-Fi
to an ESP32-S3 controller that just needs to reliably turn frames into
light.

- **Home Assistant is the artist.** Scenes are small Python files that
  return a 64x64 image; add or edit one without touching the ESP32 at all.
- **The ESP32 is the appliance.** It joins Wi-Fi, connects to the Home
  Assistant add-on, and displays whatever frames arrive — reconnecting
  automatically and falling back to a quiet idle state if the network or
  the frames stop.
- **The protocol in between is small and frozen.** A persistent WebSocket
  connection carrying RGB565 frames, documented byte-for-byte in
  [`docs/protocol.md`](docs/protocol.md), with golden test fixtures both
  sides are tested against.

```
Home Assistant (Pi 5, HAOS)  ──Wi-Fi, Protocol v1──▶  ESP32-S3  ──HUB75──▶  64x64 P3 panel
        scenes + HA state                         reconnect + no-signal fallback
```

See [`docs/architecture.md`](docs/architecture.md) for the full picture.

## Repository layout

| Path | What |
|---|---|
| [`docs/`](docs/) | Architecture, the frozen protocol spec, hardware notes, dev guide |
| [`protocol/`](protocol/) | The wire contract: Python codec, C++ header, golden fixtures, contract tests |
| [`home-assistant/`](home-assistant/) | The Matrix Studio Home Assistant add-on (scene engine, renderer, ingress UI) |
| [`esp32/`](esp32/) | ESP32-S3 firmware (HUB75 DMA driver, Wi-Fi, protocol client) |

## Status

Early / MVP. See each subdirectory's README for its own setup and current
limitations, and `docs/hardware.md` for hardware assumptions that still need
verification against your specific board.

## License

MIT — see [`LICENSE`](LICENSE).
