"""Image near-duplicate detection via 256-bit perceptual hash (dHash 16x16).

Two complementary passes:
  * **thumbnails** — Telegram/exports often keep a small ``X_thumb.ext`` beside the
    full ``X``; these are dropped by name when the full exists (100% precise).
  * **perceptual** — re-saved / re-encoded / resized copies clustered by Hamming
    distance; the largest (highest-res) file in each cluster is kept.

Pure engine — no CLI. `navig media dedupe-images` drives it. Non-destructive:
callers quarantine extras (move, never delete).
"""
from __future__ import annotations

import os
import shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
_SIZE = 16  # 17x16 diff = 256 bits
_LUT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint16)


def dhash(path: Path) -> np.ndarray | None:
    try:
        im = Image.open(path).convert("L").resize((_SIZE + 1, _SIZE), Image.LANCZOS)
        a = np.asarray(im, dtype=np.int16)
        diff = a[:, 1:] > a[:, :-1]
        return np.packbits(diff.flatten())  # uint8[32]
    except Exception:  # noqa: BLE001
        return None


def list_images(root: Path) -> list[Path]:
    return [p for p in root.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXT]


_THUMB_OF_MEDIA = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v",
                   ".jpg", ".jpeg", ".png", ".webp", ".gif"}


def redundant_thumbs(root: Path) -> list[str]:
    """Export thumbnails ``X_thumb.ext`` that are redundant: either the full image
    ``X`` exists here, or ``X`` is itself a media file (e.g. ``clip.mp4_thumb.jpg``
    is just a poster for a video we already keep elsewhere)."""
    names = {p.name for p in list_images(root)}
    out = []
    for n in names:
        low = n.lower()
        for tag in ("_thumb.jpg", "_thumb.jpeg", "_thumb.png", "_thumb.webp"):
            if low.endswith(tag):
                base = n[: -len(tag)]
                base_ext = os.path.splitext(base)[1].lower()
                if (base in names
                        or any((base + e) in names for e in IMAGE_EXT)
                        or base_ext in _THUMB_OF_MEDIA):
                    out.append(n)
                break
    return out


def hash_dir(root: Path, workers: int = 8, skip: set[str] | None = None,
             progress=None) -> dict[str, np.ndarray]:
    files = [p for p in list_images(root) if not skip or p.name not in skip]
    out: dict[str, np.ndarray] = {}
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for p, h in zip(files, ex.map(dhash, files)):
            done += 1
            if h is not None:
                out[p.name] = h
            if progress and done % 1000 == 0:
                progress(done, len(files))
    return out


def cluster(hashes: dict[str, np.ndarray], threshold: int = 0) -> list[list[str]]:
    names = list(hashes)
    if not names:
        return []
    H = np.array([hashes[n] for n in names])  # [N,32] uint8
    N = len(names)
    parent = list(range(N))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(N):
        d = _LUT[H[i + 1:] ^ H[i]].sum(axis=1)
        for off in np.nonzero(d <= threshold)[0]:
            j = i + 1 + int(off)
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)

    groups: dict[int, list[str]] = defaultdict(list)
    for idx in range(N):
        groups[find(idx)].append(names[idx])
    return [g for g in groups.values() if len(g) > 1]


def quarantine(root: Path, names: list[str], qdir: Path) -> dict:
    """MOVE the given files to *qdir* (reversible). Returns summary."""
    qdir.mkdir(parents=True, exist_ok=True)
    moved, freed = 0, 0
    for n in names:
        src = root / n
        if not src.exists():
            continue
        freed += src.stat().st_size
        dst = qdir / n
        if dst.exists():
            dst = qdir / (src.stem + "._dup" + src.suffix)
        shutil.move(str(src), str(dst))
        moved += 1
    return {"quarantined": moved, "reclaimed_bytes": freed, "quarantine_dir": str(qdir)}


def extras_to_drop(root: Path, clusters: list[list[str]]) -> list[str]:
    """From each cluster keep the largest file; return the rest (to quarantine)."""
    drop = []
    for g in clusters:
        keep = max(g, key=lambda n: (root / n).stat().st_size if (root / n).exists() else 0)
        drop += [n for n in g if n != keep]
    return drop
