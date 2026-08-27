"""Whole-add-on tests: the composed app, the ingress UI, and the preview mode.

These start the real `MatrixStudioApp` (engine + device server + web UI) on
ephemeral ports with a stubbed Home Assistant, then drive it exactly as the
ingress UI and a real ESP32 would.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import threading
import time

import aiohttp
import pytest

from matrix_studio.app import MatrixStudioApp
from matrix_studio.options import Options
from matrix_studio.preview import _StaticStateAdapter, fake_home_state, render_frames
from matrix_studio.vendor import matrix_studio_protocol as proto

HELLO = proto.Hello(1, 64, 64, proto.PixelFormat.RGB565, "itest-device", "9.9.9").encode()


@pytest.fixture
async def studio(tmp_path, home_state):
    options = Options.from_mapping(
        {
            "active_scene": "plasma",
            "target_fps": 40,
            "brightness": 123,
            "scenes_dir": str(tmp_path / "scenes"),
            "ws_port": 0,
            "ingress_port": 0,
            "hot_reload": True,
        }
    )
    app = MatrixStudioApp(options, state_adapter=_StaticStateAdapter(home_state))
    await app.start()
    try:
        yield app
    finally:
        await app.stop()


@pytest.fixture
async def client():
    session = aiohttp.ClientSession()
    yield session
    await session.close()


def base_url(app: MatrixStudioApp) -> str:
    return f"http://127.0.0.1:{app.web.port}"


# ------------------------------------------------------------------- lifecycle


async def test_app_starts_serves_frames_and_stops_cleanly(studio):
    await asyncio.sleep(0.2)
    assert studio.engine.stats.frames_rendered > 0
    assert studio.engine.bus.latest is not None
    assert len(studio.engine.bus.latest.pixels) == 8192
    assert studio.server.port and studio.server.port > 0
    assert studio.web.port and studio.web.port > 0
    assert studio.server.device_count == 0  # healthy


async def test_scenes_directory_is_created_and_seeded(studio, tmp_path):
    scenes = pathlib.Path(studio.options.scenes_dir)
    assert scenes.is_dir()
    seeded = {path.name for path in scenes.glob("*.py")}
    assert seeded, "the scenes directory should be seeded with examples on first run"
    assert "example_light_meter" in studio.registry.names()


# ------------------------------------------------------------------ ingress UI


async def test_status_endpoint_reports_the_whole_system(studio, client):
    await asyncio.sleep(0.15)
    async with client.get(f"{base_url(studio)}/api/status") as response:
        assert response.status == 200
        status = await response.json()

    assert status["protocol_version"] == 1
    assert status["panel"] == {"width": 64, "height": 64, "pixel_format": "RGB565"}
    assert status["controls"]["brightness"] == 123
    assert status["controls"]["active_scene"] == "plasma"
    assert status["engine"]["frames_rendered"] > 0
    assert status["engine"]["target_fps"] == 40
    assert status["device_count"] == 0
    assert status["server"]["ws_path"] == "/matrix-studio"
    assert {"plasma", "starfield", "landscape", "home_pulse"} <= {s["name"] for s in status["scenes"]}
    # Must be JSON-serialisable end to end.
    json.dumps(status)


async def test_index_is_rewritten_for_the_ingress_prefix(studio, client):
    async with client.get(base_url(studio)) as response:
        assert response.status == 200
        assert "{{BASE}}" not in await response.text()

    headers = {"X-Ingress-Path": "/api/hassio_ingress/abc123"}
    async with client.get(base_url(studio), headers=headers) as response:
        body = await response.text()
    assert '<base href="/api/hassio_ingress/abc123/" />' in body
    # Every asset/fetch must be relative so the prefix applies.
    assert 'src="static/app.js"' in body and 'href="static/style.css"' in body


async def test_static_assets_are_served(studio, client):
    for path, content_type in (("/static/app.js", "javascript"), ("/static/style.css", "css")):
        async with client.get(base_url(studio) + path) as response:
            assert response.status == 200
            assert content_type in response.headers["Content-Type"]


async def test_preview_uses_native_frames_and_non_overlapping_refreshes(studio, client):
    async with client.get(f"{base_url(studio)}/static/app.js") as response:
        javascript = await response.text()

    assert "api/preview.png?scale=1" in javascript
    assert 'addEventListener("load", schedulePreview)' in javascript
    assert "setInterval(refreshPreview" not in javascript


async def test_preview_endpoint_returns_a_png_of_the_live_frame(studio, client):
    await asyncio.sleep(0.15)
    async with client.get(f"{base_url(studio)}/api/preview.png?scale=4") as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "image/png"
        body = await response.read()
    assert body.startswith(b"\x89PNG")

    from PIL import Image
    import io

    image = Image.open(io.BytesIO(body))
    assert image.size == (256, 256)


async def test_concurrent_preview_requests_share_one_dedicated_encode(studio, client, monkeypatch):
    from matrix_studio import web as web_module

    await asyncio.sleep(0.1)
    await studio.engine.stop()  # Pin the latest frame so every request has the same cache key.
    original = web_module._encode_preview_png
    calls = []

    def counted_encode(pixels, scale):
        calls.append(threading.current_thread().name)
        time.sleep(0.03)
        return original(pixels, scale)

    monkeypatch.setattr(web_module, "_encode_preview_png", counted_encode)
    url = f"{base_url(studio)}/api/preview.png?scale=1"
    responses = await asyncio.gather(*(client.get(url) for _ in range(6)))
    try:
        assert all(response.status == 200 for response in responses)
        assert len(calls) == 1
        assert calls[0].startswith("matrix-preview")
    finally:
        for response in responses:
            response.release()


async def test_ui_controls_change_brightness_scene_and_blank(studio, client):
    url = base_url(studio)

    async with client.post(f"{url}/api/brightness", json={"value": 400}) as response:
        assert (await response.json())["brightness"] == 255
    assert studio.controls.brightness == 255

    async with client.post(f"{url}/api/scene", json={"name": "testcard"}) as response:
        assert (await response.json())["active_scene"] == "testcard"
    async with client.post(f"{url}/api/scene", json={"name": "nope"}) as response:
        assert response.status == 400

    async with client.post(f"{url}/api/blank", json={"blank": True}) as response:
        assert (await response.json())["blank"] is True
    assert studio.controls.blank is True

    async with client.post(f"{url}/api/brightness", json={"value": "abc"}) as response:
        assert response.status == 400


async def test_reload_button_picks_up_a_new_scene_file(studio, client):
    scenes = pathlib.Path(studio.options.scenes_dir)
    (scenes / "added_later.py").write_text(
        "from PIL import Image\n"
        "def render(t, home, controls):\n"
        "    return Image.new('RGB', (64, 64), (5, 6, 7))\n"
    )
    async with client.post(f"{base_url(studio)}/api/reload", json={}) as response:
        assert "added_later" in (await response.json())["scenes"]

    async with client.post(f"{base_url(studio)}/api/scene", json={"name": "added_later"}) as response:
        assert response.status == 200
    await asyncio.sleep(0.15)
    assert studio.engine.stats.rendering_scene == "added_later"


async def test_a_broken_scene_file_does_not_take_down_the_ui_or_the_stream(studio, client):
    """The end-to-end version of the broken-scene guarantee."""
    scenes = pathlib.Path(studio.options.scenes_dir)
    (scenes / "landmine.py").write_text(
        "def render(t, home, controls):\n    raise RuntimeError('deliberately broken scene')\n"
    )
    async with client.post(f"{base_url(studio)}/api/reload", json={}) as response:
        assert response.status == 200
    async with client.post(f"{base_url(studio)}/api/scene", json={"name": "landmine"}) as response:
        assert response.status == 200

    await asyncio.sleep(0.4)
    before = studio.engine.stats.frames_rendered
    await asyncio.sleep(0.3)

    assert studio.engine.stats.frames_rendered > before, "frames stopped flowing"
    assert studio.engine.stats.fallback_active
    assert "landmine" in studio.engine.stats.quarantined

    async with client.get(f"{base_url(studio)}/api/status") as response:
        assert response.status == 200
        status = await response.json()
    assert "landmine" in status["engine"]["quarantined"]
    assert status["engine"]["rendering_scene"] != "landmine"


# --------------------------------------------------------- device end to end


async def test_a_device_connects_and_receives_live_frames(studio, client):
    url = f"http://127.0.0.1:{studio.server.port}{studio.options.ws_path}"
    async with client.ws_connect(url) as ws:
        await ws.send_bytes(HELLO)
        message = await asyncio.wait_for(ws.receive(), timeout=5)
        assert proto.decode_header(message.data).type == proto.MessageType.HELLO_ACK

        seen_brightness = None
        frames = []
        deadline = asyncio.get_running_loop().time() + 5
        while len(frames) < 8 and asyncio.get_running_loop().time() < deadline:
            message = await asyncio.wait_for(ws.receive(), timeout=5)
            header = proto.decode_header(message.data)
            payload = message.data[8:]
            if header.type == proto.MessageType.FRAME:
                frames.append(proto.Frame.decode_payload(payload))
            elif header.type == proto.MessageType.BRIGHTNESS:
                seen_brightness = proto.Brightness.decode_payload(payload).brightness

        assert len(frames) == 8
        assert seen_brightness == 123, "the configured brightness must be pushed on connect"
        assert [frame.sequence for frame in frames] == list(range(8))
        assert all(len(frame.pixels) == 8192 for frame in frames)
        assert any(frames[0].pixels != frame.pixels for frame in frames[1:]), "scene should be animating"

        await asyncio.sleep(0.05)
        assert studio.server.device_count == 1
        device = studio.status()["devices"][0]
        assert device["device_id"] == "itest-device"
        assert device["frames_dropped"] == 0
        assert device["cadence_stale"] is False
        assert device["send_fps"] == pytest.approx(40, abs=8)
        assert device["send_jitter_ms"] < 25
        assert device["max_frame_gap_ms"] < 80


async def test_brightness_change_reaches_a_connected_device(studio, client):
    url = f"http://127.0.0.1:{studio.server.port}{studio.options.ws_path}"
    async with client.ws_connect(url) as ws:
        await ws.send_bytes(HELLO)
        await asyncio.wait_for(ws.receive(), timeout=5)  # HELLO_ACK

        async with client.post(f"{base_url(studio)}/api/brightness", json={"value": 7}):
            pass

        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            message = await asyncio.wait_for(ws.receive(), timeout=5)
            header = proto.decode_header(message.data)
            if header.type == proto.MessageType.BRIGHTNESS:
                if proto.Brightness.decode_payload(message.data[8:]).brightness == 7:
                    return
        pytest.fail("brightness change never reached the device")


# -------------------------------------------------------------- preview mode


def test_render_frames_works_without_any_hardware_or_home_assistant():
    images = render_frames("landscape", frames=4, fps=10.0)
    assert len(images) == 4
    assert all(image.size == (64, 64) and image.mode == "RGB" for image in images)


def test_render_frames_uses_user_scenes_and_reports_unknown_ones(tmp_path):
    (tmp_path / "solid.py").write_text(
        "from PIL import Image\n"
        "def render(t, home, controls):\n"
        "    return Image.new('RGB', (64, 64), (8, 16, 24))\n"
    )
    images = render_frames("solid", scenes_dir=str(tmp_path))
    assert images[0].getpixel((0, 0)) == (8, 16, 24)

    with pytest.raises(KeyError):
        render_frames("not_a_scene", scenes_dir=str(tmp_path))


def test_fake_home_state_drives_the_ha_reactive_scene():
    home = fake_home_state()
    assert home.available and home.lights_total == 6 and home.lights_on == 4
    assert home.weather == "rainy" and home.occupied is True
    frames = render_frames("home_pulse", frames=2, home=home)
    assert all(frame.size == (64, 64) for frame in frames)


def test_ha_reactive_scene_visibly_differs_with_different_state(home_state):
    from matrix_studio.scene_api import EntityState, HomeState

    dark = HomeState(
        entities={"light.a": EntityState("light.a", "off"), "light.b": EntityState("light.b", "off")},
        roles=dict(home_state.roles),
        light_entity_ids=("light.a", "light.b"),
        available=True,
    )
    lit = HomeState(
        entities={"light.a": EntityState("light.a", "on"), "light.b": EntityState("light.b", "on")},
        roles=dict(home_state.roles),
        light_entity_ids=("light.a", "light.b"),
        available=True,
    )
    dark_frame = render_frames("home_pulse", home=dark)[0]
    lit_frame = render_frames("home_pulse", home=lit)[0]
    assert dark_frame.tobytes() != lit_frame.tobytes(), "home_pulse must react to Home Assistant state"


def test_ha_reactive_scene_still_renders_with_no_home_assistant_at_all():
    from matrix_studio.scene_api import HomeState

    frames = render_frames("home_pulse", frames=2, home=HomeState())
    assert all(frame.size == (64, 64) for frame in frames)
