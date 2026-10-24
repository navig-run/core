"""Runtime inventory — the states a report must not blur together.

Every case here is a failure mode the live box actually produced or could: a plugin
present in source and absent from the daemon (navig-msstore, 2026-10-20), an installed
dist whose entry point went missing, a package that loads from another folder, a load
that never happened, and a "healthy" verdict with no evidence at all.

Fixtures are synthetic checkouts in ``tmp_path`` plus a fake site-packages written as
metadata (``*.dist-info``), so nothing here touches the operator's interpreters, gateway
log or ``~/.navig`` — and no plugin module is ever imported (the collection under test
must not import either; see ``runtime_inventory``'s module docstring).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from navig.plugins.runtime_inventory import (
    gateway_activation,
    inventory_rows,
    runtime_inventory,
)

# ── fixtures ─────────────────────────────────────────────────────────────────────────


def make_source(root: Path, name: str = "demo", declares: bool = True, version: str = "1.4.0") -> Path:
    """A first-party source tree: ``plugins/navig-<name>`` with a pyproject + package."""
    source = root / "plugins" / f"navig-{name}"
    (source / f"navig_{name}").mkdir(parents=True)
    (source / f"navig_{name}" / "__init__.py").write_text("# pkg\n", encoding="utf-8")
    eps = ""
    if declares:
        eps = (
            '[project.entry-points."navig.plugins"]\n'
            f'{name} = "navig_{name}.plugin"\n'
        )
    (source / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "navig-{name}"
            version = "{version}"
            {eps}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return source


def make_site(tmp_path: Path, name: str = "demo", version: str = "1.4.0", *, editable_to: Path | None = None, entry_point: str | None = "demo") -> Path:
    """A site-packages holding ``navig-{name}`` as metadata (wheel, or editable)."""
    site = tmp_path / "site-packages"
    dist_info = site / f"navig_{name}-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: navig-{name}\nVersion: {version}\n", encoding="utf-8"
    )
    if entry_point is not None:
        (dist_info / "entry_points.txt").write_text(
            f"[navig.plugins]\n{entry_point} = navig_{name}.plugin\n", encoding="utf-8"
        )
    if editable_to is not None:
        import json as _json

        (dist_info / "direct_url.json").write_text(
            _json.dumps({"url": Path(editable_to).as_uri(), "dir_info": {"editable": True}}),
            encoding="utf-8",
        )
        (dist_info / "RECORD").write_text("", encoding="utf-8")
    else:
        pkg = site / f"navig_{name}"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("# installed\n", encoding="utf-8")
    return site


def daemon_env(site: Path, *, exe: str = r"C:\runtime\venv\Scripts\pythonw.exe", ambiguous: bool = False) -> dict:
    return {
        "pid": 1234,
        "exe": exe,
        "site_packages": [str(site)],
        "gateways": 1,
        "ambiguous": ambiguous,
    }


def run(tmp_path: Path, *, root: Path, site: Path | None = None, loaded: set[str] | None = None,
        failed: dict | None = None, bounded: bool = True, **kw) -> dict:
    # ``site=None`` means "the daemon's Python is unreadable" — injected as an explicit
    # shape so the test never falls through to the REAL gateway_environment() on this box.
    daemon = daemon_env(site) if site is not None else {
        "pid": 0, "exe": None, "site_packages": None, "gateways": 0, "ambiguous": False,
    }
    return runtime_inventory(
        root,
        daemon=daemon,
        loaded=loaded if loaded is not None else set(),
        failed=failed or {},
        bounded=bounded,
        **kw,
    )


# ── the four states every report must distinguish ────────────────────────────────────


def test_missing_plugin_is_reported_not_concealed(tmp_path):
    root = tmp_path / "repo"
    make_source(root)  # source exists; the daemon has no such dist
    (root / "plugins").mkdir(exist_ok=True)
    inv = run(tmp_path, root=root, site=tmp_path / "empty-site")
    assert inv is not None
    [rec] = inv["records"]
    assert rec["state"] == "not_installed"
    assert rec["repair"] and "pip install -e" in rec["repair"]

    rows = inventory_rows(inv)
    # The mission's acceptance: a result that hides the gap is a failure.
    assert rows[0]["ok"] is False
    assert "1 not installed" in rows[0]["detail"]
    assert rows[0]["data"]["records"][0]["package"] == "navig-demo"


def test_installed_but_entry_point_missing_is_undiscoverable(tmp_path):
    root = tmp_path / "repo"
    make_source(root, declares=True)
    site = make_site(tmp_path, entry_point=None)  # dist present, entry_points.txt absent
    inv = run(tmp_path, root=root, site=site)
    [rec] = inv["records"]
    assert rec["state"] == "undiscoverable"
    assert "entry point" in rec["activation_detail"]
    assert rec["repair"]


def test_discovered_but_not_loaded_is_inactive_with_unknown_activation(tmp_path):
    root = tmp_path / "repo"
    make_source(root)
    site = make_site(tmp_path)
    inv = run(tmp_path, root=root, site=site, loaded=set())  # evidence exists, no load line
    [rec] = inv["records"]
    assert rec["state"] == "inactive"
    assert rec["activation"] == "unknown"
    assert "not loaded" in rec["activation_detail"]
    assert inv["activation_evidence"] is True


def test_installed_loaded_present_is_healthy(tmp_path):
    root = tmp_path / "repo"
    make_source(root)
    site = make_site(tmp_path)
    inv = run(tmp_path, root=root, site=site, loaded={"demo"})
    [rec] = inv["records"]
    assert rec["state"] == "healthy"
    assert rec["activation"] == "verified"
    assert rec["package_files_present"] is True
    assert rec["daemon_installed"] is True


# ── the environment boundary (the bug that made msstore vanish) ─────────────────────


def test_installed_in_the_cli_but_not_the_daemon_says_so(tmp_path):
    root = tmp_path / "repo"
    make_source(root)
    # Present in THIS interpreter only: fake it through the caller-supplied `elsewhere`
    # plus the records the collection makes from its own metadata.
    inv = run(tmp_path, root=root, site=tmp_path / "empty-site", elsewhere={"navig-demo": r"C:\py313\python.exe"})
    [rec] = inv["records"]
    assert rec["state"] == "not_installed"
    assert rec["installed_elsewhere"].endswith("python.exe")
    assert "not the daemon" in rec["activation_detail"]


def test_wrong_interpreter_is_never_read_as_the_daemons(tmp_path):
    """The daemon's site-packages decides the state, not the caller's interpreter."""
    root = tmp_path / "repo"
    make_source(root)
    site = tmp_path / "empty"  # the daemon has nothing
    inv = run(tmp_path, root=root, site=site)
    assert inv["daemon_known"] is True
    assert inv["records"][0]["state"] == "not_installed"
    # …and the inventory says which Python it measured.
    assert inv["daemon_interpreter"].endswith("pythonw.exe")


def test_unreadable_daemon_python_is_unknown_not_substituted(tmp_path):
    root = tmp_path / "repo"
    make_source(root)
    inv = run(tmp_path, root=root, site=None)  # gateway_environment() → None
    assert inv["daemon_known"] is False
    rows = inventory_rows(inv)
    assert "activation unverified" in rows[0]["detail"]
    assert "gateway.pid" in rows[0]["detail"]
    assert rows[0]["ok"] is False  # green over an unknown is forbidden


# ── drift, dead copies, collisions ───────────────────────────────────────────────────


def test_stale_editable_loads_but_is_flagged_with_a_repair(tmp_path):
    root = tmp_path / "repo"
    source = make_source(root, version="1.5.0")
    elsewhere = tmp_path / "old-checkout" / "navig-demo"
    (elsewhere / "navig_demo").mkdir(parents=True)  # the old checkout, package and all
    (elsewhere / "navig_demo" / "__init__.py").write_text("# old\n", encoding="utf-8")
    site = make_site(tmp_path, version="1.5.0", editable_to=elsewhere, entry_point="demo")
    inv = run(tmp_path, root=root, site=site, loaded={"demo"})
    [rec] = inv["records"]
    assert rec["state"] == "healthy"  # it DOES load — from the wrong folder
    assert rec["stale_editable"] is True
    assert rec["repair"] and "pip install -e" in rec["repair"]
    assert str(source) in rec["source_path"]


def test_editable_pointing_at_a_gone_folder_is_undiscoverable(tmp_path):
    root = tmp_path / "repo"
    make_source(root)
    gone = tmp_path / "deleted-worktree" / "navig-demo"
    site = make_site(tmp_path, editable_to=gone, entry_point="demo")  # folder never exists
    inv = run(tmp_path, root=root, site=site, loaded={"demo"})
    [rec] = inv["records"]
    assert rec["state"] == "undiscoverable"
    assert rec["dead_editable"] is True
    assert "missing folder" in rec["activation_detail"]


def test_conflicting_versions_are_flagged_as_drift(tmp_path):
    root = tmp_path / "repo"
    make_source(root, version="2.0.0")
    site = make_site(tmp_path, version="1.4.0")  # installed copy is older than the source
    inv = run(tmp_path, root=root, site=site, loaded={"demo"})
    [rec] = inv["records"]
    assert rec["version_drift"] is True
    assert rec["expected_version"] == "2.0.0"
    assert rec["daemon_version"] == "1.4.0"
    assert rec["repair"]


def test_a_failed_load_keeps_the_reason(tmp_path):
    root = tmp_path / "repo"
    make_source(root)
    site = make_site(tmp_path)
    inv = run(
        tmp_path, root=root, site=site,
        loaded=set(), failed={"demo": "No module named 'navig_demo.dep'"},
    )
    [rec] = inv["records"]
    assert rec["state"] == "activation_failed"
    assert rec["activation"] == "failed"
    assert "No module named" in rec["activation_detail"]
    rows = inventory_rows(inv)
    assert rows[1]["ok"] is False and rows[1]["label"] == "runtime plugin navig-demo"
    assert rows[1]["data"] is None


def test_two_gateways_on_different_pythons_make_the_log_unattributable(tmp_path):
    root = tmp_path / "repo"
    make_source(root)
    site = make_site(tmp_path)
    daemon = daemon_env(site, ambiguous=True)
    inv = runtime_inventory(root, daemon=daemon, loaded={"demo"}, failed={}, bounded=True)
    assert inv["activation_evidence"] is False
    [rec] = inv["records"]
    assert rec["state"] == "inactive" and rec["activation"] == "unknown"
    assert "gateways on different Pythons" in rec["activation_detail"]


# ── the shapes that must NOT be called broken ────────────────────────────────────────


def test_a_library_with_no_entry_point_is_healthy_not_inactive(tmp_path):
    """navig-vault / navig-media are imported by core; the gateway never logs them.

    The first version of this diagnostic called them undiscoverable, then inactive —
    two false reds on an install that was perfectly healthy.
    """
    root = tmp_path / "repo"
    make_source(root, name="vaultlib", declares=False)
    site = make_site(tmp_path, name="vaultlib", version="1.4.0", entry_point=None)
    inv = run(tmp_path, root=root, site=site, loaded=set())
    [rec] = inv["records"]
    assert rec["state"] == "healthy"
    assert rec["activation"] == "not_applicable"
    assert "no navig.plugins entry point" in rec["activation_detail"]


def test_no_evidence_means_unknown_activation_for_a_loader_plugin(tmp_path):
    """gateway.pid missing → nobody may claim the plugin is active."""
    root = tmp_path / "repo"
    make_source(root)
    site = make_site(tmp_path)
    inv = run(tmp_path, root=root, site=site, loaded={"demo"}, bounded=False)
    [rec] = inv["records"]
    assert rec["activation"] == "unknown" and rec["state"] == "inactive"
    assert inv["activation_evidence"] is False


def test_outside_a_checkout_there_is_no_verdict(tmp_path):
    assert runtime_inventory(tmp_path, daemon=None, loaded=set(), failed={}, bounded=True) is None
    assert inventory_rows(None) == []


# ── the gateway log parser (the only activation evidence) ────────────────────────────


def test_log_evidence_is_bounded_to_the_current_boot(tmp_path):
    import datetime as dt

    log = tmp_path / "gateway.log"
    # Stamps chosen so the bound is deterministic at any time of day: the previous
    # boot sits at 00:00:00, the current one at 23:59:59, and the bound at 00:00:01.
    # chr(10) rather than an escape: this file's bytes must never depend on escaping.
    since = dt.datetime.now().replace(hour=0, minute=0, second=1, microsecond=0).timestamp()
    log.write_text(
        chr(10).join(
            [
                "00:00:00  INFO  plugins        navig plugin loaded: old-plugin",
                "23:59:59  INFO  plugins        navig plugin loaded: fresh-plugin",
                "23:59:59  WARN  plugins        navig plugin 'fresh-plugin' failed to load: boom",
            ]
        )
        + chr(10),
        encoding="utf-8",
    )
    loaded, failed, bounded = gateway_activation(log, since=since)
    assert bounded is True
    assert loaded == {"fresh-plugin"}  # the previous boot's line is not evidence
    assert failed == {"fresh-plugin": "boom"}
    # No bound at all → nothing may be claimed.
    assert gateway_activation(Path(tmp_path / "nope.log"))[2] is False
