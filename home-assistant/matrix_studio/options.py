"""Add-on options: parsing, validation and defaults.

Home Assistant Supervisor renders the user's add-on configuration to
`/data/options.json` before starting the container; `config.yaml`'s `schema:`
block has already type-checked it, but this module re-validates and clamps
everything anyway so that the same code runs unchanged outside Supervisor
(standalone preview/emulator, tests, CI).

Nothing user-specific is ever hardcoded here: every entity id comes from
options, and the defaults are empty.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from .vendor import matrix_studio_protocol as protocol

_LOGGER = logging.getLogger(__name__)

DEFAULT_OPTIONS_PATH = "/data/options.json"

#: Named roles a scene can ask `HomeState` for. Each maps to one user-chosen
#: entity id; all are optional.
ENTITY_ROLES = ("indoor_temperature", "outdoor_temperature", "weather", "occupancy")

_LOG_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}


class OptionsError(ValueError):
    """Raised when options are structurally unusable (wrong types)."""


def _as_int(value: Any, name: str, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise OptionsError(f"option {name!r} must be an integer, got {value!r}") from None
    clamped = max(low, min(high, parsed))
    if clamped != parsed:
        _LOGGER.warning("option %s=%s out of range [%s, %s]; clamped to %s", name, parsed, low, high, clamped)
    return clamped


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_str_list(value: Any, name: str) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        # Tolerate a comma-separated string (handy for env-var overrides).
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if not isinstance(value, (list, tuple)):
        raise OptionsError(f"option {name!r} must be a list of entity ids, got {value!r}")
    return tuple(str(item).strip() for item in value if str(item).strip())


@dataclass(frozen=True)
class Options:
    """Validated add-on configuration."""

    active_scene: str = "plasma"
    target_fps: int = 24
    brightness: int = 90
    blank: bool = False
    scenes_dir: str = "/config/matrix_studio/scenes"
    ws_port: int = protocol.DEFAULT_WS_PORT
    ws_path: str = protocol.DEFAULT_WS_PATH
    ingress_port: int = 8099
    state_poll_interval: int = 5
    hot_reload: bool = True
    log_level: str = "info"
    #: entity ids treated as "lights"; empty means auto-discover the light domain
    light_entities: tuple[str, ...] = ()
    auto_discover_lights: bool = True
    #: role -> entity_id, only for roles the user actually configured
    entity_roles: Mapping[str, str] = field(default_factory=dict)

    @property
    def frame_interval_hint_ms(self) -> int:
        return max(1, round(1000 / self.target_fps))

    @property
    def python_log_level(self) -> int:
        return _LOG_LEVELS.get(self.log_level.lower(), logging.INFO)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "Options":
        raw = dict(raw or {})
        entities = raw.get("entities") or {}
        if not isinstance(entities, Mapping):
            raise OptionsError(f"option 'entities' must be a mapping, got {entities!r}")

        roles: dict[str, str] = {}
        for role in ENTITY_ROLES:
            entity_id = _as_str(entities.get(role))
            if entity_id:
                roles[role] = entity_id

        ws_path = _as_str(raw.get("ws_path"), protocol.DEFAULT_WS_PATH) or protocol.DEFAULT_WS_PATH
        if not ws_path.startswith("/"):
            ws_path = "/" + ws_path

        return cls(
            active_scene=_as_str(raw.get("active_scene"), "plasma") or "plasma",
            target_fps=_as_int(raw.get("target_fps"), "target_fps", 24, 1, 60),
            brightness=_as_int(raw.get("brightness"), "brightness", 90, 0, 255),
            blank=_as_bool(raw.get("blank"), False),
            scenes_dir=_as_str(raw.get("scenes_dir"), "/config/matrix_studio/scenes")
            or "/config/matrix_studio/scenes",
            # Port 0 is allowed and means "bind an ephemeral port" — used by the
            # test suite and by standalone preview runs, never by Supervisor.
            ws_port=_as_int(raw.get("ws_port"), "ws_port", protocol.DEFAULT_WS_PORT, 0, 65535),
            ws_path=ws_path,
            ingress_port=_as_int(raw.get("ingress_port"), "ingress_port", 8099, 0, 65535),
            state_poll_interval=_as_int(raw.get("state_poll_interval"), "state_poll_interval", 5, 1, 3600),
            hot_reload=_as_bool(raw.get("hot_reload"), True),
            log_level=_as_str(raw.get("log_level"), "info").lower() or "info",
            light_entities=_as_str_list(entities.get("lights"), "entities.lights"),
            auto_discover_lights=_as_bool(raw.get("auto_discover_lights"), True),
            entity_roles=roles,
        )

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Options":
        """Load from `/data/options.json` (or `path`), tolerating its absence.

        Running outside Supervisor is a first-class case: the emulator and the
        test suite both go through here with no options file at all.
        """
        options_path = pathlib.Path(path or os.environ.get("MATRIX_STUDIO_OPTIONS", DEFAULT_OPTIONS_PATH))
        raw: dict[str, Any] = {}
        if options_path.is_file():
            try:
                raw = json.loads(options_path.read_text() or "{}")
            except json.JSONDecodeError as exc:
                raise OptionsError(f"{options_path} is not valid JSON: {exc}") from exc
        else:
            _LOGGER.info("no options file at %s; using defaults", options_path)
        return cls.from_mapping(_apply_env_overrides(raw))


def _apply_env_overrides(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Allow `MATRIX_STUDIO_<OPTION>` env vars to override, for standalone runs.

    Supervisor never sets these; they exist so `python -m matrix_studio.preview`
    and docker-less development can be configured without an options file.
    """
    merged = dict(raw)
    simple_keys = (
        "active_scene",
        "target_fps",
        "brightness",
        "scenes_dir",
        "ws_port",
        "ws_path",
        "ingress_port",
        "state_poll_interval",
        "hot_reload",
        "log_level",
    )
    for key in simple_keys:
        env_value = os.environ.get(f"MATRIX_STUDIO_{key.upper()}")
        if env_value is not None:
            merged[key] = env_value

    entities = dict(merged.get("entities") or {})
    lights = os.environ.get("MATRIX_STUDIO_LIGHTS")
    if lights is not None:
        entities["lights"] = lights
    for role in ENTITY_ROLES:
        env_value = os.environ.get(f"MATRIX_STUDIO_{role.upper()}")
        if env_value is not None:
            entities[role] = env_value
    if entities:
        merged["entities"] = entities
    return merged
