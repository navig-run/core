"""The CATALOG sweep — probing the ids an install can substitute IN.

`probe_routes` covers what an install ROUTES to (modes, fallbacks, tiers).
Nothing covered the two hand-maintained lists those routes are substituted FROM:
`registry.ALL_PROVIDERS[*].models` and `types.BUILTIN_PROVIDERS[*].models`, where
``models[0]`` is both the credential probe and the routing substitution default.

Measured 2026-09-19: the table's first row was retired for all four providers
that machine held a key for. Measured 2026-09-26 with a key added for a fifth:
**all eleven** of groq's manifest ids were gone in one audit. A retirement lands
in the catalog long before it reaches a route, and it was visible from neither
side.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from navig.llm import liveness
from navig.llm.liveness import (
    RETIRED_MODELS,
    catalog_entries,
    denylist_lines,
    is_retired,
    probe_catalog,
    retired_catalog_entries,
    retired_model_ids,
)

# ── catalog_entries: both lists, deduplicated, honest about where ──────────


def test_entries_cover_both_lists_and_mark_the_overlap():
    from navig.providers.registry import get_provider
    from navig.providers.types import BUILTIN_PROVIDERS

    rows = catalog_entries()
    assert len(rows) >= 40, "scan floor: the catalog cannot be nearly empty"

    wheres = {r["where"] for r in rows}
    assert wheres <= {"manifest", "table", "both"}
    assert "both" in wheres, "no id is shared? one of the two lists was not read"
    assert "manifest" in wheres

    # An id named by both lists appears exactly once, marked `both`.
    xai = [r for r in catalog_entries("xai")]
    ids = [r["model"] for r in xai]
    assert len(ids) == len(set(ids)), f"duplicate rows: {ids}"
    table_ids = {m.id for m in BUILTIN_PROVIDERS["xai"].models}
    manifest_ids = set(get_provider("xai").models)
    for r in xai:
        expected = (
            "both" if r["model"] in table_ids and r["model"] in manifest_ids
            else ("table" if r["model"] in table_ids else "manifest")
        )
        assert r["where"] == expected, f"{r['model']}: {r['where']} != {expected}"


def test_entries_keep_catalog_order_so_models0_is_row_one():
    """``models[0]`` is the credential probe and the substitution default, so the
    row an operator reads first must be the one those pick."""
    from navig.providers.registry import get_provider

    rows = catalog_entries("openai")
    assert rows[0]["model"] == get_provider("openai").models[0]


def test_a_provider_filter_is_exact():
    assert {r["provider"] for r in catalog_entries("groq")} == {"groq"}
    assert catalog_entries("no-such-provider") == []


# ── probe_catalog: never a tick over an unknown, never a wasted call ───────


@pytest.fixture
def no_network(monkeypatch):
    """Probe nothing for real; record what WOULD have been called."""
    calls: list[tuple[str, str]] = []

    def _probe(provider, model, **_kw):
        calls.append((provider, model))
        return ("live", "ok")

    monkeypatch.setattr(liveness, "probe_model", _probe)
    return calls


def _keys(monkeypatch, have: set[str]) -> None:
    class _Auth:
        def resolve_auth(self, pid):
            return ("k", "test") if pid in have else (None, "not_found")

    import navig.providers.auth as auth_mod

    monkeypatch.setattr(auth_mod, "AuthProfileManager", lambda: _Auth())


def test_a_provider_with_no_key_is_reported_unjudged_not_green(monkeypatch, no_network):
    _keys(monkeypatch, set())

    rows = probe_catalog("openai")

    assert rows, "a keyless provider must still be listed, not silently dropped"
    assert {r["status"] for r in rows} == {"nokey"}
    # The status carries "not judged"; the detail carries the way OUT of it.
    assert all("navig ai providers --add openai" in r["detail"] for r in rows)
    assert no_network == [], "a keyless provider must cost zero calls"
    assert retired_catalog_entries(rows) == [], "an unjudged id is not a finding"


def test_an_already_denylisted_id_costs_no_call(monkeypatch, no_network):
    _keys(monkeypatch, {"groq"})
    monkeypatch.setattr(
        liveness, "RETIRED_MODELS", frozenset({"groq:openai/gpt-oss-120b"})
    )

    rows = probe_catalog("groq")

    hit = [r for r in rows if r["model"] == "openai/gpt-oss-120b"]
    assert hit and hit[0]["status"] == "denylisted"
    assert ("groq", "openai/gpt-oss-120b") not in no_network
    assert retired_catalog_entries(rows) == [], (
        "a paid debt must not be re-reported as a fresh finding"
    )


def test_an_id_in_both_lists_is_probed_once(monkeypatch, no_network):
    _keys(monkeypatch, {"xai"})

    rows = probe_catalog("xai")

    shared = [r for r in rows if r["where"] == "both"]
    assert shared, "premise: xai names ids in both lists"
    assert len(no_network) == len(set(no_network)), f"duplicate probes: {no_network}"
    assert len(no_network) == len(rows)


def test_the_cache_is_shared_across_calls(monkeypatch, no_network):
    _keys(monkeypatch, {"xai", "groq"})
    cache: dict = {}

    probe_catalog("xai", probed_cache=cache)
    first = len(no_network)
    probe_catalog("xai", probed_cache=cache)

    assert len(no_network) == first, "a second sweep re-probed a cached id"


def test_only_dead_is_a_finding(monkeypatch):
    """`auth` and `unreachable` say nothing about the ID — unlike `dead_modes`,
    which answers a different question ("would a run break")."""
    _keys(monkeypatch, {"groq"})
    verdicts = iter([
        ("dead", "model not found (404)"),
        ("auth", "auth failed (401)"),
        ("unreachable", "endpoint unreachable / timed out"),
        ("slow", "slow/cold, not gone"),
        ("transient", "busy"),
    ])
    monkeypatch.setattr(
        liveness, "probe_model", lambda p, m, **_kw: next(verdicts, ("live", "ok"))
    )

    rows = probe_catalog("groq")

    assert [r["status"] for r in retired_catalog_entries(rows)] == ["dead"]


# ── the denylist line: paste-ready means it PARSES ─────────────────────────


def test_a_denylist_line_round_trips_into_a_real_denylist(monkeypatch):
    """The advice names `RETIRED_MODELS`, so the line it prints must be accepted
    by the thing that reads it. A hint that looks right and does not parse is the
    phantom-hint class."""
    _keys(monkeypatch, {"groq"})
    monkeypatch.setattr(liveness, "probe_model", lambda p, m, **_kw: ("dead", "404"))

    rows = probe_catalog("groq")
    lines = denylist_lines(rows)

    assert len(lines) == len(rows) >= 2
    entries = []
    for line in lines:
        code, _, comment = line.partition("#")
        assert comment.strip(), "every entry must carry its evidence"
        value = ast.literal_eval(code.strip().rstrip(","))
        assert isinstance(value, str) and value.count(":") >= 1
        entries.append(value)

    monkeypatch.setattr(liveness, "RETIRED_MODELS", frozenset(entries))
    for r in rows:
        assert is_retired(r["model"], r["provider"]), f"{r['model']} did not round-trip"
        assert not is_retired(r["model"], "openai"), "an entry leaked to another provider"


def test_the_emitted_shape_matches_the_shipped_file():
    """Teeth against drift: the real file's entries must satisfy the same parse
    the generated line is asserted against."""
    for entry in RETIRED_MODELS:
        prov, sep, model = entry.partition(":")
        assert sep and prov and model, f"unparseable denylist entry: {entry!r}"
        assert prov == prov.lower().strip()
    assert "llama-3.3-70b-versatile" in retired_model_ids("groq")
    assert "llama-3.3-70b-versatile" not in retired_model_ids("openai")


def test_no_denylisted_id_is_still_offered_by_the_catalog():
    """The sweep's whole output is "denylist it, then replace it" — if a shipped
    catalog still lists a denylisted id, half the job was skipped. (The offline
    guard in test_mode_liveness_guard asserts this per-list; this asserts it
    through the same entry point the sweep uses.)"""
    offenders = [
        f"{r['provider']}:{r['model']} (in {r['where']})"
        for r in catalog_entries()
        if is_retired(r["model"], r["provider"])
    ]
    assert not offenders, f"catalog still offers retired id(s): {offenders}"


def test_the_sweep_is_named_where_a_reader_will_look():
    """A capability nobody can find is not wired. The module docstring lists its
    consumers; `navig ai models --check` must be one of them."""
    src = Path(liveness.__file__).read_text(encoding="utf-8")
    assert "navig ai models --check" in src.split('"""')[1]


# ── the CLI surface: `navig ai models --check` ─────────────────────────────


def _run_check(monkeypatch, verdict, args=("models", "--check", "--provider", "groq")):
    from typer.testing import CliRunner

    from navig.commands import ai as ai_cmd

    _keys(monkeypatch, {"groq"})
    monkeypatch.setattr(liveness, "probe_model", lambda p, m, **_kw: verdict)
    return CliRunner().invoke(ai_cmd.ai_app, list(args), env={"COLUMNS": "200"})


def test_check_exits_nonzero_on_a_retired_id_and_prints_the_entry(monkeypatch):
    r = _run_check(monkeypatch, ("dead", "model not found (404)"))

    assert r.exit_code == 1, r.output
    assert "RETIRED" in r.output
    assert "RETIRED_MODELS" in r.output, "the operator is not told where the finding goes"
    assert '"groq:openai/gpt-oss-120b",' in r.output


def test_check_is_green_when_every_id_answers(monkeypatch):
    r = _run_check(monkeypatch, ("live", "ok"))

    assert r.exit_code == 0, r.output
    assert "0 retired" in r.output
    assert "RETIRED_MODELS" not in r.output, "clean runs must not print remediation"


@pytest.mark.parametrize("status", ["slow", "transient", "unreachable", "auth"])
def test_check_does_not_fail_on_a_verdict_that_is_not_about_the_id(monkeypatch, status):
    """A red exit over latency, a busy free tier, or a bad key teaches people to
    ignore the command — and none of those means the model id is gone."""
    r = _run_check(monkeypatch, (status, "detail"))

    assert r.exit_code == 0, f"{status} failed the audit:\n{r.output}"


def test_check_says_which_providers_went_unjudged(monkeypatch):
    from typer.testing import CliRunner

    from navig.commands import ai as ai_cmd

    _keys(monkeypatch, set())
    monkeypatch.setattr(liveness, "probe_model", lambda p, m, **_kw: ("live", "ok"))

    r = CliRunner().invoke(ai_cmd.ai_app, ["models", "--check", "--provider", "groq"],
                           env={"COLUMNS": "200"})

    assert r.exit_code == 0
    assert "not judged" in r.output and "groq" in r.output
    assert "not probed" in r.output


def test_check_json_is_machine_readable(monkeypatch):
    import json

    r = _run_check(monkeypatch, ("dead", "404"),
                   args=("models", "--check", "-p", "groq", "--json"))

    assert r.exit_code == 1
    rows = json.loads(r.output)
    assert rows and {"provider", "model", "where", "status", "detail"} <= set(rows[0])


def test_plain_models_listing_still_works(monkeypatch):
    """`--check` is additive: the display command it hangs off must be untouched."""
    from typer.testing import CliRunner

    from navig.commands import ai as ai_cmd

    r = CliRunner().invoke(ai_cmd.ai_app, ["models", "--provider", "groq"],
                           env={"COLUMNS": "200"})

    assert r.exit_code == 0
    assert "gpt-oss-120b" in r.output


# ── the head-only probe: what makes a DAILY check affordable ───────────────


def test_heads_cost_one_call_per_keyed_provider(monkeypatch, no_network):
    """The daily heartbeat runs this. A refactor that drops `heads_only` would
    silently turn 5 calls into the full catalog — measured 52 on this machine —
    once a day, against providers that rate-limit."""
    _keys(monkeypatch, {"groq", "xai"})

    heads = liveness.probe_catalog_heads()

    assert len(no_network) == 2, f"one call per keyed provider, got {no_network}"
    assert {r["provider"] for r in heads} == {"groq", "xai"}
    assert len(heads) == 2
    assert all(r["kind"] == "catalog" and r["label"] == r["provider"] for r in heads)


def test_a_head_is_the_id_the_probe_and_the_router_would_pick(monkeypatch, no_network):
    from navig.providers.registry import get_provider

    _keys(monkeypatch, {"openai"})

    heads = liveness.probe_catalog_heads()

    assert heads[0]["model"] == get_provider("openai").models[0]


def test_a_keyless_provider_contributes_no_head(monkeypatch, no_network):
    _keys(monkeypatch, set())

    assert liveness.probe_catalog_heads() == []
    assert no_network == []


def test_the_nokey_advice_names_a_real_option():
    """The phantom-hint class: advice that names something which does not exist.
    `--add` must still be an option on `navig ai providers`."""
    import click
    import typer

    from navig.commands import ai as ai_cmd

    cmd = typer.main.get_command(ai_cmd.ai_app)
    assert isinstance(cmd, click.Group)
    providers = cmd.commands["providers"]
    ctx = click.Context(providers)
    opts = {o for p in providers.get_params(ctx) for o in getattr(p, "opts", ())}
    assert "--add" in opts, f"the nokey advice names --add, which is gone: {sorted(opts)}"


def test_unjudged_rows_collapse_unless_that_provider_was_asked_about(monkeypatch):
    from typer.testing import CliRunner

    from navig.commands import ai as ai_cmd

    _keys(monkeypatch, {"groq"})
    monkeypatch.setattr(liveness, "probe_model", lambda p, m, **_kw: ("live", "ok"))

    every = CliRunner().invoke(ai_cmd.ai_app, ["models", "--check"], env={"COLUMNS": "200"})
    one = CliRunner().invoke(ai_cmd.ai_app, ["models", "--check", "-p", "xai"],
                             env={"COLUMNS": "200"})

    assert every.exit_code == 0 and "not judged" in every.output
    # groq HAS a key here, so its rows show; xai does not, so its do not. The
    # footer still counts and NAMES every unjudged provider.
    assert "openai/gpt-oss-120b" in every.output, "a judged provider's rows must show"
    assert "grok-4.6" not in every.output, "a hundred rows of 'no key' is not an audit"
    assert "xai" in every.output, "an unjudged provider must still be named"
    # Asking about ONE provider must never answer with an empty table.
    assert one.exit_code == 0 and "grok-4.6" in one.output and "not judged" in one.output


# ── a floor: the Telegram tier picker must land ON the catalog ─────────────


def test_every_providers_tier_defaults_are_ids_that_provider_lists():
    """`/providers` in Telegram picks small/big/coder for a provider by matching
    SUBSTRINGS ("70b", "mixtral", "8x7b") against its model list. Those patterns
    were written for the catalog of the day, so a rotation can silently make the
    picker choose an id the provider no longer has — or, worse, one this repo has
    already denylisted.

    Found 0 offenders when added (11 providers), which is the point: this is a
    floor placed before something falls through it. The mechanism is demonstrated
    — four providers' first rows died in one week, then all eleven of groq's.
    """
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
    from navig.providers.registry import list_all_providers

    checked = 0
    offenders: list[str] = []
    for man in list_all_providers():
        models = list(getattr(man, "models", None) or [])
        if not models:
            continue
        checked += 1
        picks = TelegramCommandsMixin._select_curated_tier_defaults(man.id, models)
        for tier, pick in picks.items():
            if not pick:
                continue
            if pick not in models:
                offenders.append(f"{man.id}.{tier} -> {pick!r} is not in its own catalog")
            elif is_retired(pick, man.id):
                offenders.append(f"{man.id}.{tier} -> {pick!r} is DENYLISTED")

    assert checked >= 8, f"scan floor: only {checked} providers had models"
    assert not offenders, "Telegram tier defaults point off-catalog: " + "; ".join(offenders)


def test_the_generic_small_tier_prefers_a_model_that_says_small():
    """Without a "small" token the generic branch fell through to `models[-1]`.
    For mistral that is `pixtral-large-latest` — a LARGE VISION model as the
    small-talk tier, with `mistral-small-latest` in the same list."""
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
    from navig.providers.registry import get_provider

    models = list(get_provider("mistral").models)
    assert "mistral-small-latest" in models and "pixtral-large-latest" in models

    picks = TelegramCommandsMixin._select_curated_tier_defaults("mistral", models)

    assert picks["small"] == "mistral-small-latest"
    assert picks["big"] == "mistral-large-latest"


def test_a_dedicated_branch_still_wins_over_the_generic_tokens():
    """Anti-over-reach: the providers with their own branch must be untouched by
    a generic-token edit — measured, this token changes mistral and nothing else."""
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin
    from navig.providers.registry import get_provider

    for pid, tier, expected in [
        ("openai", "small", "gpt-4o-mini"),
        ("anthropic", "small", "claude-3-5-haiku-20241022"),
        ("xai", "small", "grok-3-mini"),
    ]:
        picks = TelegramCommandsMixin._select_curated_tier_defaults(
            pid, list(get_provider(pid).models)
        )
        assert picks[tier] == expected, f"{pid}.{tier} drifted to {picks[tier]!r}"
