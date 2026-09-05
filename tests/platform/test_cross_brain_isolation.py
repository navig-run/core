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
    # Screenshots and browser profiles: both are `config_dir()/...` and both are
    # MKDIR'd by BrowserController / StealthBrowser / DesktopController __init__, so a
    # second brain sharing them would write into the first brain's home. They were
    # hardcoded "~/.navig/..." strings until the write audit caught them creating
    # directories in the operator's real home from the test suite.
    "screenshot_dir",
    # ── User-installed / user-authored content, all `config_dir()/<name>`. Reviewed as a
    # group: each is state the operator INSTALLED or WROTE into this brain, and a second
    # brain reading it is the #180 shape (one brain's writer, another's reader). They are
    # the counterpart of `builtin_store_dir`, which is SHARED precisely because it ships
    # read-only inside the package and is identical everywhere.
    "plugins_dir",          # `navig plugin` installs — code this brain loads and executes
    "scripts_dir",          # `navig script` / ScriptEvolver — user automation (#276/#285)
    "skills_dir",           # `navig evolve skill` / skill_drafter writes, agent reads (#281)
    "spaces_dir",           # `navig space install` clones here — the brain's workshops
    "wiki_dir",             # the GLOBAL wiki; a project-local wiki is a different scope
    "workflows_dir",        # `navig evolve workflow` writes, WorkflowEngine reads (#271)
})

# MACHINE-GLOBAL: intentionally shared across every brain on the machine. Each needs a
# reason — this is the reviewed list, not an accident.
EXPECTED_SHARED: dict[str, str] = {
    "home_dir": "the OS home directory — not navig state",
    "ssh_key_dir": "~/.ssh — an OS-level location navig reads, never brain state",
    "builtin_store_dir": "read-only content SHIPPED inside the package; identical for all",
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

# MACHINE-GLOBAL, but only RESOLVABLE on some platforms. These cannot live in
# EXPECTED_SHARED: that set is asserted to be exactly what resolved, so an entry missing on
# the running OS reads as "no longer machine-global" and fails. Nor can they be omitted —
# then the platform where they DO resolve reports them as an unclassified new path.
#
# This is not hypothetical, and it is why the bucket exists: `shell_rc_path()` returns None
# on Windows (there is no rc file) and a real ``~/.zshrc`` / ``~/.bashrc`` on POSIX. It was
# therefore invisible on the machine this suite is developed on, while on Linux/macOS it
# entered the resolved set accounted for nowhere — so `pytest tests/platform` failed with
# "New path function(s) not classified: shell_rc_path" for anyone who cloned the PUBLIC
# core repo and ran the tests on a non-Windows box. Verified by simulating the POSIX branch
# on Windows — see test_platform_conditional_paths_are_accounted_for.
EXPECTED_SHARED_WHEN_PRESENT: dict[str, str] = {
    "shell_rc_path": "the user's shell rc file (~/.zshrc, ~/.bashrc) — an OS-level file "
                     "navig appends PATH lines to, never brain state; None on Windows",
}


# Not brain state roots at all, so classifying them as per-brain vs machine-global is a
# category error — they answer a different question and belong in neither column.
_NOT_A_BRAIN_PATH: frozenset[str] = frozenset({
    # `find_app_root()` walks up from the **CWD** for a `.navig/` directory. Its value does
    # not derive from config_dir(); it derives from where the process is standing, so it is
    # the same under two brains only because both were measured from one cwd. It was listed
    # in EXPECTED_SHARED as "source-checkout discovery (walks up for pyproject/.git), not
    # state" — wrong twice over: it walks for `.navig/`, and it DOES decide state, because
    # ConfigManager feeds it into `base_dir` (hosts_dir / apps_dir / cache_dir). That false
    # reassurance is part of why the suite spent months writing test state into the source
    # tree; see tests/platform/test_suite_state_isolation.py.
    "find_app_root",
})


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
        if name in _NOT_A_BRAIN_PATH:
            continue
        sig = inspect.signature(fn)
        if any(p.default is inspect.Parameter.empty for p in sig.parameters.values()):
            continue  # needs an argument (e.g. media_dir(kind)) — not a fixed brain path
        # Only CALL functions whose return annotation can produce a Path. This is not a
        # micro-optimisation: enumerating a module and invoking everything in it executes
        # that module's side effects. Here that meant `ensure_dirs()` — which mkdir'd the
        # tree it was handed, and is why this test wrote two directories to the drive root
        # on every run — plus `check_docker()`, which spawns the `docker` CLI, twelve times
        # per run. Neither was ever classified (both return dicts/None), so calling them
        # bought nothing. Soundness rests on the companion test below: every public function
        # in paths.py is annotated, and nothing returns a Path unless its annotation says so.
        ann = sig.return_annotation
        if ann is inspect.Signature.empty or "Path" not in str(ann):
            continue
        try:
            result = fn()
        except Exception:  # noqa: BLE001 — a raising path fn is not our concern here
            continue
        if isinstance(result, Path):  # `Path | None` fns return None on some platforms
            out[name] = result.resolve()
    return out


def test_annotation_filter_cannot_miss_a_path_function(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cheap annotation filter in ``_brain_paths`` must agree with reality.

    ``_brain_paths`` runs 12x per session and skips any function whose annotation cannot
    yield a ``Path``. That shortcut is what stops it executing ``ensure_dirs()`` and
    spawning ``docker`` — but it is only sound while the annotations are honest. A function
    annotated ``-> Any`` (or left unannotated) that actually hands back a ``Path`` would be
    silently skipped, and a genuinely new brain path would escape classification entirely —
    the exact silent-default failure this module exists to prevent.

    So this test pays the full price ONCE: it calls every zero-arg public function and
    asserts that the set which really returns a ``Path`` is a subset of the set the
    annotations predicted. The reverse direction is legitimately allowed — ``find_app_root``
    and ``shell_rc_path`` are ``Path | None`` and return None on some platforms.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "probe" / ".navig"))

    predicted: set[str] = set()
    actual: set[str] = set()
    unannotated: list[str] = []
    for name, fn in inspect.getmembers(paths, inspect.isfunction):
        if name.startswith("_") or fn.__module__ != "navig.platform.paths":
            continue
        sig = inspect.signature(fn)
        if any(p.default is inspect.Parameter.empty for p in sig.parameters.values()):
            continue
        ann = sig.return_annotation
        if ann is inspect.Signature.empty:
            unannotated.append(name)
        elif "Path" in str(ann):
            predicted.add(name)
        try:
            if isinstance(fn(), Path):
                actual.add(name)
        except Exception:  # noqa: BLE001 — a raising path fn is not our concern here
            continue

    assert not unannotated, (
        "These public paths.py functions have no return annotation, so _brain_paths cannot "
        "tell whether they yield a Path and will SKIP them:\n"
        + "\n".join(f"  {n}" for n in sorted(unannotated))
        + "\n\nAnnotate them — the filter's soundness depends on it."
    )
    missed = sorted(actual - predicted)
    assert not missed, (
        "These functions RETURN a Path but their annotation does not say so, so "
        "_brain_paths skips them and they escape classification entirely:\n"
        + "\n".join(f"  {n} -> {inspect.signature(getattr(paths, n)).return_annotation}"
                    for n in missed)
        + "\n\nFix the annotation (an accurate `-> Path` / `-> Path | None`), or the "
        "cross-brain invariant silently stops covering them."
    )


def _classify(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Resolve two brains, both rooted inside pytest's per-test tmp dir.

    The roots MUST stay under ``tmp_path``. They were hardcoded to ``C:/nav-brain-A`` and
    ``C:/nav-brain-B`` (``/tmp/...`` off Windows), and ``_brain_paths`` used to call **every**
    zero-arg function in ``paths.py`` — ``ensure_dirs()`` among them, which creates the tree it
    is handed. So this test silently created two directories at the **drive root** on every
    single run while reporting green, and they accumulated for weeks.

    Both halves of that are now closed: the roots are contained here, and ``_brain_paths`` no
    longer calls the non-Path functions at all. The assert below is the remaining belt-and-
    braces — it stops a future edit from re-hardcoding a root, whatever else changes.
    """
    a = _brain_paths(tmp_path / "brain-A" / ".navig", monkeypatch)
    b = _brain_paths(tmp_path / "brain-B" / ".navig", monkeypatch)
    assert a.keys() == b.keys()

    # Only the OWNED paths are checked: the machine-global ones (log_dir, cache_dir,
    # temp_dir) resolve to OS-idiomatic locations outside tmp *by design*. Everything a
    # brain owns lives under its config_dir (pinned by
    # test_no_owned_path_leaks_into_the_other_brain), so containing the root contains all.
    root = tmp_path.resolve()
    for label, brain in (("A", a), ("B", b)):
        assert brain["config_dir"].is_relative_to(root), (
            f"brain-{label} config_dir resolved to {brain['config_dir']}, outside pytest's "
            f"tmp dir ({root}). Anything this module materialises must land under tmp_path — "
            "a literal root litters the filesystem. Use tmp_path, never a hardcoded path."
        )

    owned = {n for n in a if a[n] != b[n]}
    shared = {n for n in a if a[n] == b[n]}
    return a, b, owned, shared


def test_every_path_function_is_classified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No path function may be left unclassified — a new one forces the 'brain state or
    machine state?' decision that, silently defaulted, caused #173/#180/#192/#196."""
    a, _b, owned, shared = _classify(monkeypatch, tmp_path)
    classified = EXPECTED_OWNED | set(EXPECTED_SHARED) | set(EXPECTED_SHARED_WHEN_PRESENT)
    unclassified = sorted(set(a) - classified)
    assert not unclassified, (
        "New path function(s) not classified as per-brain OWNED or machine-global SHARED:\n"
        + "\n".join(f"  {n} -> {a[n]}" for n in unclassified)
        + "\n\nAdd each to EXPECTED_OWNED (if it must differ per brain) or EXPECTED_SHARED "
        "(with a reason). Defaulting silently is exactly how a second brain ends up reading "
        "the operator's state."
    )


def test_owned_state_differs_between_brains(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The core invariant: everything a brain OWNS is scoped to its config dir, so two
    brains never touch the same config / vault / spaces / sessions file."""
    _a, _b, owned, _shared = _classify(monkeypatch, tmp_path)
    missing = sorted(EXPECTED_OWNED - owned)
    assert not missing, (
        "These paths are declared per-brain but did NOT change between two config dirs — "
        "they resolve to a shared location, so a second brain reads the first's state:\n"
        + "\n".join(f"  {n}" for n in missing)
    )


def test_no_owned_path_leaks_into_the_other_brain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Beyond 'different', a brain's owned paths must live UNDER its own config dir — never
    inside the other brain's tree (the #180 shape: writer under one root, reader another)."""
    a, b, _owned, _shared = _classify(monkeypatch, tmp_path)
    a_root = a["config_dir"]
    b_root = b["config_dir"]
    for name in EXPECTED_OWNED:
        assert a[name].is_relative_to(a_root), f"brain-A {name} ({a[name]}) escaped {a_root}"
        assert not a[name].is_relative_to(b_root), f"brain-A {name} leaked into brain-B's tree"
        assert b[name].is_relative_to(b_root), f"brain-B {name} ({b[name]}) escaped {b_root}"


def test_shared_set_is_exactly_the_reviewed_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A path that becomes machine-global unexpectedly (the #192 failure mode) shows up here
    as a shared path missing its reason."""
    _a, _b, _owned, shared = _classify(monkeypatch, tmp_path)
    unexpected = sorted(shared - set(EXPECTED_SHARED) - set(EXPECTED_SHARED_WHEN_PRESENT))
    assert not unexpected, (
        "These paths are shared across brains but not in the reviewed EXPECTED_SHARED list:\n"
        + "\n".join(f"  {n}" for n in unexpected)
        + "\n\nIf that is intentional, add it with a reason; if not, make it derive from "
        "config_dir()."
    )
    # Only EXPECTED_SHARED is required to be present. EXPECTED_SHARED_WHEN_PRESENT is
    # deliberately excluded: its entries resolve on some platforms and not others, and
    # demanding them here is precisely what would break the OS where they are None.
    stale = sorted(set(EXPECTED_SHARED) - shared)
    assert not stale, (
        "These are no longer machine-global (now per-brain) — remove from EXPECTED_SHARED "
        "or move to EXPECTED_OWNED:\n" + "\n".join(f"  {n}" for n in stale)
    )


def test_platform_conditional_paths_are_accounted_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that only resolves on SOME platforms must still be classified on those.

    ``shell_rc_path()`` returns None on Windows and a real ``~/.zshrc`` / ``~/.bashrc`` on
    POSIX — so the platform this suite is developed on is exactly the one where it stays
    invisible. On Linux/macOS it enters the resolved set, and it was in no list at all, so a
    fresh clone of the PUBLIC core repo failed its very first ``pytest tests/platform`` with
    "New path function(s) not classified: shell_rc_path" — a confusing failure that looks
    like the contributor's fault rather than a platform-conditional path.

    The reasoning alone is not evidence, so this drives the POSIX branch directly. That keeps
    the guard honest on the Windows box where the suite actually runs, instead of deferring
    to a platform nobody here can execute.
    """
    # EXPECTED_OWNED and EXPECTED_SHARED are self-pinning: a name that does not exist shows
    # up as `missing` / `stale` because both are compared against what actually resolved.
    # This bucket cannot be — its entries are ALLOWED to be absent, which is the whole point
    # — so a typo here fires nothing and silently classifies a path forever. Pin the names.
    unknown = sorted(n for n in EXPECTED_SHARED_WHEN_PRESENT if not hasattr(paths, n))
    assert not unknown, (
        "EXPECTED_SHARED_WHEN_PRESENT names functions that do not exist in paths.py:\n"
        + "\n".join(f"  {n}" for n in unknown)
        + "\n\nUnlike the other two sets nothing else catches this, so the path the entry "
        "was meant to cover stays unclassified on the platform where it resolves."
    )

    monkeypatch.setattr(paths, "is_windows", lambda: False)
    monkeypatch.setattr(paths, "shell_name", lambda: "bash")

    resolved = paths.shell_rc_path()
    assert isinstance(resolved, Path), (
        "shell_rc_path's POSIX branch no longer yields a Path. If it can never resolve on "
        "any platform, drop it from EXPECTED_SHARED_WHEN_PRESENT — a bucket entry that is "
        "always absent is dead weight that hides the next real one."
    )
    known = EXPECTED_OWNED | set(EXPECTED_SHARED) | set(EXPECTED_SHARED_WHEN_PRESENT)
    assert "shell_rc_path" in known, (
        f"shell_rc_path resolves to {resolved} on POSIX, so it joins the classified set "
        "there — but it is in no expected list, so every non-Windows contributor's first "
        "test run fails. Add it to EXPECTED_SHARED_WHEN_PRESENT with a reason."
    )


def test_vault_is_never_shared(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The one that matters most: two brains must NEVER resolve the same secrets vault."""
    a, b, _owned, _shared = _classify(monkeypatch, tmp_path)
    assert a["vault_dir"] != b["vault_dir"]
    assert not a["vault_dir"].is_relative_to(b["config_dir"])
    # and the vault lives under the brain's own config dir, not a machine-global spot
    assert a["vault_dir"].is_relative_to(a["config_dir"])


def test_log_and_cache_are_machine_global_by_design(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DOCUMENTED, not a bug: log_dir/cache_dir/debug_log_path are OS-idiomatic and shared
    across brains — which is precisely why the debug-log readers in #192 could diverge from
    the writer. Pinned so the sharing is a known, reviewed property. NAVIG_LOG_DIR /
    NAVIG_CACHE_DIR isolate them per-brain when a caller actually needs that."""
    a, b, _owned, _shared = _classify(monkeypatch, tmp_path)
    for name in ("log_dir", "cache_dir", "debug_log_path"):
        assert a[name] == b[name], f"{name} unexpectedly became per-brain — update the docs/guard"

    # And the override DOES isolate them, so a caller that needs a private log can get one.
    private = tmp_path / "private-logs"
    monkeypatch.setenv("NAVIG_LOG_DIR", str(private))
    from navig.platform.paths import log_dir
    assert log_dir() == private, "NAVIG_LOG_DIR must isolate log_dir per-brain when set"
