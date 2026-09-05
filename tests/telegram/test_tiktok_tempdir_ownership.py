"""Every TikTok action leaked the directory it downloaded into.

`engine.fetch_file` defaults its destination to `tempfile.mkdtemp(prefix=…)`, so
the directory belongs to whoever called it — and nobody claimed it. All four
fetch sites removed the FILE and left the directory: **25 empty
`navig_tiktok_*` directories on the operator's machine** when this was measured,
one per action ever run, accumulating for as long as the bot has existed.

Nothing errors, nothing is slow, nobody notices. `%TEMP%` just fills up.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("navig_download", reason="tiktok actions need navig-download")

from navig.telegram import tiktok_actions as t  # noqa: E402


@pytest.fixture(autouse=True)
def _unswept():
    """The sweep runs once per process; each case needs it armed."""
    t._swept = False
    yield
    t._swept = False


@pytest.fixture
def _temp_root(monkeypatch, tmp_path):
    """Point both mkdtemp and the sweep at a directory of our own."""
    monkeypatch.setattr(t.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    t.tempfile.tempdir = str(tmp_path)
    yield tmp_path
    t.tempfile.tempdir = None


def _patch_fetch(monkeypatch, *, fail: Exception | None = None):
    async def _fetch(url, *, dest_dir=None, audio_only=False, **kw):
        if fail is not None:
            raise fail
        target = os.path.join(dest_dir, "clip.mp4")
        Path(target).write_bytes(b"x" * 64)
        return target

    monkeypatch.setattr(t.engine, "fetch_file_async", _fetch)


# ── the directory is owned, and always released ───────────────────────────────


class TestOwnership:
    async def test_the_caller_names_the_destination(self, monkeypatch, _temp_root):
        """Letting yt-dlp pick is exactly how the directory became unowned."""
        seen = {}

        async def _fetch(url, *, dest_dir=None, audio_only=False, **kw):
            seen["dest_dir"] = dest_dir
            target = os.path.join(dest_dir, "clip.mp4")
            Path(target).write_bytes(b"x")
            return target

        monkeypatch.setattr(t.engine, "fetch_file_async", _fetch)

        async with t._fetched("u") as clip:
            assert clip.path.startswith(seen["dest_dir"])
        assert seen["dest_dir"], "no dest_dir passed — the engine would mkdtemp its own"

    async def test_the_directory_goes_not_just_the_file(self, monkeypatch, _temp_root):
        _patch_fetch(monkeypatch)
        async with t._fetched("u") as clip:
            workdir = os.path.dirname(clip.path)
            assert os.path.isdir(workdir)
        assert not os.path.exists(workdir), "the empty directory was left behind"

    async def test_a_failed_fetch_leaves_nothing(self, monkeypatch, _temp_root):
        """A partial download plus its directory is the worst leak — it has bytes."""
        _patch_fetch(monkeypatch, fail=RuntimeError("network down"))
        before = set(os.listdir(_temp_root))
        with pytest.raises(RuntimeError):
            async with t._fetched("u"):
                pass  # pragma: no cover
        assert set(os.listdir(_temp_root)) == before

    async def test_an_exception_in_the_body_still_cleans_up(
        self, monkeypatch, _temp_root
    ):
        _patch_fetch(monkeypatch)
        captured = {}
        with pytest.raises(ValueError):
            async with t._fetched("u") as clip:
                captured["dir"] = os.path.dirname(clip.path)
                raise ValueError("worker blew up")
        assert not os.path.exists(captured["dir"])

    async def test_audio_only_is_forwarded(self, monkeypatch, _temp_root):
        seen = {}

        async def _fetch(url, *, dest_dir=None, audio_only=False, **kw):
            seen["audio_only"] = audio_only
            target = os.path.join(dest_dir, "a.m4a")
            Path(target).write_bytes(b"a")
            return target

        monkeypatch.setattr(t.engine, "fetch_file_async", _fetch)
        async with t._fetched("u", audio_only=True):
            pass
        assert seen["audio_only"] is True


# ── keep(): a path we print must outlive the directory ────────────────────────


class TestKeep:
    async def test_a_kept_file_survives_cleanup(self, monkeypatch, _temp_root, tmp_path):
        monkeypatch.setattr(
            "navig.platform.paths.media_dir", lambda kind: tmp_path / "media" / kind
        )
        _patch_fetch(monkeypatch)
        async with t._fetched("u") as clip:
            workdir = os.path.dirname(clip.path)
            saved = clip.keep("videos")
        assert not os.path.exists(workdir)
        assert os.path.exists(saved), "we printed a path and then deleted it"

    async def test_kept_files_land_somewhere_durable(
        self, monkeypatch, _temp_root, tmp_path
    ):
        """A temp path is not somewhere to send a person — the OS may reap it."""
        media = tmp_path / "media"
        monkeypatch.setattr("navig.platform.paths.media_dir", lambda kind: media / kind)
        _patch_fetch(monkeypatch)
        async with t._fetched("u") as clip:
            saved = clip.keep("videos")
        assert Path(saved).parent == media / "videos"

    async def test_a_failed_move_still_leaves_a_real_file_at_the_printed_path(
        self, monkeypatch, _temp_root
    ):
        """The download succeeded; only the relocation failed. Raising here would
        put the caller in its "Couldn't download that video" branch and report a
        failure that did not happen — and the printed path must still be real."""

        def _boom(kind):
            raise OSError("read-only filesystem")

        monkeypatch.setattr("navig.platform.paths.media_dir", _boom)
        _patch_fetch(monkeypatch)
        async with t._fetched("u") as clip:
            saved = clip.keep("videos")
        assert saved, "keep() must return a path, never None"
        assert os.path.exists(saved), "we printed a path and then deleted it"

    async def test_when_nothing_can_be_relocated_the_directory_is_kept(
        self, monkeypatch, _temp_root
    ):
        """The last resort: leaving a directory behind beats deleting the file we
        just told the operator to go and get."""

        def _boom(*a, **kw):
            raise OSError("nowhere to move to")

        monkeypatch.setattr("navig.platform.paths.media_dir", _boom)
        monkeypatch.setattr(t.shutil, "move", _boom)
        _patch_fetch(monkeypatch)
        async with t._fetched("u") as clip:
            workdir = clip.workdir
            saved = clip.keep("videos")
        assert clip.detached is True
        assert os.path.exists(saved)
        assert os.path.isdir(workdir), "the kept file's directory was removed anyway"


# ── the sweep collects what best-effort cleanup leaves behind ─────────────────


class TestSweep:
    def test_stale_directories_are_removed(self, _temp_root):
        stale = _temp_root / f"{t._TMP_PREFIX}old"
        stale.mkdir()
        (stale / "junk.mp4").write_bytes(b"x")
        os.utime(stale, (time.time() - t._SWEEP_AGE_S - 60,) * 2)

        t._sweep_stale_tempdirs()

        assert not stale.exists()

    def test_a_recent_directory_is_left_alone(self, _temp_root):
        """It could be a download in flight in another process."""
        fresh = _temp_root / f"{t._TMP_PREFIX}new"
        fresh.mkdir()

        t._sweep_stale_tempdirs()

        assert fresh.exists()

    def test_nothing_outside_our_prefix_is_touched(self, _temp_root):
        """This deletes from the SYSTEM temp dir — scope is the whole safety story."""
        for name in ("tmpsomethingelse", "important", "navig_other_thing"):
            other = _temp_root / name
            other.mkdir()
            os.utime(other, (time.time() - t._SWEEP_AGE_S - 60,) * 2)

        t._sweep_stale_tempdirs()

        assert sorted(p.name for p in _temp_root.iterdir()) == [
            "important", "navig_other_thing", "tmpsomethingelse",
        ]

    def test_a_stale_FILE_with_our_prefix_is_not_deleted(self, _temp_root):
        """We only ever create directories; a file by that name is not ours."""
        f = _temp_root / f"{t._TMP_PREFIX}notadir"
        f.write_bytes(b"x")
        os.utime(f, (time.time() - t._SWEEP_AGE_S - 60,) * 2)

        t._sweep_stale_tempdirs()

        assert f.exists()

    def test_it_runs_once_per_process(self, _temp_root, monkeypatch):
        """Once per action would stat the whole temp dir on every tap."""
        calls = {"n": 0}
        real = Path.glob

        def _counting(self, pattern):
            if pattern.startswith(t._TMP_PREFIX):
                calls["n"] += 1
            return real(self, pattern)

        monkeypatch.setattr(Path, "glob", _counting)
        for _ in range(3):
            t._sweep_stale_tempdirs()
        assert calls["n"] == 1

    def test_a_sweep_failure_never_propagates(self, monkeypatch):
        """Housekeeping must not break a tap the user is waiting on."""
        monkeypatch.setattr(
            t.tempfile, "gettempdir", lambda: (_ for _ in ()).throw(OSError("nope"))
        )
        t._sweep_stale_tempdirs()  # must not raise
