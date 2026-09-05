"""MemoryDeleteTool must report whether a fact was actually deleted.

The agent `memory_delete` tool ignored `KeyFactStore.soft_delete()`'s bool return and
unconditionally returned `{"deleted": True}` — so deleting a non-existent id told the
agent (and the user) the delete worked. `soft_delete` returns False only when no fact
with that id exists (an already-deleted fact still matches its row → True), so a False
return is a genuine miss. Under the old code the first test fails (success=True).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from navig.agent.tools.memory_tools import MemoryDeleteTool


def _store(delete_returns: bool) -> MagicMock:
    store = MagicMock()
    store.soft_delete.return_value = delete_returns
    return store


async def test_delete_missing_id_is_failure_not_phantom_success():
    store = _store(False)  # soft_delete: no row with this id
    with patch("navig.memory.key_facts.KeyFactStore", return_value=store):
        result = await MemoryDeleteTool().run({"id": "does-not-exist"})
    assert result.success is False
    assert "does-not-exist" in (result.error or "")
    store.soft_delete.assert_called_once_with("does-not-exist")


async def test_delete_existing_id_succeeds():
    store = _store(True)
    with patch("navig.memory.key_facts.KeyFactStore", return_value=store):
        result = await MemoryDeleteTool().run({"id": "fact-1"})
    assert result.success is True
    assert result.output == {"deleted": True, "id": "fact-1"}


async def test_delete_blank_id_is_rejected():
    result = await MemoryDeleteTool().run({"id": "  "})
    assert result.success is False
    assert "id" in (result.error or "").lower()
