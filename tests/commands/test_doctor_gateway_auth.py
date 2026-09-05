"""`navig doctor` must say when nothing is authenticating the local gateway.

`require_bearer_auth` opens with `if not token: return None` — **no token means open
access** — and seventeen route modules sit behind it, including
`POST /approval/{id}/respond`. An unauthenticated gateway lets any local process list
the agent's pending approvals and answer them.

The gateway now mints and persists a token on its first start, which makes "no token"
mean two very different things. This row must tell them apart, because the same words
are reassuring in one case and alarming in the other:

* never started → the token appears when it does. Informational.
* has started, still no token → the mint could not write, and a running gateway is
  serving its admin routes to anything on this machine. A fault.

`gateway.json` is the discriminator: the live gateway writes it when it binds.

An earlier version of this row predated the mint and told the operator to set a token by
hand as though they had simply never configured one — right for the failure, misleading
for the ordinary case, which is exactly why the two are separated now.
"""

from __future__ import annotations

import pytest

from navig.commands import doctor


@pytest.fixture
def gateway(monkeypatch):
    """Drive the row from a synthetic config and a synthetic "has it run" answer.

    ⚠ Deliberately patches `doctor._gateway_has_ever_run`, NOT `paths.config_dir`. An
    earlier version patched the global path resolver, which poisoned whatever had
    already cached a config dir — two unrelated rows in `test_doctor_vault.py` went
    green-but-empty, and only in a run that included both files. monkeypatch restores
    the attribute; it cannot restore what someone else cached from it.
    """
    cfg: dict = {}
    state = {"has_run": False}

    class _CM:
        @staticmethod
        def get(key, default=None):
            return cfg if key == "gateway" else default

    monkeypatch.setattr("navig.config.ConfigManager", lambda *a, **k: _CM())
    monkeypatch.setattr(doctor, "_gateway_has_ever_run", lambda: state["has_run"])

    def _mark_started() -> None:
        state["has_run"] = True

    return cfg, _mark_started


def test_a_token_is_a_pass(gateway) -> None:
    cfg, _ = gateway
    cfg["auth"] = {"token": "s3cret"}

    row = doctor.check_gateway_auth()[0]
    assert row[1] is True
    assert "s3cret" not in row.detail, "the row must never print the token"


def test_a_gateway_that_never_started_is_informational(gateway) -> None:
    """Nothing is wrong yet — saying otherwise trains the operator to ignore the row."""
    row = doctor.check_gateway_auth()[0]

    assert row[1] is True
    assert "first time the gateway starts" in row.detail


def test_a_gateway_that_has_run_without_a_token_is_a_fault(gateway) -> None:
    """The mint could not write, so a RUNNING gateway is unauthenticated."""
    cfg, mark_started = gateway
    mark_started()

    row = doctor.check_gateway_auth()[0]

    assert row[1] is False, "a running unauthenticated gateway must not render as ✓"
    assert "has run but has NO token" in row.detail
    assert "approval responses" in row.detail, "the row must name what is exposed"
    assert "navig config set gateway.auth.token" in row.detail, "no remedy given"


def test_loopback_is_a_warning(gateway) -> None:
    """Local processes only — serious, but not the same as being on the network."""
    cfg, mark_started = gateway
    cfg["host"] = "127.0.0.1"
    mark_started()

    row = doctor.check_gateway_auth()[0]
    assert row[0] == doctor._WARN
    assert "local processes only" in row.detail


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10"])
def test_a_public_bind_is_an_error_not_a_warning(gateway, host) -> None:
    """Reachable off this machine with no authentication is not a nudge."""
    cfg, mark_started = gateway
    cfg["host"] = host
    mark_started()

    row = doctor.check_gateway_auth()[0]
    assert row[0] == doctor._ERR
    assert "reachable off this machine" in row.detail


def test_an_unreadable_config_dir_assumes_the_worse_case(monkeypatch) -> None:
    """If we cannot tell whether the gateway has run, report the fault rather than the
    reassurance — a green row over an unknown is the failure this file exists to stop.

    Exercises the real helper, so it also pins that the resolver is wrapped at all.
    """
    monkeypatch.setattr(
        "navig.platform.paths.config_dir",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no config dir")),
    )

    assert doctor._gateway_has_ever_run() is True


def test_a_broken_read_warns_rather_than_ticking(monkeypatch) -> None:
    def _boom(*a, **k):
        raise RuntimeError("config on fire")

    monkeypatch.setattr("navig.config.ConfigManager", _boom)

    row = doctor.check_gateway_auth()[0]
    assert row[1] is False
    assert "on fire" in row.detail


def test_the_check_is_wired_into_the_report(gateway) -> None:
    """A check nothing calls is documentation."""
    sections = dict(doctor._collect_sections(port=None))
    assert any(r.label == "Gateway auth" for r in sections["Gateway"])
