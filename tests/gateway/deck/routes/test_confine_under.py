"""The one deck-routes path-containment guard: `_utils.confine_under`.

Consolidates plans `_confined_doc_path`, wiki `_confined_page_path`, and apps
`_confined_space_dir` — every deck handler that turns client input into a filesystem path routes
through it, so a weaker copy can't drift back in (the gap that let apps.py ship without one).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_confine_under_semantics(tmp_path):
    from navig.gateway.deck.routes._utils import confine_under

    base = tmp_path / "base"
    (base / "sub").mkdir(parents=True)

    assert confine_under(base, "sub") == (base / "sub").resolve()
    assert confine_under(base, "sub/x.md") == (base / "sub" / "x.md").resolve()
    assert confine_under(base, "a\\b") == (base / "a" / "b").resolve()  # backslash normalized
    # escapes / bad input → None
    assert confine_under(base, "../etc") is None
    assert confine_under(base, "..") is None
    assert confine_under(base, "sub/../../escape") is None
    assert confine_under(base, "") is None
    assert confine_under(base, None) is None
    assert confine_under(base, str(tmp_path / "abs")) is None  # absolute


def test_all_three_wrappers_delegate(tmp_path, monkeypatch):
    import navig.gateway.deck.routes.apps as apps
    from navig.gateway.deck.routes import plans, wiki

    base = tmp_path / "d"
    (base / "ok").mkdir(parents=True)

    assert plans._confined_doc_path(base, "ok") == (base / "ok").resolve()
    assert plans._confined_doc_path(base, "../x") is None
    assert wiki._confined_page_path(base, "ok") == (base / "ok").resolve()
    assert wiki._confined_page_path(base, "../x") is None

    spaces = tmp_path / "spaces"
    (spaces / "s").mkdir(parents=True)
    monkeypatch.setattr(apps, "_get_spaces_dir", lambda: spaces)
    assert apps._confined_space_dir("s") == (spaces / "s").resolve()
    assert apps._confined_space_dir("../x") is None
