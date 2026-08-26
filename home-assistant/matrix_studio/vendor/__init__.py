"""Vendored, byte-identical copies of shared monorepo modules.

`matrix_studio_protocol.py` in this package is a verbatim copy of
`protocol/matrix_studio_protocol.py` from the repository root. It is vendored
(rather than symlinked or imported by relative path) because a Home Assistant
Supervisor add-on is built with **the add-on directory as the Docker build
context** — nothing outside `home-assistant/` exists at image build time.

Never hand-edit the vendored copy. Re-sync it with:

    python3 home-assistant/tools/sync_protocol.py

`tests/test_protocol_contract.py` fails the build if the vendored copy ever
drifts from the canonical one, so the frozen wire contract cannot silently
diverge.
"""
