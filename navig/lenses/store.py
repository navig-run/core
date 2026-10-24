"""The Lens store — ``<config_dir>/lenses/``.

Layout::

    lenses/
      index.json                     # per-Lens state: enabled · origin · install URL · @require hashes
      <id>/lens.user.js              # the source, byte-for-byte as installed (export = this file)
      <id>/pending.user.js           # a staged change awaiting review (agent edit or update)
      <id>/require/<sha256>.js       # @require bodies, fetched once at save time

Core owns the store; every host (NAVIG OS, ``navig cdp`` browsers, the Chrome extension)
reads the compiled set from here (in-process or via ``/api/deck/lenses``).

Safety rules this module exists to keep:

* A failed read of ``index.json`` is an ERROR on every mutating path, never ``{}`` — a
  transient lock must not turn into a save that forgets every Lens
  (:func:`navig.core.json_io.load_json_for_update`).
* Code an agent wrote never runs until a person approves it. A NEW Lens saved with
  ``origin="agent"`` is stored disabled and marked ``review: "proposed"``; turning it on is
  the approval. An agent's edit of a Lens a person already approved is STAGED beside it
  (``pending.user.js``) — the approved copy keeps running, unchanged, until the edit is
  approved. (``origin`` is the honest caller's label, not an auth boundary: every deck
  caller is already authenticated with the deck key.)
* An update fetched from a Lens's install link is staged the same way — never applied
  unseen (see :mod:`navig.lenses.review`).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import shutil
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.core.json_io import atomic_write_json, load_json_for_update, load_json_safe
from navig.core.yaml_io import atomic_write_text
from navig.lenses.compile import (
    CompiledLens,
    bundle_etag,
    check_source_syntax,
    compile_lens,
)
from navig.lenses.fetch import fetch_text
from navig.lenses.manifest import LensFormatError, LensManifest, parse_manifest

logger = logging.getLogger(__name__)

ORIGINS = ("user", "agent", "url", "bay")
_INDEX_DEFAULT: dict[str, Any] = {"version": 1, "lenses": {}}

# index.json is read-modify-written by the daemon's routes, the CLI and the agent, in
# different threads and processes. One writer at a time, or two saves each write back the
# index they read and one Lens silently disappears from it. Re-entrant per thread, because
# save() stages an agent's edit through stage().
_THREAD_LOCK = threading.RLock()
_HELD = threading.local()


@contextmanager
def _index_lock(index_path: Path) -> Iterator[None]:
    depth = getattr(_HELD, "depth", 0)
    if depth:
        _HELD.depth = depth + 1
        try:
            yield
        finally:
            _HELD.depth -= 1
        return
    with _THREAD_LOCK:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(index_path.with_name(index_path.name + ".lock"), "a+", encoding="utf-8") as fh:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)  # retries ~10 s, then OSError
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            _HELD.depth = 1
            try:
                yield
            finally:
                _HELD.depth = 0
                try:
                    if sys.platform == "win32":
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError as exc:  # closing the handle releases it anyway
                    logger.debug("lens index unlock: %s", exc)


def lenses_dir() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "lenses"


def _now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_proposed(entry: dict[str, Any]) -> bool:
    """An agent's Lens nobody has approved yet.

    ``review`` was added after Lenses shipped: an agent's Lens saved before it carries no
    key at all — ``origin: agent``, switched off — and is still a proposal. Approval writes
    ``review: "approved"`` rather than deleting the key, so a Lens a person approved and
    later switched off is never mistaken for a fresh proposal.
    """
    review = entry.get("review")
    if review is not None:
        return review == "proposed"
    return entry.get("origin") == "agent" and not entry.get("enabled")


@dataclass
class LensRecord:
    id: str
    enabled: bool
    origin: str
    source: str = ""
    manifest: LensManifest | None = None
    install_url: str | None = None
    requires: list[dict[str, str]] = field(default_factory=list)
    syntax: str = "unchecked"
    created_at: str = ""
    updated_at: str = ""
    error: str | None = None
    review: str | None = None  # "proposed" — an agent's new Lens, not yet approved
    pending: dict[str, Any] | None = None  # a staged change: {kind, version, sha256, at, url}

    @property
    def needs_review(self) -> bool:
        return self.review == "proposed" or self.pending is not None

    @property
    def name(self) -> str:
        return self.manifest.name if self.manifest else self.id

    def to_dict(self, *, with_source: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "origin": self.origin,
            "install_url": self.install_url,
            "requires": list(self.requires),
            "syntax": self.syntax,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "manifest": self.manifest.to_dict() if self.manifest else None,
            "review": self.review,
            "pending": dict(self.pending) if self.pending else None,
            "needs_review": self.needs_review,
        }
        if with_source:
            out["source"] = self.source
        return out


@dataclass
class CompiledSet:
    etag: str
    lenses: list[CompiledLens]
    skipped: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "etag": self.etag,
            "lenses": [lens.to_dict() for lens in self.lenses],
            "skipped": list(self.skipped),
        }


class LensStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else lenses_dir()

    # ── paths ───────────────────────────────────────────────────────────────────────
    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def _lens_dir(self, lens_id: str) -> Path:
        # ids are validated by the manifest parser ([a-z0-9._-]); re-check containment
        # anyway — this path is joined from API input on get/remove.
        target = (self.root / lens_id).resolve()
        if target.parent != self.root.resolve() or lens_id in ("", ".", ".."):
            raise ValueError(f"invalid lens id {lens_id!r}")
        return target

    def _source_path(self, lens_id: str) -> Path:
        return self._lens_dir(lens_id) / "lens.user.js"

    def _require_path(self, lens_id: str, digest: str) -> Path:
        return self._lens_dir(lens_id) / "require" / f"{digest}.js"

    def _pending_path(self, lens_id: str) -> Path:
        return self._lens_dir(lens_id) / "pending.user.js"

    # ── reads ───────────────────────────────────────────────────────────────────────
    def _index_entries(self) -> dict[str, dict[str, Any]]:
        data = load_json_safe(self.index_path, default=_INDEX_DEFAULT)
        entries = data.get("lenses") if isinstance(data, dict) else None
        return entries if isinstance(entries, dict) else {}

    def _record(self, lens_id: str, entry: dict[str, Any]) -> LensRecord:
        rec = LensRecord(
            id=lens_id,
            enabled=bool(entry.get("enabled")),
            origin=str(entry.get("origin") or "user"),
            install_url=entry.get("install_url"),
            requires=list(entry.get("requires") or []),
            syntax=str(entry.get("syntax") or "unchecked"),
            created_at=str(entry.get("created_at") or ""),
            updated_at=str(entry.get("updated_at") or ""),
            review="proposed" if _is_proposed(entry) else None,
            pending=entry.get("pending") if isinstance(entry.get("pending"), dict) else None,
        )
        try:
            rec.source = self._source_path(lens_id).read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            rec.error = f"source unreadable: {exc}"
            return rec
        try:
            rec.manifest = parse_manifest(rec.source)
        except LensFormatError as exc:
            rec.error = str(exc)
        return rec

    def records(self) -> list[LensRecord]:
        records = [self._record(i, e) for i, e in self._index_entries().items()]
        return sorted(records, key=lambda r: r.name.lower())

    def get(self, lens_id: str) -> LensRecord | None:
        entry = self._index_entries().get(lens_id)
        return self._record(lens_id, entry) if entry is not None else None

    def _require_bodies(self, rec: LensRecord) -> list[str]:
        bodies: list[str] = []
        for req in rec.requires:
            path = self._require_path(rec.id, str(req.get("sha256", "")))
            bodies.append(path.read_text(encoding="utf-8"))
        return bodies

    # ── writes ──────────────────────────────────────────────────────────────────────
    def _load_for_update(self) -> dict[str, Any]:
        data = load_json_for_update(self.index_path, default=_INDEX_DEFAULT)
        if not isinstance(data.get("lenses"), dict):
            data["lenses"] = {}
        data.setdefault("version", 1)
        return data

    def preview(self, source: str) -> LensManifest:
        """Parse without saving (install-from-URL preview, editor validation)."""
        return parse_manifest(source)

    def save(
        self,
        source: str,
        *,
        origin: str = "user",
        install_url: str | None = None,
        enabled: bool | None = None,
        fetch: Callable[[str], str] = fetch_text,
        _approved: bool = False,
    ) -> LensRecord:
        """Validate and store a Lens (new or update). Raises :class:`LensFormatError`.

        ``enabled=None`` keeps an existing Lens's state and enables a new one. With
        ``origin="agent"`` a new Lens is stored disabled and ``proposed``, and an edit of an
        approved Lens is staged for review instead of replacing it (:meth:`stage`).
        ``_approved`` is for :meth:`approve` only — the person's approval of that change.
        """
        if origin not in ORIGINS:
            raise ValueError(f"unknown origin {origin!r}")
        manifest = parse_manifest(source)
        self._load_for_update()  # raises before anything is fetched or written
        agent_unapproved = origin == "agent" and not _approved
        snapshot = self._index_entries().get(manifest.id) or {}
        if agent_unapproved and snapshot and not _is_proposed(snapshot):
            return self.stage(manifest.id, source, kind="agent")

        # The network (@require) and the syntax check happen OUTSIDE the index lock; the
        # index is re-read under it below. Bodies are keyed by URL, so a snapshot that is
        # stale by then can only cost a re-fetch, never a wrong body.
        cached = {r.get("url"): r.get("sha256") for r in snapshot.get("requires") or []}
        requires: list[dict[str, str]] = []
        bodies: list[str] = []
        for url in manifest.requires:
            digest = cached.get(url)
            body: str | None = None
            if digest:
                try:
                    body = self._require_path(manifest.id, digest).read_text(encoding="utf-8")
                except OSError:
                    body = None
            if body is None:
                body = fetch(url)
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            requires.append({"url": url, "sha256": str(digest)})
            bodies.append(body)

        ok, message = check_source_syntax(source, bodies)
        if ok is False:
            raise LensFormatError(f"JavaScript syntax error at {message}")

        with _index_lock(self.index_path):
            index = self._load_for_update()  # raises before anything is written
            entries: dict[str, Any] = index["lenses"]
            prev = entries.get(manifest.id) or {}
            if agent_unapproved and prev and not _is_proposed(prev):
                # approved by a person while the fetch ran: stage instead of replacing
                return self.stage(manifest.id, source, kind="agent")

            if agent_unapproved:
                on = False
            elif enabled is not None:
                on = bool(enabled)
            else:
                on = bool(prev.get("enabled", True)) if prev else True

            atomic_write_text(self._source_path(manifest.id), source)
            for req, body in zip(requires, bodies):
                path = self._require_path(manifest.id, req["sha256"])
                if not path.is_file():
                    atomic_write_text(path, body)
            now = _now()
            entries[manifest.id] = {
                "enabled": on,
                "origin": origin,
                "install_url": install_url or prev.get("install_url"),
                "requires": requires,
                "syntax": "ok" if ok else "unchecked",
                "created_at": prev.get("created_at") or now,
                "updated_at": now,
            }
            # A save keeps a staged update, the rejected hashes, and the review state (an
            # approved agent Lens that is switched off must not read as a fresh proposal).
            for key in ("pending", "rejected", "review"):
                if key in prev:
                    entries[manifest.id][key] = prev[key]
            if agent_unapproved:
                entries[manifest.id]["review"] = "proposed"
            atomic_write_json(index, self.index_path)
            self._prune_requires(manifest.id, {r["sha256"] for r in requires})
        rec = self.get(manifest.id)
        if rec is None:  # just written — only a concurrent remove can get here
            raise KeyError(manifest.id)
        return rec

    def _prune_requires(self, lens_id: str, keep: set[str]) -> None:
        folder = self._lens_dir(lens_id) / "require"
        if not folder.is_dir():
            return
        for path in folder.glob("*.js"):
            if path.stem not in keep:
                try:
                    path.unlink()
                except OSError as exc:
                    logger.debug("could not prune %s: %s", path, exc)

    def set_enabled(self, lens_id: str, on: bool) -> LensRecord:
        with _index_lock(self.index_path):
            index = self._load_for_update()
            entry = index["lenses"].get(lens_id)
            if entry is None:
                raise KeyError(lens_id)
            entry["enabled"] = bool(on)
            if on:
                entry["review"] = "approved"  # turning a proposed Lens on IS the approval
            entry["updated_at"] = _now()
            atomic_write_json(index, self.index_path)
        rec = self.get(lens_id)
        if rec is None:
            raise KeyError(lens_id)
        return rec

    def remove(self, lens_id: str) -> bool:
        with _index_lock(self.index_path):
            index = self._load_for_update()
            if lens_id not in index["lenses"]:
                return False
            folder = self._lens_dir(lens_id)
            del index["lenses"][lens_id]
            atomic_write_json(index, self.index_path)
            shutil.rmtree(folder, ignore_errors=True)
        return True

    # ── review: staged changes ──────────────────────────────────────────────────────
    def stage(
        self, lens_id: str, source: str, *, kind: str, url: str | None = None
    ) -> LensRecord:
        """Park ``source`` beside the running copy of ``lens_id`` until a person decides.

        ``kind`` is ``"agent"`` (an agent's edit) or ``"update"`` (fetched from the install
        link). The running Lens is not touched. Raises :class:`LensFormatError` for a
        source that does not parse, or that is a different Lens.
        """
        if kind not in ("agent", "update"):
            raise ValueError(f"unknown staged-change kind {kind!r}")
        manifest = parse_manifest(source)
        if manifest.id != lens_id:
            raise LensFormatError(
                f"this change is a different Lens ({manifest.id!r}, not {lens_id!r})"
            )
        ok, message = check_source_syntax(source)
        if ok is False:
            raise LensFormatError(f"JavaScript syntax error at {message}")
        with _index_lock(self.index_path):
            index = self._load_for_update()
            entry = index["lenses"].get(lens_id)
            if entry is None:
                raise KeyError(lens_id)
            atomic_write_text(self._pending_path(lens_id), source)
            entry["pending"] = {
                "kind": kind,
                "version": manifest.version,
                "sha256": _sha(source),
                "at": _now(),
                "url": url,
            }
            atomic_write_json(index, self.index_path)
        rec = self.get(lens_id)
        if rec is None:
            raise KeyError(lens_id)
        return rec

    def reviews(self) -> list[LensRecord]:
        """Every Lens waiting on a person: an agent's proposal or a staged change."""
        return [r for r in self.records() if r.needs_review]

    def review_details(self, lens_id: str) -> dict[str, Any]:
        """The diff and the power a decision would grant. Raises KeyError."""
        from navig.lenses import review

        rec = self.get(lens_id)
        if rec is None or not rec.needs_review:
            raise KeyError(lens_id)
        if rec.pending:
            proposed = self._pending_path(lens_id).read_text(encoding="utf-8")
            new = parse_manifest(proposed)
            return {
                "lens": rec.to_dict(),
                "kind": rec.pending.get("kind"),
                "current": rec.source,
                "proposed": proposed,
                "diff": review.unified_diff(rec.source, proposed),
                "changes": [c.to_dict() for c in review.changes(rec.manifest, new)],
            }
        new = rec.manifest or parse_manifest(rec.source)
        return {
            "lens": rec.to_dict(),
            "kind": "proposed",
            "current": "",
            "proposed": rec.source,
            "diff": review.unified_diff("", rec.source),
            "changes": [c.to_dict() for c in review.changes(None, new)],
        }

    def approve(
        self, lens_id: str, *, fetch: Callable[[str], str] | None = None
    ) -> LensRecord:
        """Apply the staged change (keeping the Lens's on/off state), or turn on a proposal."""
        rec = self.get(lens_id)
        if rec is None or not rec.needs_review:
            raise KeyError(lens_id)
        if rec.pending:
            source = self._pending_path(lens_id).read_text(encoding="utf-8")
            url = rec.pending.get("url") if rec.pending.get("kind") == "update" else None
            self.save(
                source,
                origin=rec.origin,
                install_url=url,
                enabled=rec.enabled,
                fetch=fetch or fetch_text,
                _approved=True,
            )
            self._clear_pending(lens_id, rejected=False)
            out = self.get(lens_id)
            if out is None:
                raise KeyError(lens_id)
            return out
        return self.set_enabled(lens_id, True)

    def reject(self, lens_id: str) -> bool:
        """Discard the staged change, or delete a proposal. Returns whether the Lens remains.

        A rejected UPDATE is remembered by hash, so the same version is not staged again.
        """
        rec = self.get(lens_id)
        if rec is None or not rec.needs_review:
            raise KeyError(lens_id)
        if rec.pending:
            self._clear_pending(lens_id, rejected=rec.pending.get("kind") == "update")
            return True
        self.remove(lens_id)
        return False

    def _clear_pending(self, lens_id: str, *, rejected: bool) -> None:
        with _index_lock(self.index_path):
            index = self._load_for_update()
            entry = index["lenses"].get(lens_id)
            if entry is None:
                return
            pending = entry.pop("pending", None) or {}
            if rejected and pending.get("sha256"):
                entry["rejected"] = [*(entry.get("rejected") or []), pending["sha256"]][-10:]
            atomic_write_json(index, self.index_path)
        try:
            self._pending_path(lens_id).unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("could not remove staged change for %s: %s", lens_id, exc)

    # ── updates from the install link ───────────────────────────────────────────────
    def last_update_check(self) -> str | None:
        data = load_json_safe(self.index_path, default=_INDEX_DEFAULT)
        value = data.get("last_update_check") if isinstance(data, dict) else None
        return value if isinstance(value, str) else None

    def check_updates(
        self, *, fetch: Callable[[str], str] | None = None, only: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch each Lens's install link and STAGE anything new. Applies nothing.

        One result per Lens with a link: ``staged`` · ``current`` · ``pending`` (that exact
        version is already waiting) · ``rejected`` (a person said no to it) · ``older`` (a
        lower ``@version`` — never offered) · ``error``.
        """
        from navig.lenses import review
        from navig.lenses.fetch import LensFetchError

        fetch = fetch or fetch_text  # resolved per call, so the fetcher can be swapped
        results: list[dict[str, Any]] = []
        for rec in self.records():
            if not rec.install_url or (only and rec.id != only):
                continue
            row: dict[str, Any] = {"id": rec.id, "name": rec.name, "url": rec.install_url}
            try:
                source = fetch(rec.install_url)
                digest = _sha(source)
                entry = self._index_entries().get(rec.id) or {}
                if digest == _sha(rec.source):
                    row["status"] = "current"
                elif rec.pending and rec.pending.get("sha256") == digest:
                    row["status"] = "pending"
                elif digest in (entry.get("rejected") or []):
                    row["status"] = "rejected"
                else:
                    new = parse_manifest(source)
                    if rec.manifest and review.is_older(new.version, rec.manifest.version):
                        row["status"] = "older"
                    else:
                        self.stage(rec.id, source, kind="update", url=rec.install_url)
                        row["status"] = "staged"
                        row["version"] = new.version
            except (LensFetchError, LensFormatError, KeyError, ValueError) as exc:
                row["status"], row["error"] = "error", str(exc)
            results.append(row)
        if only is None:
            with _index_lock(self.index_path):
                index = self._load_for_update()
                index["last_update_check"] = _now()
                atomic_write_json(index, self.index_path)
        return results

    # ── hosts ───────────────────────────────────────────────────────────────────────
    def compiled(self, host: str, *, caps: list[str] | None = None) -> CompiledSet:
        """Every enabled, entitled, valid Lens compiled for ``host``.

        ``caps`` = the license capabilities; ``None`` reads the live license. When the
        license can't be read, a Lens that declares ``@navig-capability`` is skipped
        (premium fails closed); free Lenses are never affected.
        """
        if caps is None:
            try:
                from navig.license.entitlement import live_caps_and_rank

                caps = live_caps_and_rank()[0]
            except Exception as exc:  # noqa: BLE001 — licensing must not hide free Lenses
                logger.debug("license unavailable for lens entitlement: %s", exc)
                caps = None
        out: list[CompiledLens] = []
        skipped: list[dict[str, str]] = []
        for rec in self.records():
            if not rec.enabled:
                continue
            if rec.error or rec.manifest is None:
                skipped.append({"id": rec.id, "reason": rec.error or "invalid"})
                continue
            cap = rec.manifest.capability
            if cap and (caps is None or cap not in caps):
                skipped.append({"id": rec.id, "reason": f"locked — needs {cap}"})
                continue
            if not rec.manifest.runs_anywhere:
                skipped.append({"id": rec.id, "reason": "no @match — runs nowhere"})
                continue
            try:
                bodies = self._require_bodies(rec)
            except (OSError, ValueError) as exc:
                skipped.append({"id": rec.id, "reason": f"@require cache missing: {exc}"})
                continue
            out.append(compile_lens(rec.manifest, rec.source, host=host, requires=bodies))
        return CompiledSet(etag=bundle_etag(out), lenses=out, skipped=skipped)
