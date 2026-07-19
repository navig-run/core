"""Tests for ``navig doctor --json`` — the machine-readable report.

House style (CLI output): any status command an agent might parse offers
``--json`` with raw structured data — humans get the table, scripts get JSON,
and neither audience parses the other's format. The contract under test:

- stdout is EXACTLY one JSON document — no header, no narration, no prompts;
- shape: ``ok`` / ``sections[] {name, ok, checks[] {label, ok, warn, detail}}``
  / ``summary {passed, warnings, failed}`` / ``version`` / ``generated_at``;
- ✓ / ⚠ / ✗ map to (ok=True) / (ok=False, warn=True) / (ok=False, warn=False);
- exit code identical to the human mode (0 only when every row is ok — a ⚠
  row flips it, exactly like ✗);
- no ANSI escapes and none of the ✓⚠✗ glyphs inside any emitted string;
- a fully-local run (gateway unreachable) still produces valid JSON with the
  gateway rows marked, instead of crashing or hanging.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from typer.testing import CliRunner

from navig.commands import doctor
from navig.commands.doctor import _check, doctor_app

runner = CliRunner()

# Every check function the doctor() callback calls, by module-global name.
_ALL_CHECKS = (
    "check_config",
    "check_runtime",
    "check_storage",
    "check_vault",
    "check_cache_dir",
    "check_sockets",
    "check_formations",
    "check_skills",
    "check_gateway",
    "check_event_processor",
    "check_ai_providers",
    "check_wiring",
    "check_config_health",
    "check_reachability",
    "check_repo_guard",
    "check_browsers",
    "check_python_deps",
)


def _stub_all_checks(monkeypatch, **overrides):
    """Replace every check with an empty stub, then apply per-check overrides.

    Overrides map a check name to the list of rows it should return.
    """
    for name in _ALL_CHECKS:
        rows = overrides.get(name, [])
        monkeypatch.setattr(doctor, name, lambda *a, _rows=rows, **k: list(_rows))


def _section(payload: dict, name: str) -> dict:
    for section in payload["sections"]:
        if section["name"] == name:
            return section
    raise AssertionError(f"no {name!r} section in {payload['sections']!r}")


def _checks_by_label(section: dict) -> dict[str, dict]:
    return {c["label"]: c for c in section["checks"]}


# ── shape ─────────────────────────────────────────────────────────────────────


def test_json_is_a_single_parseable_document_with_the_stable_shape(monkeypatch):
    _stub_all_checks(
        monkeypatch,
        check_config=[_check("Config file", True, "valid YAML")],
        check_storage=[_check("Disk Space", True, "42.0GB free (OK)")],
        check_vault=[
            _check("Vault", True, "2 item(s) · encryption OK"),
            _check("Legacy credentials", False, "legacy DB present", warn=True),
        ],
        check_gateway=[_check("Gateway", False, "no response")],
    )

    result = runner.invoke(doctor_app, ["--json"])

    # stdout must be nothing but the document — parse it whole.
    payload = json.loads(result.stdout)

    assert set(payload) == {"ok", "sections", "summary", "version", "generated_at"}
    assert isinstance(payload["sections"], list)
    for section in payload["sections"]:
        assert set(section) == {"name", "ok", "checks"}
        for check in section["checks"]:
            assert set(check) == {"label", "ok", "warn", "detail"}
            assert isinstance(check["ok"], bool)
            assert isinstance(check["warn"], bool)

    storage = _section(payload, "Storage")
    assert storage["ok"] is False  # carries the ⚠ legacy row
    rows = _checks_by_label(storage)
    assert rows["Vault"] == {
        "label": "Vault",
        "ok": True,
        "warn": False,
        "detail": "2 item(s) · encryption OK",
    }

    # Seeded ok rows: Config file + Disk Space + Vault.
    assert payload["summary"] == {"passed": 3, "warnings": 1, "failed": 1}
    assert payload["ok"] is False
    assert result.exit_code == 1

    # version + timestamp are real, not placeholders.
    import navig

    assert payload["version"] == navig.__version__
    datetime.fromisoformat(payload["generated_at"])  # raises if malformed


def test_state_mapping_ok_warn_fail(monkeypatch):
    _stub_all_checks(
        monkeypatch,
        check_config=[
            _check("green", True, "fine"),
            _check("amber", False, "look at me", warn=True),
            _check("red", False, "broken"),
        ],
    )

    payload = json.loads(runner.invoke(doctor_app, ["--json"]).stdout)
    rows = _checks_by_label(_section(payload, "Config"))

    assert (rows["green"]["ok"], rows["green"]["warn"]) == (True, False)
    assert (rows["amber"]["ok"], rows["amber"]["warn"]) == (False, True)
    assert (rows["red"]["ok"], rows["red"]["warn"]) == (False, False)
    # An ok=True row constructed with warn=True renders ✓ — JSON must agree.
    _stub_all_checks(monkeypatch, check_config=[_check("info", True, "0 installed", warn=True)])
    payload = json.loads(runner.invoke(doctor_app, ["--json"]).stdout)
    info = _checks_by_label(_section(payload, "Config"))["info"]
    assert (info["ok"], info["warn"]) == (True, False)


# ── exit codes ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rows", "expected_exit"),
    [
        ([_check("all good", True, "fine")], 0),
        ([_check("warned", False, "attention", warn=True)], 1),
        ([_check("failed", False, "broken")], 1),
    ],
    ids=["ok", "warn", "fail"],
)
def test_exit_code_matches_human_mode(monkeypatch, rows, expected_exit):
    _stub_all_checks(monkeypatch, check_config=rows)

    json_result = runner.invoke(doctor_app, ["--json"])
    human_result = runner.invoke(doctor_app, [])

    assert json_result.exit_code == expected_exit
    assert human_result.exit_code == expected_exit

    payload = json.loads(json_result.stdout)
    assert payload["ok"] is (expected_exit == 0)


# ── plain text only ───────────────────────────────────────────────────────────


def _all_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _all_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_strings(v)


def test_no_ansi_or_glyphs_in_any_emitted_string(monkeypatch):
    # Adversarial rows: a detail that smuggles ANSI + glyphs must come out plain.
    _stub_all_checks(
        monkeypatch,
        check_config=[_check("Config file", True, "\x1b[32mvalid\x1b[0m ✓ really")],
        check_vault=[_check("Vault", False, "⚠ needs attention ✗", warn=True)],
    )

    result = runner.invoke(doctor_app, ["--json"])
    payload = json.loads(result.stdout)

    for text in _all_strings(payload):
        assert "\x1b" not in text
        for glyph in ("✓", "⚠", "✗"):
            assert glyph not in text, f"glyph {glyph!r} leaked into JSON string {text!r}"

    detail = _checks_by_label(_section(payload, "Config"))["Config file"]["detail"]
    assert detail == "valid  really"


# ── fully-local run: gateway unreachable ──────────────────────────────────────


def test_gateway_unreachable_local_run_produces_valid_json(monkeypatch, tmp_path):
    """A real run against a throwaway config dir and a dead port: the local
    checks execute for real, the gateway rows degrade honestly, and stdout is
    still one valid JSON document with the human-identical exit code."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.yaml").write_text("version: 1\ngateway: {}\n", encoding="utf-8")

    # Keep the run local + fast: stub only the environment-heavy checks
    # (subprocesses, entry-point sweeps, process scans, singletons). The
    # config / storage / vault / cache / sockets / formations / skills /
    # gateway / event-processor / deps checks all run for REAL.
    for heavy in (
        "check_runtime",
        "check_ai_providers",
        "check_wiring",
        "check_config_health",
        "check_reachability",
        "check_repo_guard",
        "check_browsers",
    ):
        monkeypatch.setattr(doctor, heavy, lambda *a, **k: [])

    # Port 1 is never a NAVIG gateway — connect is refused immediately.
    result = runner.invoke(doctor_app, ["--json", "--port", "1"])
    payload = json.loads(result.stdout)

    gateway = _checks_by_label(_section(payload, "Gateway"))
    assert gateway["Gateway"]["ok"] is False
    assert gateway["Gateway"]["warn"] is True
    assert "no response" in gateway["Gateway"]["detail"].lower()
    assert gateway["Event processor"]["ok"] is False
    assert gateway["Event processor"]["warn"] is True
    assert "not checked" in gateway["Event processor"]["detail"]

    # Down gateway ⇒ overall verdict false, exit 1 — same as the human mode.
    assert payload["ok"] is False
    assert result.exit_code == 1

    summary = payload["summary"]
    assert summary["passed"] + summary["warnings"] + summary["failed"] == sum(
        len(s["checks"]) for s in payload["sections"]
    )

    for text in _all_strings(payload):
        assert "\x1b" not in text
        for glyph in ("✓", "⚠", "✗"):
            assert glyph not in text
