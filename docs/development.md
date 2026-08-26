# Development

## Repository layout

```
matrix-studio/
├── docs/                 architecture, protocol, hardware, this file
├── protocol/             frozen wire contract: Python codec, C++ header, golden fixtures, contract tests
├── home-assistant/       the Matrix Studio Home Assistant add-on (Python)
├── esp32/                ESP32-S3 firmware (PlatformIO/Arduino + HUB75 DMA)
└── tools/                misc scripts shared by both sides
```

## Protocol

The wire contract is frozen (`docs/protocol.md`). If you find a genuine
problem with it, do not patch around it silently in one implementation —
open an issue describing the mismatch; the protocol doc and both sides'
fixtures need to move together.

Regenerate fixtures after any (rare, deliberate) protocol change:

```sh
python3 protocol/fixtures/generate_fixtures.py
```

## Home Assistant add-on

```sh
cd home-assistant
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

See `home-assistant/README.md` for add-on installation, scene authoring, and
emulator/preview usage.

## End-to-end integration tests

`tests/integration/` starts the real Home Assistant add-on and drives it over
an actual WebSocket connection using the *canonical* protocol codec
(`protocol/matrix_studio_protocol.py`), not the add-on's internal vendored
copy — proving the two independently-built sides of the frozen contract
actually agree on the wire, not just that each is internally self-consistent.

```sh
python3 -m venv .venv && .venv/bin/pip install -r tests/integration/requirements-dev.txt
.venv/bin/python -m pytest tests/integration -q
```

## ESP32 firmware

See `esp32/README.md` for toolchain setup, building, flashing, and Wi-Fi
configuration. Host-side protocol-parsing unit tests can be run without any
hardware; see `esp32/tests/`.

## Continuous integration

`.github/workflows/ci.yml` runs, on every push/PR:
- protocol contract tests (`protocol/tests/`)
- Home Assistant add-on tests (`home-assistant/tests/`)
- ESP32 host-side protocol parser tests
- ESP32 firmware compilation (PlatformIO, no hardware required)
