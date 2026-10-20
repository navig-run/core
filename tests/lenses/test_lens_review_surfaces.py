"""The review flow's other surfaces: the daily update monitor and the `navig lens` verbs."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.lens import lens_app
from navig.lenses import LensStore, lenses_dir
from navig.notify.monitors import lens_updates

URL = "https://lenses.example/banner.user.js"


def _src(version: str = "1.0.0", body: str = "window.__hit = 1;", extra: str = "") -> str:
    return (
        "// ==UserScript==\n// @name Banner\n// @match *://example.com/*\n"
        f"// @version {version}\n{extra}// ==/UserScript==\n{body}\n"
    )


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LensStore:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    assert lenses_dir() == tmp_path / "cfg" / "lenses"
    return LensStore()


# ── the monitor ───────────────────────────────────────────────────────────────────


def test_the_check_is_paced_from_the_last_check_not_from_boot() -> None:
    now = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)
    assert lens_updates.seconds_until_due(None, now=now) == 0.0
    assert lens_updates.seconds_until_due("garbage", now=now) == 0.0
    an_hour_ago = (now - dt.timedelta(hours=1)).isoformat()
    assert lens_updates.seconds_until_due(an_hour_ago, now=now) == pytest.approx(23 * 3600)
    yesterday = (now - dt.timedelta(hours=25)).isoformat()
    assert lens_updates.seconds_until_due(yesterday, now=now) == 0.0


async def test_a_pass_that_stages_an_update_notifies_once(store, monkeypatch) -> None:
    store.save(_src(), origin="url", install_url=URL)
    monkeypatch.setattr(
        "navig.lenses.store.fetch_text", lambda url: _src("1.1.0", "window.__v = 2;")
    )
    sent: list[tuple[str, str]] = []

    async def fake_dispatch(type_key, title, body="", **kw):  # noqa: ANN001
        sent.append((type_key, title))
        return {"ok": True}

    monkeypatch.setattr("navig.notify.dispatch", fake_dispatch)
    rows = await lens_updates.check_once()
    assert [r["status"] for r in rows or []] == ["staged"]
    assert sent == [("lens_review", "1 Lens update to review")]
    rows = await lens_updates.check_once()  # the same version again: already waiting
    assert [r["status"] for r in rows or []] == ["pending"]
    assert len(sent) == 1


async def test_a_failed_pass_returns_none_so_the_loop_backs_off(monkeypatch) -> None:
    def boom(self, **kw):  # noqa: ANN001, ANN003
        raise OSError("index unreadable")

    monkeypatch.setattr(LensStore, "check_updates", boom)
    assert await lens_updates.check_once() is None


def test_the_monitor_is_registered_default_on_in_both_tables() -> None:
    from navig.gateway.deck.routes.notify import _DEFAULT_ON, _MONITOR_KEYS
    from navig.gateway.server import NavigGateway
    from navig.notify.producers.samples import monitor_sample
    from navig.notify.types import TYPE_KEYS

    assert "lens_updates" in NavigGateway.MONITOR_KEYS and "lens_updates" in _MONITOR_KEYS
    assert "lens_updates" in NavigGateway.MONITORS_DEFAULT_ON and "lens_updates" in _DEFAULT_ON
    assert "lens_review" in TYPE_KEYS
    assert monitor_sample("lens_updates")[0] == "lens_review"


# ── navig lens review | approve | reject | update ─────────────────────────────────

runner = CliRunner()


def test_review_lists_and_shows_what_an_update_widens(store, monkeypatch) -> None:
    store.save(_src(), origin="url", install_url=URL)
    wider = _src("2.0.0", extra="// @match <all_urls>\n")
    monkeypatch.setattr("navig.lenses.store.fetch_text", lambda url: wider)

    result = runner.invoke(lens_app, ["update"])
    assert result.exit_code == 0, result.output
    assert "staged" in result.output

    listed = runner.invoke(lens_app, ["review"])
    assert listed.exit_code == 0 and "banner" in listed.output and "update" in listed.output

    shown = runner.invoke(lens_app, ["review", "banner"])
    assert shown.exit_code == 0, shown.output
    assert "<all_urls>" in shown.output and "▲" in shown.output

    approved = runner.invoke(lens_app, ["approve", "banner"])
    assert approved.exit_code == 0, approved.output
    assert "<all_urls>" in (store.get("banner").source)  # type: ignore[union-attr]
    assert (
        runner.invoke(lens_app, ["review"]).output.strip().endswith("Nothing waiting for review.")
    )


def test_reject_and_unknown_ids_exit_nonzero_with_a_reason(store) -> None:
    store.save(_src(), origin="agent")
    result = runner.invoke(lens_app, ["reject", "banner"])
    assert result.exit_code == 0 and "Deleted the proposed Lens" in result.output
    missing = runner.invoke(lens_app, ["approve", "banner"])
    assert missing.exit_code == 1 and "nothing waiting" in missing.output


def test_update_exits_nonzero_when_a_link_cannot_be_checked(store, monkeypatch) -> None:
    from navig.lenses import LensFetchError

    store.save(_src(), origin="url", install_url=URL)

    def down(url: str) -> str:
        raise LensFetchError("offline")

    monkeypatch.setattr("navig.lenses.store.fetch_text", down)
    result = runner.invoke(lens_app, ["update"])
    assert result.exit_code == 1 and "could not check" in result.output
