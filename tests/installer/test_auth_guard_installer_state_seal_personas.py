"""Batch 67 — gateway/auth_guard, installer/state, blackbox/seal, personas/store."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.gateway.auth_guard — AuthGuard
# ---------------------------------------------------------------------------

class TestAuthGuard:
    def _make(self, allowed_users=None, allowed_groups=None):
        from navig.gateway.auth_guard import AuthGuard
        return AuthGuard(allowed_users=allowed_users, allowed_groups=allowed_groups)

    def test_open_mode_allows_everyone(self):
        guard = self._make()  # no allowed_users
        assert guard.is_authorized(user_id=999, chat_id=1) is True

    def test_user_in_allowed_list(self):
        guard = self._make(allowed_users={123, 456})
        assert guard.is_authorized(user_id=123, chat_id=0) is True

    def test_user_not_in_list_denied(self):
        guard = self._make(allowed_users={123})
        assert guard.is_authorized(user_id=999, chat_id=100) is False

    def test_group_chat_allowed_via_groups(self):
        guard = self._make(allowed_users={1}, allowed_groups={-100})
        assert guard.is_authorized(user_id=999, chat_id=-100, is_group=True) is True

    def test_group_denied_when_not_in_groups(self):
        guard = self._make(allowed_users={1}, allowed_groups={-100})
        assert guard.is_authorized(user_id=999, chat_id=-200, is_group=True) is False

    def test_group_flag_false_ignores_allowed_groups(self):
        guard = self._make(allowed_users={1}, allowed_groups={-100})
        assert guard.is_authorized(user_id=999, chat_id=-100, is_group=False) is False

    def test_allowed_users_defaults_to_empty_set(self):
        from navig.gateway.auth_guard import AuthGuard
        guard = AuthGuard()
        assert isinstance(guard.allowed_users, set)
        assert len(guard.allowed_users) == 0

    def test_is_authorized_default_is_group_false(self):
        guard = self._make(allowed_users={1})
        # is_group defaults to False → groups not checked
        assert guard.is_authorized(user_id=999, chat_id=-100) is False


# ---------------------------------------------------------------------------
# navig.installer.state — save, load_last
# ---------------------------------------------------------------------------

class TestInstallerState:
    def _make_ctx(self, tmp_path, profile="node"):
        from navig.installer.contracts import InstallerContext
        return InstallerContext(profile=profile, config_dir=tmp_path)

    def _make_action(self, action_id="act1"):
        from navig.installer.contracts import Action
        return Action(id=action_id, description="Test action", module="test_mod", reversible=True)

    def _make_result(self, action_id="act1"):
        from navig.installer.contracts import ModuleState, Result
        return Result(action_id=action_id, state=ModuleState.APPLIED, message="done")

    def test_save_returns_path(self, tmp_path):
        from navig.installer.state import save
        ctx = self._make_ctx(tmp_path)
        actions = [self._make_action()]
        results = [self._make_result()]
        path = save(actions, results, ctx)
        assert path.exists()
        assert path.suffix == ".jsonl"

    def test_save_uses_custom_manifest_path(self, tmp_path):
        from navig.installer.state import save
        ctx = self._make_ctx(tmp_path)
        manifest = tmp_path / "custom.jsonl"
        path = save([self._make_action()], [self._make_result()], ctx, manifest_path=manifest)
        assert path == manifest
        assert manifest.exists()

    def test_save_records_profile_and_action_id(self, tmp_path):
        import json
        from navig.installer.state import save
        ctx = self._make_ctx(tmp_path, profile="operator")
        path = save([self._make_action("my.action")], [self._make_result("my.action")], ctx)
        line = json.loads(path.read_text().strip())
        assert line["profile"] == "operator"
        assert line["action_id"] == "my.action"

    def test_save_persists_state_value(self, tmp_path):
        import json
        from navig.installer.state import save
        from navig.installer.contracts import ModuleState, Result
        ctx = self._make_ctx(tmp_path)
        result = Result(action_id="a1", state=ModuleState.SKIPPED, message="skipped")
        path = save([self._make_action("a1")], [result], ctx)
        record = json.loads(path.read_text().strip())
        assert record["state"] == ModuleState.SKIPPED.value

    def test_load_last_missing_history_dir_returns_empty(self, tmp_path):
        from navig.installer.state import load_last
        assert load_last(tmp_path) == []

    def test_load_last_returns_most_recent_manifest(self, tmp_path):
        import json
        from navig.installer.state import save, load_last
        ctx = self._make_ctx(tmp_path, profile="node")
        save([self._make_action()], [self._make_result()], ctx)
        records = load_last(tmp_path, profile="node")
        assert len(records) == 1
        assert records[0]["profile"] == "node"

    def test_load_last_profile_filter(self, tmp_path):
        import json
        from navig.installer.state import save, load_last
        ctx_node = self._make_ctx(tmp_path, profile="node")
        ctx_op = self._make_ctx(tmp_path, profile="operator")
        save([self._make_action()], [self._make_result()], ctx_node)
        save([self._make_action()], [self._make_result()], ctx_op)
        records = load_last(tmp_path, profile="node")
        assert all(r["profile"] == "node" for r in records)


# ---------------------------------------------------------------------------
# navig.blackbox.seal — seal_bundle, is_sealed, unseal
# ---------------------------------------------------------------------------

class TestBlackboxSeal:
    def _make_bundle(self):
        bundle = MagicMock()
        bundle.created_at.isoformat.return_value = "2024-01-01T00:00:00+00:00"
        return bundle

    def test_seal_creates_marker_file(self, tmp_path):
        from navig.blackbox.seal import seal_bundle
        bundle = self._make_bundle()
        seal_bundle(bundle, blackbox_dir=tmp_path)
        assert (tmp_path / "SEALED").exists()

    def test_seal_sets_bundle_sealed_true(self, tmp_path):
        from navig.blackbox.seal import seal_bundle
        bundle = self._make_bundle()
        result = seal_bundle(bundle, blackbox_dir=tmp_path)
        assert bundle.sealed is True
        assert result is bundle

    def test_is_sealed_true_when_marker_exists(self, tmp_path):
        from navig.blackbox.seal import is_sealed
        (tmp_path / "SEALED").write_text("ts", encoding="utf-8")
        assert is_sealed(tmp_path) is True

    def test_is_sealed_false_when_no_marker(self, tmp_path):
        from navig.blackbox.seal import is_sealed
        assert is_sealed(tmp_path) is False

    def test_unseal_removes_marker(self, tmp_path):
        from navig.blackbox.seal import is_sealed, unseal
        (tmp_path / "SEALED").write_text("ts", encoding="utf-8")
        result = unseal(tmp_path)
        assert result is True
        assert not is_sealed(tmp_path)

    def test_unseal_returns_false_when_no_marker(self, tmp_path):
        from navig.blackbox.seal import unseal
        assert unseal(tmp_path) is False


# ---------------------------------------------------------------------------
# navig.personas.store — get_active_persona, set_active_persona
# ---------------------------------------------------------------------------

class TestPersonasStore:
    def test_get_active_persona_default_when_no_state(self):
        from navig.personas.store import get_active_persona
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = None
        with patch("navig.store.runtime.get_runtime_store", return_value=mock_store):
            result = get_active_persona(user_id=999)
        assert result == "default"

    def test_get_active_persona_returns_stored_value(self):
        from navig.personas.store import get_active_persona
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {"persona": "devops"}
        with patch("navig.store.runtime.get_runtime_store", return_value=mock_store):
            result = get_active_persona(user_id=123)
        assert result == "devops"

    def test_get_active_persona_blank_persona_returns_default(self):
        from navig.personas.store import get_active_persona
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {"persona": "  "}
        with patch("navig.store.runtime.get_runtime_store", return_value=mock_store):
            result = get_active_persona(user_id=1)
        assert result == "default"

    def test_get_active_persona_exception_returns_default(self):
        from navig.personas.store import get_active_persona
        with patch("navig.store.runtime.get_runtime_store", side_effect=RuntimeError("db down")):
            result = get_active_persona(user_id=1)
        assert result == "default"

    def test_set_active_persona_calls_store(self):
        from navig.personas.store import set_active_persona
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {"mode": "active"}
        with patch("navig.store.runtime.get_runtime_store", return_value=mock_store):
            set_active_persona(user_id=1, chat_id=10, persona_name="ops")
        mock_store.set_ai_state.assert_called_once()
        call_kwargs = mock_store.set_ai_state.call_args[1]
        assert call_kwargs.get("persona") == "ops"

    def test_set_active_persona_preserves_mode(self):
        from navig.personas.store import set_active_persona
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {"mode": "quiet"}
        with patch("navig.store.runtime.get_runtime_store", return_value=mock_store):
            set_active_persona(user_id=1, chat_id=10, persona_name="ops")
        call_kwargs = mock_store.set_ai_state.call_args[1]
        assert call_kwargs.get("mode") == "quiet"

    def test_set_active_persona_raises_on_store_exception(self):
        from navig.personas.store import set_active_persona
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {}
        mock_store.set_ai_state.side_effect = RuntimeError("write fail")
        with patch("navig.store.runtime.get_runtime_store", return_value=mock_store):
            with pytest.raises(RuntimeError, match="write fail"):
                set_active_persona(user_id=1, chat_id=10, persona_name="ops")
