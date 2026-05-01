"""Batch 54 — hermetic unit tests.

Modules covered:
- navig.installer.contracts     (ModuleState, Action, Result, InstallerContext)
- navig.gateway.channels.utils.messages  (Messages constants)
- navig.boot_messages           (NAVIG_BOOT_MESSAGES, get_boot_message)
- navig.memory.store            (_MemoryStoreCompat, get_memory_store)
- navig.formations.registry     (FormationRegistry singleton)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


# ──────────────────────────────────────────────────────────────────────────────
# navig.installer.contracts
# ──────────────────────────────────────────────────────────────────────────────


class TestModuleState:
    """ModuleState enum — string values."""

    def test_pending_value(self):
        from navig.installer.contracts import ModuleState

        assert ModuleState.PENDING.value == "pending"

    def test_applied_value(self):
        from navig.installer.contracts import ModuleState

        assert ModuleState.APPLIED.value == "applied"

    def test_failed_value(self):
        from navig.installer.contracts import ModuleState

        assert ModuleState.FAILED.value == "failed"

    def test_skipped_value(self):
        from navig.installer.contracts import ModuleState

        assert ModuleState.SKIPPED.value == "skipped"

    def test_rolled_back_value(self):
        from navig.installer.contracts import ModuleState

        assert ModuleState.ROLLED_BACK.value == "rolled_back"

    def test_five_states(self):
        from navig.installer.contracts import ModuleState

        assert len(ModuleState) == 5


class TestAction:
    """Action dataclass — idempotent installer step."""

    def test_basic_construction(self):
        from navig.installer.contracts import Action

        a = Action(id="create_dir", description="Create config dir", module="core")
        assert a.id == "create_dir"
        assert a.module == "core"

    def test_defaults(self):
        from navig.installer.contracts import Action

        a = Action(id="x", description="y", module="z")
        assert a.data == {}
        assert a.reversible is True
        assert a.undo_data == {}

    def test_custom_data(self):
        from navig.installer.contracts import Action

        a = Action(id="x", description="y", module="z", data={"path": "/tmp"})
        assert a.data["path"] == "/tmp"

    def test_not_reversible(self):
        from navig.installer.contracts import Action

        a = Action(id="x", description="y", module="z", reversible=False)
        assert a.reversible is False


class TestResult:
    """Result dataclass — outcome of applying an Action."""

    def test_applied_is_ok(self):
        from navig.installer.contracts import ModuleState, Result

        r = Result(action_id="act1", state=ModuleState.APPLIED)
        assert r.ok is True

    def test_skipped_is_ok(self):
        from navig.installer.contracts import ModuleState, Result

        r = Result(action_id="act1", state=ModuleState.SKIPPED)
        assert r.ok is True

    def test_failed_is_not_ok(self):
        from navig.installer.contracts import ModuleState, Result

        r = Result(action_id="act1", state=ModuleState.FAILED)
        assert r.ok is False

    def test_pending_is_not_ok(self):
        from navig.installer.contracts import ModuleState, Result

        r = Result(action_id="act1", state=ModuleState.PENDING)
        assert r.ok is False

    def test_error_stored(self):
        from navig.installer.contracts import ModuleState, Result

        r = Result(action_id="act1", state=ModuleState.FAILED, error="permission denied")
        assert r.error == "permission denied"

    def test_message_stored(self):
        from navig.installer.contracts import ModuleState, Result

        r = Result(action_id="act1", state=ModuleState.APPLIED, message="created")
        assert r.message == "created"

    def test_defaults(self):
        from navig.installer.contracts import ModuleState, Result

        r = Result(action_id="a", state=ModuleState.APPLIED)
        assert r.message == ""
        assert r.error is None
        assert r.undo_data == {}


class TestInstallerContext:
    """InstallerContext — immutable shared state."""

    def test_minimal_construction(self):
        from navig.installer.contracts import InstallerContext

        ctx = InstallerContext(profile="default")
        assert ctx.profile == "default"
        assert ctx.dry_run is False
        assert ctx.quiet is False

    def test_dry_run(self):
        from navig.installer.contracts import InstallerContext

        ctx = InstallerContext(profile="default", dry_run=True)
        assert ctx.dry_run is True

    def test_config_dir_is_path(self):
        from navig.installer.contracts import InstallerContext

        ctx = InstallerContext(profile="test")
        assert isinstance(ctx.config_dir, Path)

    def test_custom_config_dir(self, tmp_path):
        from navig.installer.contracts import InstallerContext

        ctx = InstallerContext(profile="test", config_dir=tmp_path)
        assert ctx.config_dir == tmp_path

    def test_extra_dict(self):
        from navig.installer.contracts import InstallerContext

        ctx = InstallerContext(profile="test", extra={"token": "abc"})
        assert ctx.extra["token"] == "abc"


# ──────────────────────────────────────────────────────────────────────────────
# navig.gateway.channels.utils.messages
# ──────────────────────────────────────────────────────────────────────────────


class TestMessages:
    """Messages class — static string constants for Telegram bot."""

    def test_error_generic_is_str(self):
        from navig.gateway.channels.utils.messages import Messages

        assert isinstance(Messages.ERROR_GENERIC, str)
        assert len(Messages.ERROR_GENERIC) > 0

    def test_error_rate_limit_is_str(self):
        from navig.gateway.channels.utils.messages import Messages

        assert isinstance(Messages.ERROR_RATE_LIMIT, str)

    def test_error_invalid_input_is_str(self):
        from navig.gateway.channels.utils.messages import Messages

        assert isinstance(Messages.ERROR_INVALID_INPUT, str)

    def test_success_operation_is_str(self):
        from navig.gateway.channels.utils.messages import Messages

        assert isinstance(Messages.SUCCESS_OPERATION, str)

    def test_not_authorized_is_str(self):
        from navig.gateway.channels.utils.messages import Messages

        assert isinstance(Messages.NOT_AUTHORIZED, str)

    def test_bot_started_is_str(self):
        from navig.gateway.channels.utils.messages import Messages

        assert isinstance(Messages.BOT_STARTED, str)

    def test_briefing_header_is_str(self):
        from navig.gateway.channels.utils.messages import Messages

        assert isinstance(Messages.BRIEFING_HEADER, str)

    def test_constants_are_unique(self):
        from navig.gateway.channels.utils.messages import Messages

        msgs = [
            Messages.ERROR_GENERIC,
            Messages.ERROR_RATE_LIMIT,
            Messages.ERROR_INVALID_INPUT,
        ]
        assert len(set(msgs)) == len(msgs)


# ──────────────────────────────────────────────────────────────────────────────
# navig.boot_messages
# ──────────────────────────────────────────────────────────────────────────────


class TestNavigBootMessages:
    """NAVIG_BOOT_MESSAGES list of boot strings."""

    def test_is_list(self):
        from navig.boot_messages import NAVIG_BOOT_MESSAGES

        assert isinstance(NAVIG_BOOT_MESSAGES, list)

    def test_has_ten_entries(self):
        from navig.boot_messages import NAVIG_BOOT_MESSAGES

        assert len(NAVIG_BOOT_MESSAGES) == 10

    def test_all_strings(self):
        from navig.boot_messages import NAVIG_BOOT_MESSAGES

        for msg in NAVIG_BOOT_MESSAGES:
            assert isinstance(msg, str)
            assert len(msg) > 0

    def test_entries_are_unique(self):
        from navig.boot_messages import NAVIG_BOOT_MESSAGES

        assert len(set(NAVIG_BOOT_MESSAGES)) == len(NAVIG_BOOT_MESSAGES)


class TestGetBootMessage:
    """get_boot_message — returns a random boot message with optional context."""

    def test_returns_string(self):
        from navig.boot_messages import get_boot_message

        result = get_boot_message()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_base_is_from_list(self):
        from navig.boot_messages import NAVIG_BOOT_MESSAGES, get_boot_message

        result = get_boot_message()
        # The result must start with one of the known messages
        assert any(result.startswith(msg) for msg in NAVIG_BOOT_MESSAGES)

    def test_with_location(self):
        from navig.boot_messages import get_boot_message

        result = get_boot_message(location="48.8566° N")
        assert "Position: 48.8566° N." in result

    def test_with_uptime(self):
        from navig.boot_messages import get_boot_message

        result = get_boot_message(uptime=3600)
        assert "Last session: 3600s." in result

    def test_with_signal_strength(self):
        from navig.boot_messages import get_boot_message

        result = get_boot_message(signal_strength=87)
        assert "Signal: 87%." in result

    def test_all_extras_combined(self):
        from navig.boot_messages import get_boot_message

        result = get_boot_message(location="lat/lon", uptime=100, signal_strength=50)
        assert "Position:" in result
        assert "Last session:" in result
        assert "Signal:" in result

    def test_separator_dot_when_extras(self):
        from navig.boot_messages import get_boot_message

        result = get_boot_message(uptime=10)
        assert " · " in result

    def test_no_separator_without_extras(self):
        from navig.boot_messages import get_boot_message

        # Deterministically pick last message by patching random
        with patch("navig.boot_messages.random.randrange", return_value=0):
            result = get_boot_message()
        assert " · " not in result


# ──────────────────────────────────────────────────────────────────────────────
# navig.memory.store
# ──────────────────────────────────────────────────────────────────────────────


class TestMemoryStoreCompat:
    """_MemoryStoreCompat.add() — delegates to KeyFact store."""

    def test_add_context_creates_key_fact(self):
        from navig.memory.store import get_memory_store

        mock_store = MagicMock()
        with patch("navig.memory.store.get_key_fact_store", return_value=mock_store):
            store = get_memory_store()
            store.add("remember this")
        mock_store.upsert.assert_called_once()

    def test_add_feedback_maps_to_preference(self):
        from navig.memory.key_facts import KeyFact
        from navig.memory.store import get_memory_store

        captured = []
        mock_store = MagicMock()
        mock_store.upsert.side_effect = lambda kf: captured.append(kf)

        with patch("navig.memory.store.get_key_fact_store", return_value=mock_store):
            store = get_memory_store()
            store.add("user prefers dark mode", memory_type="FEEDBACK")

        assert len(captured) == 1
        assert captured[0].category == "preference"

    def test_add_context_type(self):
        from navig.memory.store import get_memory_store

        captured = []
        mock_store = MagicMock()
        mock_store.upsert.side_effect = lambda kf: captured.append(kf)

        with patch("navig.memory.store.get_key_fact_store", return_value=mock_store):
            store = get_memory_store()
            store.add("some context", memory_type="context")

        assert captured[0].category == "context"

    def test_metadata_passed_through(self):
        from navig.memory.store import get_memory_store

        captured = []
        mock_store = MagicMock()
        mock_store.upsert.side_effect = lambda kf: captured.append(kf)

        with patch("navig.memory.store.get_key_fact_store", return_value=mock_store):
            store = get_memory_store()
            store.add("fact", metadata={"source": "telegram"})

        assert captured[0].metadata == {"source": "telegram"}

    def test_null_metadata_defaults_to_empty(self):
        from navig.memory.store import get_memory_store

        captured = []
        mock_store = MagicMock()
        mock_store.upsert.side_effect = lambda kf: captured.append(kf)

        with patch("navig.memory.store.get_key_fact_store", return_value=mock_store):
            store = get_memory_store()
            store.add("fact")

        assert captured[0].metadata == {}


class TestGetMemoryStore:
    """get_memory_store — returns _MemoryStoreCompat instance."""

    def test_returns_compat(self):
        from navig.memory.store import _MemoryStoreCompat, get_memory_store

        mock_store = MagicMock()
        with patch("navig.memory.store.get_key_fact_store", return_value=mock_store):
            store = get_memory_store()
        assert isinstance(store, _MemoryStoreCompat)

    def test_has_add_method(self):
        from navig.memory.store import get_memory_store

        mock_store = MagicMock()
        with patch("navig.memory.store.get_key_fact_store", return_value=mock_store):
            store = get_memory_store()
        assert callable(store.add)


# ──────────────────────────────────────────────────────────────────────────────
# navig.formations.registry
# ──────────────────────────────────────────────────────────────────────────────


class TestFormationRegistry:
    """FormationRegistry singleton — caches active formation."""

    def _make_fresh(self):
        """Return a fresh (non-singleton) instance by resetting class state."""
        from navig.formations import registry as reg_mod

        reg_mod.FormationRegistry._instance = None
        return reg_mod.FormationRegistry()

    def test_singleton_returns_same_instance(self):
        from navig.formations import registry as reg_mod
        from navig.formations.registry import FormationRegistry

        reg_mod.FormationRegistry._instance = None
        a = FormationRegistry.get_instance()
        b = FormationRegistry.get_instance()
        assert a is b
        reg_mod.FormationRegistry._instance = None  # cleanup

    def test_initial_state(self):
        reg = self._make_fresh()
        assert reg.get_active() is None
        assert reg.get_formation_map() == {}

    def test_initialize_sets_active(self):
        from navig.formations import registry as reg_mod

        mock_formation = MagicMock()
        mock_formation.name = "default"

        with (
            patch.object(reg_mod, "discover_formations", return_value={}),
            patch.object(reg_mod, "get_active_formation", return_value=mock_formation),
        ):
            reg = self._make_fresh()
            reg.initialize()

        assert reg.get_active() is mock_formation

    def test_initialize_idempotent(self):
        from navig.formations import registry as reg_mod

        call_count = 0

        def counting_discover():
            nonlocal call_count
            call_count += 1
            return {}

        with (
            patch.object(reg_mod, "discover_formations", side_effect=counting_discover),
            patch.object(reg_mod, "get_active_formation", return_value=None),
        ):
            reg = self._make_fresh()
            reg.initialize()
            reg.initialize()  # second call should be no-op

        assert call_count == 1

    def test_reload_forces_reinit(self):
        from navig.formations import registry as reg_mod

        call_count = 0

        def counting_discover():
            nonlocal call_count
            call_count += 1
            return {}

        with (
            patch.object(reg_mod, "discover_formations", side_effect=counting_discover),
            patch.object(reg_mod, "get_active_formation", return_value=None),
        ):
            reg = self._make_fresh()
            reg.initialize()
            reg.reload()

        assert call_count == 2

    def test_formation_map_stored(self):
        from navig.formations import registry as reg_mod

        fake_map = {"default": Path("/formations/default.yaml")}
        with (
            patch.object(reg_mod, "discover_formations", return_value=fake_map),
            patch.object(reg_mod, "get_active_formation", return_value=None),
        ):
            reg = self._make_fresh()
            reg.initialize()

        assert reg.get_formation_map() == fake_map

    def test_get_registry_returns_formation_registry(self):
        from navig.formations import registry as reg_mod
        from navig.formations.registry import FormationRegistry, get_registry

        reg_mod.FormationRegistry._instance = None
        result = get_registry()
        assert isinstance(result, FormationRegistry)
        reg_mod.FormationRegistry._instance = None  # cleanup
