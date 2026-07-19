"""navig.selfheal.doctor_remediation — mapping + execution safety floor.

The observe→repair contract under test:

- plan() pairs FAILING checks (and only failing checks) with remediations that
  already exist — a passing check is never planned, an unmapped failing check
  is listed with no remediation;
- detail predicates narrow labels that fail for more than one reason (the
  "Event processor: not checked — gateway not running" degradation must NOT
  map to a daemon restart);
- execute() NEVER runs a safe=False remediation (report-only), NEVER runs
  anything under dry_run, runs each remediation id at most once per pass,
  and survives an action that raises;
- _mark_healed() records per-action whether the check went green after.
"""

from __future__ import annotations

from typing import Any

from navig.selfheal import doctor_remediation as dr
from navig.selfheal.doctor_remediation import PlannedAction, Remediation, execute, plan


def _report(*sections: tuple[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Build a minimal doctor report dict in the collect_report() shape."""
    out = []
    for name, checks in sections:
        out.append({"name": name, "ok": all(c["ok"] for c in checks), "checks": checks})
    return {"ok": all(s["ok"] for s in out), "sections": out}


def _check(label: str, ok: bool, detail: str = "", warn: bool = False) -> dict[str, Any]:
    return {"label": label, "ok": ok, "warn": warn, "detail": detail}


# ── plan(): mapping selection ─────────────────────────────────────────────────


class TestPlanMapping:
    def test_failing_gateway_maps_to_start_daemon(self):
        report = _report(("Gateway", [_check("Gateway", False, "No response at 127.0.0.1:1")]))
        actions = plan(report)
        assert len(actions) == 1
        assert actions[0].remediation is not None
        assert actions[0].remediation.id == "start-daemon"
        assert actions[0].remediation.safe is True

    def test_passing_checks_are_never_planned(self):
        report = _report(
            ("Gateway", [_check("Gateway", True, "Responding")]),
            ("Storage", [_check("Vault", True, "2 item(s)")]),
        )
        assert plan(report) == []

    def test_unmapped_failing_check_is_listed_without_remediation(self):
        report = _report(("Storage", [_check("Disk Space", False, "only 0.5GB free")]))
        actions = plan(report)
        assert len(actions) == 1
        assert actions[0].remediation is None

    def test_legacy_credentials_map_to_the_migration_entry_point(self):
        report = _report(
            ("Storage", [_check("Legacy credentials", False, "legacy DB present", warn=True)])
        )
        (action,) = plan(report)
        assert action.remediation is not None
        assert action.remediation.id == "migrate-legacy-vault"
        assert action.remediation.safe is True

    def test_mesh_token_shares_the_start_daemon_remediation(self):
        report = _report(
            ("Gateway", [_check("MESH_TOKEN", False, "not set (minted on start)", warn=True)])
        )
        (action,) = plan(report)
        assert action.remediation is not None
        assert action.remediation.id == "start-daemon"

    def test_plan_preserves_section_label_detail_and_warn(self):
        report = _report(("Gateway", [_check("Gateway", False, "No response", warn=True)]))
        (action,) = plan(report)
        assert (action.section, action.label, action.detail, action.warn) == (
            "Gateway",
            "Gateway",
            "No response",
            True,
        )


class TestPlanPredicates:
    """Labels that fail for several reasons only map when the reason applies."""

    def test_event_processor_not_running_maps_to_restart_report_only(self):
        report = _report(
            ("Gateway", [_check("Event processor", False, "NOT RUNNING — events piling up")])
        )
        (action,) = plan(report)
        assert action.remediation is not None
        assert action.remediation.id == "restart-daemon"
        assert action.remediation.safe is False  # restarting a LIVE daemon is disruptive

    def test_event_processor_degraded_not_checked_does_not_map(self):
        """Gateway down already maps on the Gateway row — the degraded event
        row must not double-prescribe a daemon restart."""
        report = _report(
            (
                "Gateway",
                [_check("Event processor", False, "not checked — gateway not running", warn=True)],
            )
        )
        (action,) = plan(report)
        assert action.remediation is None

    def test_telegram_webhook_stale_tenant_maps_but_could_not_verify_does_not(self):
        stale = _report(
            ("Reachability", [_check("Telegram webhook", False, "STALE tenant — rotated")])
        )
        (action,) = plan(stale)
        assert action.remediation is not None and action.remediation.id == "restart-daemon"

        unverified = _report(
            (
                "Reachability",
                [_check("Telegram webhook", False, "COULD NOT VERIFY (boom)", warn=True)],
            )
        )
        (action,) = plan(unverified)
        assert action.remediation is None

    def test_leaked_browsers_are_report_only(self):
        report = _report(
            ("Browsers", [_check("Leaked browsers", False, "3 still running", warn=True)])
        )
        (action,) = plan(report)
        assert action.remediation is not None
        assert action.remediation.safe is False
        assert "cdp stop" in action.remediation.hint


# ── execute(): the safety floor ───────────────────────────────────────────────


def _planned(remediation: Remediation | None, label: str = "Gateway") -> PlannedAction:
    return PlannedAction(
        section="Gateway", label=label, detail="", warn=False, remediation=remediation
    )


class TestExecuteSafety:
    def test_dry_run_executes_nothing(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setitem(
            dr._ACTIONS, "start_daemon", lambda: calls.append("ran") or (True, "ok")
        )

        (action,) = execute([_planned(dr._START_DAEMON)], dry_run=True)

        assert calls == []
        assert action.executed is False
        assert action.ok is None
        assert "would" in action.message

    def test_unsafe_is_never_executed_even_with_an_action_key(self, monkeypatch):
        """safe=False is the contract, not the absence of a callable."""
        calls: list[str] = []
        monkeypatch.setitem(
            dr._ACTIONS, "start_daemon", lambda: calls.append("ran") or (True, "ok")
        )
        unsafe = Remediation(
            id="restart-daemon",
            title="restart",
            safe=False,
            hint="navig service restart",
            action="start_daemon",
        )

        (action,) = execute([_planned(unsafe)], dry_run=False)

        assert calls == []
        assert action.executed is False
        assert "report-only" in action.message
        assert "navig service restart" in action.message

    def test_unmapped_action_gets_the_no_remediation_message(self):
        (action,) = execute([_planned(None, label="Disk Space")])
        assert action.executed is False
        assert action.message == "no automatic remediation"

    def test_safe_action_runs_and_records_outcome(self, monkeypatch):
        monkeypatch.setitem(dr._ACTIONS, "start_daemon", lambda: (True, "daemon running (pid=7)"))

        (action,) = execute([_planned(dr._START_DAEMON)])

        assert action.executed is True
        assert action.ok is True
        assert "pid=7" in action.message

    def test_shared_remediation_runs_once_and_both_actions_inherit(self, monkeypatch):
        calls: list[str] = []

        def _fake():
            calls.append("ran")
            return True, "started"

        monkeypatch.setitem(dr._ACTIONS, "start_daemon", _fake)
        actions = [
            _planned(dr._START_DAEMON, label="Gateway"),
            _planned(dr._START_DAEMON, label="MESH_TOKEN"),
        ]

        execute(actions)

        assert calls == ["ran"]  # dedupe by remediation id
        assert all(a.executed and a.ok for a in actions)
        assert {a.message for a in actions} == {"started"}

    def test_raising_action_is_caught_not_propagated(self, monkeypatch):
        def _boom():
            raise RuntimeError("kaput")

        monkeypatch.setitem(dr._ACTIONS, "start_daemon", _boom)

        (action,) = execute([_planned(dr._START_DAEMON)])

        assert action.executed is True
        assert action.ok is False
        assert "failed" in action.message

    def test_unknown_action_key_degrades_to_a_manual_hint(self):
        drifted = Remediation(
            id="x", title="do x", safe=True, hint="navig x", action="no_such_action"
        )
        (action,) = execute([_planned(drifted)])
        assert action.executed is False
        assert "navig x" in action.message


# ── the action adapters call the EXISTING entry points ───────────────────────


class TestActionAdapters:
    def test_migrate_legacy_vault_calls_migrate_from_legacy(self, monkeypatch):
        class _FakeReport:
            def ok(self):
                return True

            def summary(self):
                return "Migration: 3 migrated, 1 skipped, 0 errors"

        called: list[bool] = []

        def _fake_migrate(*args, **kwargs):
            called.append(True)
            return _FakeReport()

        monkeypatch.setattr("navig.vault.migrate.migrate_from_legacy", _fake_migrate)

        ok, message = dr._migrate_legacy_vault()

        assert called == [True]
        assert ok is True
        assert "3 migrated" in message

    def test_migrate_legacy_vault_never_raises(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise OSError("locked")

        monkeypatch.setattr("navig.vault.migrate.migrate_from_legacy", _boom)

        ok, message = dr._migrate_legacy_vault()

        assert ok is False
        assert "OSError" in message

    def test_start_daemon_reports_refusal_when_daemon_not_running_after(self, monkeypatch):
        """The stop-intent flag path: service_start returns quietly without
        starting — the adapter must report that honestly, not claim success."""
        monkeypatch.setattr("navig.commands.service.service_start", lambda **kw: None)
        monkeypatch.setattr(
            "navig.daemon.supervisor.NavigDaemon.is_running", staticmethod(lambda: False)
        )

        ok, message = dr._start_daemon()

        assert ok is False
        assert "navig service start" in message

    def test_start_daemon_success_verifies_via_supervisor(self, monkeypatch):
        monkeypatch.setattr("navig.commands.service.service_start", lambda **kw: None)
        monkeypatch.setattr(
            "navig.daemon.supervisor.NavigDaemon.is_running", staticmethod(lambda: True)
        )
        monkeypatch.setattr(
            "navig.daemon.supervisor.NavigDaemon.read_pid", staticmethod(lambda: 4242)
        )

        ok, message = dr._start_daemon()

        assert ok is True
        assert "4242" in message


# ── _mark_healed(): before/after verdict ─────────────────────────────────────


class TestMarkHealed:
    def test_executed_actions_get_their_after_state(self):
        actions = [
            _planned(dr._START_DAEMON, label="Gateway"),
            _planned(dr._START_DAEMON, label="MESH_TOKEN"),
        ]
        for a in actions:
            a.executed = True
        after = _report(
            (
                "Gateway",
                [
                    _check("Gateway", True, "Responding"),
                    _check("MESH_TOKEN", False, "still not set", warn=True),
                ],
            )
        )

        dr._mark_healed(actions, after)

        assert actions[0].healed is True
        assert actions[1].healed is False

    def test_non_executed_actions_are_left_alone(self):
        (action,) = [_planned(None, label="Disk Space")]
        dr._mark_healed([action], _report(("Storage", [_check("Disk Space", True, "ok")])))
        assert action.healed is None
