"""✓ must mean "verified healthy" — never "I could not look".

`navig doctor` renders ✓ whenever a row reports ok=True. Several rows returned ok=True
with the text "not checked (…)", so a check that TIMED OUT, got an HTTP error, or found
malformed data printed a cheerful green tick.

The `Event processor` row was the worst of them: it exists *specifically* as a regression
guard for a silent failure where "every light stayed green" — and it reported green when
it could not verify anything at all. Observed on the operator's own machine:

    ✓ Event processor: not checked (HTTPConnectionPool(...): Read timed out.)

A green light over an unknown is the exact failure mode this whole area was hardened
against. It is worse than a red one, because it actively tells you not to look.
"""

from __future__ import annotations

import re

import pytest

from navig.commands import doctor


def _row(results, label):
    for icon, ok, text in results:
        if label in text:
            return icon, ok, text
    raise AssertionError(f"no {label!r} row in {results}")


# ── the invariant, enforced across the whole file ────────────────────────────


def test_no_check_reports_green_when_it_could_not_verify():
    """A source-level guard: any row whose text admits it did not verify must not be
    constructed with ok=True. Enforced over the file so a NEW check cannot reintroduce
    the class the way three separate rows already did."""
    import inspect

    src = inspect.getsource(doctor)

    # `_check("Label", True, "... not checked / could not verify ...")`
    offenders = re.findall(
        r'_check\(\s*\n?\s*"[^"]+",\s*\n?\s*True,\s*\n?\s*f?"[^"]*'
        r"(?:not checked|COULD NOT VERIFY|could not verify|could not read|could not resolve)",
        src,
        re.IGNORECASE,
    )
    assert not offenders, (
        "a check that could not verify must NOT render as ✓ (ok=True) — "
        f"offending sites: {offenders}"
    )


def test_no_check_hides_its_own_failure_by_returning_no_row():
    """The other half of the same rule: a check that FAILED must not vanish.

    `test_no_check_reports_green_when_it_could_not_verify` catches a green tick over an
    unknown. This catches the quieter version — `except Exception: return []`, which renders
    as NO ROW, and no row reads as "nothing to report". Both are the reassuring answer to an
    unanswerable question; only one of them is visible in the output.

    Four rows shipped that way, each one hiding the failure of a check written for a defect
    that is *already* invisible: the browser-leak scan (~24 leaked browsers and 3.6 GB of
    profiles accumulated before anyone noticed), the ledger's in-flight markers (interrupted
    operations), the repo guard (a half-wired guard gives false safety), and daemon
    freshness (the merged-but-not-live trap). A silent failure in any of them restores
    exactly the condition it was added to end.

    An IMPORT guard is exempt, and that exemption is principled rather than a suppression:
    when the try block does nothing but import, the failure means the subsystem is not
    installed — "there is genuinely nothing to report" is an answer, not a blind spot.

    Scope is this module, and that was MEASURED rather than assumed: exactly two files in
    the tree define functions returning ``list[tuple[str, bool, str]]`` — this one (29 of
    them) and ``navig_github/engine/transfer.py`` (1), whose function is a pre-transfer
    validator rather than a health-row renderer and whose early return carries the failing
    check with it. So the surface really is doctor, not a path this guard happens to point
    at. Re-measure before widening.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(doctor))
    offenders = []

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "list[tuple[str, bool, str]]" not in (ast.unparse(func.returns) if func.returns else ""):
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Try):
                continue
            imports_only = node.body and all(
                isinstance(st, (ast.Import, ast.ImportFrom)) for st in node.body
            )
            if imports_only:
                continue  # the subsystem is absent — that is an answer
            for handler in node.handlers:
                for stmt in ast.walk(handler):
                    if isinstance(stmt, ast.Return) and stmt.value is not None:
                        if ast.unparse(stmt.value) in ("[]", "list()"):
                            offenders.append(f"{func.name}:{stmt.lineno}")

    assert not offenders, (
        "these checks hide their own failure by returning no row, which reads as "
        f"'nothing to report': {offenders}. "
        "Return a warn row instead: "
        '_check("<Label>", False, f"COULD NOT VERIFY ({exc})", warn=True)'
    )


# ── the event processor: every failure path must be honest ───────────────────


def test_event_processor_is_not_green_when_the_gateway_is_down(monkeypatch):
    monkeypatch.setattr(doctor, "_gateway_reachable", lambda *a, **k: False)

    _icon, ok, text = _row(doctor.check_event_processor(port=8789), "Event processor")

    assert ok is False, "cannot check ≠ healthy"
    assert "not checked" in text


def test_event_processor_is_not_green_when_the_status_call_TIMES_OUT(monkeypatch):
    """THE observed bug: the gateway was up, /api/deck/status timed out, and the row
    printed ✓."""
    monkeypatch.setattr(doctor, "_gateway_reachable", lambda *a, **k: True)

    import requests

    def boom(*a, **k):
        raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=3.0)")

    monkeypatch.setattr(requests, "get", boom)

    _icon, ok, text = _row(doctor.check_event_processor(port=8789), "Event processor")

    assert ok is False, "a timed-out check must never report ✓"
    assert "COULD NOT VERIFY" in text


def test_event_processor_still_reports_green_when_it_ACTUALLY_verifies(monkeypatch):
    """The point is honesty, not pessimism: a real, drained processor stays green."""
    monkeypatch.setattr(doctor, "_gateway_reachable", lambda *a, **k: True)

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"events": {"running": True, "pending": 0, "history": 12}}

    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())

    _icon, ok, _text = _row(doctor.check_event_processor(port=8789), "Event processor")

    assert ok is True


def test_the_status_timeout_is_not_a_hair_trigger():
    """3s was too tight for a gateway still loading plugins + 99 skills on boot."""
    assert doctor._STATUS_TIMEOUT >= 5.0


# ── MESH_TOKEN: the row that could never go green ────────────────────────────


def test_mesh_token_is_read_from_CONFIG_not_a_file_that_never_existed(monkeypatch, tmp_path):
    """The gateway mints `gateway.mesh_token` into CONFIG (_ensure_mesh_token). doctor
    stat()'d `<config_dir>/cache/mesh_token` — a path nothing has ever written — so a
    healthy install with a perfectly good token warned "not found" on EVERY run.

    A warning that can never go green is worse than none: it trains you to skim past the
    one row that might have mattered.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "gateway:\n  mesh_token: " + "a" * 64 + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(doctor, "_gateway_reachable", lambda *a, **k: True)

    _icon, ok, text = _row(doctor.check_gateway(port=8789), "MESH_TOKEN")

    assert ok is True, "the token IS present in config — doctor must not cry wolf"
    assert "present" in text


def test_mesh_token_warns_only_when_it_is_genuinely_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.yaml").write_text("gateway: {}\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "_gateway_reachable", lambda *a, **k: True)

    _icon, ok, _text = _row(doctor.check_gateway(port=8789), "MESH_TOKEN")

    assert ok is False


@pytest.mark.parametrize("label", ["Vault", "Telegram webhook"])
def test_other_rows_do_not_print_a_green_not_checked(label):
    """The same lie appeared in the Vault row and in my own Reachability row."""
    import inspect

    src = inspect.getsource(doctor)
    assert f'_check("{label}", True, f"not checked' not in src


# ── Config-Health incidents: a self-heal is never a green tick ────────────────


def test_no_check_pairs_literal_ok_true_with_warn_true():
    """`_check(..., ok=True, ..., warn=True)` is always a bug: `_check` renders ✓ whenever
    ok=True and IGNORES warn=, so the author wanted ⚠ but shipped a green ✓. That is exactly
    how the Config-Health incident continuation rows rendered a green tick over a real
    self-healing incident (a DECK_KEY_REIDENTIFIED showed as `✓ :  …`).

    Only a *literal* True is an offender — `_check(expr, ..., warn=True)` with a runtime
    boolean (e.g. `uv_exe.exists()`) is the correct soft-check idiom and must stay allowed.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(doctor))
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "_check":
            continue
        ok_is_literal_true = (
            len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value is True
        )
        warn_is_true = any(
            kw.arg == "warn"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        if ok_is_literal_true and warn_is_true:
            offenders.append(getattr(node, "lineno", -1))
    assert not offenders, (
        "_check(ok=True, warn=True) renders ✓ (warn is ignored) — meant ⚠, shipped green. "
        f"Offending doctor.py lines: {offenders}"
    )


def test_config_health_incident_rows_are_never_green(monkeypatch):
    """Every surfaced self-healing incident — the summary AND each continuation row — must
    render honestly (✗/⚠), never ✓, and must carry a non-empty label (an empty label prints
    `✓ :  detail` in the console and emits `"label": ""` in --json)."""
    from navig.core import incidents

    fake = [
        {"type": "CONFIG_EMPTY_WRITE_REFUSED"},
        {"type": "DECK_KEY_REIDENTIFIED"},
        {"type": "DECK_KEY_REIDENTIFIED"},
    ]
    monkeypatch.setattr(incidents, "recent", lambda limit=3: fake[:limit])
    monkeypatch.setattr(incidents, "describe", lambda e: f"desc:{e['type']}")

    rows = doctor.check_config_health()
    incident_rows = [r for r in rows if "desc:" in r[2]]  # summary + continuations
    assert len(incident_rows) == 3, f"expected 3 incident rows, got {incident_rows!r}"

    for icon, ok, text in incident_rows:
        assert ok is False, f"an incident row rendered green (ok=True): {text!r}"
        assert icon != doctor._OK, f"an incident row shows a ✓ glyph: {text!r}"
    for row in incident_rows:
        assert row.label, f"an incident row has an empty label: {row!r}"


def test_config_health_says_so_when_it_cannot_read_the_incident_log(monkeypatch):
    """"No rescue was needed" and "I could not read the log" are OPPOSITE answers.

    The incidents probe ended in `except Exception: pass`, so a failed read produced no
    Incidents row at all — and an absent row reads as the clean case, which the sibling
    test above asserts renders as a green "none". This section exists because a daemon that
    heals itself at 3am and tells nobody looks exactly like a healthy one; a silent failure
    here rebuilds that.

    The AST guard cannot see this shape — `pass` returns nothing to inspect — so it is
    pinned behaviourally.
    """
    from navig.core import incidents

    def boom(limit=3):
        raise OSError("incident log unreadable")

    monkeypatch.setattr(incidents, "recent", boom)

    rows = doctor.check_config_health()
    incident_rows = [r for r in rows if "Incidents" in r[0] or "Incidents" in str(r)]

    assert incident_rows, "a failed incident read produced no row — it reads as 'none'"
    assert any("COULD NOT VERIFY" in text for _icon, _ok, text in incident_rows), (
        f"the failure was not reported: {incident_rows!r}"
    )
    assert not any(ok for _icon, ok, text in incident_rows if "COULD NOT VERIFY" in text), (
        "a could-not-verify row rendered green"
    )


def test_config_health_is_silent_and_green_when_there_were_no_incidents(monkeypatch):
    """Honesty cuts both ways: a clean install must still go green — one ✓ 'none' row."""
    from navig.core import incidents

    monkeypatch.setattr(incidents, "recent", lambda limit=3: [])

    rows = doctor.check_config_health()
    incident_rows = [r for r in rows if r.label == "Incidents"]
    assert len(incident_rows) == 1
    icon, ok, text = incident_rows[0]
    assert ok is True and icon == doctor._OK
    assert "none" in text
