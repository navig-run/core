"""Video near-duplicate detection via perceptual frame signatures.

Each video → N evenly-spaced keyframes → 256-bit dHash each → a signature.
Two videos are duplicates when their aligned frame hashes are close (average
per-frame Hamming below threshold), scoped to a similar-duration bucket so we
never compare unrelated clips. Catches re-encoded / re-uploaded copies.

Pure engine — `navig media dedupe-video` drives it. Non-destructive.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from navig.media.image_dedupe import dhash  # reuse the 256-bit hasher

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}
_NFRAMES = 4


def _probe_dur(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=20)
        return float(r.stdout.strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def signature(path: Path, nframes: int = _NFRAMES) -> tuple[int, np.ndarray] | None:
    """Return (duration_int, ndarray[nframes,32]) or None.

    Frames are grabbed with fast ``-ss`` seeks at evenly-spread fractions, so
    cost is bounded even for very long files (no full-video decode)."""
    dur = _probe_dur(path)
    if dur <= 0:
        return None
    fracs = [0.1, 0.37, 0.63, 0.9][:nframes]
    d = tempfile.mkdtemp(prefix="vsig_")
    try:
        hashes = []
        for i, fr in enumerate(fracs):
            out = os.path.join(d, f"f_{i}.png")
            try:
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{dur * fr:.2f}",
                                "-i", str(path), "-frames:v", "1", "-vf", "scale=64:64", out],
                               capture_output=True, timeout=30)
            except Exception:  # noqa: BLE001
                continue
            h = dhash(Path(out)) if os.path.exists(out) else None
            if h is not None:
                hashes.append(h)
        if not hashes:
            return None
        while len(hashes) < nframes:      # pad short clips by repeating last
            hashes.append(hashes[-1])
        return int(dur), np.array(hashes[:nframes])
    finally:
        for f in Path(d).glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass


def signature_dir(root: Path, workers: int = 6, progress=None) -> dict[str, tuple[int, np.ndarray]]:
    files = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT]
    out: dict[str, tuple[int, np.ndarray]] = {}
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for p, sig in zip(files, ex.map(signature, files)):
            done += 1
            if sig is not None:
                out[p.name] = sig
            if progress and done % 100 == 0:
                progress(done, len(files))
    return out


_LUT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint16)


def _avg_ham(a: np.ndarray, b: np.ndarray) -> float:
    """Mean per-frame Hamming distance between two [nframes,32] signatures."""
    return float(_LUT[a ^ b].sum(axis=1).mean())


def cluster(sigs: dict[str, tuple[int, np.ndarray]], threshold: int = 24) -> list[list[str]]:
    """Group videos whose avg per-frame Hamming <= threshold, within ±1s duration."""
    names = list(sigs)
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_dur: dict[int, list[str]] = defaultdict(list)
    for n in names:
        by_dur[sigs[n][0]].append(n)

    for d in sorted(by_dur):
        cand = by_dur[d] + by_dur.get(d + 1, []) + by_dur.get(d - 1, [])
        for i in range(len(cand)):
            for j in range(i + 1, len(cand)):
                a, b = cand[i], cand[j]
                if find(a) == find(b):
                    continue
                if _avg_ham(sigs[a][1], sigs[b][1]) <= threshold:
                    ra, rb = find(a), find(b)
                    parent[rb] = ra

    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        groups[find(n)].append(n)
    return [g for g in groups.values() if len(g) > 1]
