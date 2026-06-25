"""The first-run Lighthouse onboarding step: metadata + non-interactive behavior."""

from __future__ import annotations

from pathlib import Path

import navig.onboarding.steps as steps


def test_lighthouse_step_metadata(tmp_path: Path):
    step = steps._step_lighthouse(tmp_path)
    assert step.id == "lighthouse"
    assert step.tier == "optional"
    assert step.phase == "configuration"
    assert step.on_failure == "skip"
    # The lighthouse step was generalized into the reachability picker (Direct /
    # Lighthouse / Tunnel / Local-only); the title reflects that broader role.
    assert "Reachability" in step.title


def test_lighthouse_step_skips_without_tty(monkeypatch, tmp_path: Path):
    # Not already deployed (fake config returns nothing for cloud.lighthouse_url)...
    import navig.core

    class _FakeCfg:
        def get(self, *_a, **_k):
            return None

    monkeypatch.setattr(navig.core, "Config", lambda: _FakeCfg())
    # ...and stdin is not a TTY → the step must skip before prompting.
    monkeypatch.setattr(
        steps,
        "_tty_check",
        lambda: steps.StepResult(status="skipped", output={"reason": "non_interactive"}),
    )

    step = steps._step_lighthouse(tmp_path)
    result = step.run()
    assert result.status == "skipped"


def test_lighthouse_step_short_circuits_when_already_deployed(monkeypatch, tmp_path: Path):
    import navig.core

    class _FakeCfg:
        def get(self, key, *_a, **_k):
            return "https://navig-lighthouse.sub.workers.dev" if key == "cloud.lighthouse_url" else None

    monkeypatch.setattr(navig.core, "Config", lambda: _FakeCfg())
    step = steps._step_lighthouse(tmp_path)
    assert step.verify() is True
    result = step.run()
    assert result.status == "completed"
    # Generalized step reports the broader "reachability already configured".
    assert result.output.get("note") == "reachability already configured"


def test_step_registered_in_wizard():
    import inspect

    src = inspect.getsource(steps.build_step_registry)
    assert "_step_lighthouse(navig_dir)" in src


def test_deferred_command_listed_for_lighthouse():
    import inspect

    from navig.onboarding import runner

    src = inspect.getsource(runner._deferred_integration_commands)
    assert "navig lighthouse deploy" in src
