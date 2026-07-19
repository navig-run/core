"""Two navig brains must not share owned state.

NAVIG's model is *one brain per config dir* (`NAVIG_CONFIG_DIR`). A whole family of
outages came from a single brain reaching into another's state, because some path was
resolved from a machine-global location instead of the configured dir:

  * #173 — `navig gateway start` force-killed an unrelated brain's daemon by port.
  * #180 — the agent wrote its pid to `~/.navig/agent` while the readers used
    `config_dir()/agent`; under a custom config dir it became unstoppable.
  * #192 — every debug diagnostic read `~/.navig/debug.log`, a file the logger (which
    writes to `log_dir()`) never touches.
  * #196 — a second brain's scheduler read `gateway.json` from the real home and
    resolved the URL of the *operator's live gateway*, then fired HTTP into it.

Each was one file whose reader and writer disagreed about which brain owned it. This test
is the standing invariant those fixes were converging on: enumerate **every** path
function, resolve it for two brains on two config dirs, and pin which paths are per-brain
(must differ) versus machine-global (shared by design). A NEW path function that lands in
neither bucket fails the test, forcing a conscious "is this brain state or machine state?"
decision instead of the silent default that caused all four incidents.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from navig.platform import paths

# Per-brain OWNED state: derives from config_dir(), so it MUST differ between two brains
# on two config dirs. If any of these ever stops following NAVIG_CONFIG_DIR, a second
# brain starts reading/writing the first brain's config, vault, spaces, sessions, …
EXPECTED_OWNED: frozenset[str] = frozenset({
    "config_dir",           # config.yaml, license, gateway.json, the brain's identity
    "data_dir",             # databases
    "store_dir",            # user content store
    "packages_dir",         # installed packs
    "stack_dir",            # infra stack (user default; a system service is machine-wide)
    "vault_dir",            # SECRETS — the one that must never leak across brains
    "blackbox_dir",         # telemetry / crash blackbox (under data_dir)
    "workspace_dir",        # SOUL/IDENTITY/USER workspace anchors
    "audio_configs_dir",
    "media_budget_path",
    "genesis_json_path",
    "entity_json_path",
    "onboarding_json_path",
    "global_config_path",
    "msg_trace_path",
})

# MACHINE-GLOBAL: intentionally shared across every brain on the machine. Each needs a
# reason — this is the reviewed list, not an accident.
EXPECTED_SHARED: dict[str, str] = {
    "home_dir": "the OS home directory — not navig state",
    "ssh_key_dir": "~/.ssh — an OS-level location navig reads, never brain state",
    "builtin_store_dir": "read-only content SHIPPED inside the package; identical for all",
    "find_app_root": "source-checkout discovery (walks up for pyproject/.git), not state",
    "temp_dir": "%TEMP%/navig — ephemeral scratch, not durable per-brain state",
    # ── The three below are why #192 happened: two brains genuinely SHARE their logs.
    # OS-idiomatic logging (all apps → %LOCALAPPDATA%\navig\logs / ~/Library/Logs / XDG
    # state) is a defensible single-brain default, but it means a second brain's logs
    # interleave with the operator's, and the test suite (which isolates only
    # NAVIG_CONFIG_DIR) writes here too. Documented, not fixed — changing it is a product
    # decision (see test_log_and_cache_are_machine_global_by_design). NAVIG_LOG_DIR /
    # NAVIG_CACHE_DIR override them per-brain when isolation is actually required.
    "log_dir": "OS-idiomatic log location (%LOCALAPPDATA%/Library/XDG) — shared by design",
    "cache_dir": "OS-idiomatic cache location — shared by design",
    "debug_log_path": "log_dir()/debug.log — shared because log_dir is (see #192)",
}


def _brain_paths(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Every zero-arg Path-returning function in paths.py, resolved for a brain whose ONLY
    isolation is NAVIG_CONFIG_DIR — exactly what a real second brain sets. The per-store
    overrides are cleared so we measure what actually derives from config_dir()."""
    for var in ("NAVIG_DATA_DIR", "NAVIG_LOG_DIR", "NAVIG_CACHE_DIR", "NAVIG_STORE_DIR",
                "NAVIG_PACKAGES_DIR", "NAVIG_STACK_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))

    out: dict[str, Path] = {}
    for name, fn in inspect.getmembers(paths, inspect.isfunction):
        if name.startswith("_") or fn.__module__ != "navig.platform.paths":
            continue
        sig = inspect.signature(fn)
        if any(p.default is inspect.Parameter.empty for p in sig.parameters.values()):
            continue  # needs an argument (e.g. media_dir(kind)) — not a fixed brain path
        try:
            result = fn()
        except Exception:  # noqa: BLE001 — a raising path fn is not our concern here
            continue
        if isinstance(result, Path):
            out[name] = result.resolve()
    return out


def _classify(monkeypatch: pytest.MonkeyPatch):
    a = _brain_paths(Path(r"C:/nav-brain-A/.navig") if _win() else Path("/tmp/nav-brain-A/.navig"),
                     monkeypatch)
    b = _brain_paths(Path(r"C:/nav-brain-B/.navig") if _win() else Path("/tmp/nav-brain-B/.navig"),
                     monkeypatch)
    assert a.keys() == b.keys()
    owned = {n for n in a if a[n] != b[n]}
    shared = {n for n in a if a[n] == b[n]}
    return a, b, owned, shared


def _win() -> bool:
    from navig.platform.paths import is_windows
    return is_windows()


def test_every_path_function_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    """No path function may be left unclassified — a new one forces the 'brain state or
    machine state?' decision that, silently defaulted, caused #173/#180/#192/#196."""
    a, _b, owned, shared = _classify(monkeypatch)
    classified = EXPECTED_OWNED | set(EXPECTED_SHARED)
    unclassified = sorted(set(a) - classified)
    assert not unclassified, (
        "New path function(s) not classified as per-brain OWNED or machine-global SHARED:\n"
        + "\n".join(f"  {n} -> {a[n]}" for n in unclassified)
        + "\n\nAdd each to EXPECTED_OWNED (if it must differ per brain) or EXPECTED_SHARED "
        "(with a reason). Defaulting silently is exactly how a second brain ends up reading "
        "the operator's state."
    )


def test_owned_state_differs_between_brains(monkeypatch: pytest.MonkeyPatch) -> None:
    """The core invariant: everything a brain OWNS is scoped to its config dir, so two
    brains never touch the same config / vault / spaces / sessions file."""
    _a, _b, owned, _shared = _classify(monkeypatch)
    missing = sorted(EXPECTED_OWNED - owned)
    assert not missing, (
        "These paths are declared per-brain but did NOT change between two config dirs — "
        "they resolve to a shared location, so a second brain reads the first's state:\n"
        + "\n".join(f"  {n}" for n in missing)
    )


def test_no_owned_path_leaks_into_the_other_brain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Beyond 'different', a brain's owned paths must live UNDER its own config dir — never
    inside the other brain's tree (the #180 shape: writer under one root, reader another)."""
    a, b, _owned, _shared = _classify(monkeypatch)
    a_root = a["config_dir"]
    b_root = b["config_dir"]
    for name in EXPECTED_OWNED:
        assert a[name].is_relative_to(a_root), f"brain-A {name} ({a[name]}) escaped {a_root}"
        assert not a[name].is_relative_to(b_root), f"brain-A {name} leaked into brain-B's tree"
        assert b[name].is_relative_to(b_root), f"brain-B {name} ({b[name]}) escaped {b_root}"


def test_shared_set_is_exactly_the_reviewed_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that becomes machine-global unexpectedly (the #192 failure mode) shows up here
    as a shared path missing its reason."""
    _a, _b, _owned, shared = _classify(monkeypatch)
    unexpected = sorted(shared - set(EXPECTED_SHARED))
    assert not unexpected, (
        "These paths are shared across brains but not in the reviewed EXPECTED_SHARED list:\n"
        + "\n".join(f"  {n}" for n in unexpected)
        + "\n\nIf that is intentional, add it with a reason; if not, make it derive from "
        "config_dir()."
    )
    stale = sorted(set(EXPECTED_SHARED) - shared)
    assert not stale, (
        "These are no longer machine-global (now per-brain) — remove from EXPECTED_SHARED "
        "or move to EXPECTED_OWNED:\n" + "\n".join(f"  {n}" for n in stale)
    )


def test_vault_is_never_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one that matters most: two brains must NEVER resolve the same secrets vault."""
    a, b, _owned, _shared = _classify(monkeypatch)
    assert a["vault_dir"] != b["vault_dir"]
    assert not a["vault_dir"].is_relative_to(b["config_dir"])
    # and the vault lives under the brain's own config dir, not a machine-global spot
    assert a["vault_dir"].is_relative_to(a["config_dir"])


def test_log_and_cache_are_machine_global_by_design(monkeypatch: pytest.MonkeyPatch) -> None:
    """DOCUMENTED, not a bug: log_dir/cache_dir/debug_log_path are OS-idiomatic and shared
    across brains — which is precisely why the debug-log readers in #192 could diverge from
    the writer. Pinned so the sharing is a known, reviewed property. NAVIG_LOG_DIR /
    NAVIG_CACHE_DIR isolate them per-brain when a caller actually needs that."""
    a, b, _owned, _shared = _classify(monkeypatch)
    for name in ("log_dir", "cache_dir", "debug_log_path"):
        assert a[name] == b[name], f"{name} unexpectedly became per-brain — update the docs/guard"

    # And the override DOES isolate them, so a caller that needs a private log can get one.
    private = Path(r"C:/nav-A/logs" if _win() else "/tmp/nav-A/logs")
    monkeypatch.setenv("NAVIG_LOG_DIR", str(private))
    from navig.platform.paths import log_dir
    assert log_dir() == private, "NAVIG_LOG_DIR must isolate log_dir per-brain when set"
