"""Scene discovery and (re)loading.

Two sources, in precedence order:

1. Built-in scenes shipped inside the add-on image (`matrix_studio/scenes/`).
2. User scenes: every `*.py` in the configured scenes directory (default
   `/config/matrix_studio/scenes`, i.e. writable from the HA File editor /
   Samba without rebuilding the add-on). A user scene whose name collides with
   a built-in one shadows it.

A user scene that fails to import, or that has no usable `render`, is recorded
as an error and skipped — it never prevents the other scenes (or the add-on)
from working.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import pathlib
import pkgutil
import sys
import traceback
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable

from .scene_api import Controls, HomeState, Scene, SceneLike

_LOGGER = logging.getLogger(__name__)

BUILTIN_PACKAGE = "matrix_studio.scenes"
#: Prefix for the synthetic module names user scenes are imported under, so
#: they can never collide with (or shadow) a real installed package.
_USER_MODULE_PREFIX = "matrix_studio_user_scene_"


@dataclass
class SceneEntry:
    """One discovered scene — loaded successfully or not."""

    name: str
    source: str  # "builtin" | "user"
    path: str | None = None
    scene: SceneLike | None = None
    description: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.scene is not None and self.error is None


class _FunctionScene:
    """Adapter so a module can just define `def render(t, home, controls)`."""

    def __init__(self, name: str, func: Callable[..., Any], description: str = "") -> None:
        self.name = name
        self.description = description
        self._func = func

    def render(self, t: float, home: HomeState, controls: Controls):
        return self._func(t, home, controls)


def _scene_from_module(module: ModuleType, name: str) -> tuple[SceneLike | None, str, str | None]:
    """Extract a scene object from a loaded module.

    Accepted, in order: a module-level `SCENE` (instance or class), a subclass
    of `Scene`, or a module-level `render` function.
    """
    description = (getattr(module, "__doc__", "") or "").strip().splitlines()
    description = description[0] if description else ""

    candidate = getattr(module, "SCENE", None)
    if candidate is None:
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, Scene) and value is not Scene:
                candidate = value
                break
    if candidate is None:
        render_func = getattr(module, "render", None)
        if callable(render_func):
            return _FunctionScene(name, render_func, description), description, None
        return None, description, "module defines no SCENE, no Scene subclass and no render() function"

    if isinstance(candidate, type):
        try:
            candidate = candidate()
        except Exception as exc:  # noqa: BLE001
            return None, description, f"instantiating {candidate.__name__} failed: {exc}"

    if not callable(getattr(candidate, "render", None)):
        return None, description, "SCENE has no callable render()"

    description = (getattr(candidate, "description", "") or description).strip()
    return candidate, description, None


class SceneRegistry:
    """Holds the current set of scenes and knows how to rebuild it."""

    def __init__(self, scenes_dir: str | pathlib.Path | None) -> None:
        self.scenes_dir = pathlib.Path(scenes_dir) if scenes_dir else None
        self._entries: dict[str, SceneEntry] = {}
        self._signature: tuple[tuple[str, float, int], ...] = ()
        self._generation = 0

    # ------------------------------------------------------------------ access

    @property
    def generation(self) -> int:
        """Bumped on every successful reload; lets callers detect changes."""
        return self._generation

    def names(self) -> list[str]:
        return sorted(name for name, entry in self._entries.items() if entry.ok)

    def entries(self) -> list[SceneEntry]:
        return sorted(self._entries.values(), key=lambda e: (e.source != "builtin", e.name))

    def get(self, name: str) -> SceneEntry | None:
        return self._entries.get(name)

    def errors(self) -> dict[str, str]:
        return {name: entry.error for name, entry in self._entries.items() if entry.error}

    # ----------------------------------------------------------------- loading

    def load(self) -> None:
        """(Re)discover every scene. Never raises."""
        entries: dict[str, SceneEntry] = {}
        for entry in self._load_builtins():
            entries[entry.name] = entry
        for entry in self._load_user_scenes():
            if entry.name in entries:
                _LOGGER.info("user scene %r shadows the built-in scene of the same name", entry.name)
            entries[entry.name] = entry
        self._entries = entries
        self._signature = self._directory_signature()
        self._generation += 1
        ok = [name for name, e in entries.items() if e.ok]
        bad = self.errors()
        _LOGGER.info("loaded %d scene(s): %s", len(ok), ", ".join(sorted(ok)) or "none")
        for name, error in bad.items():
            _LOGGER.error("scene %r failed to load: %s", name, error)

    def _load_builtins(self) -> list[SceneEntry]:
        entries: list[SceneEntry] = []
        try:
            package = importlib.import_module(BUILTIN_PACKAGE)
        except Exception as exc:  # noqa: BLE001 - defensive; should never happen
            _LOGGER.error("built-in scene package failed to import: %s", exc)
            return entries
        for module_info in pkgutil.iter_modules(package.__path__):
            if module_info.name.startswith("_"):
                continue
            full_name = f"{BUILTIN_PACKAGE}.{module_info.name}"
            try:
                module = importlib.import_module(full_name)
                module = importlib.reload(module)
            except Exception:  # noqa: BLE001
                entries.append(
                    SceneEntry(
                        name=module_info.name,
                        source="builtin",
                        error=traceback.format_exc(limit=3).strip(),
                    )
                )
                continue
            scene, description, error = _scene_from_module(module, module_info.name)
            entries.append(
                SceneEntry(
                    name=module_info.name,
                    source="builtin",
                    path=getattr(module, "__file__", None),
                    scene=scene,
                    description=description,
                    error=error,
                )
            )
        return entries

    def _user_scene_files(self) -> list[pathlib.Path]:
        if not self.scenes_dir or not self.scenes_dir.is_dir():
            return []
        return sorted(
            path
            for path in self.scenes_dir.glob("*.py")
            if path.is_file() and not path.name.startswith("_")
        )

    def _load_user_scenes(self) -> list[SceneEntry]:
        entries: list[SceneEntry] = []
        for path in self._user_scene_files():
            name = path.stem
            module_name = f"{_USER_MODULE_PREFIX}{name}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise ImportError(f"cannot build an import spec for {path}")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            except Exception:  # noqa: BLE001 - a broken user file must not be fatal
                sys.modules.pop(module_name, None)
                entries.append(
                    SceneEntry(
                        name=name,
                        source="user",
                        path=str(path),
                        error=traceback.format_exc(limit=5).strip(),
                    )
                )
                continue
            scene, description, error = _scene_from_module(module, name)
            entries.append(
                SceneEntry(
                    name=name,
                    source="user",
                    path=str(path),
                    scene=scene,
                    description=description,
                    error=error,
                )
            )
        return entries

    # -------------------------------------------------------------- hot reload

    def _directory_signature(self) -> tuple[tuple[str, float, int], ...]:
        signature = []
        for path in self._user_scene_files():
            try:
                stat = path.stat()
            except OSError:
                continue
            signature.append((str(path), stat.st_mtime, stat.st_size))
        return tuple(signature)

    def changed_on_disk(self) -> bool:
        return self._directory_signature() != self._signature

    def reload_if_changed(self) -> bool:
        """Reload when a user scene file appeared/vanished/changed. Returns True if reloaded."""
        if not self.changed_on_disk():
            return False
        _LOGGER.info("scene directory changed; reloading scenes")
        self.load()
        return True
