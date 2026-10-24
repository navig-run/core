"""Regression: unrelated AI-state updates silently switched users to "assistant".

~15 call sites saved ``state.get("persona") or "assistant"`` on mode/context
updates (continuation pause, auto-reply toggles…), so a user who never chose a
persona ended up on "assistant" instead of the NAVIG default. Now:

* ``set_ai_state(persona=None)`` keeps the stored persona;
* ``switch_persona`` records ``persona_explicit``;
* a one-time v3→v4 store migration resets incidental "assistant" rows to "default"
  (v2→v3 is the reminders business-chat migration that landed first).
"""

from __future__ import annotations

import sqlite3

import pytest


def _v2_db(path) -> None:
    """A runtime.db exactly as schema v2 left it (no persona_explicit column)."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        INSERT INTO schema_version (version) VALUES (2);
        CREATE TABLE ai_state (
            user_id INTEGER PRIMARY KEY, chat_id INTEGER, mode TEXT, persona TEXT,
            started_at TEXT, context TEXT, last_active_at TEXT
        );
        INSERT INTO ai_state (user_id, chat_id, mode, persona) VALUES
            (1, 1, 'active', 'assistant'),
            (2, 2, 'active', 'tyler'),
            (3, 3, 'inactive', NULL);
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from navig.store import runtime as runtime_mod

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    s = runtime_mod.RuntimeStore(tmp_path / "runtime.db")
    monkeypatch.setattr(runtime_mod, "_store", s)
    yield s
    s.close()


def test_migration_resets_incidental_assistant_only(tmp_path):
    from navig.store.runtime import RuntimeStore

    db = tmp_path / "runtime.db"
    _v2_db(db)
    s = RuntimeStore(db)
    assert s.get_ai_state(1)["persona"] == "default"  # incidental → NAVIG
    assert s.get_ai_state(2)["persona"] == "tyler"  # a real persona is untouched
    assert s.get_ai_state(3)["persona"] is None
    assert s.get_schema_version() == RuntimeStore.SCHEMA_VERSION


def test_explicit_assistant_survives_and_rerun_is_noop(tmp_path):
    from navig.store.runtime import RuntimeStore

    db = tmp_path / "runtime.db"
    s = RuntimeStore(db)
    s.set_ai_state(user_id=5, chat_id=5, mode="active", persona="assistant", persona_explicit=True)
    s.set_ai_state(user_id=6, chat_id=6, mode="active", persona="assistant")
    conn = s._get_conn()
    # Running the reset again (e.g. a legacy import) only touches un-chosen rows…
    assert RuntimeStore._reset_incidental_assistant_persona(conn) == 1
    # …and a second run changes nothing.
    assert RuntimeStore._reset_incidental_assistant_persona(conn) == 0
    conn.commit()
    assert s.get_ai_state(5)["persona"] == "assistant"
    assert s.get_ai_state(5)["persona_explicit"] is True
    assert s.get_ai_state(6)["persona"] == "default"

    # Re-opening a migrated DB does not re-run the migration.
    s2 = RuntimeStore(db)
    assert s2.get_ai_state(5)["persona"] == "assistant"


def test_unrelated_ai_state_update_keeps_persona(store):
    from navig.personas.store import get_active_persona, get_persona_choice, set_active_persona

    set_active_persona(7, 7, "tyler")
    assert get_persona_choice(7) == ("tyler", True)

    # What the telegram/agent call sites now do on a mode/context update.
    state = store.get_ai_state(7) or {}
    store.set_ai_state(
        user_id=7, chat_id=7, mode="inactive", persona=state.get("persona"), context={"x": 1}
    )
    store.set_ai_state(user_id=7, chat_id=7, mode="active", persona=None, context="intake")
    assert get_persona_choice(7) == ("tyler", True)

    # A user with no state never gets a persona invented for them.
    store.set_ai_state(user_id=8, chat_id=8, mode="active", persona=None)
    assert store.get_ai_state(8)["persona"] is None
    assert get_active_persona(8) == "default"
    assert get_persona_choice(8) == ("default", False)


async def test_switch_persona_marks_explicit(store, monkeypatch):
    from navig.personas import loader as loader_mod
    from navig.personas import resolver as resolver_mod
    from navig.personas.manager import switch_persona
    from navig.personas.store import get_persona_choice

    monkeypatch.setattr(resolver_mod, "resolve_persona", lambda name, cwd=None: object())
    monkeypatch.setattr(loader_mod, "load_persona", lambda name, cwd=None: (object(), ""))
    store.set_ai_state(user_id=9, chat_id=9, mode="active", persona="assistant")
    assert get_persona_choice(9) == ("assistant", False)

    await switch_persona("assistant", 9, 9, deliver_assets=False)
    assert get_persona_choice(9) == ("assistant", True)


def test_a_v3_database_from_main_gets_the_persona_reset(tmp_path):
    """The live path: the daemon already ran main's v2→v3 (reminder bcid/claimed_at),
    so the persona reset must arrive as its OWN later step, not share v3."""
    from navig.store.runtime import RuntimeStore

    db = tmp_path / "runtime.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        INSERT INTO schema_version (version) VALUES (3);
        CREATE TABLE ai_state (
            user_id INTEGER PRIMARY KEY, chat_id INTEGER, mode TEXT, persona TEXT,
            started_at TEXT, context TEXT, last_active_at TEXT
        );
        INSERT INTO ai_state (user_id, chat_id, mode, persona, context) VALUES
            (159901607, 159901607, 'inactive', 'assistant', '"intake"');
        """
    )
    conn.commit()
    conn.close()

    s = RuntimeStore(db)
    assert s.get_ai_state(159901607)["persona"] == "default"
    assert s.get_schema_version() == RuntimeStore.SCHEMA_VERSION
    s.close()
