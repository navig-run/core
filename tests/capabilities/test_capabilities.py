"""Capability discovery for coding agents: probes, pipelines, safety, navig_run.

Isolated from the real install: probes and the installed command set are injected, and
the manifest cache lives under a temp ``NAVIG_DATA_DIR``/config dir.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from navig.capabilities import pipelines, probes, safety
from navig.capabilities.digest import build_digest

REPO_CORE = Path(__file__).resolve().parents[2]
MANIFEST = REPO_CORE / "generated" / "commands.json"


# ── probes ────────────────────────────────────────────────────────────────────────


def test_probe_results_keep_only_contract_fields() -> None:
    def leaky():
        return {
            "accounts": [
                {"provider": "x", "label": "@me", "state": "connected", "token": "SECRET-1"}
            ],
            "api_key": "SECRET-2",
        }

    result = probes.run_probes(probes={"leaky": leaky})
    dumped = json.dumps(result)
    assert "SECRET" not in dumped
    assert result["leaky"]["accounts"][0]["provider"] == "x"


def test_a_raising_probe_is_reported_and_does_not_affect_others() -> None:
    def ok():
        return {"accounts": [{"provider": "a", "state": "connected"}]}

    def bad():
        raise RuntimeError("vault locked")

    result = probes.run_probes(probes={"ok": ok, "bad": bad})
    assert result["ok"]["accounts"][0]["provider"] == "a"
    assert result["bad"]["accounts"] == []
    assert "vault locked" in result["bad"]["error"]


def test_a_hung_probe_times_out_without_blocking() -> None:
    def hang():
        time.sleep(30)

    started = time.monotonic()
    result = probes.run_probes(probes={"hang": hang}, timeout_s=0.3)
    assert time.monotonic() - started < 3
    assert "timed out" in result["hang"]["error"]


def test_unknown_state_is_normalised() -> None:
    result = probes.run_probes(
        probes={"p": lambda: {"accounts": [{"provider": "a", "state": "great"}]}}
    )
    assert result["p"]["accounts"][0]["state"] == "unknown"


# ── safety ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "level"),
    [
        ("navig social upload", "dangerous"),
        ("navig social relay post", "dangerous"),
        ("navig social fan-out", "dangerous"),
        ("navig email send", "dangerous"),
        ("navig social connect", "dangerous"),
        ("navig mobile devices", "safe"),
        ("navig social status", "safe"),
        ("navig audio loudness", "safe"),
        ("navig audio score", "moderate"),
        ("navig generate gen", "moderate"),
        ("navig generate analyse", "moderate"),  # writes a briefing.md
        ("navig", "dangerous"),
        ("", "dangerous"),
    ],
)
def test_classify(path: str, level: str) -> None:
    assert safety.classify(path) == level


# ── pipelines ─────────────────────────────────────────────────────────────────────


def _all_pipeline_paths() -> set[str]:
    return {step.path for steps in pipelines.STEPS.values() for step in steps}


def test_every_pipeline_command_exists_in_the_manifest() -> None:
    if not MANIFEST.exists():
        pytest.skip("generated/commands.json is a dev-checkout artifact")
    known = {c["path"] for c in json.loads(MANIFEST.read_text(encoding="utf-8"))["commands"]}
    missing = sorted(_all_pipeline_paths() - known)
    assert missing == [], f"pipeline names commands that do not exist: {missing}"


def test_video_with_a_connected_youtube_offers_one_upload_per_channel() -> None:
    connected = {
        "social": {
            "accounts": [
                {"provider": "youtube", "id": "UC1", "label": "SCHEMA", "state": "connected"},
                {"provider": "youtube", "id": "UC2", "label": "Second", "state": "connected"},
            ]
        }
    }
    result = pipelines.suggest(
        "E:/clips/a.mp4", installed=_all_pipeline_paths(), probe_results=connected
    )
    uploads = [s for s in result["steps"] if s["path"] == "navig social upload"]
    assert [s["account"]["label"] for s in uploads] == ["SCHEMA", "Second"]
    assert uploads[0]["args"] == ["E:/clips/a.mp4", "--account", "UC1", "--dry-run", "--json"]
    assert all(s["safety"] == "dangerous" for s in uploads)
    assert result["unlocks"] == []


def test_a_disconnected_network_is_an_unlock_not_a_step() -> None:
    result = pipelines.suggest("a.mp4", installed=_all_pipeline_paths(), probe_results={})
    assert not any(s["path"] == "navig social upload" for s in result["steps"])
    assert result["unlocks"][0]["connect"] == "navig social connect youtube"


def test_steps_for_uninstalled_plugins_are_hidden() -> None:
    result = pipelines.suggest("a.png", installed={"navig generate palette"}, probe_results={})
    assert [s["path"] for s in result["steps"]] == ["navig generate palette"]


def test_unknown_file_type_points_to_find() -> None:
    result = pipelines.suggest("notes.xyz", installed=set(), probe_results={})
    assert result["kind"] is None and "find" in result["hint"]


# ── digest ────────────────────────────────────────────────────────────────────────


def test_digest_names_accounts_and_keeps_the_instruction_within_budget() -> None:
    overview = {
        "plugins": [
            {"plugin": "navig-social", "accounts": [
                {"provider": "instagram", "label": "@brand", "state": "connected"},
                {"provider": "threads", "label": "", "state": "missing"},
            ]},
            {"plugin": "navig-generate", "accounts": []},
        ]
        + [{"plugin": f"navig-extra{i}", "accounts": []} for i in range(60)]
    }  # fmt: skip
    text = build_digest(overview, max_chars=600)
    assert len(text) <= 600
    assert "instagram @brand ✓" in text
    assert "navig_capabilities action=suggest" in text


def test_digest_survives_a_tiny_budget() -> None:
    overview = {"plugins": [{"plugin": f"navig-p{i}", "accounts": []} for i in range(40)]}
    assert "navig_capabilities" in build_digest(overview, max_chars=50)


# ── navig_run ─────────────────────────────────────────────────────────────────────


class _FakeServer:
    def __init__(self) -> None:
        self.tools: dict = {}
        self._tool_handlers: dict = {}
        self._tool_safety: dict = {}
        self._concurrent_tools: set = set()


@pytest.fixture
def run_handler(monkeypatch):
    from navig.capabilities import registry
    from navig.mcp.tools import capabilities as bundle

    rows = [
        {"path": "navig mobile devices", "summary": "", "plugin": "navig-mobile",
         "arguments": [], "options": [], "json": True},
    ]  # fmt: skip
    monkeypatch.setattr(registry, "commands", lambda refresh=False: rows)
    monkeypatch.setattr(registry, "get", lambda p: next((r for r in rows if r["path"] == p), None))
    server = _FakeServer()
    bundle.register(server)
    return server, bundle


@pytest.mark.parametrize(
    "path",
    [
        "navig mobile devices; rm -rf /",
        "navig mobile devices && calc",
        "NAVIG mobile devices",
        "navig ../../etc",
        "navig nope nope",
        ["navig", "mobile"],
    ],
)
def test_run_refuses_anything_that_is_not_an_exact_installed_path(run_handler, path) -> None:
    server, _bundle = run_handler
    result = server._tool_handlers["navig_run_cli"](server, {"path": path})
    assert result["isError"] is True


def test_run_refuses_non_string_args(run_handler) -> None:
    server, _bundle = run_handler
    result = server._tool_handlers["navig_run_cli"](
        server, {"path": "navig mobile devices", "args": [1, 2]}
    )
    assert result["isError"] is True


def test_run_executes_without_a_shell_and_parses_json(run_handler, monkeypatch) -> None:
    import subprocess

    server, bundle = run_handler
    seen: dict = {}

    class FakeProc:
        returncode = 0

        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs

        def communicate(self, timeout=None):
            return '[{"udid": "1"}]', ""

        def kill(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", FakeProc)
    result = server._tool_handlers["navig_run_cli"](
        server, {"path": "navig mobile devices", "args": ["--json"]}
    )
    assert result["exit_code"] == 0 and result["json"] == [{"udid": "1"}]
    assert seen["argv"][1:] == ["-m", "navig", "mobile", "devices", "--json"]
    assert "shell" not in seen["kwargs"]
    assert seen["kwargs"]["env"]["NAVIG_NONINTERACTIVE"] == "1"


def test_gate_resolves_the_exact_command_level(run_handler, monkeypatch) -> None:
    from navig.capabilities import registry
    from navig.mcp.routing import run_cli_gate

    assert run_cli_gate({"path": "navig mobile devices"}) == ("cli:navig mobile devices", "safe")
    # Not installed → the strictest level, whatever its words say.
    assert run_cli_gate({"path": "navig social status"}) == ("navig_run_cli", "dangerous")
    monkeypatch.setattr(
        registry, "get", lambda p: {"path": p} if p == "navig social upload" else None
    )
    assert run_cli_gate({"path": "navig social upload"}) == (
        "cli:navig social upload",
        "dangerous",
    )
