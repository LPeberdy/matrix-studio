"""`python -m matrix_studio` — the add-on's entrypoint (see run.sh)."""
from __future__ import annotations

import asyncio

from .app import run


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        pass


if __name__ == "__main__":
    main()
