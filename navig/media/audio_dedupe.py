"""Audio near-duplicate detection via Chromaprint (fpcalc) acoustic fingerprints.

Catches the *same track re-shared / re-encoded* (e.g. a TikTok song saved many
times, or the same song as both .m4a and .mp3) that byte-hashing misses.

Pure engine — no CLI. `navig media dedupe-audio` (commands/media.py) drives it.
Non-destructive: callers quarantine extras; nothing is deleted here.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".oga", ".wav", ".opus", ".aac", ".flac", ".wma"}


def fpcalc_bin() -> str:
    return os.environ.get("NAVIG_FPCALC", "fpcalc")


def fpcalc_available() -> bool:
    try:
        subprocess.run([fpcalc_bin(), "-version"], capture_output=True, timeout=10)
        return True
    except Exception:  # noqa: BLE001
        return False


def fingerprint(path: Path) -> tuple[int, list[int]] | None:
    """Return (duration_seconds, raw_fingerprint_ints) or None on failure."""
    try:
        out = subprocess.run(
            [fpcalc_bin(), "-raw", "-length", "120", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:  # noqa: BLE001
        return None
    dur, fp = 0, []
    for line in out.stdout.splitlines():
        if line.startswith("DURATION="):
            try:
                dur = int(float(line.split("=", 1)[1]))
            except ValueError:
                dur = 0
        elif line.startswith("FINGERPRINT="):
            fp = [int(x) for x in line.split("=", 1)[1].split(",") if x]
    return (dur, fp) if fp else None


def fingerprint_dir(root: Path, workers: int = 6, progress=None) -> dict[str, tuple[int, list[int]]]:
    """Fingerprint every audio file directly under *root*. Returns name -> (dur, fp)."""
    files = [p for p in root.iterdir()
             if p.is_file() and p.suffix.lower() in AUDIO_EXT]
    out: dict[str, tuple[int, list[int]]] = {}
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for p, res in zip(files, ex.map(fingerprint, files)):
            done += 1
            if res:
                out[p.name] = res
            if progress and done % 200 == 0:
                progress(done, len(files))
    return out


def _similar(a: list[int], b: list[int]) -> float:
    """Fraction of matching bits over the aligned prefix (0..1). 1.0 = identical."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    same_bits = 0
    for x, y in zip(a[:n], b[:n]):
        same_bits += 32 - bin(x ^ y).count("1")
    return same_bits / (n * 32)


def cluster(fps: dict[str, tuple[int, list[int]]], threshold: float = 0.90) -> list[list[str]]:
    """Group near-duplicate tracks. Exact-fp matches always group; near matches
    (>= threshold bit-similarity) group within the same ~duration bucket."""
    names = list(fps)
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 1) exact fingerprint → instant grouping (re-shared identical files)
    by_exact: dict[tuple, list[str]] = defaultdict(list)
    for n in names:
        by_exact[tuple(fps[n][1])].append(n)
    for group in by_exact.values():
        for other in group[1:]:
            union(group[0], other)

    # 2) fuzzy within duration buckets (re-encodes / format changes)
    by_dur: dict[int, list[str]] = defaultdict(list)
    for n in names:
        by_dur[fps[n][0]].append(n)
    durs = sorted(by_dur)
    for d in durs:
        cand = by_dur[d] + by_dur.get(d + 1, []) + by_dur.get(d - 1, [])
        for i in range(len(cand)):
            for j in range(i + 1, len(cand)):
                a, b = cand[i], cand[j]
                if find(a) == find(b):
                    continue
                if _similar(fps[a][1], fps[b][1]) >= threshold:
                    union(a, b)

    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        groups[find(n)].append(n)
    return [g for g in groups.values() if len(g) > 1]


def quarantine(root: Path, clusters: list[list[str]], quarantine_dir: Path) -> dict:
    """Keep the largest file per cluster; MOVE the rest to *quarantine_dir*.
    Reversible (move, not delete). Returns a summary dict."""
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    moved, freed = 0, 0
    for group in clusters:
        keep = max(group, key=lambda n: (root / n).stat().st_size if (root / n).exists() else 0)
        for n in group:
            if n == keep:
                continue
            src = root / n
            if not src.exists():
                continue
            freed += src.stat().st_size
            dst = quarantine_dir / n
            if dst.exists():
                dst = quarantine_dir / (src.stem + "._dup" + src.suffix)
            shutil.move(str(src), str(dst))
            moved += 1
    return {"quarantined": moved, "reclaimed_bytes": freed, "quarantine_dir": str(quarantine_dir)}
