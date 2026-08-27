"""Bundled user-scene examples and upgrade seeding."""
from __future__ import annotations

import hashlib
import pathlib
import shutil

import numpy as np

from matrix_studio import app as app_module
from matrix_studio.app import MatrixStudioApp
from matrix_studio.loader import SceneRegistry
from matrix_studio.options import Options
from matrix_studio.scene_api import Controls, HomeState

HA_DIR = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES_DIR = HA_DIR / "example_scenes"
FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"


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


def test_editable_plasma_moves_as_one_field_at_a_constant_velocity():
    registry = SceneRegistry(EXAMPLES_DIR)
    registry.load()
    scene = registry.get("plasma").scene

    start = np.asarray(scene.render(0.0, HomeState(), Controls()), dtype=np.int16)
    after_one_second = np.asarray(scene.render(1.0, HomeState(), Controls()), dtype=np.int16)
    expected = np.roll(start, shift=(3, 6), axis=(0, 1))

    assert np.abs(after_one_second - expected).max() <= 1


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


def test_unmodified_superseded_plasma_is_upgraded(tmp_path):
    legacy_plasma = (FIXTURES_DIR / "plasma_0_1_2.py").read_bytes()
    assert hashlib.sha256(legacy_plasma).hexdigest() == (
        "8190b750927936991d399b5ecd97ad7612b73326597d8e4b12f648d52b3e7b30"
    )

    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()
    installed_plasma = scenes_dir / "plasma.py"
    installed_plasma.write_bytes(legacy_plasma)

    app = MatrixStudioApp(Options.from_mapping({"scenes_dir": str(scenes_dir)}))
    app.ensure_scenes_dir()

    assert installed_plasma.read_bytes() == (EXAMPLES_DIR / "plasma.py").read_bytes()


def test_failed_starter_upgrade_keeps_legacy_scene_intact(tmp_path, monkeypatch):
    legacy_plasma = (FIXTURES_DIR / "plasma_0_1_2.py").read_bytes()
    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()
    installed_plasma = scenes_dir / "plasma.py"
    installed_plasma.write_bytes(legacy_plasma)

    real_copy2 = shutil.copy2

    def fail_plasma_copy(source, destination):
        destination = pathlib.Path(destination)
        if destination.name == ".plasma.py.seed.tmp":
            destination.write_bytes(b"partial replacement")
            raise OSError("simulated interrupted copy")
        return real_copy2(source, destination)

    monkeypatch.setattr(app_module.shutil, "copy2", fail_plasma_copy)
    app = MatrixStudioApp(Options.from_mapping({"scenes_dir": str(scenes_dir)}))
    app.ensure_scenes_dir()

    assert installed_plasma.read_bytes() == legacy_plasma
    assert not (scenes_dir / ".plasma.py.seed.tmp").exists()
