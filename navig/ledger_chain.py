"""
Hash chain for the operations ledger (T-067, plan-evidence-ledger.md).

Every entry ``OperationRecorder.record()`` appends to ``operations.jsonl``
carries two extra fields:

- ``prev`` — the previous chained entry's ``hash`` (or ``null`` when the entry
  starts a chain: fresh file after ``clear_history()``, first chained entry
  after legacy unchained lines, or an unreadable tail).
- ``hash`` — ``"sha256:" + sha256(prev + canonical_payload)`` where ``prev``
  is the previous hash string (``""`` for a chain start) and
  ``canonical_payload`` is the compact, key-sorted JSON of the entry
  WITHOUT the chain fields.

Delete, edit, or reorder any line and the fingerprints stop matching;
``navig ledger verify`` re-walks the file and reports where.

Honesty contract: this is tamper-EVIDENT, not tamper-proof. A local attacker
who can rewrite the whole file can also recompute the whole chain. The value
is integrity evidence for the audit story and the substrate for signed
returns (plan-writ-protocol.md).

Forward compatibility with the Writ protocol (deliberate, do not break):

- Hash values are ``sha256:``-prefixed (algorithm-agile), exactly the
  notation Writ returns use for their ``prev`` field and the gateway
  audit log uses for ``input_hash``.
- ``CHAIN_FIELDS`` reserves ``sig`` alongside ``prev``/``hash``: the
  canonical payload EXCLUDES it, so a future signed return can add an
  Ed25519 signature over ``hash`` without invalidating existing chains
  (a signature cannot sign itself). Nothing writes ``sig`` today.

Pure stdlib on purpose — importable from both the writer
(``navig.operation_recorder``) and the CLI (``navig ledger verify``) without
dragging in heavy modules.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

#: Algorithm prefix carried inside every hash value (Writ-return notation).
HASH_PREFIX = "sha256:"

#: Chain format version, reported by ``verify`` output.
CHAIN_FORMAT = 1

#: Fields excluded from the canonical payload. ``prev`` is covered by
#: prepension, ``hash`` is the output, ``sig`` is RESERVED for Writ-signed
#: returns (never written today — see module docstring).
CHAIN_FIELDS: tuple[str, ...] = ("prev", "hash", "sig")

#: How much of the file tail to read when resuming the chain. Entries are
#: output-truncated at ~10 KiB by the recorder, so this is generous; a last
#: line longer than this fails to parse and the chain restarts (reported by
#: verify as a restart, never a crash).
_TAIL_READ_BYTES = 256 * 1024


def canonical_payload(entry: Mapping[str, Any]) -> str:
    """Compact, key-sorted JSON of *entry* without the chain fields.

    Round-trip stable: computing it from the in-memory dict at write time and
    from the re-parsed JSON line at verify time yields the same string
    (sorted keys, compact separators, ``ensure_ascii=False`` on both sides).
    """
    body = {k: v for k, v in entry.items() if k not in CHAIN_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_entry_hash(prev: str | None, entry: Mapping[str, Any]) -> str:
    """``sha256:<hex>`` over the previous hash + this entry's canonical payload."""
    material = (prev or "") + canonical_payload(entry)
    return HASH_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()


def read_tail_hash(path: Path) -> str | None:
    """The ``hash`` of the last chained entry in *path*, or ``None``.

    ``None`` means "start a new chain": missing file, empty file, an
    unchained (legacy) tail line, or an unreadable/unparseable tail.
    Reads only the file tail — never the whole ledger.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            span = min(size, _TAIL_READ_BYTES)
            fh.seek(size - span)
            chunk = fh.read(span)
    except OSError:
        return None

    for raw in reversed(chunk.splitlines()):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        entry_hash = data.get("hash") if isinstance(data, dict) else None
        if isinstance(entry_hash, str) and entry_hash.startswith(HASH_PREFIX):
            return entry_hash
        return None
    return None


@dataclass
class ChainBreak:
    """One point where the chain stopped matching."""

    line: int  # 1-based line number in the ledger file
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"line": self.line, "reason": self.reason}


@dataclass
class LedgerVerification:
    """Result of walking a ledger file and re-computing its chain.

    ``ok`` is the exit-code contract: ``True`` → exit 0 (intact — including
    the honest non-failure states missing/empty/legacy), ``False`` → exit 1.
    """

    path: str
    exists: bool = True
    total: int = 0  # parsed entries (chained + unchained)
    chained: int = 0  # entries carrying prev/hash
    unchained: int = 0  # legacy entries written before the chain existed
    malformed: int = 0  # unparseable lines (pre-chain ones are legacy noise)
    verified: int = 0  # chained entries whose hash AND link re-checked clean
    breaks: list[ChainBreak] = field(default_factory=list)
    restarts: list[int] = field(default_factory=list)  # mid-file chain restarts
    anchor: str | None = None  # dangling prev of the first chained entry (rotation carry)

    @property
    def ok(self) -> bool:
        return not self.breaks

    @property
    def first_broken_line(self) -> int | None:
        return self.breaks[0].line if self.breaks else None

    @property
    def status(self) -> str:
        if not self.exists:
            return "missing"
        if self.total == 0 and self.malformed == 0:
            return "empty"
        if self.breaks:
            return "broken"
        if self.chained == 0:
            return "legacy"
        return "intact"

    @property
    def segments(self) -> int:
        return (1 + len(self.restarts)) if self.chained else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "ok": self.ok,
            "total": self.total,
            "chained": self.chained,
            "unchained": self.unchained,
            "malformed": self.malformed,
            "verified": self.verified,
            "breaks": [b.to_dict() for b in self.breaks],
            "first_broken_line": self.first_broken_line,
            "restarts": self.restarts,
            "segments": self.segments,
            "anchor": self.anchor,
            "algorithm": "sha256",
            "format": CHAIN_FORMAT,
            "guarantee": "tamper-evident",
        }


def verify_ledger(path: Path | str) -> LedgerVerification:
    """Walk *path* line by line, re-compute the chain, report — never crash.

    Rules (per plan-evidence-ledger.md wrinkles):

    - entries without ``hash`` are LEGACY (pre-chain) — counted, never a break;
    - a fresh file after ``clear_history()`` simply starts a new chain
      (``prev: null`` at the first chained entry) — a clean restart, not a
      failure;
    - a mid-file ``prev: null`` after chained entries is a RESTART — reported
      in ``restarts``, not a break (the writer legitimately restarts when the
      tail was unreadable);
    - a non-null ``prev`` on the FIRST chained entry is the rotation ANCHOR
      (``_rotate()`` keeps recent lines verbatim, so the first survivor still
      names a rotated-out hash) — reported, not a break;
    - a BREAK is a hash mismatch (entry rewritten/corrupted) or a prev-hash
      mismatch (entries deleted, reordered, or inserted), or an unparseable
      line inside the chained region.
    """
    ledger_path = Path(path)
    result = LedgerVerification(path=str(ledger_path))

    if not ledger_path.exists():
        result.exists = False
        return result

    try:
        with open(ledger_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        result.exists = False
        return result

    last_hash: str | None = None
    saw_chained = False

    for line_no, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped:
            continue

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            result.malformed += 1
            if saw_chained:
                # Garbage where a chained entry should be is evidence, not noise.
                result.breaks.append(
                    ChainBreak(line_no, "unparseable line inside the chained region")
                )
            continue

        if not isinstance(data, dict) or "hash" not in data:
            result.total += 1
            result.unchained += 1
            continue

        result.total += 1
        result.chained += 1
        stored_prev = data.get("prev")
        stored_hash = data.get("hash")
        entry_ok = True

        # Link check.
        if not saw_chained:
            if stored_prev is not None:
                result.anchor = stored_prev  # rotation carry — unverifiable, not broken
        elif stored_prev is None:
            result.restarts.append(line_no)
        elif stored_prev != last_hash:
            result.breaks.append(
                ChainBreak(
                    line_no,
                    "prev-hash mismatch (entries deleted, reordered, or inserted before this line)",
                )
            )
            entry_ok = False

        # Content check: recompute this entry's fingerprint from its own bytes.
        recomputed = compute_entry_hash(stored_prev if isinstance(stored_prev, str) else None, data)
        if recomputed != stored_hash:
            result.breaks.append(
                ChainBreak(line_no, "hash mismatch (entry rewritten or corrupted)")
            )
            entry_ok = False

        if entry_ok:
            result.verified += 1

        # Chain off the STORED hash — the writer of the next entry saw this
        # value, so one tampered entry yields exactly one precise break.
        last_hash = stored_hash if isinstance(stored_hash, str) else last_hash
        saw_chained = True

    return result
