"""Tests for the navig media *analysis* half — frames / probe / briefing / CLI.

The generation half has its own tests (test_generation_service*, test_processing, …).
Frame/probe/briefing tests need ffmpeg; they skip cleanly when it's absent.
"""
from __future__ import annotations

import subprocess

import pytest

from navig.media.frames import extract_frames, ffmpeg_available, probe

_HAS_FFMPEG = ffmpeg_available()
_SKIP = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not installed")


@pytest.fixture
def clip(tmp_path):
    """A 2s silent test video (no audio track → transcript stays empty, keeps tests fast)."""
    if not _HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    p = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=2:size=320x240:rate=10", str(p)],
        check=True, timeout=60,
    )
    return p


def test_ffmpeg_available_returns_bool():
    assert isinstance(ffmpeg_available(), bool)


def test_extract_frames_missing_ffmpeg_returns_empty(tmp_path, monkeypatch):
    # force the "no ffmpeg" branch regardless of the host
    monkeypatch.setattr("navig.media.frames.shutil.which", lambda _: None)
    assert extract_frames(tmp_path / "nope.mp4", tmp_path / "frames") == []


@_SKIP
def test_probe_reads_metadata(clip):
    m = probe(clip)
    assert m["width"] == 320 and m["height"] == 240
    assert m["duration"] > 0 and m["fps"] == 10.0


@_SKIP
def test_extract_frames_interval(clip, tmp_path):
    frames = extract_frames(clip, tmp_path / "frames", mode="interval", interval=0.5, max_frames=10)
    assert len(frames) >= 1
    assert all(f.suffix == ".jpg" and f.is_file() for f in frames)


@_SKIP
def test_extract_frames_scene_falls_back_to_interval(clip, tmp_path):
    # testsrc is near-static → scene detection yields ~0 cuts → the fallback still returns frames
    frames = extract_frames(clip, tmp_path / "frames", mode="scene", max_frames=10)
    assert len(frames) >= 1


@_SKIP
def test_build_briefing_video(clip, tmp_path):
    from navig.media.briefing import build_briefing
    res = build_briefing(clip, tmp_path / "out", profile="generic", max_frames=6)
    assert res["kind"] == "video"
    assert res["frames"] >= 1
    briefing = res["briefing"]
    assert briefing.is_file()
    text = briefing.read_text(encoding="utf-8")
    assert text.startswith("# Briefing")
    assert "## Frames" in text


def test_media_cli_has_analysis_and_generation_commands():
    from navig.commands.media import media_app
    names = {c.name for c in media_app.registered_commands}
    assert {"analyze", "frames", "probe"} <= names          # analysis half
    assert {"ingest", "keep", "reject"} <= names            # generation passthroughs


def test_media_registered_in_cli_map():
    from navig.cli.registration import _EXTERNAL_CMD_MAP
    assert _EXTERNAL_CMD_MAP.get("media") == ("navig.commands.media", "media_app")
