from __future__ import annotations

from typing import Any

from navig.vault import get_vault


class VaultManager:
    """Compatibility wrapper around the unified Vault API."""

    def __init__(self) -> None:
        self._vault = get_vault()

    def get(self, key: str) -> Any:
        return self._vault.get(key)

    def list(self, *args: Any, **kwargs: Any):
        return self._vault.list(*args, **kwargs)
