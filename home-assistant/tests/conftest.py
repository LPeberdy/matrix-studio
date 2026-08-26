"""Shared fixtures for the add-on test suite."""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

HA_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = HA_DIR.parent
if str(HA_DIR) not in sys.path:
    sys.path.insert(0, str(HA_DIR))

FIXTURES_DIR = REPO_ROOT / "protocol" / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> pathlib.Path:
    assert FIXTURES_DIR.is_dir(), f"golden protocol fixtures missing at {FIXTURES_DIR}"
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def fixture_manifest(fixtures_dir: pathlib.Path) -> dict:
    return json.loads((fixtures_dir / "manifest.json").read_text())


@pytest.fixture
def read_fixture(fixtures_dir: pathlib.Path):
    def _read(name: str) -> bytes:
        return (fixtures_dir / name).read_bytes()

    return _read


@pytest.fixture
def scenes_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    directory = tmp_path / "scenes"
    directory.mkdir()
    return directory


@pytest.fixture
def home_state():
    """A populated HomeState so HA-reactive scenes have something to react to."""
    from matrix_studio.scene_api import EntityState, HomeState

    entities = {
        "light.a": EntityState("light.a", "on"),
        "light.b": EntityState("light.b", "off"),
        "light.c": EntityState("light.c", "on"),
        "sensor.indoor": EntityState("sensor.indoor", "21.5", {"unit_of_measurement": "°C"}),
        "sensor.outdoor": EntityState("sensor.outdoor", "4.0", {"unit_of_measurement": "°C"}),
        "weather.home": EntityState("weather.home", "rainy"),
        "binary_sensor.presence": EntityState("binary_sensor.presence", "on"),
    }
    return HomeState(
        entities=entities,
        roles={
            "indoor_temperature": "sensor.indoor",
            "outdoor_temperature": "sensor.outdoor",
            "weather": "weather.home",
            "occupancy": "binary_sensor.presence",
        },
        light_entity_ids=("light.a", "light.b", "light.c"),
        available=True,
        updated_at=1_700_000_000.0,
    )
