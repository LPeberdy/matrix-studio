"""Make the add-on package importable when pytest is run from the repo root.

CI does `pytest home-assistant/tests`, so `home-assistant/` itself has to be on
sys.path for `import matrix_studio` to resolve.
"""
import pathlib
import sys

HA_DIR = pathlib.Path(__file__).resolve().parent
if str(HA_DIR) not in sys.path:
    sys.path.insert(0, str(HA_DIR))
