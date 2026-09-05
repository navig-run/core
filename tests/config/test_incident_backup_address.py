"""An incident must name the way BACK, not just the thing that broke.

Five producers quarantine an unreadable file to ``<name>.corrupt`` before falling
back to defaults, and that copy is the entire recovery path. ``doctor`` surfaced it
for exactly one hardcoded filename (``config.yaml.corrupt``); every other
quarantined file — browser profiles, user_state, settings.json, the MCP registry,
and the desktop stores — left the operator with a problem and no way back.
"""

from __future__ import annotations

from navig.core import incidents


def test_describe_names_the_preserved_copy() -> None:
    line = incidents.describe(
        {
            "ts": 0,
            "event": incidents.LOAD_FAILED,
            "data": {"path": "/cfg/config.yaml", "backup": "/cfg/config.yaml.corrupt"},
        }
    )
    assert "/cfg/config.yaml.corrupt" in line, line
    # The failing file is still named — the backup is additional, not a replacement.
    assert "/cfg/config.yaml" in line


def test_describe_is_unchanged_when_no_backup_was_made() -> None:
    """A failed copy must not produce a phantom address."""
    line = incidents.describe(
        {"ts": 0, "event": incidents.LOAD_FAILED, "data": {"path": "/cfg/config.yaml"}}
    )
    assert "preserved" not in line, line
    assert "/cfg/config.yaml" in line


def test_describe_handles_a_desktop_store_incident() -> None:
    """The Rust desktop journal writes `store` + `backup` (navig-shared-desktop)."""
    line = incidents.describe(
        {
            "ts": 0,
            "event": incidents.STORE_READ_FAILED,
            "data": {
                "store": r"C:\Users\x\.navig\anchor\config.json",
                "backup": r"C:\Users\x\.navig\anchor\config.json.corrupt",
                "source": "desktop",
            },
        }
    )
    assert r"C:\Users\x\.navig\anchor\config.json.corrupt" in line, line


def test_describe_survives_a_malformed_entry() -> None:
    """`describe` renders whatever it is handed — it must never raise into doctor."""
    assert incidents.describe({})
    assert incidents.describe({"event": "unknown_id", "data": "not-a-dict", "ts": "x"})


def test_config_load_failure_records_the_backup_path(tmp_path, monkeypatch) -> None:
    """The producer that COMPUTES the backup path must pass it on.

    It logged the address and then recorded an incident without it, so the one
    place the operator would look could not show it.
    """
    recorded: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        incidents, "record", lambda event, **data: recorded.append((event, data))
    )

    from navig import config as config_mod

    monkeypatch.setattr(config_mod, "_incidents", incidents, raising=False)

    bad = tmp_path / "config.yaml"
    bad.write_text("this: is: not: valid: yaml:\n  - [", encoding="utf-8")

    cm = config_mod.ConfigManager.__new__(config_mod.ConfigManager)
    cm._recover_after_failed_load(bad, ValueError("boom"))  # type: ignore[attr-defined]

    assert recorded, "a failed load must record an incident"
    # Select by EVENT, never by position: when a last-known-good cache exists the
    # recovery path records a SECOND incident (`config_recovered_from_cache`) right
    # after this one, so `recorded[-1]` passes alone and fails in a full suite run
    # depending on whether a cache happens to be present.
    matching = [data for event, data in recorded if event == incidents.LOAD_FAILED]
    assert matching, f"expected a {incidents.LOAD_FAILED} incident, got {[e for e, _ in recorded]}"
    assert matching[0].get("backup") == str(tmp_path / "config.yaml.corrupt"), matching[0]
    assert (tmp_path / "config.yaml.corrupt").exists(), "the copy must actually be made"
