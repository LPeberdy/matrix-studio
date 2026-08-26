---
name: matrix-studio-scenes
description: Create, install, or change generative scenes for the user's Matrix Studio 64x64 LED display through Home Assistant. Use when the user asks for a Matrix Studio visual, animation, scene, generative artwork, or a scene that reacts to Home Assistant state.
---

# Matrix Studio scenes

Matrix Studio is a Home Assistant add-on that renders animated `64x64` RGB frames and streams them to an ESP32-driven HUB75 LED panel. Scene code runs inside Matrix Studio on the Home Assistant machine; the display receives the rendered frames.

Home Assistant MCP is the control plane. Work through Matrix Studio's public scene contract and add-on API.

## 1. Design the scene

Turn the user's visual reference into a small generative system suited to a 64x64 display. Preserve the important visual behaviours rather than trying to reproduce fine detail that cannot survive at this resolution.

Decide whether the scene is:

- purely time-driven; or
- reactive to Home Assistant state.

When the request refers to specific devices or sensors, use Home Assistant MCP to resolve their real entity IDs before writing the scene.

**Complete when:** the visual behaviour, scene name, and any Home Assistant inputs are explicit.

## 2. Write the scene source

A user scene is one Python module. The simplest shape is:

```python
from PIL import Image
from matrix_studio.scene_api import Controls, HomeState


def render(t: float, home: HomeState, controls: Controls) -> Image.Image:
    ...
```

`render()` is called repeatedly, normally around 24 FPS, and should return a `64x64` RGB `PIL.Image.Image`.

NumPy and Pillow are available. Keep rendering fast and self-contained: compute from `t`, the supplied `home` snapshot, `controls`, and in-memory state. For simulations that need persistent state, define a `Scene` subclass and expose an instance as `SCENE`.

Useful Home Assistant inputs:

- `home.available`
- `home.lights_on`
- `home.lights_total`
- `home.lights_on_fraction`
- `home.indoor_temperature`
- `home.outdoor_temperature`
- `home.weather`
- `home.occupied`
- `home.get("domain.entity_id")` → entity with `.state`, `.attributes`, `.is_on`, `.numeric`

Home Assistant state is already supplied to the scene. Use it rather than making network calls from `render()`.

**Complete when:** the source is a self-contained Matrix Studio scene and every render path produces a usable image.

## 3. Install and enable it through Home Assistant MCP

Use the Home Assistant MCP tools available in the chat:

1. Find the installed add-on with `ha_get_addon(source="installed")` and select **Matrix Studio**. Keep its returned `slug`.
2. Inspect the running add-on with `ha_manage_addon(slug=<slug>, path="/api/status")`.
3. Install the scene through Matrix Studio's API:

```text
ha_manage_addon(
  slug=<slug>,
  path="/api/scenes/<scene_name>",
  method="PUT",
  body={
    "source": <complete Python source>,
    "activate": true
  }
)
```

Matrix Studio owns scene persistence, reload and validation. A successful new install returns HTTP 201; replacing an existing user scene returns 200. A failed import is rejected and the previous working version is restored.

4. Read `/api/status` again. Confirm:
   - the scene is listed with `source: "user"` and `ok: true`;
   - `controls.active_scene` is the new scene;
   - after a few rendered frames, it is not listed in `engine.quarantined`.

If the user wants this scene to remain the startup default after add-on restarts, update the Matrix Studio add-on option `active_scene` through `ha_manage_addon`, then restart only the Matrix Studio add-on.

**Complete when:** the running add-on reports the scene healthy and active.

## 4. Iterate visually

Treat the first installation as a live draft. Use the Matrix Studio preview/status surface and the user's feedback to adjust motion, density, palette, tempo, contrast, or Home Assistant responsiveness. Reinstall the same scene name through the same API to replace it in place.

Keep normal scene work at this scene/API layer.
