from __future__ import annotations


class _VaultStub:
    def __init__(self, existing_ids: set[str] | None = None):
        self._existing_ids = existing_ids or set()

    def get_by_id(self, credential_id: str):
        if credential_id in self._existing_ids:
            return object()
        return None


def test_resolve_target_prefers_provider_for_8char_name_without_matching_id():
    from navig.commands.vault import _resolve_test_target_mode

    mode, value = _resolve_test_target_mode(_VaultStub(), "deepgram", None, None)
    assert mode == "provider"
    assert value == "deepgram"


def test_resolve_target_uses_id_when_matching_short_id_exists():
    from navig.commands.vault import _resolve_test_target_mode

    mode, value = _resolve_test_target_mode(
        _VaultStub(existing_ids={"4d731848"}), "4d731848", None, None
    )
    assert mode == "id"
    assert value == "4d731848"


def test_resolve_target_with_explicit_provider_flag():
    from navig.commands.vault import _resolve_test_target_mode

    mode, value = _resolve_test_target_mode(
        _VaultStub(existing_ids={"4d731848"}), "4d731848", "deepgram", None
    )
    assert mode == "provider"
    assert value == "deepgram"


def test_resolve_target_rejects_conflicting_flags():
    from navig.commands.vault import _resolve_test_target_mode

    try:
        _resolve_test_target_mode(_VaultStub(), "", "deepgram", "4d731848")
    except ValueError as exc:
        assert "either --id or --provider" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
