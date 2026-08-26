"""Add-on options parsing, and agreement between config.yaml and the parser."""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from matrix_studio.options import ENTITY_ROLES, Options, OptionsError
from matrix_studio.vendor import matrix_studio_protocol as proto

HA_DIR = pathlib.Path(__file__).resolve().parents[1]
CONFIG_YAML = HA_DIR / "config.yaml"


def test_defaults_match_the_frozen_protocol_defaults():
    options = Options.from_mapping({})
    assert options.ws_port == proto.DEFAULT_WS_PORT == 7887
    assert options.ws_path == proto.DEFAULT_WS_PATH == "/matrix-studio"
    assert options.active_scene == "plasma"
    assert 1 <= options.target_fps <= 60
    assert options.entity_roles == {}
    assert options.light_entities == ()


def test_frame_interval_hint_follows_target_fps():
    assert Options.from_mapping({"target_fps": 25}).frame_interval_hint_ms == 40
    assert Options.from_mapping({"target_fps": 1}).frame_interval_hint_ms == 1000


def test_entity_roles_are_only_populated_when_configured():
    options = Options.from_mapping(
        {
            "entities": {
                "lights": ["light.one", " light.two ", ""],
                "indoor_temperature": "sensor.inside",
                "outdoor_temperature": "",
                "weather": "weather.home",
            }
        }
    )
    assert options.light_entities == ("light.one", "light.two")
    assert options.entity_roles == {"indoor_temperature": "sensor.inside", "weather": "weather.home"}
    assert "outdoor_temperature" not in options.entity_roles


def test_out_of_range_values_are_clamped_not_rejected():
    options = Options.from_mapping({"target_fps": 500, "brightness": -20})
    assert options.target_fps == 60
    assert options.brightness == 0


def test_ws_path_is_normalised():
    assert Options.from_mapping({"ws_path": "matrix-studio"}).ws_path == "/matrix-studio"
    assert Options.from_mapping({"ws_path": ""}).ws_path == "/matrix-studio"


def test_structurally_broken_options_raise():
    with pytest.raises(OptionsError):
        Options.from_mapping({"target_fps": "not a number"})
    with pytest.raises(OptionsError):
        Options.from_mapping({"entities": "not a mapping"})
    with pytest.raises(OptionsError):
        Options.from_mapping({"entities": {"lights": 5}})


def test_load_reads_the_supervisor_options_file(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(
        json.dumps(
            {
                "active_scene": "landscape",
                "target_fps": 30,
                "brightness": 90,
                "scenes_dir": "/config/scenes",
                "entities": {"lights": ["light.x"], "occupancy": "binary_sensor.y"},
            }
        )
    )
    options = Options.load(path)
    assert options.active_scene == "landscape"
    assert options.target_fps == 30
    assert options.brightness == 90
    assert options.light_entities == ("light.x",)
    assert options.entity_roles["occupancy"] == "binary_sensor.y"


def test_load_without_an_options_file_uses_defaults(tmp_path):
    options = Options.load(tmp_path / "definitely-not-here.json")
    assert options.active_scene == "plasma"
    assert options.ws_port == 7887


def test_load_rejects_invalid_json(tmp_path):
    path = tmp_path / "options.json"
    path.write_text("{not json")
    with pytest.raises(OptionsError):
        Options.load(path)


def test_env_overrides_apply_for_standalone_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_STUDIO_TARGET_FPS", "15")
    monkeypatch.setenv("MATRIX_STUDIO_ACTIVE_SCENE", "testcard")
    monkeypatch.setenv("MATRIX_STUDIO_LIGHTS", "light.a, light.b")
    monkeypatch.setenv("MATRIX_STUDIO_WEATHER", "weather.somewhere")
    options = Options.load(tmp_path / "missing.json")
    assert options.target_fps == 15
    assert options.active_scene == "testcard"
    assert options.light_entities == ("light.a", "light.b")
    assert options.entity_roles["weather"] == "weather.somewhere"


# ------------------------------------------------- config.yaml <-> parser parity


def _config_yaml_text() -> str:
    return CONFIG_YAML.read_text()


def test_config_yaml_declares_the_add_on_essentials():
    text = _config_yaml_text()
    for required in ("name:", "version:", "slug: matrix_studio", "startup: application", "boot: auto"):
        assert required in text, f"config.yaml is missing {required!r}"
    assert "aarch64" in text, "this add-on must build for the Raspberry Pi's architecture"
    assert "ingress: true" in text and "ingress_port:" in text
    assert "homeassistant_api: true" in text, "needed to read state via the Supervisor proxy"
    assert "7887/tcp" in text, "the device endpoint must be published"


def test_config_yaml_options_are_all_understood_by_the_parser():
    """Every key in the manifest's `options:` block must survive parsing."""
    text = _config_yaml_text()
    block = text.split("\noptions:\n", 1)[1].split("\nschema:\n", 1)[0]
    top_level = re.findall(r"^  ([a-z_]+):", block, flags=re.MULTILINE)
    assert "active_scene" in top_level and "brightness" in top_level and "entities" in top_level

    known = set(Options.from_mapping({}).__dataclass_fields__) | {"entities", "ingress_port"}
    aliases = {"light_entities", "entity_roles"}
    for key in top_level:
        assert key in known or key in aliases, f"config.yaml offers option {key!r} that options.py ignores"


def test_config_yaml_exposes_every_entity_role():
    block = _config_yaml_text().split("\nschema:\n", 1)[1]
    for role in ENTITY_ROLES:
        assert role in block, f"entity role {role!r} is not configurable in config.yaml"
    assert "lights:" in block


def test_no_real_entity_ids_or_secrets_are_committed():
    """Defaults must never contain a specific user's entities or tokens."""
    text = _config_yaml_text()
    options_block = text.split("\noptions:\n", 1)[1].split("\nschema:\n", 1)[0]
    assert not re.search(r'"[a-z_]+\.[a-z0-9_]+"', options_block), "config.yaml default options name real entities"
    for secret in ("token", "password", "ssid", "psk"):
        assert secret not in options_block.lower()
