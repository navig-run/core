"""Gateway test isolation.

Delivery now reserves one arrival for a cooldown — the reminder rows and the notify
matrix both claim a key in ``notify_settings`` (``dedupe:payload:…``) so the operator
sees one payment once and one reminder once. Tests here deliver the same words to the
same chat repeatedly, so without a reset the second one is treated as the duplicate
under test instead of the thing under test.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _empty_claim_store():
    """Start every gateway test with no announcement claims held."""
    from navig.notify import store

    store.init_db()
    with store.conn() as c:
        c.execute("DELETE FROM notify_settings WHERE key LIKE 'dedupe:payload:%'")
    yield
