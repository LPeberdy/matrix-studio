"""Bundled user-scene examples and upgrade seeding."""
from __future__ import annotations

import pathlib

from matrix_studio.app import MatrixStudioApp
from matrix_studio.loader import SceneRegistry
from matrix_studio.options import Options
from matrix_studio.scene_api import Controls, HomeState

HA_DIR = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES_DIR = HA_DIR / "example_scenes"


def test_plasma_and_glitch_life_examples_load_and_render():
    registry = SceneRegistry(EXAMPLES_DIR)
    registry.load()

    for name in ("plasma", "glitch_life"):
        entry = registry.get(name)
        assert entry is not None
        assert entry.source == "user"
        assert entry.ok, entry.error
        image = entry.scene.render(1.25, HomeState(), Controls())
        assert image.size == (64, 64)
        assert image.mode == "RGB"


def test_existing_scene_directory_gets_missing_examples_without_overwrite(tmp_path):
    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()
    existing = scenes_dir / "plasma.py"
    existing.write_text("# user's plasma stays untouched\n")

    app = MatrixStudioApp(Options.from_mapping({"scenes_dir": str(scenes_dir)}))
    app.ensure_scenes_dir()

    assert existing.read_text() == "# user's plasma stays untouched\n"
    assert (scenes_dir / "glitch_life.py").is_file()
    assert (scenes_dir / "README.txt").is_file()
