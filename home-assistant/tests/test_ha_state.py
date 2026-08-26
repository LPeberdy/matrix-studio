"""HA state adapter, against a stand-in for the Supervisor's Core API proxy.

The fake server below speaks the same `/api/states` shape Home Assistant does,
so the adapter is exercised over a real HTTP connection (auth header included)
rather than by monkeypatching its internals.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import web

from matrix_studio.engine import SceneEngine
from matrix_studio.ha_state import HaStateAdapter
from matrix_studio.loader import SceneRegistry
from matrix_studio.options import Options
from matrix_studio.scene_api import Controls

TOKEN = "test-supervisor-token"

STATES = [
    {"entity_id": "light.kitchen", "state": "on", "attributes": {"brightness": 200}},
    {"entity_id": "light.hall", "state": "off", "attributes": {}},
    {"entity_id": "light.lamp", "state": "on", "attributes": {}},
    {"entity_id": "sensor.indoor_temp", "state": "21.4", "attributes": {"unit_of_measurement": "°C"}},
    {"entity_id": "sensor.outdoor_temp", "state": "3.9", "attributes": {}},
    {"entity_id": "weather.home", "state": "cloudy", "attributes": {}},
    {"entity_id": "binary_sensor.presence", "state": "off", "attributes": {}},
    {"entity_id": "sensor.irrelevant", "state": "1234", "attributes": {}},
]


class FakeCore:
    """A minimal stand-in for `http://supervisor/core/api`."""

    def __init__(self) -> None:
        self.status = 200
        self.payload = STATES
        self.delay = 0.0
        self.requests: list[str] = []
        self._runner: web.AppRunner | None = None
        self.base_url = ""

    async def start(self) -> str:
        app = web.Application()
        app.router.add_get("/api/states", self._states)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        self.base_url = f"http://127.0.0.1:{port}/api"
        return self.base_url

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _states(self, request: web.Request) -> web.Response:
        self.requests.append(request.headers.get("Authorization", ""))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.status != 200:
            return web.Response(status=self.status, text="nope")
        return web.json_response(self.payload)


@pytest.fixture
async def core():
    server = FakeCore()
    await server.start()
    yield server
    await server.stop()


def make_options(**overrides) -> Options:
    raw = {
        "state_poll_interval": 1,
        "entities": {
            "indoor_temperature": "sensor.indoor_temp",
            "outdoor_temperature": "sensor.outdoor_temp",
            "weather": "weather.home",
            "occupancy": "binary_sensor.presence",
        },
    }
    raw.update(overrides)
    return Options.from_mapping(raw)


async def make_adapter(core: FakeCore, options: Options | None = None) -> HaStateAdapter:
    return HaStateAdapter(options or make_options(), base_url=core.base_url, token=TOKEN)


# ------------------------------------------------------------------- happy path


async def test_poll_builds_a_snapshot_with_roles_and_lights(core):
    adapter = await make_adapter(core)
    try:
        assert await adapter.poll_once() is True
        snapshot = adapter.snapshot()

        assert snapshot.available is True
        assert snapshot.indoor_temperature == pytest.approx(21.4)
        assert snapshot.outdoor_temperature == pytest.approx(3.9)
        assert snapshot.weather == "cloudy"
        assert snapshot.occupied is False
        assert snapshot.lights_total == 3
        assert snapshot.lights_on == 2
        assert snapshot.lights_on_fraction == pytest.approx(2 / 3)
        assert snapshot.updated_at > 0
    finally:
        await adapter.stop()


async def test_supervisor_token_is_sent_as_a_bearer_header(core):
    adapter = await make_adapter(core)
    try:
        await adapter.poll_once()
        assert core.requests == [f"Bearer {TOKEN}"]
    finally:
        await adapter.stop()


async def test_unrelated_entities_are_not_retained(core):
    adapter = await make_adapter(core)
    try:
        await adapter.poll_once()
        assert "sensor.irrelevant" not in adapter.snapshot().entities
        assert "sensor.indoor_temp" in adapter.snapshot().entities
    finally:
        await adapter.stop()


async def test_explicit_light_list_disables_auto_discovery(core):
    options = Options.from_mapping({"entities": {"lights": ["light.hall", "light.lamp"]}})
    assert options.auto_discover_lights is True  # irrelevant once lights are listed
    adapter = await make_adapter(core, options)
    try:
        await adapter.poll_once()
        snapshot = adapter.snapshot()
        assert snapshot.light_entity_ids == ("light.hall", "light.lamp")
        assert snapshot.lights_total == 2
        assert snapshot.lights_on == 1
        assert "light.kitchen" not in snapshot.entities
    finally:
        await adapter.stop()


async def test_missing_configured_entity_is_tolerated(core):
    options = Options.from_mapping({"entities": {"weather": "weather.does_not_exist"}})
    adapter = await make_adapter(core, options)
    try:
        await adapter.poll_once()
        snapshot = adapter.snapshot()
        assert snapshot.weather is None
        assert snapshot.numeric("indoor_temperature", default=18.0) == 18.0
        assert snapshot.available is True
    finally:
        await adapter.stop()


# ---------------------------------------------------------------- failure modes


async def test_http_error_marks_state_stale_but_keeps_the_last_snapshot(core):
    adapter = await make_adapter(core)
    try:
        await adapter.poll_once()
        assert adapter.snapshot().lights_on == 2

        core.status = 401
        assert await adapter.poll_once() is False
        snapshot = adapter.snapshot()
        assert snapshot.available is False
        assert snapshot.lights_on == 2, "stale data should be kept, not discarded"
        assert "401" in adapter.status()["last_error"]

        core.status = 200
        assert await adapter.poll_once() is True
        assert adapter.snapshot().available is True
        assert adapter.status()["last_error"] is None
    finally:
        await adapter.stop()


async def test_unreachable_server_does_not_raise():
    options = make_options()
    adapter = HaStateAdapter(options, base_url="http://127.0.0.1:1/api", token=TOKEN)
    try:
        assert await adapter.poll_once() is False
        assert adapter.snapshot().available is False
        assert adapter.status()["last_error"]
    finally:
        await adapter.stop()


async def test_garbage_payload_is_rejected_without_raising(core):
    core.payload = {"not": "a list"}
    adapter = await make_adapter(core)
    try:
        assert await adapter.poll_once() is False
        assert adapter.snapshot().available is False
    finally:
        await adapter.stop()


async def test_no_supervisor_token_is_a_supported_standalone_mode():
    adapter = HaStateAdapter(make_options(), base_url="http://127.0.0.1:1/api", token="")
    assert adapter.configured is False
    await adapter.start()
    try:
        await asyncio.sleep(0.05)
        snapshot = adapter.snapshot()
        assert snapshot.available is False
        assert snapshot.entities == {}
        assert adapter.status()["poll_count"] == 0
    finally:
        await adapter.stop()


# ---------------------------------------------------------------- decoupling


async def test_a_hanging_state_fetch_does_not_stall_rendering(core):
    """The whole point of the adapter being a separate task."""
    core.delay = 5.0
    adapter = await make_adapter(core)
    registry = SceneRegistry(None)
    registry.load()
    engine = SceneEngine(
        registry,
        Controls(active_scene="plasma"),
        target_fps=50,
        home_state_provider=adapter.snapshot,
        hot_reload=False,
    )

    await adapter.start()
    try:
        started = time.monotonic()
        for _ in range(5):
            frame = await engine.tick()
            assert frame is not None and len(frame.pixels) == 8192
        elapsed = time.monotonic() - started
        assert elapsed < 2.0, f"rendering was blocked by the state fetch ({elapsed:.2f}s)"
        # ... and scenes simply see an unavailable snapshot in the meantime.
        assert adapter.snapshot().available is False
    finally:
        await adapter.stop()
        await engine.stop()


async def test_status_summary_is_json_friendly(core):
    adapter = await make_adapter(core)
    try:
        await adapter.poll_once()
        status = adapter.status()
        assert status["configured"] is True
        assert status["available"] is True
        assert status["poll_count"] == 1
        assert status["entity_count"] == len(STATES)
        assert status["lights_on"] == 2
        assert status["age_seconds"] >= 0
    finally:
        await adapter.stop()
