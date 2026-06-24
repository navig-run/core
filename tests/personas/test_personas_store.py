"""Tests for navig.personas.store — get_active_persona, set_active_persona."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

MODULE = "navig.store.runtime"


class TestGetActivePersona:
    def test_returns_default_when_no_state(self):
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = None
        with patch(f"{MODULE}.get_runtime_store", return_value=mock_store):
            from navig.personas.store import get_active_persona
            result = get_active_persona(user_id=1)
        assert result == "default"

    def test_returns_default_when_persona_empty(self):
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {"persona": ""}
        with patch(f"{MODULE}.get_runtime_store", return_value=mock_store):
            from navig.personas.store import get_active_persona
            result = get_active_persona(user_id=1)
        assert result == "default"

    def test_returns_stored_persona(self):
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {"persona": "hermes"}
        with patch(f"{MODULE}.get_runtime_store", return_value=mock_store):
            from navig.personas.store import get_active_persona
            result = get_active_persona(user_id=1)
        assert result == "hermes"

    def test_strips_whitespace_from_persona(self):
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {"persona": "  nero  "}
        with patch(f"{MODULE}.get_runtime_store", return_value=mock_store):
            from navig.personas.store import get_active_persona
            result = get_active_persona(user_id=1)
        assert result == "nero"

    def test_falls_back_on_import_error(self):
        with patch.dict("sys.modules", {"navig.store.runtime": None}):
            from navig.personas.store import get_active_persona
            result = get_active_persona(user_id=99)
        assert result == "default"

    def test_falls_back_on_exception(self):
        mock_store = MagicMock()
        mock_store.get_ai_state.side_effect = RuntimeError("db down")
        with patch(f"{MODULE}.get_runtime_store", return_value=mock_store):
            from navig.personas.store import get_active_persona
            result = get_active_persona(user_id=1)
        assert result == "default"


class TestSetActivePersona:
    def test_calls_set_ai_state(self):
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {"mode": "active", "context": None}
        with patch(f"{MODULE}.get_runtime_store", return_value=mock_store):
            from navig.personas.store import set_active_persona
            set_active_persona(user_id=1, chat_id=100, persona_name="hermes")
        mock_store.set_ai_state.assert_called_once()

    def test_preserves_existing_mode(self):
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {"mode": "paused", "context": "ctx"}
        with patch(f"{MODULE}.get_runtime_store", return_value=mock_store):
            from navig.personas.store import set_active_persona
            set_active_persona(user_id=1, chat_id=100, persona_name="nero")
        call_kw = mock_store.set_ai_state.call_args.kwargs
        assert call_kw["mode"] == "paused"
        assert call_kw["persona"] == "nero"

    def test_re_raises_on_exception(self):
        mock_store = MagicMock()
        mock_store.get_ai_state.return_value = {}
        mock_store.set_ai_state.side_effect = RuntimeError("save failed")
        with patch(f"{MODULE}.get_runtime_store", return_value=mock_store):
            from navig.personas.store import set_active_persona
            with pytest.raises(RuntimeError, match="save failed"):
                set_active_persona(user_id=1, chat_id=100, persona_name="x")
