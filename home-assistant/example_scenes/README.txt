Matrix Studio — user scenes
===========================

Drop .py files in this directory to add your own scenes. They are picked up
automatically (hot reload is on by default; the ingress UI also has a
"Reload scenes" button). Files starting with "_" are ignored.

A scene is anything with:

    render(t, home, controls) -> PIL.Image.Image   # 64x64, mode "RGB"

  t        seconds since the add-on started (float, monotonic)
  home     a matrix_studio.scene_api.HomeState snapshot (see below)
  controls a matrix_studio.scene_api.Controls (brightness, blank, active_scene)

Three accepted shapes, in the order the loader checks them:

  1. a module-level object named SCENE (instance or class)
  2. any subclass of matrix_studio.scene_api.Scene defined in the module
  3. a module-level function called render(t, home, controls)

HomeState (all accessors are safe — they never raise, and return sensible
defaults when Home Assistant is unavailable or an entity is unknown):

  home.available                True once state has been fetched successfully
  home.lights_on                count of configured/discovered lights that are on
  home.lights_total             how many are being tracked
  home.lights_on_fraction       0.0 - 1.0
  home.indoor_temperature       float or None   (options: entities.indoor_temperature)
  home.outdoor_temperature      float or None   (options: entities.outdoor_temperature)
  home.weather                  str or None     (options: entities.weather)
  home.occupied                 bool            (options: entities.occupancy)
  home.get("sensor.anything")   EntityState or None (.state, .attributes, .is_on, .numeric)

Naming a file the same as a built-in scene (plasma.py, starfield.py,
landscape.py, home_pulse.py, testcard.py) overrides that built-in.

If your scene raises, the engine logs it and falls back to another scene; after
3 consecutive failures the scene is quarantined until you fix it and press
"Reload scenes". The add-on itself keeps running and keeps streaming.

Preview scenes without any hardware, from a checkout of the repo:

    python -m matrix_studio.preview --list
    python -m matrix_studio.preview --scene my_scene --scenes-dir . --out /tmp/f.png
    python -m matrix_studio.preview --serve
