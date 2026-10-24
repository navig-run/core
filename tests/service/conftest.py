"""Machinery tests must not inherit the operator's CLIENT state.

``<home>/.navig-os/brain-link.json`` marks an install as a client of a remote brain;
``navig service start`` then refuses (correctly) because starting a local daemon would
create a second authority. These suites exercise the autostart/systemd machinery, not
the refusal — on a developer machine that really is a client, they would otherwise fail
for a reason that has nothing to do with what they assert. The refusal itself is covered
by ``tests/commands/test_service_client_brain_refusal.py``.
"""

from __future__ import annotations

import pytest

from navig.commands import service as svc


@pytest.fixture(autouse=True)
def _no_client_link(monkeypatch, tmp_path):
    monkeypatch.setattr(
        svc, "_brain_link_path", lambda: tmp_path / "no-brain-link.json", raising=False
    )
