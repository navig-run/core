"""`navig doctor` must not call a foreign gateway "healthy".

Why this test exists
--------------------
Before this change the port row was::

    if connect_ex(("127.0.0.1", port)) == 0:
        _check("Port Occupation", True, f"Port {port} is bound (Gateway running)")

**Any** process listening on the gateway port produced a green ✓ saying the gateway was
running. On 2026-10-22 that is exactly what happened: the brain had moved to the outpost,
NEURON's daemon was disabled on purpose, and a gateway started by an unintended path held
8789. `navig doctor` would have reported "Gateway running" — a green light over a second
authority.

A bound port answers "is something listening". The question that matters is "is the thing
listening *mine*". These tests pin the second question.
"""

from __future__ import annotations

from navig.commands import doctor as doc


def test_bound_port_owned_by_this_install_is_ok():
    row = doc._classify_gateway_owner(8789, {42}, {42})
    assert row[1] is True, row[2]
    assert "this install" in row.detail


def test_bound_port_owned_by_a_foreign_process_fails():
    row = doc._classify_gateway_owner(8789, {999}, {42})
    assert row[1] is False, "a foreign listener must not be reported as a healthy gateway"
    assert row[0] == doc._ERR, "a second authority is an error, not a warning"
    assert "999" in row.detail
    assert "second NAVIG authority" in row.detail


def test_foreign_owner_is_named_even_when_this_install_owns_nothing():
    """The client case: NEURON had no daemon at all, yet 8789 was bound."""
    row = doc._classify_gateway_owner(8789, {31337}, set())
    assert row[1] is False
    assert "31337" in row.detail


def test_unattributable_owner_is_never_reported_ok():
    """If the OS will not name the owner, the honest answer is not "healthy"."""
    row = doc._classify_gateway_owner(8789, None, {42})
    assert row[1] is False, "an unknown owner must not produce a green row"
    assert row[0] == doc._WARN
    assert "cannot" in row.detail or "unknown" in row.detail


def test_no_listener_is_not_claimed_as_running():
    row = doc._classify_gateway_owner(8789, set(), {42})
    assert row[1] is False
    assert "running" not in row.detail.lower()


def test_managed_pids_are_read_from_the_pid_files(monkeypatch, tmp_path):
    """own_pids must come from the files the supervisor writes, not from a guess."""
    (tmp_path / "gateway.pid").write_text("4242\n", encoding="utf-8")
    (tmp_path / "worker.pid").write_text("5353\n", encoding="utf-8")
    monkeypatch.setattr(doc, "_managed_pid_files_dir", lambda: tmp_path, raising=False)
    assert doc._managed_daemon_pids() == {4242, 5353}


def test_managed_pids_tolerate_missing_and_corrupt_files(monkeypatch, tmp_path):
    (tmp_path / "gateway.pid").write_text("not-a-pid", encoding="utf-8")
    monkeypatch.setattr(doc, "_managed_pid_files_dir", lambda: tmp_path, raising=False)
    assert doc._managed_daemon_pids() == set()
