"""Built-in scenes shipped inside the add-on image.

Every module here exposes a module-level `SCENE` object with a
`render(t, home, controls) -> PIL.Image.Image` method (see
`matrix_studio.scene_api`). User scenes dropped into the configured scenes
directory are loaded the same way and may shadow these by filename.
"""
