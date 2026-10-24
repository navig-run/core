"""Acceptance — adversarial architecture guards (mission DoD + plan §2.3/§3.4/§5).

These are the "no parallel system" checks: no LLM import in the deterministic path, no
second device inventory in core, commit tokens in the existing RuntimeStore only, the
campaign owner's plugin tree untouched, and the campaign CLI still working.
"""

from __future__ import annotations

import re
import subprocess
import sys

import _support as S
import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.integration

LLM_IMPORT_PREFIXES = ("navig.agent", "navig.llm", "navig.commands.ai", "openai", "anthropic")
PROVIDER_IMPORT_PREFIXES = ("navig_home",)


def _python_files(relative_dir: str) -> list:
    directory = S.require_path(relative_dir)
    if not directory.is_dir():
        pytest.fail(f"{relative_dir} is not a directory", pytrace=False)
    files = sorted(directory.rglob("*.py"))
    assert files, f"{relative_dir} contains no Python files — nothing was built there yet"
    return files


def _offending_imports(files, prefixes, *, relative_to: str) -> list[str]:
    offenders: list[str] = []
    for path in files:
        for name in S.normalise_imports(path):
            if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
                offenders.append(f"{path.relative_to(S.REPO_ROOT)} imports {name}")
    return offenders


# --------------------------------------------------------------------------- #
# 1. No LLM / no provider import in the deterministic path (plan §3.3, §2.3)
# --------------------------------------------------------------------------- #
def test_preclassify_and_endpoints_import_no_llm():
    preclassify = S.require_path("core/navig/reflex/preclassify.py")
    endpoints = _python_files("core/navig/endpoints")
    offenders = _offending_imports([preclassify, *endpoints], LLM_IMPORT_PREFIXES, relative_to="core")
    assert not offenders, "the deterministic path must not import LLM machinery:\n" + "\n".join(offenders)


def test_endpoints_import_no_home_provider_module():
    endpoints = _python_files("core/navig/endpoints")
    offenders = _offending_imports(endpoints, PROVIDER_IMPORT_PREFIXES, relative_to="core")
    assert not offenders, (
        "endpoints must read navig-home through the CLI bridge, never by importing its provider "
        "package (plan §2.3):\n" + "\n".join(offenders)
    )


def test_no_second_device_inventory_in_core():
    endpoints = _python_files("core/navig/endpoints")
    problems: list[str] = []
    for path in endpoints:
        relative = path.relative_to(S.REPO_ROOT)
        text = path.read_text(encoding="utf-8")
        imports = S.normalise_imports(path)
        if "yaml" in imports:
            problems.append(f"{relative} imports yaml — core must not parse the inventory file (plan §2.3)")
        if re.search(r"\byaml\.(safe_load|load|load_all)\b", text):
            problems.append(f"{relative} calls yaml.*_load — a second inventory parser")
    assert not problems, "no second device inventory allowed:\n" + "\n".join(problems)


# --------------------------------------------------------------------------- #
# 2b. Risk is a gate, not a weight (lead invariant #1, labs memo §3)
# --------------------------------------------------------------------------- #
def test_reflex_risk_is_a_gate_not_a_cost_latency_quality_weight():
    reflex_files = _python_files("core/navig/reflex")
    offenders: list[str] = []
    for path in reflex_files:
        imports = S.normalise_imports(path)
        for name in imports:
            if any(token in name.lower() for token in ("scoring", "cost", "pricing", "latency")):
                offenders.append(f"{path.relative_to(S.REPO_ROOT)} imports {name}")
        offenders.extend(
            f"{path.relative_to(S.REPO_ROOT)} uses scoring identifier {use}"
            for use in S.scoring_identifier_uses(path)
        )
    assert not offenders, (
        "tool candidacy must be filtered by capability/risk gates, never ranked by "
        "cost/latency/quality weights:\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 2c. Campaign status is derived through the existing read-only command (invariant #5)
# --------------------------------------------------------------------------- #
def test_campaign_status_is_the_existing_read_only_command():
    manifest = S.require("navig.reflex.manifest")
    entries = dict(S.tool_entries(manifest))
    assert "campaigns.status" in entries, "the campaigns.status tool is required (plan §3.2)"
    entry = entries["campaigns.status"]
    command = S.entry_command(entry)
    assert command[:3] == ("tasks", "campaigns", "list"), (
        "campaign status must be projected from `navig tasks campaigns list --json`, got "
        f"{command!r}"
    )
    assert S.entry_field(entry, "read_only") is True, "campaigns.status must be read-only"
    known = manifest.known_command_paths()
    assert "navig tasks campaigns list" in known, (
        "the campaign tool must bind to a command that exists in the single command manifest: "
        f"'navig tasks campaigns list' not in known_command_paths()"
    )

    reflex_files = _python_files("core/navig/reflex")
    offenders: list[str] = []
    for path in reflex_files:
        imports = S.normalise_imports(path)
        if any(name == "navig_tasks" or name.startswith("navig_tasks.") for name in imports):
            offenders.append(f"{path.relative_to(S.REPO_ROOT)} imports {sorted(imports)}")
        text = path.read_text(encoding="utf-8")
        if re.search(r"CREATE\s+TABLE[^;]*campaign", text, re.IGNORECASE):
            offenders.append(f"{path.relative_to(S.REPO_ROOT)} creates a campaign table")
    assert not offenders, (
        "Reflex reads campaign state only through the existing command — no campaign table/store/"
        "DB and no navig_tasks import (plan §5):\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 2. Commit tokens live in the existing RuntimeStore — no new DB (plan §3.4)
# --------------------------------------------------------------------------- #
def test_commit_tokens_live_in_the_runtime_store(fake_home, capsys, cli_env):
    exit_code, stdout, stderr = S.invoke_cli(
        ["ask", "turn off the desk lamp", "--plan", "--json"], capsys
    )
    text = S.combined(stdout, stderr)
    assert exit_code == 0, f"plan failed (exit {exit_code}): {text[-1200:]}"
    plan = S.plan_of(S.json_payload(text))
    S.token_of(plan)

    from navig.platform.paths import (
        config_dir,  # noqa: F401 — inspect the isolated dir
        data_dir,
    )

    runtime_db = S.runtime_db_path()
    assert runtime_db.exists(), f"the token store must be the existing RuntimeStore DB at {runtime_db}"
    assert runtime_db.name == "runtime.db", f"tokens must live in runtime.db, got {runtime_db}"

    import sqlite3

    with sqlite3.connect(runtime_db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "commit_plans" in tables, f"commit tokens need a commit_plans table in runtime.db, got {sorted(tables)}"

    databases = {path.name for path in runtime_db.parent.glob("*.db")}
    assert databases == {"runtime.db"}, (
        f"commit tokens must not create a new DB file; {sorted(databases)} in {runtime_db.parent}"
    )

    stray = sorted(
        path.name
        for path in data_dir().rglob("*")
        if path.is_file() and path.suffix == ".db" and any(word in path.name.lower() for word in ("plan", "token", "reflex"))
    )
    assert not stray, f"a second token store appeared under the data dir: {stray}"


def test_reflex_token_store_does_not_open_sqlite_directly():
    store_path = S.require_path("core/navig/reflex/store.py")
    text = store_path.read_text(encoding="utf-8")
    assert "sqlite3.connect" not in text, (
        "navig.reflex.store must use the existing RuntimeStore/BaseStore, not a direct sqlite "
        "connection (plan §3.4; MASTER storage law)"
    )


def test_reflex_never_opens_tasks_or_campaign_store():
    reflex_files = _python_files("core/navig/reflex")
    offenders: list[str] = []
    for path in reflex_files:
        relative = path.relative_to(S.REPO_ROOT)
        imports = S.normalise_imports(path)
        for name in sorted(imports):
            if name == "navig_tasks" or name.startswith("navig_tasks."):
                offenders.append(f"{relative} imports {name} (campaign store owner)")
            if name in {"sqlite3"}:
                offenders.append(f"{relative} imports {name} directly (must use RuntimeStore)")
        text = path.read_text(encoding="utf-8")
        if re.search(r"CREATE\s+TABLE[^;]*campaign", text, re.IGNORECASE):
            offenders.append(f"{relative} creates a campaign table")
        if re.search(r"sqlite3\.connect\(", text):
            offenders.append(f"{relative} opens a sqlite connection directly")
    assert not offenders, (
        "Reflex reads campaign state only through the existing read-only command and tokens "
        "live in RuntimeStore — no campaign table/store/DB, no direct sqlite (plan §5, "
        "lead invariants #5/#6):\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 3. The campaign owner's plugin tree is untouched (mission DoD)
# --------------------------------------------------------------------------- #
def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(S.REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )


def test_tasks_plugin_survives_the_campaign_freeze_ending():
    """The campaign lane froze ``plugins/navig-tasks`` for the length of its mission.

    That mission completed on 2026-10-22, so the freeze is over: the plugin owns its
    own suite (``plugins/navig-tasks/tests``) as the contract now. Keeping a permanent
    "diff against an old base must be empty" assertion after the owner's mission ends
    would freeze the plugin forever — it would turn every future fix (including the
    duplicate-task replay fix this repository needed) into a contract violation.
    """
    plugin = S.REPO_ROOT / "plugins" / "navig-tasks" / "navig_tasks"
    assert (plugin / "engine.py").is_file(), "plugins/navig-tasks disappeared"
    assert (plugin / "store.py").is_file(), "plugins/navig-tasks lost its store"


def test_campaign_cli_still_works(cli_env):
    plugin_dir = S.REPO_ROOT / "plugins" / "navig-tasks"
    assert plugin_dir.exists(), "plugins/navig-tasks is missing"
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))

    try:
        from navig_tasks.commands.tasks import tasks_app
    except ImportError as exc:
        pytest.fail(
            f"navig tasks CLI is not importable from this tree ({exc}) — the campaign layer must "
            "stay intact (mission DoD)",
            pytrace=False,
        )

    result = CliRunner().invoke(tasks_app, ["campaigns", "list", "--json"])
    output = result.output or ""
    assert result.exit_code == 0, (
        f"navig tasks campaigns list --json failed (exit {result.exit_code}): {output[-1500:]}"
    )
    payload = S.json_value(output)
    if isinstance(payload, list):
        campaigns = payload
    else:
        assert isinstance(payload, dict), f"campaign list --json must emit JSON, got {type(payload)}"
        if "ok" in payload:
            assert payload["ok"] is True, f"campaign list must answer ok:true, got {payload!r}"
        campaigns = payload.get("campaigns")
    assert isinstance(campaigns, list), f"campaign list must carry a campaigns list: {payload!r}"
