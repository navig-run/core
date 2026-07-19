"""Exact-duplicate detection via content SHA-256 (any file type).

Pure engine — `navig media dedupe-files` drives it. Non-destructive.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def sha256(path: Path, buf: int = 1 << 20) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(buf):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return None


def hash_dir(root: Path, recursive: bool = False, workers: int = 8,
             progress=None) -> dict[str, str]:
    it = root.rglob("*") if recursive else root.iterdir()
    files = [p for p in it if p.is_file()]
    out: dict[str, str] = {}
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for p, h in zip(files, ex.map(sha256, files)):
            done += 1
            if h:
                out[str(p.relative_to(root))] = h
            if progress and done % 1000 == 0:
                progress(done, len(files))
    return out


def cluster(hashes: dict[str, str]) -> list[list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for rel, h in hashes.items():
        groups[h].append(rel)
    return [sorted(g) for g in groups.values() if len(g) > 1]
