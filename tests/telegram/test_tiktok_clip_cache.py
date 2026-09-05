"""Tapping two buttons on one clip downloaded it twice.

Every action begins by fetching the video, so 📝 Transcript then ⬇️ Download
pulled the same bytes twice — wasted bandwidth, and a second request at exactly
the moment TikTok is deciding whether we look like a bot. (Analyse's audio
enrichment and 🎧 Audio are the same pair on the audio-only key.) Since the
whole point of the surrounding work was to stop *provoking* bot-walls and then
report them honestly, halving the requests is the other half of that.

The invariant that makes this safe: **a borrower never shares the cached file.**
`_fetched` always yields a private copy in its own workdir, so `keep()` (which
MOVES the file), the workdir cleanup, and cache eviction cannot interact. And on
a MISS the download still goes straight into the workdir — a cache that misses
is invisible, which is what keeps the ordinary path the tested path.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("navig_download", reason="tiktok actions need navig-download")

from navig.telegram import tiktok_actions as t  # noqa: E402

_URL = "https://www.tiktok.com/@x/video/1"


@pytest.fixture
def _temp_root(monkeypatch, tmp_path):
    monkeypatch.setattr(t.tempfile, "gettempdir", lambda: str(tmp_path))
    t.tempfile.tempdir = str(tmp_path)
    yield tmp_path
    t.tempfile.tempdir = None


@pytest.fixture
def fetches(monkeypatch):
    """Count real downloads; each writes into the dest_dir it was given."""
    calls: list[dict] = []

    async def _fetch(url, *, dest_dir=None, audio_only=False, **kw):
        calls.append({"url": url, "dest_dir": dest_dir, "audio_only": audio_only})
        name = "clip.m4a" if audio_only else "clip.mp4"
        target = Path(dest_dir) / name
        target.write_bytes(b"video-bytes" * 16)
        return str(target)

    monkeypatch.setattr(t.engine, "fetch_file_async", _fetch)
    return calls


# ── the point of the whole thing ──────────────────────────────────────────────


async def test_a_second_action_on_the_same_clip_does_not_re_download(
    _temp_root, fetches
):
    async with t._fetched(_URL) as first:
        assert first.path
    async with t._fetched(_URL) as second:
        assert Path(second.path).read_bytes() == b"video-bytes" * 16

    assert len(fetches) == 1, f"downloaded {len(fetches)}x — the cache did nothing"


async def test_audio_and_video_are_different_entries(_temp_root, fetches):
    """The transcript needs pixels; the audio button must not be served a video."""
    async with t._fetched(_URL) as video:
        assert video.path.endswith(".mp4")
    async with t._fetched(_URL, audio_only=True) as audio:
        assert audio.path.endswith(".m4a")

    assert len(fetches) == 2
    assert [c["audio_only"] for c in fetches] == [False, True]


async def test_a_different_url_is_not_served_the_wrong_clip(_temp_root, fetches):
    async with t._fetched(_URL):
        pass
    async with t._fetched("https://www.tiktok.com/@y/video/2"):
        pass
    assert len(fetches) == 2


# ── a miss must look exactly like the pre-cache behaviour ─────────────────────


async def test_on_a_miss_the_download_still_lands_in_the_workdir(_temp_root, fetches):
    """A cache that misses is invisible — this is what keeps the ordinary path
    the path everything else was built and tested against."""
    async with t._fetched(_URL) as clip:
        assert fetches[0]["dest_dir"] == clip.workdir
        assert os.path.dirname(clip.path) == clip.workdir


async def test_a_hit_also_yields_a_file_inside_the_workdir(_temp_root, fetches):
    async with t._fetched(_URL):
        pass
    async with t._fetched(_URL) as clip:
        assert os.path.dirname(clip.path) == clip.workdir
        assert os.path.exists(clip.path)


async def test_a_failing_fetch_still_raises_through_the_cache(_temp_root, monkeypatch):
    async def _boom(url, *, dest_dir=None, audio_only=False, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(t.engine, "fetch_file_async", _boom)
    with pytest.raises(RuntimeError):
        async with t._fetched(_URL):
            pass  # pragma: no cover


# ── the borrower's copy is private: keep() and cleanup cannot collide ─────────


class TestPrivateCopies:
    async def test_keep_moves_the_borrowers_copy_not_the_cached_one(
        self, _temp_root, fetches, monkeypatch, tmp_path
    ):
        """`keep()` MOVES the file. If it moved the cached entry, the next action
        would get a cache hit pointing at a file that is no longer there."""
        monkeypatch.setattr(
            "navig.platform.paths.media_dir", lambda kind: tmp_path / "media" / kind
        )
        async with t._fetched(_URL) as clip:
            clip.keep("videos")

        async with t._fetched(_URL) as second:
            assert os.path.exists(second.path)
        assert len(fetches) == 1, "keep() destroyed the cache entry"

    async def test_workdir_cleanup_does_not_empty_the_cache(self, _temp_root, fetches):
        async with t._fetched(_URL) as clip:
            workdir = clip.workdir
        assert not os.path.exists(workdir)

        async with t._fetched(_URL):
            pass
        assert len(fetches) == 1


# ── bounded: by age, and by bytes ─────────────────────────────────────────────


class TestBounds:
    async def test_an_expired_entry_is_re_fetched_and_removed(
        self, _temp_root, fetches, monkeypatch
    ):
        async with t._fetched(_URL):
            pass
        (_cached, first_expiry) = t._cache[(_URL, False)]
        real_time = time.time  # capture BEFORE patching, or the lambda recurses
        later = real_time() + t._CACHE_TTL_S + 1
        monkeypatch.setattr(t.time, "time", lambda: later)

        async with t._fetched(_URL):
            pass

        assert len(fetches) == 2, "an expired entry was served instead of re-fetched"
        # The cached filename is deterministic per key, so the expired file is
        # unlinked and then rewritten at the same path — the observable proof of
        # expiry is the re-fetch above plus the refreshed deadline here. That
        # eviction actually DELETES rather than just forgetting is asserted by
        # `test_an_evicted_file_is_deleted_not_just_forgotten`.
        assert t._cache[(_URL, False)][1] > first_expiry

    async def test_the_cache_stays_under_its_byte_budget(
        self, _temp_root, fetches, monkeypatch
    ):
        monkeypatch.setattr(t, "_CACHE_MAX_BYTES", 200)  # ~1 clip of 176 bytes
        for i in range(4):
            async with t._fetched(f"{_URL}/{i}"):
                pass

        total = sum(
            os.path.getsize(p) for p, _ in t._cache.values() if os.path.exists(p)
        )
        assert total <= 200, f"cache grew past its budget: {total}"

    async def test_an_evicted_file_is_deleted_not_just_forgotten(
        self, _temp_root, fetches, monkeypatch
    ):
        monkeypatch.setattr(t, "_CACHE_MAX_BYTES", 200)
        async with t._fetched(f"{_URL}/a"):
            pass
        first = t._cache[(f"{_URL}/a", False)][0]
        for i in range(3):
            async with t._fetched(f"{_URL}/b{i}"):
                pass
        if (f"{_URL}/a", False) not in t._cache:
            assert not os.path.exists(first), "evicted from the dict but left on disk"

    async def test_a_cached_file_deleted_underneath_us_is_a_miss(
        self, _temp_root, fetches
    ):
        """The stale-tempdir sweep can remove the cache dir; that is not an error."""
        async with t._fetched(_URL):
            pass
        Path(t._cache[(_URL, False)][0]).unlink()

        async with t._fetched(_URL) as clip:
            assert os.path.exists(clip.path)
        assert len(fetches) == 2


class TestLockDictIsBounded:
    """`_cache` is bounded by TTL and by bytes; `_cache_locks` was bounded by
    nothing — one entry per distinct (url, audio_only) for the life of the daemon.
    Small per entry, unbounded in aggregate, and in a process that runs for months.
    """

    async def test_locks_do_not_accumulate_for_evicted_clips(
        self, _temp_root, fetches, monkeypatch
    ):
        monkeypatch.setattr(t, "_CACHE_MAX_BYTES", 200)  # force eviction
        for i in range(6):
            async with t._fetched(f"{_URL}/{i}"):
                pass
        assert len(t._cache_locks) <= len(t._cache) + 1, (
            f"{len(t._cache_locks)} locks for {len(t._cache)} cached clips"
        )

    async def test_the_prune_runs_on_the_ordinary_under_budget_path(
        self, _temp_root, fetches, monkeypatch
    ):
        """`_evict` used to return early when under budget, which would have made
        the prune dead exactly where it matters — the cache is normally small."""
        async with t._fetched(_URL):
            pass
        t._cache.clear()          # entry gone, its lock is now garbage
        t._evict(time.time())     # under budget: the early-return path
        assert (_URL, False) not in t._cache_locks

    async def test_a_held_lock_is_never_pruned(self, _temp_root, monkeypatch):
        """A held lock has a coroutine inside it. Dropping it would let a second
        caller build a fresh lock and fetch the same clip concurrently."""
        import asyncio

        release = asyncio.Event()

        async def _slow(url, *, dest_dir=None, audio_only=False, **kw):
            t._cache.clear()
            t._evict(time.time())   # prune WHILE this key's lock is held
            await release.wait()
            target = Path(dest_dir) / "clip.mp4"
            target.write_bytes(b"x")
            return str(target)

        monkeypatch.setattr(t.engine, "fetch_file_async", _slow)
        task = asyncio.create_task(_use_once())
        await asyncio.sleep(0)
        release.set()
        await task
        # It survived the prune that ran inside the fetch.
        assert (_URL, False) in t._cache_locks


async def _use_once():
    async with t._fetched(_URL):
        return True


# ── concurrency: two taps coalesce into one download ──────────────────────────


async def test_two_concurrent_actions_share_one_download(_temp_root, monkeypatch):
    """Transcript and Download can run at the same time on the same clip. Without
    the per-key lock both would miss and both would fetch."""
    import asyncio

    started = {"n": 0}
    release = asyncio.Event()

    async def _slow(url, *, dest_dir=None, audio_only=False, **kw):
        started["n"] += 1
        await release.wait()
        target = Path(dest_dir) / "clip.mp4"
        target.write_bytes(b"x" * 32)
        return str(target)

    monkeypatch.setattr(t.engine, "fetch_file_async", _slow)

    async def _use():
        async with t._fetched(_URL) as clip:
            return Path(clip.path).read_bytes()

    task_a = asyncio.create_task(_use())
    task_b = asyncio.create_task(_use())
    await asyncio.sleep(0)
    release.set()
    a, b = await asyncio.gather(task_a, task_b)

    assert a == b == b"x" * 32
    assert started["n"] == 1, f"{started['n']} concurrent downloads of one clip"
