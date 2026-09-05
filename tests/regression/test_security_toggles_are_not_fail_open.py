"""A security control read from config must not fail OPEN on a string value.

`navig config set <k> <v>` stores its argument VERBATIM as a string, and
``bool("false")`` is ``True``. For a *negative-polarity* control — one whose True
state is the unsafe one — that turns the operator's "off" into "on":

    server_config = {"trust_new_host": "false"}
      before -> StrictHostKeyChecking=accept-new   (MITM protection DISABLED)
      after  -> StrictHostKeyChecking=yes

That was measured against the real code path before the fix, not inferred. These
tests pin every such read, and are written against the *observable posture*
(the SSH argv, the paramiko policy) rather than the coercion helper — a test that
asserts `coerce_bool` was called would still pass if the argv stopped depending
on it.
"""
from __future__ import annotations

from typing import Any

import pytest

# Spellings an operator plausibly types for "off". Each one used to
# select the UNSAFE branch; a bare `bool()` sees every one of them as True.
FALSEY_STRINGS = ["false", "False", "no", "off", "0"]


def _ssh_base_args(cfg: dict[str, Any]) -> list[str]:
    from navig.remote import RemoteOperations

    ops = RemoteOperations.__new__(RemoteOperations)
    return ops._ssh_base_args(cfg)


def _strict_mode(argv: list[str]) -> str:
    """The value of the -o StrictHostKeyChecking=… option in an SSH argv."""
    for i, tok in enumerate(argv):
        if tok == "-o" and argv[i + 1].startswith("StrictHostKeyChecking="):
            return argv[i + 1].split("=", 1)[1]
    raise AssertionError(f"no StrictHostKeyChecking option in {argv!r}")


@pytest.mark.parametrize("value", FALSEY_STRINGS)
def test_trust_new_host_as_a_string_keeps_strict_host_checking(value: str) -> None:
    """remote.py — the SSH argv builder. `"false"` must not accept new host keys."""
    argv = _ssh_base_args({"host": "h", "user": "u", "trust_new_host": value})
    assert _strict_mode(argv) == "yes"


def test_trust_new_host_still_works_when_genuinely_enabled() -> None:
    """The partner assertion: the feature must still be reachable. A guard that
    only proves the safe direction passes just as well on code that hardwired it."""
    for enabled in (True, "true", "yes", "on", "1"):
        argv = _ssh_base_args({"host": "h", "user": "u", "trust_new_host": enabled})
        assert _strict_mode(argv) == "accept-new", enabled


def test_trust_new_host_defaults_to_strict_when_absent() -> None:
    assert _strict_mode(_ssh_base_args({"host": "h", "user": "u"})) == "yes"


def _tunnel_ssh_argv(monkeypatch: pytest.MonkeyPatch, value: Any) -> list[str]:
    """Drive the REAL `TunnelManager.start_tunnel` and capture the argv it builds.

    Stubbing the config + Popen (rather than re-evaluating tunnel's expression in
    the test) is the point: a test that recomputes the ternary itself passes even
    if the module stops using it.
    """
    import navig.tunnel as tunnel_mod

    mgr = tunnel_mod.TunnelManager.__new__(tunnel_mod.TunnelManager)
    mgr.config = type(
        "Cfg",
        (),
        {
            "get_active_server": lambda self: "srv",
            "load_server_config": lambda self, name: {
                "host": "h",
                "user": "u",
                "trust_new_host": value,
                "database": {"remote_port": 3306},
            },
        },
    )()
    monkeypatch.setattr(tunnel_mod.TunnelManager, "get_tunnel_status", lambda *a: None)
    monkeypatch.setattr(
        tunnel_mod.TunnelManager, "_find_available_port", lambda *a, **k: 3307
    )

    captured: list[list[str]] = []

    def _popen(argv, *a, **k):  # noqa: ANN001 - stub
        captured.append(list(argv))
        raise RuntimeError("stop before spawning ssh")

    monkeypatch.setattr(tunnel_mod.subprocess, "Popen", _popen)
    with pytest.raises(Exception):  # noqa: B017 - the stub aborts the call
        mgr.start_tunnel("srv")
    assert captured, "start_tunnel never reached the ssh spawn"
    return captured[0]


@pytest.mark.parametrize("value", FALSEY_STRINGS)
def test_tunnel_trust_new_host_as_a_string_keeps_strict_host_checking(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tunnel.py builds its own SSH options and had the same read."""
    assert _strict_mode(_tunnel_ssh_argv(monkeypatch, value)) == "yes"


def test_tunnel_trust_new_host_still_works_when_genuinely_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _strict_mode(_tunnel_ssh_argv(monkeypatch, True)) == "accept-new"


def _pool_policy(value: Any) -> str:
    """Drive the REAL `SSHConnectionPool._create_connection` with a fake paramiko
    and report which missing-host-key policy it installed."""
    import navig.connection_pool as pool_mod

    installed: list[str] = []

    class _FakeClient:
        def load_system_host_keys(self) -> None: ...
        def set_missing_host_key_policy(self, policy: Any) -> None:
            installed.append(type(policy).__name__)

        def connect(self, **kwargs: Any) -> None:
            raise RuntimeError("stop before connecting")

    class _AutoAddPolicy:
        pass

    class _RejectPolicy:
        pass

    fake = type(
        "paramiko",
        (),
        {
            "SSHClient": _FakeClient,
            "AutoAddPolicy": _AutoAddPolicy,
            "RejectPolicy": _RejectPolicy,
        },
    )
    original = pool_mod._get_paramiko
    pool_mod._get_paramiko = lambda: fake
    try:
        pool = pool_mod.SSHConnectionPool.__new__(pool_mod.SSHConnectionPool)
        pool.connect_timeout = 1
        try:
            pool._create_connection(
                {"host": "h", "user": "u", "trust_new_host": value}
            )
        except Exception:
            pass
    finally:
        pool_mod._get_paramiko = original
    assert installed, "_create_connection never chose a host-key policy"
    return installed[0]


@pytest.mark.parametrize("value", FALSEY_STRINGS)
def test_pool_trust_new_host_as_a_string_rejects_unknown_hosts(value: str) -> None:
    """connection_pool.py chooses a paramiko missing-host-key policy. `"false"`
    must not select AutoAddPolicy, which silently trusts any key it is shown."""
    assert _pool_policy(value) == "_RejectPolicy"


def test_pool_trust_new_host_still_works_when_genuinely_enabled() -> None:
    assert _pool_policy(True) == "_AutoAddPolicy"


@pytest.mark.parametrize("value", FALSEY_STRINGS)
def test_ignore_https_errors_as_a_string_keeps_tls_verification_on(
    value: str,
) -> None:
    """browser/controller.py — `"false"` must not disable certificate checking."""
    from navig.browser.controller import BrowserConfig

    cfg = BrowserConfig.from_config({"browser": {"ignore_https_errors": value}})
    assert cfg.ignore_https_errors is False


def test_ignore_https_errors_is_still_settable() -> None:
    from navig.browser.controller import BrowserConfig

    cfg = BrowserConfig.from_config({"browser": {"ignore_https_errors": "true"}})
    assert cfg.ignore_https_errors is True


@pytest.mark.parametrize("value", FALSEY_STRINGS)
def test_allow_insecure_as_a_string_does_not_report_a_phantom_finding(
    value: str,
) -> None:
    """core/security.py audits the config. Reading `"false"` as True made the audit
    report a permissive setting the operator had actually turned off — a finding
    that cannot be cleared is as useless as one that never fires."""
    from navig.core.security import check_config_security

    findings = check_config_security({"allow_insecure": value})
    assert not [f for f in findings if f.check_id == "allow-insecure"]


def test_allow_insecure_is_still_reported_when_genuinely_on() -> None:
    from navig.core.security import check_config_security

    findings = check_config_security({"allow_insecure": True})
    assert [f for f in findings if f.check_id == "allow-insecure"]
