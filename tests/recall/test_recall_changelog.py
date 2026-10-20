"""Legacy changelog parsing + the migrate pipeline (dry-run → backup → import →
verify → retire), against fixture shapes CUT from the three real families:

* GRABNITE's dated dev entries, * NAVIG's archived session addenda,
* Keep-a-Changelog release notes (anchor/dock/netwizard).

Retirement is guarded four ways — release notes are never retired, a consumer blocks
retirement, a missing root blocks retirement, and an unparsable file raises before
anything happens. Re-import is idempotent (file digest + content-addressed ids).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.recall import changelog as cl
from navig.recall.archive import Archive
from navig.recall.ingest import Ingestor
from navig.recall.projects import ProjectResolver
from navig.recall.sources import changelog as cl_src
from navig.recall.store import RecallStore
from navig.recall.vault import Vault

_GRABNITE = """# Grabnite Changelog

Ahoy! The chronicles of our voyage.

---

## 2025-11-22: TypeScript Strict Mode Mastery
**What's New:**
- Achieved 0 type errors
- Fixed 9 exhaustive-deps warnings

## 2025-11-01: Earlier Voyage
- Planted the first flag
"""

_ADDENDA = """# NAVIG session ledger — 2026 (archived)

> Moved here verbatim; search it, do not read it.

## ✅ Session Addendum — 2026-10-13 (Agent Control Phase 1)

The Observer plugin ships read-only. ⚠ Conflict radar is opt-in.

| piece | where |
|---|---|
| plugin | `plugins/navig-agentctl/` |

## Addendum 2026-10-10 — OS trust pass

- Linked-machine timeouts were slow handlers, not the relay.

## Notes section (not an addendum)

Foreign prose under its own header.

## Addendum 2026-10-01 — Another

- last entry
"""

_RELEASE = """# Changelog

All notable changes. Format: Keep a Changelog.

## [Unreleased]

### Changed
- **One window: the Command Center.**

## [0.1.0] - 2026-01-01

### Added
- **Speed Testing** - download/upload

"""


@pytest.fixture
def ing(tmp_path: Path) -> Ingestor:
    root = tmp_path / "recall"
    return Ingestor(store=RecallStore(root / "index.db"), archive=Archive(root / "archive"),
                    vault=Vault(root / "vault"),
                    resolver=ProjectResolver(entries=[("alpha", r"E:\proj\alpha")],
                                             home=r"C:\Users\someone"))


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ── parser: the three families ──────────────────────────────


def test_parse_dated_dev_entries() -> None:
    entries = cl.parse(_GRABNITE)
    assert [e.title for e in entries] == ["TypeScript Strict Mode Mastery", "Earlier Voyage"]
    assert [e.date for e in entries] == ["2025-11-22", "2025-11-01"]
    assert entries[0].start == 7 and entries[0].end == 11
    assert entries[1].start == entries[0].end + 1
    assert "Fixed 9 exhaustive-deps" in entries[0].text
    assert cl.classify(entries) == "private_dev"


def test_parse_session_addenda_with_a_foreign_section() -> None:
    entries = cl.parse(_ADDENDA)
    titles = [e.title for e in entries]
    assert titles == ["Agent Control Phase 1", "OS trust pass",
                      "Notes section (not an addendum)", "Another"]
    assert entries[0].date == "2026-10-13"
    assert entries[1].date == "2026-10-10"
    # the foreign section is its own entry — nothing bleeds across headers
    assert "Conflict radar" in entries[0].text
    assert "Conflict radar" not in entries[1].text
    assert entries[-1].date == "2026-10-01"
    assert cl.classify(entries) == "private_dev"


def test_parse_release_notes_and_classify() -> None:
    entries = cl.parse(_RELEASE)
    assert [e.title for e in entries] == ["Unreleased", "0.1.0"]
    assert entries[1].date == "2026-01-01"
    assert cl.classify(entries) == "release_notes"
    # an empty `## [Unreleased]` (netwizard) is skipped, not indexed
    assert [e.title for e in cl.parse("## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n- x\n")] == ["0.1.0"]


def test_parse_edge_cases() -> None:
    assert cl.parse("") == []
    with pytest.raises(ValueError, match="no recognized entry headers"):
        cl.parse("just prose, no headers at all\nmore prose\n")
    # ids are content-addressed: same content → same id, edited entry → new id
    a, b = cl.parse(_GRABNITE), cl.parse(_GRABNITE)
    assert [e.sha for e in a] == [e.sha for e in b]
    edited = cl.parse(_GRABNITE.replace("Planted the first flag", "Planted a different flag"))
    assert a[1].sha != edited[1].sha
    loc = json.loads(a[0].locator)
    assert loc["lines"] == [a[0].start, a[0].end]


# ── pipeline: dry-run, apply, verify, retire ────────────────


def test_dry_run_writes_nothing(ing, tmp_path: Path) -> None:
    p = _write(tmp_path, "CHANGELOG.md", _GRABNITE)
    out = ing.import_changelog("alpha", p, root=tmp_path, apply=False)
    assert out["applied"] is False and out["entries"] == 2
    assert out["classification"] == "private_dev"
    assert out["record_ids"] and out["sha256"]
    # zero writes: no backup dir, no source, no records
    assert not (ing.vault.root / ".migrations").exists()
    assert ing.store.stats()["records"] == 0
    assert p.exists()


def test_apply_imports_verifies_and_backs_up(ing, tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    p = _write(proj, "CHANGELOG.md", _GRABNITE)
    out = ing.import_changelog("alpha", p, root=proj, apply=True)
    assert out["applied"] is True and out["verify"]["ok"] is True
    # source + turns + records + evidence
    stats = ing.store.stats()
    assert stats["records"] == 2 and stats["turns"] == 2
    src = ing.store.get_source(cl_src.source_id_for("alpha", "CHANGELOG.md"))
    assert src is not None and src["adapter"] == "changelog" and src["turns"] == 2
    rid = out["record_ids"][0]
    rec = ing.store.get_record(rid)
    assert rec is not None
    assert rec.status_basis == "audit" and rec.lifecycle == "unknown"
    assert rec.evidence[0].speaker == "imported_doc"
    assert rec.evidence[0].locator and "lines" in rec.evidence[0].locator
    # the evidence msg_id exists in the store (content-addressed: L<start>-<end>-<sha>)
    entry = cl.parse(_GRABNITE)[0]
    assert f"L{entry.start}-{entry.end}-{entry.sha}" in ing.store.known_msg_ids(
        cl_src.source_id_for("alpha", "CHANGELOG.md"))
    # backup with matching bytes + manifest json
    backup = ing.vault.root / ".migrations" / "alpha" / "CHANGELOG.md"
    assert backup.exists() and backup.read_bytes() == p.read_bytes()
    man = json.loads((ing.vault.root / ".migrations" / "alpha" / "CHANGELOG.md.manifest.json")
                     .read_text(encoding="utf-8"))
    assert man["sha256"] == out["sha256"] and man["entries"] == 2


def test_reimport_is_idempotent(ing, tmp_path: Path) -> None:
    p = _write(tmp_path, "CHANGELOG.md", _GRABNITE)
    first = ing.import_changelog("alpha", p, root=tmp_path, apply=True)
    counts = ing.store.stats()
    second = ing.import_changelog("alpha", p, root=tmp_path, apply=True)
    assert second["applied"] == "unchanged"
    assert ing.store.stats()["records"] == counts["records"]
    assert first["record_ids"] == second["record_ids"]


def test_reindex_reproduces_the_import(ing, tmp_path: Path) -> None:
    p = _write(tmp_path, "CHANGELOG.md", _GRABNITE)
    ing.import_changelog("alpha", p, root=tmp_path, apply=True)
    before = ing.store.stats()
    ing.reindex()
    after = ing.store.stats()
    assert after["records"] == before["records"] == 2
    assert after["links"] == before["links"]
    rec = ing.store.get_record(
        ing.store.list_records(rtype="lore", projects=["alpha"], limit=1)[0].id)
    assert rec is not None and rec.status_basis == "audit"


def test_retire_requires_private_dev_and_no_consumers(ing, tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    p = _write(proj, "CHANGELOG.md", _GRABNITE)
    (proj / "README.md").write_text("see CHANGELOG.md for the voyage\n", encoding="utf-8")
    # consumer present → refused, file stays
    out = ing.import_changelog("alpha", p, root=proj, apply=True, retire=True)
    assert out["retired"] is False and "consumer" in out["retire_refused"]
    assert p.exists()
    # consumer gone → retired: file moved (backup keeps the bytes), source marks it
    (proj / "README.md").unlink()
    out2 = ing.import_changelog("alpha", p, root=proj, apply=True, retire=True)
    assert out2["retired"] is True and out2["verify"]["ok"] is True
    assert not p.exists()
    backup = ing.vault.root / ".migrations" / "alpha" / "CHANGELOG.md"
    assert backup.read_bytes().startswith(b"# Grabnite Changelog")
    from navig.recall.sources import changelog as cl_src  # noqa: PLC0415

    src = ing.store.get_source(cl_src.source_id_for("alpha", "CHANGELOG.md"))
    assert (src or {}).get("meta", {}).get("retired_at")


def test_runtime_history_is_never_a_consumer(ing, tmp_path: Path) -> None:
    """.navig/history/** is the operation log — it ECHOES command paths (including this
    file's) and must not block a retirement."""
    proj = tmp_path / "proj"
    proj.mkdir()
    p = _write(proj, "CHANGELOG.md", _GRABNITE)
    hist = proj / ".navig" / "history" / "inflight"
    hist.mkdir(parents=True)
    (hist / "op.json").write_text('{"cmd": "migrate CHANGELOG.md --retire"}', encoding="utf-8")
    out = ing.import_changelog("alpha", p, root=proj, apply=True, retire=True)
    assert out["consumers"] == []
    assert out["retired"] is True and not p.exists()


def test_release_notes_are_never_retired(ing, tmp_path: Path) -> None:
    p = _write(tmp_path, "CHANGELOG.md", _RELEASE)
    out = ing.import_changelog("alpha", p, root=tmp_path, apply=True, retire=True)
    assert out["applied"] is True  # migrated…
    assert out["retired"] is False and "release_notes" in out["retire_refused"]
    assert p.exists()  # …and the release documentation stays


def test_retire_without_a_root_is_refused(ing, tmp_path: Path) -> None:
    p = _write(tmp_path, "CHANGELOG.md", _GRABNITE)
    out = ing.import_changelog("alpha", p, root=None, apply=True, retire=True)
    assert out["retired"] is False and "root" in out["retire_refused"]
    assert p.exists()


def test_unparsable_file_never_touches_anything(ing, tmp_path: Path) -> None:
    p = _write(tmp_path, "CHANGELOG.md", "prose without a single header\n")
    for kwargs in ({"apply": False}, {"apply": True, "retire": True}):
        with pytest.raises(ValueError, match="no recognized entry headers"):
            ing.import_changelog("alpha", p, root=tmp_path, **kwargs)
    assert p.exists()
    assert ing.store.stats()["records"] == 0
    assert not (ing.vault.root / ".migrations").exists()
