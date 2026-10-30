"""Regression: the wizard/review screens resolve the config path at USE time.

`config_model.DEFAULT_CONFIG_FILE` is call-time (PEP 562 ``__getattr__``), but
review.py and wizard.py did ``from navig.tui.config_model import DEFAULT_CONFIG_FILE``
at module top, binding a concrete Path at import. When ``NAVIG_CONFIG_DIR`` changed
afterwards, the overwrite check and the config save still targeted the original
(the operator's real) config directory.
"""

from __future__ import annotations

import ast
from pathlib import Path

import navig

_TUI = Path(navig.__path__[0]) / "tui"
_CALL_TIME_NAMES = {"DEFAULT_CONFIG_FILE", "DEFAULT_WORKSPACE_DIR"}


def test_no_module_level_import_freezes_default_paths():
    offenders = []
    files = sorted(_TUI.rglob("*.py"))
    assert len(files) > 10  # scan floor: an empty tree must not pass vacuously
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only — imports inside functions are call-time
            if isinstance(node, ast.ImportFrom) and node.module == "navig.tui.config_model":
                frozen = _CALL_TIME_NAMES & {a.name for a in node.names}
                if frozen:
                    offenders.append(f"{path.relative_to(_TUI)}: {sorted(frozen)}")
    assert not offenders, offenders


def test_review_overwrite_check_follows_late_config_dir(monkeypatch, tmp_path):
    from navig.tui.screens import review

    late = tmp_path / "late-config"
    late.mkdir()
    (late / "navig.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(late))

    pushed = []

    class _App:
        def push_screen(self, screen, *a, **k):
            pushed.append(screen)

    screen = review.ReviewScreen.__new__(review.ReviewScreen)
    monkeypatch.setattr(review.ReviewScreen, "app", _App(), raising=False)

    screen.on_mount()

    # navig.json exists only in the late dir — the confirm modal proves it was read.
    assert len(pushed) == 1
