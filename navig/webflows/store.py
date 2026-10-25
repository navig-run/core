"""The Webflow store — ``<config_dir>/webflows/``.

Layout::

    webflows/
      <id>/r1.json        # every save is a new, immutable revision
      <id>/r2.json        # the highest number is the current one

There is deliberately no index file. A shared index is read-modify-written by the daemon,
the CLI and agents at once, and every store with one needs a cross-process lock and the
failed-read-never-writes discipline (see :mod:`navig.lenses.store`). Here a save only ever
*creates* a file: the revision is written to a temp file and hard-linked into place, which
fails when another writer took that number first — the loser retries with the next one.
Nothing is ever read and written back, so an unreadable file cannot turn into data loss.

Old revisions stay on disk so a later repair can be rolled back (Phase D); ``delete``
removes a flow and all its revisions.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navig.core.json_io import load_json_safe
from navig.webflows.schema import WebflowError, dumps, require_valid, slugify

logger = logging.getLogger(__name__)

_REV = re.compile(r"^r([0-9]{1,6})\.json$")
_ID = re.compile(r"^wf_[a-z0-9][a-z0-9-]{0,59}$")


def webflows_dir() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "webflows"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class WebflowSummary:
    id: str
    name: str
    revision: int
    revisions: int
    steps: int
    origins: tuple[str, ...]
    params: tuple[str, ...]
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "revision": self.revision,
            "revisions": self.revisions, "steps": self.steps, "origins": list(self.origins),
            "params": list(self.params), "updated_at": self.updated_at,
        }


class WebflowStore:
    def __init__(self, root: Path | None = None):
        self.root = root or webflows_dir()

    # ── paths ──────────────────────────────────────────────────────────────────

    def _dir(self, wf_id: str) -> Path:
        if not _ID.match(str(wf_id)):
            raise WebflowError([f"{wf_id!r} is not a webflow id"])
        return self.root / wf_id

    def revisions(self, wf_id: str) -> list[int]:
        d = self._dir(wf_id)
        if not d.is_dir():
            return []
        return sorted(int(m.group(1)) for p in d.iterdir() if (m := _REV.match(p.name)))

    # ── reads ──────────────────────────────────────────────────────────────────

    def get(self, wf_id: str, revision: int | None = None) -> dict[str, Any] | None:
        revs = self.revisions(wf_id)
        if not revs:
            return None
        rev = revs[-1] if revision is None else revision
        if rev not in revs:
            return None
        raw = load_json_safe(self._dir(wf_id) / f"r{rev}.json", default={})
        if not raw:
            logger.warning("webflow %s r%s is unreadable", wf_id, rev)
            return None
        try:
            return require_valid(raw)
        except WebflowError as exc:
            # A file edited by hand into an invalid state is reported, never replayed.
            logger.warning("webflow %s r%s is invalid: %s", wf_id, rev, exc)
            return None

    def list(self) -> list[WebflowSummary]:
        if not self.root.is_dir():
            return []
        out: list[WebflowSummary] = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or not _ID.match(d.name):
                continue
            doc = self.get(d.name)
            if doc is None:
                continue
            out.append(WebflowSummary(
                id=doc["id"], name=doc["name"], revision=doc["revision"],
                revisions=len(self.revisions(d.name)), steps=len(doc["steps"]),
                origins=tuple(doc["origins"]), params=tuple(p["name"] for p in doc["params"]),
                updated_at=doc.get("updated_at", ""),
            ))
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    # ── writes ─────────────────────────────────────────────────────────────────

    def save(self, raw: dict[str, Any], *, wf_id: str | None = None) -> dict[str, Any]:
        """Validate *raw* and store it as the next revision; returns the stored document.

        *wf_id* (or ``raw["id"]``) updates that flow; without one a new id is derived from
        the name (``wf_<slug>``, ``-2``… on a clash). Raises :class:`WebflowError`.
        """
        doc = require_valid(raw)
        target = wf_id or doc.get("id")
        created = None
        if target:
            self._dir(target)  # validates the id
            prior = self.get(target)
            created = prior.get("created_at") if prior else None
        else:
            target = self._fresh_id(doc["name"])
        now = _now()
        doc["id"] = target
        doc["created_at"] = created or doc.get("created_at") or now
        doc["updated_at"] = now
        d = self._dir(target)
        d.mkdir(parents=True, exist_ok=True)
        for _ in range(50):
            revs = self.revisions(target)
            doc["revision"] = (revs[-1] if revs else 0) + 1
            if self._create(d / f"r{doc['revision']}.json", dumps(doc)):
                return doc
        raise WebflowError([f"could not store a new revision of {target} (too much contention)"])

    def _fresh_id(self, name: str) -> str:
        base = slugify(name)
        cand, n = base, 1
        while (self.root / cand).exists():
            n += 1
            cand = f"{base[:56]}-{n}"
        return cand

    @staticmethod
    def _create(path: Path, text: str) -> bool:
        """Write *path* atomically, only if it does not exist yet. False = taken."""
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                return False
            return True
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def delete(self, wf_id: str) -> bool:
        d = self._dir(wf_id)
        if not d.is_dir():
            return False
        shutil.rmtree(d)
        return True

    def export(self, wf_id: str, revision: int | None = None) -> str | None:
        """The canonical, already-sanitized JSON text of a flow (what import accepts)."""
        doc = self.get(wf_id, revision)
        return dumps(doc) if doc else None
