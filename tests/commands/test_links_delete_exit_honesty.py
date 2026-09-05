"""Regression: a failed `navig links delete` must not be reported by silence.

    if db.delete(link_id):
        _ch.success(...)      # <- and no else

A delete that returned False printed NOTHING AT ALL and exited 0: the bookmark was
still there and the command looked like it worked. The same function was already
careful one line up — it exits 1 on not-found and aborts on a declined confirm — so it
was inconsistent with itself.

No error-then-return scan can see this shape (nothing pairs an error call with a
return); `tests/quality/test_no_silent_success_branch.py` is the guard that can.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer

import navig.commands.links as links


class _DB:
    """A store whose delete always fails — the case that used to be silent."""

    def __init__(self, link, deleted: bool = False):
        self._link = link
        self._deleted = deleted

    def get(self, _id):
        return self._link

    def delete(self, _id):
        return self._deleted


@pytest.fixture
def reported(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(links._ch, "error", lambda msg, *a, **k: seen.append(f"ERR:{msg}"))
    monkeypatch.setattr(links._ch, "success", lambda msg, *a, **k: seen.append("SUCCESS-CLAIMED"))
    return seen


def _install_db(monkeypatch, db):
    monkeypatch.setattr(links._links_db_mod, "get_links_db", lambda: db)


def test_failed_delete_is_loud_and_nonzero(monkeypatch, reported):
    link = SimpleNamespace(url="https://example.test/x", id="abc123")
    _install_db(monkeypatch, _DB(link, deleted=False))

    with pytest.raises(typer.Exit) as exc:
        links.delete_link("abc123", force=True)

    assert exc.value.exit_code == 1
    assert any(e.startswith("ERR:") for e in reported), "the failure must be announced"
    assert "SUCCESS-CLAIMED" not in reported


def test_successful_delete_still_exits_zero(monkeypatch, reported):
    """Anti-vacuity: without this, a handler that raised unconditionally would make
    the test above pass while `links delete` was broken for everyone."""
    link = SimpleNamespace(url="https://example.test/x", id="abc123")
    _install_db(monkeypatch, _DB(link, deleted=True))

    links.delete_link("abc123", force=True)  # must NOT raise

    assert "SUCCESS-CLAIMED" in reported
    assert not any(e.startswith("ERR:") for e in reported)


def test_unknown_link_is_still_reported(monkeypatch, reported):
    """The pre-existing not-found path must survive the change."""
    _install_db(monkeypatch, _DB(None))

    with pytest.raises(typer.Exit) as exc:
        links.delete_link("ghost", force=True)

    assert exc.value.exit_code == 1
    assert any("not found" in e for e in reported)
