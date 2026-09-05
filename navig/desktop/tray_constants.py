"""Constants shared between the tray app and the `navig tray` CLI.

They live here, apart from :mod:`navig.desktop.tray_app`, because that module
**replaces ``sys.stdout`` and ``sys.stderr`` at import time** (it runs as a windowed
process where the default streams have no encoding). Importing it just to read a
constant would hand that side effect to every `navig tray` invocation — and to
anything else that ever needs these names.

The Run-key pair is written by ``desktop/install-tray.ps1`` and deleted by
``commands/tray.py``. Keep those three in agreement: `navig tray uninstall` re-typed
the pair as literals and, when its removal call turned out to be broken, nothing tied
the two halves together.
"""
from __future__ import annotations

APP_NAME = "NAVIG Tray"

#: HKEY_CURRENT_USER subkey holding per-user auto-start entries.
REGISTRY_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"

#: The value name under :data:`REGISTRY_KEY` that starts the tray at login.
REGISTRY_VALUE = "NavigTray"
