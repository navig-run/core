"""`navig media browse` served the wrong picture from a shared thumbnail cache.

The cache key was `md5(rel)` — the path RELATIVE to the browsed folder — inside one
directory shared by every invocation. So browsing folder A and then folder B, where both
hold `img1.jpg`, keyed to the same file: B's gallery showed A's photo. The cache also
lived in the SHARED temp dir, so on a multi-user box the first account to browse owned the
directory everyone else wrote into.

And it was write-once (`if not cp.exists()`), so editing a photo left the old thumbnail
forever.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

from navig.media.browse import _thumb_cache, _thumb_is_stale, thumb_path


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    d = tmp_path / "cache"
    d.mkdir()
    return d


def _file(root: Path, rel: str, content: bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


# ── the collision ────────────────────────────────────────────────────────────


def test_same_relative_name_in_two_folders_gets_two_entries(tmp_path, cache):
    """THE BUG: browse A, then B — B showed A's thumbnail."""
    a = _file(tmp_path / "A", "img1.jpg", b"photo-a")
    b = _file(tmp_path / "B", "img1.jpg", b"photo-b")

    assert thumb_path(cache, a) != thumb_path(cache, b), (
        "two different files sharing a relative name still collide in the cache"
    )


def test_the_old_relative_key_really_did_collide(tmp_path):
    """Pin the reason this test exists, so the fix is not 'simplified' back."""
    old_key = lambda rel: hashlib.md5(rel.encode()).hexdigest()  # noqa: E731
    assert old_key("img1.jpg") == old_key("img1.jpg"), "the historic key was root-independent"


def test_the_same_file_reached_two_ways_is_one_entry(tmp_path, cache):
    """Anti-vacuity: a cache that never hits is not a cache.

    The key must be the RESOLVED file, so `A/img1.jpg` and `A/./img1.jpg` share an entry.
    """
    a = _file(tmp_path / "A", "img1.jpg", b"photo-a")
    indirect = tmp_path / "A" / "." / "img1.jpg"

    assert thumb_path(cache, a) == thumb_path(cache, indirect)


def test_two_files_in_one_folder_get_two_entries(tmp_path, cache):
    a = _file(tmp_path, "one.jpg", b"1")
    b = _file(tmp_path, "two.jpg", b"2")
    assert thumb_path(cache, a) != thumb_path(cache, b)


# ── staleness ────────────────────────────────────────────────────────────────


def test_an_edited_source_invalidates_its_thumbnail(tmp_path, cache):
    src = _file(tmp_path, "pic.jpg", b"before")
    cached = cache / "t.jpg"
    cached.write_bytes(b"thumb")

    future = time.time() + 60
    os.utime(src, (future, future))

    assert _thumb_is_stale(cached, src), "an edited photo kept its old thumbnail forever"


def test_an_untouched_source_keeps_its_thumbnail(tmp_path, cache):
    """Anti-vacuity: 'always stale' would regenerate every thumbnail on every request."""
    src = _file(tmp_path, "pic.jpg", b"content")
    cached = cache / "t.jpg"
    time.sleep(0.01)
    cached.write_bytes(b"thumb")

    assert not _thumb_is_stale(cached, src)


def test_an_unreadable_source_regenerates_rather_than_serving_stale(tmp_path, cache):
    cached = cache / "t.jpg"
    cached.write_bytes(b"thumb")
    assert _thumb_is_stale(cached, tmp_path / "gone.jpg"), (
        "when staleness cannot be determined the safe answer is to regenerate"
    )


# ── where the cache lives ────────────────────────────────────────────────────


def test_the_cache_is_per_user_not_the_shared_temp_dir(monkeypatch, tmp_path):
    """A fixed name in the shared temp dir is the first account's to own."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    import tempfile

    d = _thumb_cache()

    assert d.is_dir()
    shared = Path(tempfile.gettempdir()).resolve()
    assert shared not in d.resolve().parents, (
        f"the thumbnail cache is under the shared temp dir ({d}) — it must be per-user"
    )
