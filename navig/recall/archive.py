"""Immutable archive of normalised, secret-redacted turns.

``archive/<adapter>/<source-dir>/seg-00001.jsonl.gz`` … plus ``manifest.json`` holding the
source row and a sha256 per segment. A segment is written once (exclusive create) and never
rewritten; new turns from a growing transcript become a new segment. Evidence therefore
stays resolvable after the original transcript is deleted or rotated.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from navig.recall.model import Turn

_SAFE = re.compile(r"[^\w.-]+")


def _dir_name(source_id: str) -> str:
    short = hashlib.sha1(source_id.encode("utf-8")).hexdigest()[:8]
    return f"{_SAFE.sub('_', source_id)[:80]}-{short}"


class Archive:
    def __init__(self, root: Path | None = None):
        if root is None:
            from navig.recall.paths import archive_dir  # noqa: PLC0415

            root = archive_dir()
        self.root = Path(root)

    def source_dir(self, adapter: str, source_id: str) -> Path:
        return self.root / adapter / _dir_name(source_id)

    def manifest(self, adapter: str, source_id: str) -> dict[str, Any]:
        p = self.source_dir(adapter, source_id) / "manifest.json"
        if not p.exists():
            return {"source": {"source_id": source_id, "adapter": adapter}, "segments": []}
        return json.loads(p.read_text(encoding="utf-8"))

    def append(self, adapter: str, source_id: str, turns: list[Turn], source: dict[str, Any]) -> dict[str, Any] | None:
        """Write *turns* as a new immutable segment; returns the segment entry (None if empty)."""
        from navig.core.json_io import atomic_write_json  # noqa: PLC0415

        d = self.source_dir(adapter, source_id)
        d.mkdir(parents=True, exist_ok=True)
        man = self.manifest(adapter, source_id)
        man["source"] = {k: v for k, v in source.items() if k != "meta_json"}
        entry = None
        if turns:
            seq = len(man["segments"]) + 1
            name = f"seg-{seq:05d}.jsonl.gz"
            payload = "".join(json.dumps(t.to_dict(), ensure_ascii=False) + "\n" for t in turns).encode("utf-8")
            blob = gzip.compress(payload, mtime=0)
            with open(d / name, "xb") as fh:  # exclusive: a segment is never overwritten
                fh.write(blob)
            entry = {"file": name, "sha256": hashlib.sha256(blob).hexdigest(), "turns": len(turns)}
            man["segments"].append(entry)
        atomic_write_json(man, d / "manifest.json")
        return entry

    def read_turns(self, adapter: str, source_id: str, *, verify: bool = True) -> Iterator[Turn]:
        d = self.source_dir(adapter, source_id)
        for seg in self.manifest(adapter, source_id)["segments"]:
            blob = (d / seg["file"]).read_bytes()
            if verify and hashlib.sha256(blob).hexdigest() != seg["sha256"]:
                raise ValueError(f"archive segment checksum mismatch: {d.name}/{seg['file']}")
            for line in gzip.decompress(blob).decode("utf-8").splitlines():
                if line.strip():
                    yield Turn.from_dict(json.loads(line))

    def forget(self, adapter: str, source_id: str) -> bool:
        """Delete one source's segments and manifest — ONLY for a source its owner withdrew
        (a chat no longer shared). Everything else in the archive stays immutable."""
        import shutil  # noqa: PLC0415

        d = self.source_dir(adapter, source_id)
        if not d.exists():
            return False
        shutil.rmtree(d)
        return True

    def manifests(self) -> Iterator[dict[str, Any]]:
        if not self.root.exists():
            return
        for p in sorted(self.root.glob("*/*/manifest.json")):
            try:
                yield json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
