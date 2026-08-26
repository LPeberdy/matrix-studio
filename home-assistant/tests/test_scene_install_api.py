"""Agent-facing user-scene installation API."""
from __future__ import annotations

import pathlib

import aiohttp
import pytest

from matrix_studio.app import MatrixStudioApp
from matrix_studio.options import Options
from matrix_studio.preview import _StaticStateAdapter


@pytest.fixture
async def studio(tmp_path, home_state):
    options = Options.from_mapping(
        {
            "active_scene": "plasma",
            "target_fps": 24,
            "brightness": 90,
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
    try:
        yield session
    finally:
        await session.close()


def base_url(app: MatrixStudioApp) -> str:
    return f"http://127.0.0.1:{app.web.port}"


async def test_scene_api_installs_and_activates_without_filesystem_knowledge(studio, client):
    source = (
        '\"\"\"Installed through the Matrix Studio API.\"\"\"\n'
        "from PIL import Image\n"
        "def render(t, home, controls):\n"
        "    return Image.new('RGB', (64, 64), (8, 16, 24))\n"
    )

    async with client.put(
        f"{base_url(studio)}/api/scenes/agent_scene",
        json={"source": source, "activate": True},
    ) as response:
        assert response.status == 201
        result = await response.json()

    assert result["ok"] is True
    assert result["name"] == "agent_scene"
    assert result["source"] == "user"
    assert result["activated"] is True
    assert result["active_scene"] == "agent_scene"

    path = pathlib.Path(studio.options.scenes_dir) / "agent_scene.py"
    assert path.read_text(encoding="utf-8") == source

    entry = studio.registry.get("agent_scene")
    assert entry is not None and entry.ok and entry.source == "user"
    assert studio.controls.active_scene == "agent_scene"


async def test_scene_api_rolls_back_a_broken_replacement(studio, client):
    good_source = (
        "from PIL import Image\n"
        "def render(t, home, controls):\n"
        "    return Image.new('RGB', (64, 64), (1, 2, 3))\n"
    )

    url = f"{base_url(studio)}/api/scenes/safe_scene"
    async with client.put(url, json={"source": good_source}) as response:
        assert response.status == 201

    broken_source = "import module_that_does_not_exist\n"
    async with client.put(url, json={"source": broken_source}) as response:
        assert response.status == 400
        result = await response.json()
    assert result["ok"] is False
    assert "failed to load" in result["error"]

    path = pathlib.Path(studio.options.scenes_dir) / "safe_scene.py"
    assert path.read_text(encoding="utf-8") == good_source
    entry = studio.registry.get("safe_scene")
    assert entry is not None and entry.ok


async def test_scene_api_rejects_invalid_names_and_syntax_errors(studio, client):
    async with client.put(
        f"{base_url(studio)}/api/scenes/Bad-Name",
        json={"source": "def render(t, home, controls):\n    pass\n"},
    ) as response:
        assert response.status == 400
        result = await response.json()
    assert "scene name must match" in result["error"]

    async with client.put(
        f"{base_url(studio)}/api/scenes/bad_syntax",
        json={"source": "def render(:\n    pass\n"},
    ) as response:
        assert response.status == 400
        result = await response.json()
    assert "syntax error" in result["error"]
    assert not (pathlib.Path(studio.options.scenes_dir) / "bad_syntax.py").exists()
