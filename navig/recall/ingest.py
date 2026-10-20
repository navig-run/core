"""Incremental, idempotent ingestion: discover → read from cursor → redact → archive → index
→ Pass 0 records → vault + events.

Integrity rules:

* A source is re-read only when its size or mtime changed. A shrink, a changed head (first
  64 KB) or a changed tail before the cursor means the file was rewritten: it is re-read from
  the start and turns are de-duplicated by message id. Nothing already archived or derived
  is ever deleted.
* New turns are archived **before** they are indexed, so the index can always be rebuilt
  from the archive (``reindex``). A failure on one source is recorded on that source and
  the run continues.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.recall import pass0
from navig.recall.archive import Archive
from navig.recall.model import Evidence, Record, Turn
from navig.recall.projects import ProjectResolver
from navig.recall.redact import redact_secrets
from navig.recall.session_links import DERIVED_RELS, LINKS_VERSION, links_from_meta
from navig.recall.sources import ADAPTERS
from navig.recall.sources.base import SourceRef, file_state, head_len, head_sha, tail_sha
from navig.recall.store import RecallStore, get_recall_store
from navig.recall.vault import Vault, make_event
from navig.store.base import _utcnow

logger = logging.getLogger(__name__)
Progress = Callable[[int, int, str], None]


@dataclass
class IngestReport:
    sources_seen: int = 0
    sources_read: int = 0
    unchanged: int = 0
    rewritten: int = 0
    missing: int = 0
    forgotten: int = 0
    turns_added: int = 0
    records_created: int = 0
    records_updated: int = 0
    bad_lines: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["errors"] = [{"source": s, "error": e} for s, e in self.errors]
        return d


class Ingestor:
    def __init__(self, store: RecallStore | None = None, archive: Archive | None = None,
                 vault: Vault | None = None, resolver: ProjectResolver | None = None):
        self.store = store or get_recall_store()
        self.archive = archive or Archive()
        self.vault = vault or Vault()
        self.resolver = resolver or ProjectResolver()

    # ── Entry point ───────────────────────────────────────────

    def run(self, adapters: Iterable[str] = ADAPTERS, *, project: str | None = None, limit: int | None = None,
            progress: Progress | None = None, claude_homes: list[Path] | None = None,
            codex_home: Path | None = None) -> IngestReport:
        rep = IngestReport()
        adapters = list(adapters)
        if "session_audit" in adapters:
            self._session_audits(rep, project, claude_homes, progress)
        if "chats" in adapters:
            try:
                self._chats(rep, project, progress)
            except Exception as exc:  # noqa: BLE001 — the chat library must not stop the run
                logger.warning("recall: chat library sources failed: %s", type(exc).__name__)
                rep.errors.append(("chats", f"{type(exc).__name__}: {exc}"[:300]))
        if "capture" in adapters:  # Navig Capture v1 notes from the Dock (S2)
            from navig.recall.sources import capture  # noqa: PLC0415

            capture.ingest_all(self, rep, project, progress)
        if "notes" in adapters:
            try:
                self._notes(rep, project, progress)
            except Exception as exc:  # noqa: BLE001 — web notes must not stop the run
                logger.warning("recall: web note sources failed: %s", type(exc).__name__)
                rep.errors.append(("notes", f"{type(exc).__name__}: {exc}"[:300]))
        jobs: list[tuple[SourceRef, Callable[..., Any]]] = []
        if "claude_code" in adapters:
            from navig.recall.sources import claude_code  # noqa: PLC0415

            jobs += [(r, claude_code.parse) for r in claude_code.discover(claude_homes)]
        if "codex" in adapters:
            from navig.recall.sources import codex  # noqa: PLC0415

            jobs += [(r, codex.parse) for r in codex.discover(codex_home)]
        if limit:
            jobs = jobs[:limit]
        for i, (ref, parser) in enumerate(jobs, 1):
            if progress:
                progress(i, len(jobs), ref.source_id)
            rep.sources_seen += 1
            try:
                self._jsonl_source(ref, parser, rep, project)
            except Exception as exc:  # noqa: BLE001 — one bad source must not stop the run
                logger.warning("recall: source %s failed: %s", ref.source_id, type(exc).__name__)
                rep.errors.append((ref.source_id, f"{type(exc).__name__}: {exc}"[:300]))
                self._mark_error(ref, exc)
        return rep

    # ── Transcripts ───────────────────────────────────────────

    def _jsonl_source(self, ref: SourceRef, parser: Callable[..., Any], rep: IngestReport,
                      only_project: str | None) -> None:
        path = Path(ref.path)
        src = self.store.get_source(ref.source_id)
        try:
            state = file_state(path)
        except OSError:
            if src and src["status"] != "source_missing":
                self.store.upsert_source({**src, "status": "source_missing", "updated_at": _utcnow()})
            rep.missing += 1
            return
        prior_meta = (src or {}).get("meta") or {}
        # Sessions read before session links existed are re-read once from the start; turns are
        # de-duplicated by message id, so only the links are new.
        relink = bool(src) and ref.adapter == "claude_code" and int(prior_meta.get("links_v") or 0) < LINKS_VERSION
        if (src and src["size"] == state.size and src["mtime"] == state.mtime and not src["error"]
                and not relink):
            rep.unchanged += 1
            return
        cursor = 0 if relink else (int(src["cursor"]) if src else 0)
        pending = {} if relink else dict(prior_meta.get("pending_tools") or {})
        status = "ok"
        if src and not relink and (state.size < cursor
                    or head_sha(path, int(prior_meta.get("head_len") or 0)) != src["head_sha"]
                    or tail_sha(path, cursor) != src["tail_sha"]):
            cursor, pending, status = 0, {}, "rewritten"
            rep.rewritten += 1
        res = parser(path, cursor, pending)
        cwd = res.cwd or (src or {}).get("cwd", "")
        proj = self.resolver.resolve(cwd).id
        if only_project and proj != only_project:
            return
        rep.sources_read += 1
        rep.bad_lines += res.bad_lines
        known = self.store.known_msg_ids(ref.source_id) if src else set()
        new = [_redacted(t) for t in res.turns if t.msg_id not in known]
        link_meta = {k: res.meta.pop(k) for k in ("touched", "planned", "commits") if k in res.meta}
        meta = dict((src or {}).get("meta") or {})
        meta.update({k: v for k, v in res.meta.items() if v not in ("", None)})
        meta.update(ref.meta)
        meta["bad_lines"] = int(meta.get("bad_lines", 0)) + res.bad_lines
        meta["head_len"] = head_len(res.end_offset)
        row = {
            "source_id": ref.source_id, "adapter": ref.adapter, "path": str(path), "project": proj,
            "title": res.title or ref.meta.get("title") or (src or {}).get("title", ""), "cwd": cwd,
            "size": state.size, "mtime": state.mtime, "head_sha": head_sha(path, meta["head_len"]),
            "tail_sha": tail_sha(path, res.end_offset), "cursor": res.end_offset, "status": status, "error": "",
            "first_ts": (src or {}).get("first_ts") or res.first_ts, "last_ts": res.last_ts or (src or {}).get("last_ts", ""),
            "turns": int((src or {}).get("turns", 0)) + len(new), "meta": meta, "updated_at": _utcnow(),
        }
        self.archive.append(ref.adapter, ref.source_id, new, _archivable(row))
        added = self.store.add_turns(ref.source_id, new)
        rep.turns_added += len(added)
        self.store.upsert_source(row)
        derived = links_from_meta(ref.source_id, link_meta, self.resolver)
        if relink or status == "rewritten":
            # The whole file was just read: this is the complete set of derived links, so
            # links an older extractor derived differently are retired (hand links are kept).
            self.vault.append_links(self.store.replace_derived_links(ref.source_id, derived, DERIVED_RELS))
        elif derived:
            self.vault.append_links(self.store.add_links(derived))
        if added:
            recs = pass0.records_from_turns(ref.source_id, proj, added, plan_for_tool=self.store.plan_for_tool)
            self.commit(recs, rep)

    # ── The chat library (only what the person shared) ────────

    def forget(self, source_id: str, adapter: str) -> int:
        """Remove a withdrawn source everywhere Recall copied it: index, archive, vault."""
        paths = self.store.forget_source(source_id)
        self.archive.forget(adapter, source_id)
        for p in paths:
            self.vault.remove(p)
        return len(paths)

    def _chats(self, rep: IngestReport, only_project: str | None, progress: Progress | None) -> None:
        from navig.recall.sources import chats  # noqa: PLC0415

        refs = {r.source_id: r for r in chats.discover()}
        # Withdrawn shares first: what is no longer shared must not stay findable.
        for src in self.store.list_sources(adapter=chats.ADAPTER):
            if src["source_id"] not in refs:
                self.forget(src["source_id"], chats.ADAPTER)
                rep.forgotten += 1
        loaded: list[tuple[dict[str, Any], list[Any]]] = []
        for i, ref in enumerate(refs.values(), 1):
            if progress:
                progress(i, len(refs), ref.source_id)
            rep.sources_seen += 1
            if only_project and ref.meta["project"] != only_project:
                continue
            try:
                conv, msgs = chats.load(ref)
            except Exception as exc:  # noqa: BLE001 — one unreadable chat must not stop the rest
                rep.errors.append((ref.source_id, f"{type(exc).__name__}: {exc}"[:300]))
                continue
            loaded.append((conv, msgs))
            src = self.store.get_source(ref.source_id)
            fp = chats.fingerprint(ref)
            if src and src["head_sha"] == fp and not src["error"]:
                rep.unchanged += 1
                continue
            if src:
                # A chat changes in place (edits, another branch shown, a new level or project):
                # start this source over rather than append to a stale copy.
                self.forget(ref.source_id, chats.ADAPTER)
                rep.rewritten += 1
            new = [_redacted(t) for t in chats.turns(ref, conv, msgs)]
            row = {
                "source_id": ref.source_id, "adapter": chats.ADAPTER, "path": ref.path,
                "project": ref.meta["project"], "title": ref.meta["title"], "cwd": "", "size": len(msgs),
                "mtime": 0.0, "head_sha": fp, "tail_sha": "", "cursor": len(msgs), "status": "ok", "error": "",
                "first_ts": next((t.ts for t in new if t.ts), ""), "last_ts": next((t.ts for t in reversed(new) if t.ts), ""),
                "turns": len(new), "meta": dict(ref.meta), "updated_at": _utcnow(),
            }
            self.archive.append(chats.ADAPTER, ref.source_id, new, _archivable(row))
            rep.turns_added += len(self.store.add_turns(ref.source_id, new))
            self.store.upsert_source(row)
            rep.sources_read += 1
        if not loaded:
            return
        # Records for every shared chat, resolved together (a later chat supersedes an earlier one).
        recs, links = chats.records(chats.extract_items(loaded), refs)
        self.commit(recs, rep)
        fresh = self.store.add_links(links)
        self.vault.append_links(fresh)

    def _notes(self, rep: IngestReport, only_project: str | None, progress: Progress | None) -> None:
        from navig.recall.sources import notes  # noqa: PLC0415

        if not notes.available():
            return
        refs = {r.source_id: r for r in notes.discover()}
        # Deleted (or no longer synced) notes first: what is gone must not stay findable.
        for src in self.store.list_sources(adapter=notes.ADAPTER):
            if src["source_id"] not in refs:
                self.forget(src["source_id"], notes.ADAPTER)
                rep.forgotten += 1
        recs = []
        for i, ref in enumerate(refs.values(), 1):
            if progress:
                progress(i, len(refs), ref.source_id)
            rep.sources_seen += 1
            if only_project and ref.meta["project"] != only_project:
                continue
            src = self.store.get_source(ref.source_id)
            fp = notes.fingerprint(ref)
            if src and src["head_sha"] == fp and not src["error"]:
                rep.unchanged += 1
                continue
            try:
                note = notes.load(ref)
            except Exception as exc:  # noqa: BLE001 — one unreadable note must not stop the rest
                rep.errors.append((ref.source_id, f"{type(exc).__name__}: {exc}"[:300]))
                continue
            if src:
                self.forget(ref.source_id, notes.ADAPTER)  # a note is edited in place: start it over
                rep.rewritten += 1
            new = [_redacted(t) for t in notes.turns(ref, note)]
            row = {
                "source_id": ref.source_id, "adapter": notes.ADAPTER, "path": ref.path,
                "project": ref.meta["project"], "title": ref.meta["title"], "cwd": "", "size": len(new),
                "mtime": 0.0, "head_sha": fp, "tail_sha": "", "cursor": len(new), "status": "ok", "error": "",
                "first_ts": ref.meta["updated_at"], "last_ts": ref.meta["updated_at"],
                "turns": len(new), "meta": dict(ref.meta), "updated_at": _utcnow(),
            }
            self.archive.append(notes.ADAPTER, ref.source_id, new, _archivable(row))
            rep.turns_added += len(self.store.add_turns(ref.source_id, new))
            self.store.upsert_source(row)
            rep.sources_read += 1
            recs += notes.records(ref, note)
        if recs:
            self.commit(recs, rep)

    def _mark_error(self, ref: SourceRef, exc: Exception) -> None:
        try:
            src = self.store.get_source(ref.source_id) or {"source_id": ref.source_id, "adapter": ref.adapter,
                                                           "path": ref.path}
            src.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300], "updated_at": _utcnow()})
            self.store.upsert_source(src)
        except Exception:  # noqa: BLE001
            logger.debug("recall: could not record the error for %s", ref.source_id)

    # ── Session audits ────────────────────────────────────────

    def _session_audits(self, rep: IngestReport, only_project: str | None, homes: list[Path] | None,
                        progress: Progress | None) -> None:
        from navig.recall.sources import session_audit as sa  # noqa: PLC0415

        for root in sa.audit_roots(homes):
            proj = sa.project_for_root(root, self.resolver)
            if only_project and proj != only_project:
                continue
            refs = list(sa.discover([root]))
            for i, ref in enumerate(refs, 1):
                if progress:
                    progress(i, len(refs), ref.source_id)
                rep.sources_seen += 1
                try:
                    self._doc_source(ref, proj, rep)
                except Exception as exc:  # noqa: BLE001
                    rep.errors.append((ref.source_id, f"{type(exc).__name__}: {exc}"[:300]))
                    self._mark_error(ref, exc)
            try:
                self._audit_records(root, proj, rep)
            except Exception as exc:  # noqa: BLE001
                rep.errors.append((f"auditrec:{root.name}", f"{type(exc).__name__}: {exc}"[:300]))

    def _doc_source(self, ref: SourceRef, proj: str, rep: IngestReport) -> None:
        from navig.recall.sources import session_audit as sa  # noqa: PLC0415

        path = Path(ref.path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        src = self.store.get_source(ref.source_id)
        if src and src["head_sha"] == digest:
            rep.unchanged += 1
            return
        res = sa.parse_doc(path)
        known = self.store.known_msg_ids(ref.source_id) if src else set()
        new = [_redacted(t) for t in res.turns if t.msg_id not in known]
        st = path.stat()
        row = {
            "source_id": ref.source_id, "adapter": ref.adapter, "path": str(path), "project": proj,
            "title": res.title, "cwd": "", "size": st.st_size, "mtime": st.st_mtime, "head_sha": digest,
            "tail_sha": "", "cursor": res.end_offset, "status": "ok", "error": "",
            "first_ts": "", "last_ts": "", "turns": int((src or {}).get("turns", 0)) + len(new),
            "meta": ref.meta, "updated_at": _utcnow(),
        }
        self.archive.append(ref.adapter, ref.source_id, new, _archivable(row))
        rep.turns_added += len(self.store.add_turns(ref.source_id, new))
        self.store.upsert_source(row)
        rep.sources_read += 1

    # ── One-off legacy changelog migration (the `migrate` command's engine) ──────

    def import_changelog(self, project: str, path: str | Path, *, root: str | Path | None = None,
                         apply: bool = False, retire: bool = False) -> dict[str, Any]:
        """Parse → dry-run manifest, or backup + import + verify + (optional) retire.

        Dry-run is the default: with ``apply=False`` nothing is written anywhere.
        Raises ``ValueError`` when the file has content but no recognizable entries —
        an unparsable source is never migrated and never retired. ``retire`` additionally
        requires ``root`` (the consumer scan), a ``private_dev`` classification and a
        clean scan; release notes are indexed but their file stays.
        """
        import json  # noqa: PLC0415
        import shutil  # noqa: PLC0415

        from navig.core.json_io import atomic_write_json  # noqa: PLC0415
        from navig.recall import changelog as cl  # noqa: PLC0415
        from navig.recall.model import canonical_key as _ck  # noqa: PLC0415
        from navig.recall.sources import changelog as cl_src  # noqa: PLC0415

        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(str(p))
        raw = p.read_bytes()
        digest16 = hashlib.sha256(raw).hexdigest()[:16]
        entries = cl.parse(raw.decode("utf-8", errors="replace"))  # ValueError → abort
        kind = cl.classify(entries)
        sid = cl_src.source_id_for(project, p.name)
        consumers = cl_src.find_consumers(Path(root), p.name) if root else []
        manifest: dict[str, Any] = {
            "project": project, "file": str(p), "filename": p.name, "sha256": digest16,
            "classification": kind, "entries": len(entries),
            "record_ids": [cl.record_id(project, p.name, e) for e in entries],
            "consumers": consumers, "applied": False, "retired": False,
            "retire_refused": "", "verify": None,
        }
        if not apply:
            return manifest  # dry-run: zero writes

        # 1. backup + import manifest (before the original is ever touched)
        bdir = self.vault.root / ".migrations" / project
        bdir.mkdir(parents=True, exist_ok=True)
        backup = bdir / p.name
        if backup.exists() and hashlib.sha256(backup.read_bytes()).hexdigest()[:16] != digest16:
            backup.rename(bdir / f"{p.name}.{hashlib.sha256(backup.read_bytes()).hexdigest()[:16]}")
        if not backup.exists():
            shutil.copy2(p, backup)
        manifest["backup"] = str(backup)
        atomic_write_json({**manifest, "imported_at": _utcnow()},
                          bdir / f"{p.name}.manifest.json")

        # 2. source + turns + records (idempotent: same digest → already-imported short-circuit)
        existing = self.store.get_source(sid)
        already = existing is not None and existing.get("head_sha") == digest16
        if not already:
            turns = [_redacted(Turn(
                msg_id=f"L{e.start}-{e.end}-{e.sha}", speaker="imported_doc", kind="doc",
                ts=e.date, text=e.text,
                meta={"title": e.title, "entry_sha": e.sha, "file_sha256": digest16},
            )) for e in entries]
            row = cl_src.source_row(project, p, digest16, len(entries),
                                    {"kind": kind, "sha256": digest16})
            row["updated_at"] = _utcnow()
            self.archive.append(cl_src.ADAPTER, sid, turns, _archivable(row))
            self.store.add_turns(sid, turns)
            self.store.upsert_source(row)
            for e in entries:
                rid = cl.record_id(project, p.name, e)
                body_lines = e.text.splitlines()[1:]
                summary = next((ln.strip() for ln in body_lines if ln.strip() and not ln.startswith("#")),
                               "")[:240]
                rec = Record(
                    id=rid, type="lore", title=e.title[:200], summary=summary, body=e.text,
                    project=project, status="confirmed", status_basis="audit", endorsement="none",
                    impl="unknown", confidence=0.8, canonical_key=_ck(e.title), tags=["changelog"],
                    attrs={"source_file": p.name, "entry_lines": [e.start, e.end],
                           "entry_sha": e.sha, "entry_date": e.date},
                    created=e.date or _utcnow(), updated=e.date or _utcnow(),
                    evidence=[Evidence(source_id=sid, msg_id=f"L{e.start}-{e.end}-{e.sha}",
                                       speaker="imported_doc", excerpt=e.text[:300],
                                       locator=json.dumps({"lines": [e.start, e.end],
                                                           "file_sha256": digest16}))],
                )
                self.store.upsert_record(rec, path=str(self.vault.write(
                    rec, existing=self.store.record_path(rid))))
            manifest["applied"] = True
        else:
            manifest["applied"] = "unchanged"

        # 3. verify against the ORIGINAL before anything may be retired
        reread = p.read_bytes()
        re_entries = cl.parse(reread.decode("utf-8", errors="replace"))
        ok_digest = hashlib.sha256(reread).hexdigest()[:16] == digest16
        ok_ids = [cl.record_id(project, p.name, e) for e in re_entries] == manifest["record_ids"]
        known = self.store.known_msg_ids(sid)
        ok_turns = all(f"L{e.start}-{e.end}-{e.sha}" in known for e in re_entries)
        if not (ok_digest and ok_ids and ok_turns):
            raise RuntimeError(f"changelog verification failed for {p} — nothing was retired")
        manifest["verify"] = {"ok": True, "entries": len(re_entries), "digest": ok_digest,
                              "records": ok_ids, "turns": ok_turns}

        # 4. retire: private dev history only, only with a root to scan, only with no consumers
        if retire:
            if kind != "private_dev":
                manifest["retire_refused"] = "release_notes — release documentation is kept"
            elif not root:
                manifest["retire_refused"] = "no project root given — consumer scan impossible"
            elif consumers:
                manifest["retire_refused"] = f"{len(consumers)} consumer(s) still reference it"
            else:
                p.unlink()  # verified backup with matching sha sits in bdir/
                src_row = dict(self.store.get_source(sid) or {})
                src_row.update({"meta": {**(src_row.get("meta") or {}),
                                         "retired_at": _utcnow(), "backup": str(backup)}})
                self.store.upsert_source(src_row)
                manifest["retired"] = True
        return manifest

    def _audit_records(self, root: Path, proj: str, rep: IngestReport) -> None:
        from navig.recall.sources import session_audit as sa  # noqa: PLC0415

        fp_parts = []
        for p in sorted(root.glob("*.md")) + sorted(root.glob("*.json")) + sorted(root.glob("registry/*.json")):
            try:
                st = p.stat()
                fp_parts.append(f"{p.name}:{st.st_size}:{st.st_mtime}")
            except OSError:
                continue
        fingerprint = hashlib.sha256("|".join(fp_parts).encode()).hexdigest()[:16]
        sid = f"auditrec:{root.name}"
        src = self.store.get_source(sid)
        if src and src["head_sha"] == fingerprint:
            return
        records, links, events = sa.build_records(root, proj)
        self.commit(records, rep, extra_events=events)
        fresh = self.store.add_links(links)
        self.vault.append_links(fresh)
        row = {
            "source_id": sid, "adapter": "session_audit", "path": str(root), "project": proj,
            "title": f"Session audit records ({root.name})", "head_sha": fingerprint, "status": "ok", "error": "",
            "turns": 0, "meta": {"records": len(records), "links": len(links)}, "updated_at": _utcnow(),
        }
        self.archive.append("session_audit", sid, [], _archivable(row))  # so reindex restores it
        self.store.upsert_source(row)

    # ── Records ───────────────────────────────────────────────

    def commit(self, records: list[Record], rep: IngestReport | None = None,
               extra_events: list[dict[str, Any]] | None = None) -> None:
        """Merge each record into the bank (vault first, then index) and log its events."""
        events: list[dict[str, Any]] = list(extra_events or [])
        for rec in records:
            existing = self.store.get_record(rec.id)
            if existing is None:
                if not rec.title:
                    continue  # a verdict for a plan this bank never saw
                merged, evs = rec, [("created", {"type": rec.type, "status": rec.status,
                                                 "basis": rec.status_basis})]
                if rep:
                    rep.records_created += 1
            else:
                merged, evs = pass0.merge(existing, rec)
                if not evs:
                    continue
                if rep:
                    rep.records_updated += 1
            path = self.vault.write(merged, existing=self.store.record_path(rec.id))
            self.store.upsert_record(merged, path=str(path))
            ts = merged.updated or _utcnow()
            events += [make_event(merged.id, kind, ts, data) for kind, data in evs]
        fresh = self.store.add_events(events)
        self.vault.append_events(fresh)

    # ── Rebuild ───────────────────────────────────────────────

    def reindex(self, progress: Progress | None = None) -> dict[str, int]:
        """Drop the index and rebuild it from the archive + vault. Nothing else is touched."""
        self.store.reset()
        counts = {"sources": 0, "turns": 0, "records": 0, "events": 0, "links": 0}
        manifests = list(self.archive.manifests())
        for i, man in enumerate(manifests, 1):
            src = dict(man.get("source") or {})
            if not src.get("source_id"):
                continue
            if progress:
                progress(i, len(manifests), src["source_id"])
            turns = list(self.archive.read_turns(src.get("adapter", ""), src["source_id"]))
            counts["turns"] += len(self.store.add_turns(src["source_id"], turns))
            self.store.upsert_source(src)
            counts["sources"] += 1
        for path, rec in self.vault.iter_records():
            self.store.upsert_record(rec, path=str(path))
            counts["records"] += 1
        counts["events"] = len(self.store.add_events(self.vault.iter_events()))
        counts["links"] = self.store.apply_links(self.vault.iter_links())
        counts["extract_runs"] = self._restore_extract_runs()
        return counts

    def _restore_extract_runs(self) -> int:
        """Replay the vault's extraction ledger so a lost index never re-sends (and re-pays for)
        windows that were already extracted. The last line per key wins."""
        import json  # noqa: PLC0415

        path = self.vault.root / ".extract_runs.jsonl"
        if not path.exists():
            return 0
        runs: dict[str, dict[str, Any]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                run = json.loads(line)
            except ValueError:
                continue
            if isinstance(run, dict) and run.get("key"):
                runs[run["key"]] = run
        for run in runs.values():
            self.store.record_run(run)
        return len(runs)


def _redacted(t: Turn) -> Turn:
    t.text = redact_secrets(t.text)
    return t


def _archivable(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "meta_json"}
