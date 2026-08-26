"""The (deliberately tiny) public API a Matrix Studio scene is written against.

A scene is any object with:

    render(t: float, home: HomeState, controls: Controls) -> PIL.Image.Image

`t` is seconds since the engine started (monotonic, float).
The returned image should be `PANEL_WIDTH x PANEL_HEIGHT` in mode "RGB";
anything else is coerced by the engine, which will also survive a scene that
raises. See `docs`/`home-assistant/README.md` for authoring guidance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from PIL import Image

PANEL_WIDTH = 64
PANEL_HEIGHT = 64

__all__ = [
    "PANEL_WIDTH",
    "PANEL_HEIGHT",
    "EntityState",
    "HomeState",
    "Controls",
    "Scene",
    "SceneLike",
    "new_canvas",
]


def new_canvas(colour: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    """A blank 64x64 RGB canvas — the conventional starting point for a scene."""
    return Image.new("RGB", (PANEL_WIDTH, PANEL_HEIGHT), colour)


@dataclass(frozen=True)
class EntityState:
    """One Home Assistant entity, flattened to the bits a scene cares about."""

    entity_id: str
    state: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def is_on(self) -> bool:
        return self.state.lower() in ("on", "home", "open", "detected", "playing", "true")

    @property
    def numeric(self) -> float | None:
        """The state parsed as a float, or None if it isn't numeric."""
        try:
            value = float(self.state)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(value) or math.isinf(value) else value


@dataclass(frozen=True)
class HomeState:
    """An immutable snapshot of Home Assistant, handed to scenes each tick.

    Scenes must treat this as possibly-stale and possibly-empty: `available`
    is False before the first successful poll and after the Supervisor API
    starts failing, and every accessor returns a sensible default rather than
    raising, so a scene never needs try/except around state access.
    """

    entities: Mapping[str, EntityState] = field(default_factory=dict)
    #: role name (e.g. "indoor_temperature") -> configured entity_id
    roles: Mapping[str, str] = field(default_factory=dict)
    #: entity_ids configured as "lights" (or auto-discovered light.* entities)
    light_entity_ids: tuple[str, ...] = ()
    available: bool = False
    updated_at: float = 0.0

    def get(self, entity_id: str) -> EntityState | None:
        return self.entities.get(entity_id)

    def role(self, role: str) -> EntityState | None:
        """The entity configured for a named role, if any and if known."""
        entity_id = self.roles.get(role)
        if not entity_id:
            return None
        return self.entities.get(entity_id)

    def numeric(self, role: str, default: float | None = None) -> float | None:
        entity = self.role(role)
        if entity is None:
            return default
        value = entity.numeric
        return default if value is None else value

    def is_on(self, role: str, default: bool = False) -> bool:
        entity = self.role(role)
        return default if entity is None else entity.is_on

    @property
    def lights_total(self) -> int:
        return len(self.light_entity_ids)

    @property
    def lights_on(self) -> int:
        return sum(
            1
            for entity_id in self.light_entity_ids
            if (entity := self.entities.get(entity_id)) is not None and entity.is_on
        )

    @property
    def lights_on_fraction(self) -> float:
        """0.0-1.0; 0.0 when nothing is configured/known."""
        return self.lights_on / self.lights_total if self.lights_total else 0.0

    @property
    def indoor_temperature(self) -> float | None:
        return self.numeric("indoor_temperature")

    @property
    def outdoor_temperature(self) -> float | None:
        return self.numeric("outdoor_temperature")

    @property
    def weather(self) -> str | None:
        entity = self.role("weather")
        return entity.state if entity else None

    @property
    def occupied(self) -> bool:
        return self.is_on("occupancy", default=False)


@dataclass
class Controls:
    """User-facing display controls, shared (mutable) between UI and engine."""

    brightness: int = 160
    blank: bool = False
    active_scene: str = "plasma"
    width: int = PANEL_WIDTH
    height: int = PANEL_HEIGHT

    def clamped_brightness(self) -> int:
        return max(0, min(255, int(self.brightness)))


@runtime_checkable
class SceneLike(Protocol):
    """Structural type for anything the engine can render."""

    def render(self, t: float, home: HomeState, controls: Controls) -> Image.Image: ...


class Scene:
    """Optional convenience base class. Subclassing is not required —
    any object (or module-level `render` function) with a matching `render`
    signature works."""

    #: Human-readable name; defaults to the class name when unset.
    name: str = ""
    #: One-line description surfaced in the ingress UI.
    description: str = ""

    def render(self, t: float, home: HomeState, controls: Controls) -> Image.Image:
        raise NotImplementedError
