"""CLI surface for Connected Spaces: link / unlink / parent / related / context.

Drives the real typer app (same harness as test_space_commands_unified) against a
per-test isolated registry. Pins the user-visible contract:
- link → related shows the edge (JSON + table), unlink removes it;
- parent refuses cycles with exit 1 and the chain named;
- related reports unavailable targets without failing;
- context shows provenance rows, honours the deny list, and echoes --for;
- unlink of a STALE id (target since deleted) still comes off the manifest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.space import space_app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture(autouse=True)
def _own_registry(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "_cfg"
    (cfg / "spaces").mkdir(parents=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("NAVIG_SPACE", raising=False)
    yield cfg


def _mk(cfg: Path, sid: str) -> Path:
    p = cfg / "spaces" / f"{sid}-space"
    (p / ".navig").mkdir(parents=True)
    (p / ".navig" / "space.json").write_text(
        json.dumps({"id": sid, "name": sid.title(), "version": "1.0.0"}) + "\n", encoding="utf-8"
    )
    res = runner.invoke(space_app, ["register", str(p)])
    assert res.exit_code == 0, f"register {sid} failed: {res.stdout}"
    return p


def _json_of(res) -> dict:
    return json.loads(res.stdout[res.stdout.index("{") :])


def test_link_related_unlink_round_trip(_own_registry):
    cfg = _own_registry
    _mk(cfg, "alpha")
    _mk(cfg, "beta")

    linked = runner.invoke(
        space_app, ["link", "alpha", "beta", "--include", "wiki/**", "--exclude", "vault/**", "--json"]
    )
    assert linked.exit_code == 0, linked.stdout
    data = _json_of(linked)
    assert data["ok"] and data["space"] == "alpha"
    assert data["relations"]["links"][0]["space"] == "beta"
    assert data["relations"]["links"][0]["include"] == ["wiki/**"]

    related = runner.invoke(space_app, ["related", "alpha", "--json"])
    assert related.exit_code == 0, related.stdout
    payload = _json_of(related)
    assert payload["links"][0]["id"] == "beta"
    assert payload["links"][0]["available"] is True
    assert payload["parent_cycle"] is False

    unlinked = runner.invoke(space_app, ["unlink", "alpha", "beta", "--json"])
    assert unlinked.exit_code == 0, unlinked.stdout
    rel_after = _json_of(unlinked)["relations"]
    # to_dict() returns None once the block is empty — the manifest key is DELETED,
    # so the JSON says null rather than an empty object.
    assert rel_after is None or rel_after.get("links", []) == []

    after = _json_of(runner.invoke(space_app, ["related", "alpha", "--json"]))
    assert after["links"] == []


def test_parent_refuses_a_cycle_with_the_chain_named(_own_registry):
    cfg = _own_registry
    _mk(cfg, "alpha")
    _mk(cfg, "beta")
    _mk(cfg, "gamma")

    assert runner.invoke(space_app, ["parent", "alpha", "beta"]).exit_code == 0
    assert runner.invoke(space_app, ["parent", "beta", "gamma"]).exit_code == 0
    cyclic = runner.invoke(space_app, ["parent", "gamma", "alpha"])
    assert cyclic.exit_code == 1
    assert "cycle" in cyclic.stdout
    assert "alpha" in cyclic.stdout  # the chain endpoint is named

    cleared = runner.invoke(space_app, ["parent", "beta", "--clear"])
    assert cleared.exit_code == 0
    payload = _json_of(runner.invoke(space_app, ["related", "beta", "--json"]))
    assert payload["parent"] is None


def test_related_reports_unknown_targets_without_failing(_own_registry):
    cfg = _own_registry
    alpha = _mk(cfg, "alpha")
    manifest = alpha / ".navig" / "space.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["relations"] = {"links": [{"space": "vanished", "access": "read"}]}
    manifest.write_text(json.dumps(data) + "\n", encoding="utf-8")

    res = runner.invoke(space_app, ["related", "alpha", "--json"])
    assert res.exit_code == 0, res.stdout  # unavailable is data, not an error
    payload = _json_of(res)
    assert payload["links"][0]["available"] is False
    assert payload["links"][0]["reason"] == "unknown-id"
    assert payload["unresolved"] == ["vanished"]


def test_unlink_of_a_stale_id_still_comes_off(_own_registry):
    """Target deleted/unregistered since the edge was written — unlink by raw id."""
    cfg = _own_registry
    alpha = _mk(cfg, "alpha")
    manifest = alpha / ".navig" / "space.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["relations"] = {"links": [{"space": "gone-away", "access": "read"}]}
    manifest.write_text(json.dumps(data) + "\n", encoding="utf-8")

    res = runner.invoke(space_app, ["unlink", "alpha", "gone-away", "--json"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "relations" not in data or not data["relations"].get("links")


def test_context_shows_provenance_and_honours_deny(_own_registry):
    cfg = _own_registry
    _mk(cfg, "alpha")
    beta = _mk(cfg, "beta")
    (beta / ".navig" / "wiki").mkdir(parents=True, exist_ok=True)
    (beta / ".navig" / "wiki" / "guide.md").write_text("# guide", encoding="utf-8")
    (beta / "vault").mkdir(exist_ok=True)
    (beta / "vault" / "keys.md").write_text("secret", encoding="utf-8")

    assert runner.invoke(space_app, ["link", "alpha", "beta", "--include", "**"]).exit_code == 0

    res = runner.invoke(space_app, ["context", "alpha", "--for", "deploy help", "--json"])
    assert res.exit_code == 0, res.stdout
    payload = _json_of(res)
    assert payload["space"] == "alpha"
    assert payload["focus"] == "deploy help"
    rels = [i["rel"] for i in payload["items"]]
    assert ".navig/wiki/guide.md" in rels
    assert all("vault" not in r for r in rels), f"deny list leaked: {rels}"
    item = next(i for i in payload["items"] if i["rel"] == ".navig/wiki/guide.md")
    assert item["space"] == "beta" and item["source"] == "link" and item["path"]

    human = runner.invoke(space_app, ["context", "alpha"])
    assert human.exit_code == 0, human.stdout
    assert ".navig/wiki/guide.md" in human.stdout
    assert "deploy help" not in human.stdout  # --for is json-only metadata


# ---------------------------------------------------------------------------
# --brief (the SessionStart-hook shape)
# ---------------------------------------------------------------------------


def test_context_brief_is_short_and_shows_edges(_own_registry):
    cfg = _own_registry
    _mk(cfg, "alpha")
    beta = _mk(cfg, "beta")
    (beta / ".navig" / "wiki").mkdir(parents=True, exist_ok=True)
    (beta / ".navig" / "wiki" / "a.md").write_text("x", encoding="utf-8")
    assert runner.invoke(space_app, ["link", "alpha", "beta"]).exit_code == 0

    res = runner.invoke(space_app, ["context", "alpha", "--brief"])
    assert res.exit_code == 0, res.stdout
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    assert len(lines) <= 10, f"brief too long ({len(lines)} lines):\n{res.stdout}"
    assert any(ln.startswith("Active space: alpha") for ln in lines)
    assert any("● beta" in ln and "1 file(s)" in ln for ln in lines)
    assert "context:" in res.stdout


def test_context_brief_reports_unavailable_edges(_own_registry):
    cfg = _own_registry
    alpha = _mk(cfg, "alpha")
    manifest = alpha / ".navig" / "space.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["relations"] = {"links": [{"space": "vanished", "access": "read"}]}
    manifest.write_text(json.dumps(data) + "\n", encoding="utf-8")

    res = runner.invoke(space_app, ["context", "alpha", "--brief"])
    assert res.exit_code == 0, res.stdout
    assert "○ vanished" in res.stdout
    assert "unknown-id" in res.stdout


def test_context_brief_and_json_conflict(_own_registry):
    res = runner.invoke(space_app, ["context", "alpha", "--brief", "--json"])
    assert res.exit_code == 2
    assert "mutually exclusive" in res.stdout or "mutually exclusive" in (res.stderr or "")


def test_context_brief_never_fails_a_session(_own_registry, monkeypatch):
    """The hook path degrades to the old one-liner on any reader error."""
    cfg = _own_registry
    _mk(cfg, "alpha")

    def _boom(*_a, **_k):
        raise RuntimeError("relations are on fire")

    monkeypatch.setattr("navig.spaces.linked_context.gather_linked", _boom)
    res = runner.invoke(space_app, ["context", "alpha", "--brief"])
    assert res.exit_code == 0, res.stdout
    assert "Active space: alpha" in res.stdout
    assert "on fire" not in res.stdout  # the failure is swallowed, not surfaced


def test_the_session_start_hook_template_uses_the_brief():
    from navig.commands.wire import _SETTINGS_JSON

    hook = _SETTINGS_JSON["hooks"]["SessionStart"][0]["hooks"][0]
    assert hook["command"] == "navig space context --brief"
