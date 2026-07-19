"""Regression tests for the onboarding bug-fix pass (Operator onboarding).

Covers:
  * ``navig miniapp deploy --no-telegram-only`` — the escape hatch that lets a
    deploy keep the Bearer api_key working (browser deck / remote brain). The
    default stays ON, so this only locks in the new opt-out.
  * ``_deferred_integration_commands`` only ever names commands that EXIST — it
    used to advertise ``navig telegram setup`` / ``email setup`` / ``social
    setup`` / ``matrix setup``, none of which are real verbs.
  * The review step's ``_PHASE_GROUPS`` displays EVERY step in the registry — it
    silently omitted ``lighthouse``, ``deck-deploy``, ``voice-provider`` and
    ``terminal-setup``, so those never appeared in the summary even when run.
"""

from __future__ import annotations

import inspect

import pytest

# ── A1: miniapp telegram-only is now opt-OUT-able ────────────────────────────


class _RecordingConfig:
    """Minimal Config stand-in: records `.set()` calls and reads them back."""

    def __init__(self, initial: dict[str, object] | None = None) -> None:
        self.values: dict[str, object] = dict(initial or {})
        self.sets: dict[str, object] = {}

    def get(self, key: str, default=None):  # noqa: ANN001, ANN201
        return self.values.get(key, default)

    def set(self, key: str, value, scope: str = "global") -> None:  # noqa: ANN001
        self.sets[key] = value
        self.values[key] = value

    def save(self, scope: str = "global") -> None:
        pass


def _run_deploy(monkeypatch, **kwargs) -> _RecordingConfig:
    """Run run_miniapp_deploy far enough to observe the telegram_only write.

    The lockdown write happens before any deck/bundle resolution, so forcing the
    "no deck found" early return is enough — and keeps the test hermetic (no
    npm, no Cloudflare, no network).
    """
    from navig.commands import miniapp

    cfg = _RecordingConfig()
    monkeypatch.setattr("navig.core.Config", lambda: cfg)
    # No source deck and no prebuilt bundle → early `no_deck` return.
    monkeypatch.setattr(miniapp, "_find_deck_dir", lambda *_a, **_k: None)
    monkeypatch.setattr(miniapp, "_find_prebuilt_deck_out", lambda *_a, **_k: None)

    res = miniapp.run_miniapp_deploy(**kwargs)
    assert res["ok"] is False
    assert res["status"] == "no_deck"
    return cfg


def test_miniapp_deploy_locks_to_telegram_by_default(monkeypatch):
    """The default is unchanged: a deployed Mini App is Telegram-only."""
    cfg = _run_deploy(monkeypatch)
    assert cfg.sets.get("deck.telegram_only") is True


def test_miniapp_deploy_no_telegram_only_opts_out(monkeypatch):
    """`--no-telegram-only` must NOT lock the daemon.

    Without this escape hatch the only way to undo the lockdown was a follow-up
    `navig config set deck.telegram_only false` — which you had to know existed.
    """
    cfg = _run_deploy(monkeypatch, telegram_only=False)
    assert "deck.telegram_only" not in cfg.sets


def test_miniapp_deploy_cli_exposes_the_toggle():
    """The CLI must surface the opt-out, defaulting to the existing behaviour."""
    from navig.commands.miniapp import miniapp_deploy, run_miniapp_deploy

    assert inspect.signature(run_miniapp_deploy).parameters["telegram_only"].default is True
    assert "telegram_only" in inspect.signature(miniapp_deploy).parameters


# ── B2: deferred-integration fix-hints must name REAL commands ───────────────

# Verbs the fix-hints used to advertise that have never existed.
_NONEXISTENT = (
    "navig telegram setup",
    "navig email setup",
    "navig social setup",
    "navig matrix setup",
)


def test_deferred_integration_commands_are_real():
    """Every fix-hint must be a command a user can actually run.

    A hint that dead-ends ("command not found") is worse than no hint at all — it
    is the last thing onboarding says to a user who skipped a step.
    """
    from navig.onboarding.engine import EngineState, StepRecord
    from navig.onboarding.runner import _deferred_integration_commands

    step_ids = ["matrix", "email", "social-networks", "telegram-bot", "lighthouse", "deck-deploy"]
    state = EngineState(
        steps=[
            StepRecord(
                id=sid,
                title=sid,
                status="skipped",
                completed_at="",
                duration_ms=0,
                output={},
            )
            for sid in step_ids
        ]
    )
    tiers = dict.fromkeys(step_ids, "optional")

    deferred = _deferred_integration_commands(state, tiers)
    assert deferred, "expected the skipped optional steps to produce fix-hints"

    commands = [cmd for cmd, _desc in deferred]
    for bad in _NONEXISTENT:
        assert bad not in commands, f"fix-hint names a command that does not exist: {bad}"

    # Everything must be one of the verbs the CLI really registers.
    allowed = {
        "navig init --reconfigure",
        "navig lighthouse deploy",
        "navig miniapp deploy",
    }
    assert set(commands) <= allowed, f"unexpected fix-hint command(s): {set(commands) - allowed}"


def test_canonical_map_only_names_real_commands():
    """The single source of truth (`INTEGRATION_FIX_HINTS`) must be honest.

    This is the map both the onboarding runner AND the TUI review screen read. Verifying it
    directly against the real command manifest means neither consumer can advertise a
    command that does not exist — which is exactly how `navig matrix setup` shipped.
    """
    from navig.onboarding.runner import INTEGRATION_FIX_HINTS
    from navig.registry.manifest import build_public_manifest

    real_paths = {c["path"] for c in build_public_manifest(validate=False)["commands"]}

    def resolves(command: str) -> bool:
        tokens = [t for t in command.split() if not t.startswith("-")]
        return any(" ".join(tokens[:n]) in real_paths for n in range(len(tokens), 0, -1))

    for step_id, (cmd, desc) in INTEGRATION_FIX_HINTS.items():
        assert resolves(cmd), f"{step_id!r} maps to {cmd!r}, which is not a real CLI command"
        assert desc.strip(), f"{step_id!r} has an empty description"


def test_review_screen_deferred_commands_are_real():
    """The TUI setup-complete screen must not advertise a fake command either.

    `review.py::_deferred_commands` had its own hand-copied list that still printed
    `navig matrix setup` / `navig social setup` after the runner was fixed. It now reads the
    canonical map; this pins that every command it can emit — across every onboarding tier —
    actually resolves, so the drift cannot come back silently.
    """
    from types import SimpleNamespace

    # review.py needs `textual` (a core dep, but absent from a stripped dev venv). Skip
    # cleanly rather than error if the screen module cannot import for any reason — the
    # canonical-map test above needs no textual and guards the shared data regardless; this
    # one adds coverage of the screen's wiring wherever the TUI actually imports.
    pytest.importorskip("textual")
    review = pytest.importorskip("navig.tui.screens.review")
    FinalScreen = review.FinalScreen

    from navig.registry.manifest import build_public_manifest

    real_paths = {c["path"] for c in build_public_manifest(validate=False)["commands"]}

    def resolves(command: str) -> bool:
        tokens = [t for t in command.split() if not t.startswith("-")]
        return any(" ".join(tokens[:n]) in real_paths for n in range(len(tokens), 0, -1))

    # Drive every branch: each tier, and the flag-based path with nothing set up.
    configs = [
        SimpleNamespace(onboarding_tier="essential"),
        SimpleNamespace(onboarding_tier="recommended"),
        SimpleNamespace(
            onboarding_tier="minimal",
            setup_matrix=False,
            setup_email=False,
            setup_social=False,
        ),
    ]
    for cfg in configs:
        screen = FinalScreen.__new__(FinalScreen)  # no Textual app needed for this pure method
        screen._cfg = cfg
        for cmd in screen._deferred_commands():
            assert resolves(cmd), (
                f"review screen would tell the user to run {cmd!r}, which is not a real "
                f"command (tier={getattr(cfg, 'onboarding_tier', '?')})"
            )


# ── B5: the review summary must not hide steps ───────────────────────────────


def test_review_phase_groups_cover_every_registry_step(tmp_path):
    """Every step the engine can run must be visible in the review summary.

    `_PHASE_GROUPS` filters the summary by id, so an id missing from the groups is
    invisible forever — which is exactly what happened to `lighthouse` and
    `deck-deploy`: you could deploy your edge and your Mini App and the summary
    would still act as if you had done neither.
    """
    from navig.onboarding.engine import EngineConfig
    from navig.onboarding.genesis import GenesisData
    from navig.onboarding.steps import _PHASE_GROUPS, build_step_registry

    genesis = GenesisData(
        nodeId="test-node",
        name="test",
        bornAt="1970-01-01T00:00:00Z",
        engineVersion="test",
        avatarPath=None,
        avatarSeed="seed",
        qrTarget="",
    )
    steps = build_step_registry(
        EngineConfig(navig_dir=tmp_path, node_name="test"), genesis
    )
    registry_ids = {s.id for s in steps}

    grouped: set[str] = set()
    for _phase, ids in _PHASE_GROUPS:
        grouped.update(ids)

    missing = registry_ids - grouped
    assert not missing, f"steps missing from the review summary: {sorted(missing)}"

    # And nothing phantom: every grouped id must be a real step.
    phantom = grouped - registry_ids
    assert not phantom, f"review summary lists steps that don't exist: {sorted(phantom)}"


# ── The Deck's URL must not corrupt the BRAIN's reachability ─────────────────
#
# `cloud.public_url` is the brain's direct-mode ingress: `navig cloud direct` and
# the tailscale funnel write it, and CloudManager reads it to decide "this brain
# is publicly reachable at <url>, no tunnel needed". `run_miniapp_deploy` used to
# overwrite it with the DECK's URL — a static-asset Worker that cannot serve
# /api/deck/* — so after any deck deploy the brain believed it was directly
# reachable at a site that can't answer it.


DECK_URL = "https://navig-deck.example.workers.dev"


def _run_successful_deploy(monkeypatch, tmp_path, initial=None) -> _RecordingConfig:
    """Drive run_miniapp_deploy through its SUCCESS path, hermetically.

    No npm, no Cloudflare, no network: a prebuilt bundle is faked and the upload
    is stubbed out, so we can assert exactly which config keys a real deploy writes.
    """
    from navig.cloud import deck_deploy
    from navig.commands import lighthouse, miniapp

    cfg = _RecordingConfig(initial)
    monkeypatch.setattr("navig.core.Config", lambda: cfg)
    monkeypatch.setattr(miniapp, "_find_deck_dir", lambda *_a, **_k: None)
    monkeypatch.setattr(miniapp, "_find_prebuilt_deck_out", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(lighthouse, "resolve_cf_token", lambda *_a, **_k: "cf-token")

    class _Res:
        url = DECK_URL

    monkeypatch.setattr(deck_deploy, "deploy", lambda *_a, **_k: _Res())

    res = miniapp.run_miniapp_deploy(register=False)
    assert res["ok"] is True, res
    assert res["url"] == DECK_URL
    return cfg


def test_deploy_records_the_deck_url(monkeypatch, tmp_path):
    """A successful deploy must record the deck's own URL."""
    cfg = _run_successful_deploy(monkeypatch, tmp_path)
    assert cfg.sets["deck.public_url"] == DECK_URL


def test_deploy_does_not_touch_the_brains_reachability(monkeypatch, tmp_path):
    """The deck's URL is NOT the brain's address — never write it to cloud.public_url."""
    cfg = _run_successful_deploy(monkeypatch, tmp_path)
    assert "cloud.public_url" not in cfg.sets, (
        "miniapp deploy wrote the Deck's URL into the brain's direct-mode ingress"
    )


def test_deploy_self_heals_a_reachability_corrupted_by_an_older_navig(monkeypatch, tmp_path):
    """An older navig set cloud.public_url = the deck URL. Clear that provably-wrong value.

    Leaving it in place keeps CloudManager in direct mode aimed at a static site.
    """
    cfg = _run_successful_deploy(
        monkeypatch, tmp_path, initial={"cloud.public_url": DECK_URL}
    )
    assert cfg.sets.get("cloud.public_url") == "", "stale deck URL was not cleared"


def test_deploy_preserves_a_REAL_direct_public_url(monkeypatch, tmp_path):
    """A genuine brain URL (a VPS) must survive a deck deploy untouched."""
    real = "https://brain.example.com"
    cfg = _run_successful_deploy(monkeypatch, tmp_path, initial={"cloud.public_url": real})
    assert "cloud.public_url" not in cfg.sets, "a real brain URL was clobbered by a deck deploy"
    assert cfg.values["cloud.public_url"] == real


def test_register_resolves_the_deck_url_not_the_brain_url(monkeypatch):
    """`miniapp register` points the bot's menu button at the DECK.

    It used to read cloud.public_url — which is the brain. Prefer deck.public_url,
    keeping cloud.public_url only as a legacy fallback for pre-deck.public_url decks.
    """
    from navig.commands import miniapp

    brain = "https://brain.example.com"
    cfg = _RecordingConfig({"deck.public_url": DECK_URL, "cloud.public_url": brain})
    monkeypatch.setattr("navig.core.Config", lambda: cfg)
    assert miniapp._resolve_public_url() == DECK_URL

    # Legacy deck (deployed before deck.public_url existed) still resolves.
    legacy = _RecordingConfig({"cloud.public_url": DECK_URL})
    monkeypatch.setattr("navig.core.Config", lambda: legacy)
    assert miniapp._resolve_public_url() == DECK_URL
