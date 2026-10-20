"""Move tests are hermetic: no test reaches a real NAVIG OS unless it injects a runner.

Without this, default discovery finds the apps/os checkout this suite runs from and
executes its transfer CLI against each fake machine's placeholder credentials.enc.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_navig_os_here(monkeypatch):
    from navig.move import state

    monkeypatch.setattr(state, "find_runner", lambda: None)
