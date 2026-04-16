from __future__ import annotations

from navig.memory.key_facts import KeyFact, get_key_fact_store


class _MemoryStoreCompat:
    """Compatibility wrapper exposing add() used by legacy reaction handlers."""

    def add(self, content: str, memory_type: str = "context", metadata: dict | None = None) -> None:
        store = get_key_fact_store()
        category = "preference" if memory_type.upper() == "FEEDBACK" else "context"
        store.upsert(
            KeyFact(
                content=content,
                category=category,
                metadata=metadata or {},
            )
        )


def get_memory_store() -> _MemoryStoreCompat:
    return _MemoryStoreCompat()
