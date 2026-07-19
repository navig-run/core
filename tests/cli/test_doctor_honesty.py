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
