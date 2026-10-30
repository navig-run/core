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


def test_the_cache_delegates_to_the_per_user_cache_dir(monkeypatch, tmp_path):
    """The cache location is `cache_dir()`'s answer, not a name of this module's own.

    Asserted as a DELEGATION rather than by inspecting the path that comes back, because
    the ambient answer cannot decide it here: `tests/conftest.py` deliberately points
    NAVIG_CACHE_DIR at a temp dir so no test writes into the operator's real cache, so
    inside this suite the cache always *is* under the shared temp dir. This test used to
    patch NAVIG_CONFIG_DIR and assert the path — `cache_dir()` reads NAVIG_CACHE_DIR, so
    the patch was inert and the assertion could never hold. See the companion test below
    for the property the original was reaching for.
    """
    from navig.platform import paths  # noqa: PLC0415

    monkeypatch.setenv("NAVIG_CACHE_DIR", str(tmp_path / "cache"))

    d = _thumb_cache()

    assert d.is_dir()
    assert d == paths.cache_dir() / "browse-thumbs", (
        f"the thumbnail cache ({d}) is not derived from cache_dir() — a name of its own "
        "cannot follow the per-user cache when that moves"
    )


@pytest.mark.parametrize("os_name", ["windows", "macos", "linux"])
def test_the_per_user_cache_dir_is_not_the_shared_temp_dir(monkeypatch, os_name):
    """A fixed name in the shared temp dir is the first account's to own.

    With nothing overriding it, `cache_dir()` must land in this account's own cache
    (LOCALAPPDATA/navig/cache, ~/Library/Caches/navig, ~/.cache/navig) — never the
    shared temp dir. Read-only: `cache_dir()` creates nothing, so this touches none of
    the operator's real directories.

    EVERY override is isolated first. Clearing only `NAVIG_CACHE_DIR` left the
    runner's own `XDG_CACHE_HOME` / `HOME` / `LOCALAPPDATA` in charge, so a valid
    per-user `XDG_CACHE_HOME=/tmp/user-cache` failed this test. Home and LOCALAPPDATA
    point at a fixed path outside the temp dir, and the result must be exactly the
    per-OS default.
    """
    import tempfile  # noqa: PLC0415

    from navig.platform import paths  # noqa: PLC0415

    shared = Path(tempfile.gettempdir()).resolve()
    home = Path(shared.anchor) / "navig-test-home"
    local_appdata = home / "AppData" / "Local"

    monkeypatch.delenv("NAVIG_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(paths, "home_dir", lambda: home)
    monkeypatch.setattr(paths, "current_os", lambda: os_name)
    monkeypatch.setattr(paths, "_is_system_service", lambda: False)

    expected = {
        "windows": local_appdata / "navig" / "cache",
        "macos": home / "Library" / "Caches" / "navig",
        "linux": home / ".cache" / "navig",
    }[os_name]
    d = paths.cache_dir()

    assert d == expected, f"{os_name} default cache dir is {d}, expected {expected}"
    assert shared not in d.parents and d != shared, (
        f"the per-user cache dir is under the shared temp dir ({d}) — it must be per-user"
    )
