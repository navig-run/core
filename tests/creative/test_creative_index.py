"""Capability index: provenance honesty, cache lifecycle, ranked query, isolation."""

from __future__ import annotations

import pytest

from navig.creative import index as idx


@pytest.fixture()
def isolated_index(tmp_path, monkeypatch):
    """Point data_dir() at a temp dir and hand back a controllable reader set."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    # Prove isolation BEFORE the first write — a defaulted path would scribble
    # on the operator's real store (the board.db incident).
    assert str(idx.index_path()).startswith(str(tmp_path)), idx.index_path()

    calls: dict[str, int] = {}

    def _reader(name: str, rows: list[dict]):
        def _fn():
            calls[name] = calls.get(name, 0) + 1
            return rows, len(rows)

        return _fn

    def _entry(eid: str, kind: str = "command", name: str | None = None, **over):
        base = {
            "id": eid,
            "kind": kind,
            "name": name or eid.split(":", 1)[-1],
            "summary": over.pop("summary", ""),
            "how_to_invoke": {"type": "cli", "ref": eid},
            "inputs": None,
            "outputs": None,
            "safety": "unknown",
            "requires": {"config": [], "plugins": [], "entitlement": None},
            "declared": {"origin": "test", "status": "stable"},
            "observed": {"state": "available", "degraded": False, "checked_at": "t", "error": None},
            "provenance": "source",
            "tags": [],
            "provider": None,
            "source_ref": "",
        }
        base.update(over)
        return base

    rows = {
        "commands": [
            _entry("command:navig docker ps", summary="List docker containers", tags=["docker"]),
            _entry("command:navig host list", summary="List hosts", tags=["ssh"]),
        ],
        "tools": [
            _entry(
                "tool:web_fetch",
                kind="tool",
                summary="Fetch a URL",
                provenance="runtime",
                how_to_invoke={"type": "agent_tool", "ref": "web_fetch"},
            ),
        ],
    }
    real_readers = idx._READERS  # stashed for the real-reader smoke test
    monkeypatch.setattr(
        idx,
        "_READERS",
        (
            ("commands", _reader("commands", rows["commands"])),
            ("tools", _reader("tools", rows["tools"])),
        ),
    )
    return {"calls": calls, "entry": _entry, "real_readers": real_readers}


# ── build / provenance ───────────────────────────────────────────────────────


def test_build_writes_snapshot_under_data_dir(isolated_index):
    snap = idx.build_index()
    assert snap["schema_version"] == idx.SCHEMA_VERSION
    assert len(snap["entries"]) == 3
    path = idx.index_path()
    assert path.exists()
    assert path.parts[-2:] == ("creative", "capability_index.json")
    loaded = idx.load_index()
    assert loaded is not None and loaded["built_at"] == snap["built_at"]


def test_provenance_is_source_or_runtime_never_assumption(isolated_index):
    snap = idx.build_index()
    for entry in snap["entries"]:
        assert entry["provenance"] in ("source", "runtime"), entry["id"]
        assert "assumption" not in entry["provenance"]


def test_reader_failure_is_recorded_not_fatal(isolated_index, monkeypatch):
    def _boom():
        raise RuntimeError("subsystem down")

    monkeypatch.setattr(
        idx,
        "_READERS",
        (
            ("commands", idx._READERS[0][1]),
            ("broken_source", _boom),
        ),
    )
    snap = idx.build_index()
    assert "broken_source" in snap["errors"]
    assert "subsystem down" in snap["errors"]["broken_source"]
    assert len(snap["entries"]) == 2  # the healthy reader still contributed
    assert snap["source_fingerprints"]["broken_source"] == -1


# ── cache lifecycle ──────────────────────────────────────────────────────────


def test_ensure_index_caches_within_ttl_then_rebuilds(isolated_index):
    idx.build_index()
    first_built = idx.load_index()["built_at"]
    idx.ensure_index()  # within TTL — served from cache, readers not re-run
    assert isolated_index["calls"]["commands"] == 1
    assert idx.load_index()["built_at"] == first_built

    idx.ensure_index(force=True)  # explicit rebuild
    assert isolated_index["calls"]["commands"] == 2

    idx.invalidate()
    assert not idx.index_path().exists()
    idx.ensure_index()
    assert isolated_index["calls"]["commands"] == 3


def test_stale_snapshot_rebuilds(isolated_index, monkeypatch):
    idx.build_index()
    # Age the snapshot past the TTL.
    snap = idx.load_index()
    snap["built_at"] = "2020-01-01T00:00:00Z"
    from navig.core.json_io import atomic_write_json

    atomic_write_json(snap, idx.index_path())
    idx.ensure_index(max_age_s=3600)
    assert isolated_index["calls"]["commands"] == 2


def test_corrupt_snapshot_is_a_miss_not_a_crash(isolated_index):
    idx.index_path().parent.mkdir(parents=True, exist_ok=True)
    idx.index_path().write_text("{not json", encoding="utf-8")
    assert idx.load_index() is None
    snap = idx.build_index()  # rebuild proceeds
    assert snap["entries"]


# ── query ────────────────────────────────────────────────────────────────────


def test_find_ranks_name_matches_first(isolated_index):
    snap = idx.build_index()
    hits = idx.find("docker", index=snap)
    assert hits, "docker query must match"
    assert hits[0]["id"] == "command:navig docker ps"


def test_find_filters_by_kind_and_respects_limit(isolated_index):
    snap = idx.build_index()
    only_tools = idx.find("fetch", kinds=["tool"], index=snap)
    assert [h["kind"] for h in only_tools] == ["tool"]
    assert len(idx.find("", limit=2, index=snap)) == 2


def test_find_no_match_returns_empty(isolated_index):
    snap = idx.build_index()
    assert idx.find("quantum-tunneling", index=snap) == []


def test_status_reports_presence_freshness_and_errors(isolated_index):
    empty = idx.status()
    assert empty["present"] is False and empty["entries"] == 0

    idx.build_index()
    fresh = idx.status()
    assert fresh["present"] is True and fresh["fresh"] is True
    assert fresh["entries"] == 3
    assert fresh["kinds"] == {"command": 2, "tool": 1}


# ── real readers ─────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_real_build_has_a_meaningful_floor(isolated_index, monkeypatch):
    """Run the REAL readers once and insist the result is a catalog, not a husk.

    Scan floor: a silent reader regression that returns nothing must fail here,
    not ship (a green suite over an empty index is the classic trap).
    """
    monkeypatch.setattr(idx, "_READERS", isolated_index["real_readers"])
    snap = idx.build_index()
    assert not snap["errors"], snap["errors"]
    # The REAL payload must persist — a soft-failed write would silently make
    # every ensure_index() rebuild forever.
    assert idx.index_path().exists(), "real snapshot failed to serialize to disk"
    assert idx.load_index() is not None
    # 1941 commands were committed at design time; 1000 is a defensive floor.
    counts = snap["source_fingerprints"]
    assert counts["commands"] >= 1000, counts
    counts_no_cmds = {k: v for k, v in counts.items() if k != "commands"}
    assert all(v >= 5 for v in counts_no_cmds.values()), counts
    assert len(snap["entries"]) >= 1000
    for entry in snap["entries"]:
        assert entry["provenance"] in ("source", "runtime")
    # Every entry self-describes how to invoke it (mission §2).
    assert all(entry.get("how_to_invoke", {}).get("ref") for entry in snap["entries"])
