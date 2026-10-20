"""`neng install plugin:…` must reach the plugin host, not a retired directory.

Two wrong destinations, one per spelling of the same asset type:

* `neng install plugin:owner/repo` parses to type **`plugin`** (an explicit scheme
  prefix), and `_dest_for` had no branch for it — so it fell through to
  `store_dir()/skills/<id>`, i.e. a plugin installed where a *skill* loader looks.
* `neng install github:owner/repo/plugins/x` infers type **`package`**, which
  `_dest_for` mapped to `packages_dir()` = `~/.neng/packs` — retired with the
  handler-pack removal in 2026-07, scanned by nothing.

Both printed a success line and installed nothing usable. `neng store install` was
already the canonical verb (`docs/plugin-spec.md` → Naming), so the fix forwards to
the SAME dispatcher rather than growing a second install path.

Also pinned: the host cannot honour a ref (`git clone --depth 1`) or a sub-path, and
it refuses a custom install root — each of which must be a loud, non-zero refusal,
never a silent install of something other than what was asked for.
"""

from __future__ import annotations

import pytest
import typer

from navig.commands import install as install_mod


@pytest.fixture()
def captured(monkeypatch):
    """Record what the shared store dispatcher was asked to do."""
    calls: list[tuple[str, str]] = []

    def fake_apply(item_id: str, action: str) -> dict:
        calls.append((item_id, action))
        return {"ok": True, "message": "installed"}

    monkeypatch.setattr("navig.hub.apply_action", fake_apply)
    return calls


# ── both spellings route to the host ─────────────────────────────────────────


def test_an_explicit_plugin_spec_goes_through_the_plugin_host(captured):
    install_mod.install_asset("plugin:navig-run/registry")
    assert captured == [
        ("plugin:https://github.com/navig-run/registry", "install")
    ], "the plugin must be installed through the shared dispatcher, not a local dir"


def test_an_inferred_package_spec_is_refused_instead_of_misfiled(captured):
    """`github:owner/repo/plugins/x` infers type `package` — and always has a sub-path,
    because type inference only runs when a sub-path is present.

    So it can never be forwarded: the host installs a repository root. What matters is
    that it is refused LOUDLY and writes nothing — previously it downloaded the subtree
    into the retired `packs/` directory and reported success.
    """
    with pytest.raises(typer.Exit) as exc:
        install_mod.install_asset("github:navig-run/registry/plugins/x")
    assert exc.value.exit_code == 2
    assert not captured, "nothing may be installed when the spec cannot be honoured"


def test_the_old_destinations_are_no_longer_produced():
    """`plugin` used to land in skills/, `package` in the retired packs/."""
    from navig.platform.paths import plugins_dir, store_dir

    for kind in ("plugin", "package"):
        dest = install_mod._dest_for(kind, "some-plugin")
        assert dest.parent == plugins_dir(), (
            f"{kind} resolved to {dest}; a plugin must live where a loader scans"
        )
        assert "skills" not in dest.parts, f"{kind} fell through to the skills store"
        assert "packs" not in dest.parts, f"{kind} went to the retired packs directory"
        assert store_dir() not in dest.parents


# ── refusals, which must be loud ─────────────────────────────────────────────


def test_a_subpath_is_refused_not_installed(captured):
    with pytest.raises(typer.Exit) as exc:
        install_mod.install_asset("plugin:navig-run/registry/plugins/x")
    assert exc.value.exit_code == 2
    assert not captured, "nothing may be installed when the spec cannot be honoured"


def test_an_explicit_ref_is_refused_not_ignored(captured):
    """The host runs `git clone --depth 1 <url>` — it cannot check out a tag.

    Installing the default branch when `@v1.2` was asked for is not a partial
    success; it is the wrong version of someone else's software.
    """
    with pytest.raises(typer.Exit) as exc:
        install_mod.install_asset("plugin:navig-run/registry@v1.2")
    assert exc.value.exit_code == 2
    assert not captured


def test_a_custom_install_root_is_refused(captured, tmp_path):
    with pytest.raises(typer.Exit) as exc:
        install_mod.install_asset(
            "plugin:navig-run/registry", install_root=tmp_path
        )
    assert exc.value.exit_code == 2
    assert not captured, "a plugin must never be written outside the host's root"


# ── dry-run changes nothing ──────────────────────────────────────────────────


def test_dry_run_does_not_call_the_dispatcher(captured, capsys):
    install_mod.install_asset("plugin:navig-run/registry", dry_run=True)
    assert not captured, "dry-run must not install anything"
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "https://github.com/navig-run/registry" in out


# ── success and failure both propagate honestly ──────────────────────────────


def test_a_host_failure_exits_non_zero(monkeypatch):
    def fail(item_id: str, action: str) -> dict:
        return {"ok": False, "message": "zip-slip guard rejected the archive"}

    monkeypatch.setattr("navig.hub.apply_action", fail)
    with pytest.raises(typer.Exit) as exc:
        install_mod.install_asset("plugin:navig-run/registry")
    assert exc.value.exit_code == 1


def test_a_spec_without_a_source_is_refused(captured):
    """A spec that cannot yield a source must not install something adjacent.

    `_parse_spec` already raises `ValueError` for anything with fewer than two path
    segments (verified: `plugin:/repo` and `plugin:not-a-repo` both raise there), so
    this pins the guard one layer up — the host is never called with an empty source.
    """
    with pytest.raises(ValueError):
        install_mod.install_asset("plugin:not-a-repo")
    assert not captured
