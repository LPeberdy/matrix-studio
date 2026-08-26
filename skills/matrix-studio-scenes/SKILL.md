---
name: matrix-studio-scenes
description: Create or deploy Matrix Studio user scenes for the 64x64 HUB75 display. Use when a request involves a Matrix Studio scene or the add-on's config/scenes directory.
---

# Matrix Studio scenes

Treat the repo and the running add-on as the source of truth. Read `home-assistant/example_scenes/README.txt` and the closest existing scene before writing code.

## 1. Resolve the scene

Establish the scene name, visual behaviour, and whether it needs Home Assistant state. If deploying, inspect the Matrix Studio add-on options and resolve its configured `scenes_dir` rather than guessing a host path.

**Complete when:** the target filename, behaviour, state inputs, and destination directory are known.

## 2. Build one user-scene file

Write a single `.py` file. User scenes are loaded by filename, and a filename matching a built-in scene intentionally shadows that built-in.

Use the public API via absolute imports, for example:

```python
from matrix_studio.scene_api import Controls, HomeState
from PIL import Image


def render(t: float, home: HomeState, controls: Controls) -> Image.Image:
    ...
```

A scene must return a `64x64` RGB `PIL.Image.Image`. Keep `render()` non-blocking and suitable for a 24 FPS loop. Put persistent animation state on a scene object when needed. Prefer NumPy for whole-frame pixel work. Home Assistant data comes from the provided `home` snapshot; do not perform network I/O from the scene.

**Complete when:** importing the file succeeds and repeated renders return `64x64` RGB images without blocking or raising.

## 3. Validate

With a repo checkout, use the real preview path:

```sh
cd home-assistant
python -m matrix_studio.preview --scene <scene-name> --scenes-dir <directory-containing-the-file> --out /tmp/<scene-name>.png
```

For a state-reactive scene, also inspect the existing scene tests and exercise representative `HomeState` values.

**Complete when:** the scene loads through Matrix Studio's loader and renders a representative frame successfully.

## 4. Deploy through Home Assistant MCP

Use Home Assistant MCP to read the Matrix Studio add-on configuration first. Resolve `scenes_dir`, then use the MCP's file/add-on write capability to create `<scenes_dir>/<scene-name>.py` with the validated source. Complete any Home Assistant approval flow the MCP presents, then retry the write.

Matrix Studio normally hot-reloads the directory. Verify the running add-on status shows the scene with `source=user` and `ok=true`. If it has not reloaded, trigger **Reload scenes** in the Matrix Studio ingress UI.

Enable the scene by selecting it in the Matrix Studio **Scene** control. To make it the persistent startup scene, use Home Assistant MCP to set the add-on option `active_scene` to the same name and restart the add-on.

**Complete when:** the running add-on reports the user scene healthy, `active_scene` is the requested scene, and the ingress preview is rendering it.

## Example: deploy `glitch_life`

1. Read the add-on config with Home Assistant MCP and confirm `scenes_dir` (normally `/config/scenes` inside the add-on).
2. Write the validated source to `<scenes_dir>/glitch_life.py` through Home Assistant MCP.
3. Wait for hot reload or press **Reload scenes**.
4. Check status: `glitch_life`, `source=user`, `ok=true`.
5. Select `glitch_life` in the Scene control.
6. If it should survive restarts as the default, set add-on option `active_scene: glitch_life` through Home Assistant MCP and restart Matrix Studio.
7. Confirm the ingress preview is visibly updating.
