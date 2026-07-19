"""Collection guard for test_init_paths (imports ``click`` directly).

click ships transitively via ``typer`` (a hard core dependency), so it is
normally importable. Guard collection defensively for the anomalous core-only
install that resolves without it (observed once in CI) so a missing click can't
turn into a hard collection error.
"""

import importlib.util

if importlib.util.find_spec("click") is None:
    collect_ignore = ["test_init_paths.py"]
