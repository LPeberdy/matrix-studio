"""Matrix Studio — the Home Assistant half of the Matrix Studio LED system.

Renders generative scenes at a fixed cadence and streams them as Protocol v1
`FRAME` messages to one or more ESP32-S3 HUB75 controllers over WebSocket.

Entry points:
    python -m matrix_studio            run the add-on (Supervisor entrypoint)
    python -m matrix_studio.preview     render/preview scenes with no hardware
"""

__version__ = "0.1.0"
