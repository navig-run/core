"""Regression: the sigil-genesis onboarding step actually creates the identity.

It imported a never-existent `ensure_sigil` (swallowed by try/except), then
wrote a success marker and reported "completed" — a green light over a missing
entity. It now creates the entity via the real `ensure_sigil`, reports success
only if the entity landed, and gates re-runs on `entity_exists()`.
"""

from __future__ import annotations


class _Genesis:
    nodeId = "node_abc123"


def test_step_creates_entity_and_verify_flips(monkeypatch, tmp_path):
    from navig.identity import sigil_store
    from navig.onboarding.steps import _step_sigil_genesis

    monkeypatch.setattr(sigil_store, "_entity_json_path", lambda: tmp_path / "id" / "entity.json")

    step = _step_sigil_genesis(tmp_path / "navig", _Genesis())

    # verify() gates the run: False (no identity) → the engine executes run().
    assert step.verify() is False

    result = step.run()

    assert result.status == "completed"
    assert result.output.get("nodeId") == "node_abc123"
    assert sigil_store.entity_exists() is True

    # identity now exists → verify() True → the engine skips on a re-run.
    assert step.verify() is True


def test_step_reports_failed_when_entity_not_created(monkeypatch, tmp_path):
    from navig.identity import sigil_store
    from navig.onboarding.steps import _step_sigil_genesis

    monkeypatch.setattr(sigil_store, "_entity_json_path", lambda: tmp_path / "id" / "entity.json")

    def boom(*a, **k):
        raise RuntimeError("no seed available")

    monkeypatch.setattr(sigil_store, "ensure_sigil", boom)

    step = _step_sigil_genesis(tmp_path / "navig", _Genesis())

    result = step.run()

    # no green light over a missing entity
    assert result.status == "failed"
    assert step.verify() is False
