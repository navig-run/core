"""The TUI import layer must not reference names that do not exist.

`navig/tui/config_model.py` defines the *functions* ``_default_config_file()`` and
``_default_workspace_dir()`` — but six TUI modules imported the *constants*
``DEFAULT_CONFIG_FILE`` / ``DEFAULT_WORKSPACE_DIR``, which were defined nowhere. So
``from navig.tui.config_model import DEFAULT_CONFIG_FILE`` raised ImportError, and because
``review.py`` did it at module top-level, importing ``navig.tui`` at all crashed whenever
textual (a core dependency) was installed — the entire TUI was dead on any real install.

It went unnoticed because those six screens (review, welcome, the four settings screens)
had **zero** test coverage — nothing imported them. These tests are that coverage: the two
constants must resolve, and every module that imports them must load. (The rest of
``tests/tui`` runs against a textual stub installed by ``conftest.py``, so this runs with or
without real textual.)
"""

from __future__ import annotations

import importlib

import pytest

# The two modules that imported the missing constants at MODULE TOP LEVEL — these are what
# crashed `import navig.tui` outright (review.py is pulled in eagerly by navig.tui.__init__).
# The four settings screens import the constants LAZILY (inside methods), so they never
# broke the package import; their fix is the same config_model change, proven by
# `test_config_model_exposes_the_default_path_constants`. They are omitted here only because
# they use textual widgets (`Input`) that the tests/tui stub does not provide — importing
# them needs real textual, which this stubbed suite deliberately does without.
_PREVIOUSLY_BROKEN = [
    "navig.tui.screens.review",
    "navig.tui.screens.welcome",
]


def test_config_model_exposes_the_default_path_constants() -> None:
    from navig.tui import config_model

    cfg_file = config_model.DEFAULT_CONFIG_FILE
    ws_dir = config_model.DEFAULT_WORKSPACE_DIR
    assert cfg_file.name == "navig.json"
    # Resolved at access time (PEP 562 __getattr__), so both point under the config dir —
    # not frozen at import (the NAVIG_CONFIG_DIR-isolation principle this module documents).
    assert cfg_file == config_model._default_config_file()
    assert ws_dir == config_model._default_workspace_dir()


def test_config_model_still_rejects_genuinely_unknown_attributes() -> None:
    """The __getattr__ fallback must not turn every typo into a silent success."""
    from navig.tui import config_model

    with pytest.raises(AttributeError):
        _ = config_model.THIS_ATTRIBUTE_DOES_NOT_EXIST


@pytest.mark.parametrize("module", _PREVIOUSLY_BROKEN)
def test_previously_broken_tui_module_imports(module: str) -> None:
    """Each screen that imported the missing constants must now load without ImportError."""
    importlib.import_module(module)
