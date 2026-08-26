#!/usr/bin/env python3
"""Re-sync the vendored Protocol v1 codec from the canonical monorepo copy.

The add-on ships a verbatim copy of `protocol/matrix_studio_protocol.py` inside
`matrix_studio/vendor/` because the Supervisor builds the add-on image with
`home-assistant/` as the Docker build context.

Usage:
    python3 home-assistant/tools/sync_protocol.py [--check]

`--check` exits non-zero if the vendored copy differs, without writing.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys

HA_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = HA_DIR.parent
CANONICAL = REPO_ROOT / "protocol" / "matrix_studio_protocol.py"
VENDORED = HA_DIR / "matrix_studio" / "vendor" / "matrix_studio_protocol.py"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only verify, do not write")
    args = parser.parse_args(argv)

    if not CANONICAL.exists():
        print(f"canonical protocol module not found: {CANONICAL}", file=sys.stderr)
        return 2

    canonical_bytes = CANONICAL.read_bytes()
    vendored_bytes = VENDORED.read_bytes() if VENDORED.exists() else None

    if canonical_bytes == vendored_bytes:
        print(f"vendored copy is up to date ({VENDORED.relative_to(REPO_ROOT)})")
        return 0

    if args.check:
        print(
            f"vendored copy is STALE: {VENDORED.relative_to(REPO_ROOT)} differs from "
            f"{CANONICAL.relative_to(REPO_ROOT)}; run tools/sync_protocol.py",
            file=sys.stderr,
        )
        return 1

    VENDORED.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CANONICAL, VENDORED)
    print(f"synced {CANONICAL.relative_to(REPO_ROOT)} -> {VENDORED.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
