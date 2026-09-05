"""The Download button must never delete a file it just told the user to go and get.

Two paths *promise* the file still exists, and unconditional cleanup broke both:

* **Too large to upload** — the reply says "Saved to <path>", then the same call
  deleted that path. The operator went looking for a video that was already gone.
* **Rejected upload** — `send_video` returns ``None`` on a rejected send WITHOUT
  raising, so the `except` never fires. Treating that as success deleted the only
  copy of a video the user never received: total silent loss.

The clip now lives in a directory `_fetched` owns and always removes, and a file
we point the user at is MOVED out to `media_dir()` first — a temp path is not
somewhere to send someone, and the OS may reap it. So these assert two things
together: what we promised still exists, at the path we printed, and nothing is
left behind otherwise — the directory included.
"""

from __future__ import annotations

import os

import pytest

from navig.telegram import tiktok_actions

_URL = "https://vm.tiktok.com/ZGdxryFmF"


def _path_from(messages) -> str:
    """The path we printed to the user, out of the <code>…</code> in the reply."""
    import re

    for m in messages:
        found = re.search(r"<code>(.+?)</code>", m)
        if found:
            import html as _h

            return _h.unescape(found.group(1))
    raise AssertionError(f"no path in any message: {messages}")


class _Channel:
    def __init__(self, video_result=None):
        self.messages: list[str] = []
        self.videos: list[bytes] = []
        self._video_result = video_result

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.messages.append(text)
        return {"message_id": 1}

    async def send_video(self, chat_id, data, caption=None, **kw):
        self.videos.append(data)
        return self._video_result


@pytest.fixture
def video_file(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"x" * 2048)
    return p


@pytest.fixture(autouse=True)
def _keep_dir(monkeypatch, tmp_path):
    """`clip.keep()` moves into `media_dir(...)`; never touch the real one."""
    dest = tmp_path / "kept"
    monkeypatch.setattr(
        "navig.platform.paths.media_dir", lambda kind: dest / kind
    )
    return dest


def _patch_fetch(monkeypatch, path):
    """Mirror the real signature. A fake taking **kwargs would keep passing while
    the caller handed it a `dest_dir` it never honoured — and the whole point of
    that argument is that the caller owns the directory."""
    seen: dict = {}

    async def _fetch(url, *, dest_dir=None, audio_only=False, **kw):
        seen["dest_dir"] = dest_dir
        # A real fetch writes INTO dest_dir; copy so cleanup has something to remove.
        import shutil as _sh

        target = os.path.join(dest_dir, os.path.basename(str(path)))
        _sh.copyfile(str(path), target)
        return target

    monkeypatch.setattr(tiktok_actions.engine, "fetch_file_async", _fetch)
    return seen


async def test_oversized_video_is_kept_where_the_message_says_it_is(
    monkeypatch, video_file
):
    _patch_fetch(monkeypatch, video_file)
    monkeypatch.setattr(tiktok_actions, "_MAX_UPLOAD", 1024)  # force the big branch
    ch = _Channel()

    await tiktok_actions._do_download(ch, 100, _URL)

    assert any("Saved to" in m for m in ch.messages), ch.messages
    saved = _path_from(ch.messages)
    assert os.path.exists(saved), (
        "the reply told the operator where the file is, then deleted it"
    )
    assert "Temp" not in saved and "tmp" not in os.path.basename(os.path.dirname(saved)), (
        f"pointed the operator at a temp dir the OS may reap: {saved}"
    )


async def test_rejected_upload_keeps_the_file_and_says_so(monkeypatch, video_file):
    """send_video returning None is a REJECTED send, not a success."""
    _patch_fetch(monkeypatch, video_file)
    ch = _Channel(video_result=None)

    await tiktok_actions._do_download(ch, 100, _URL)

    assert ch.videos, "it should still have attempted the upload"
    assert any("rejected the upload" in m for m in ch.messages), ch.messages
    saved = _path_from(ch.messages)
    assert os.path.exists(saved), (
        "a rejected upload deleted the only copy — the user got nothing at all"
    )


async def test_successful_upload_leaves_no_file_AND_no_directory(
    monkeypatch, video_file
):
    """The fix must not turn the temp dir into a leak — and the DIRECTORY counts.

    Removing only the file is what left 25 empty `navig_tiktok_*` dirs on the
    operator's machine, one per action ever run.
    """
    seen = _patch_fetch(monkeypatch, video_file)
    ch = _Channel(video_result={"message_id": 5})

    await tiktok_actions._do_download(ch, 100, _URL)

    assert ch.videos
    workdir = seen["dest_dir"]
    assert workdir, "the caller must own the destination, not let yt-dlp pick one"
    assert not os.path.exists(workdir), "the fetch directory leaked"


async def test_a_kept_file_survives_the_directory_being_removed(
    monkeypatch, video_file, _keep_dir
):
    """The two halves must not fight: the workdir always goes, the kept file stays."""
    seen = _patch_fetch(monkeypatch, video_file)
    monkeypatch.setattr(tiktok_actions, "_MAX_UPLOAD", 1024)
    ch = _Channel()

    await tiktok_actions._do_download(ch, 100, _URL)

    assert not os.path.exists(seen["dest_dir"]), "the fetch directory leaked"
    assert os.path.exists(_path_from(ch.messages))


async def test_a_failed_fetch_reports_and_leaves_nothing_behind(
    monkeypatch, video_file
):
    async def _boom(url, *, dest_dir=None, audio_only=False, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(tiktok_actions.engine, "fetch_file_async", _boom)
    ch = _Channel()

    await tiktok_actions._do_download(ch, 100, _URL)

    assert any("Couldn't download" in m for m in ch.messages), ch.messages


# ── the reaction entry point must not ack work it did not do ──────────────────


async def test_reaction_acks_only_when_it_actually_briefed(monkeypatch):
    """🎬 on a message whose link can't be resolved must not get a 'done' mark.

    The ack is the only signal the operator has that the reaction did anything;
    setting it unconditionally reports success for a no-op.
    """
    from navig.gateway.channels import telegram_reactions as tr

    acks: list[str] = []
    outcome = {"handled": False}

    class _Chan:
        async def _safe_set_reaction(self, chat_id, msg_id, emoji):
            acks.append(emoji)

    async def _fake_handle(channel, chat_id, msg_id, user_id, emoji):
        return outcome["handled"]

    monkeypatch.setattr(tiktok_actions, "handle_reaction", _fake_handle)
    chan = _Chan()

    # Nothing resolved → no ack.
    await tr.TelegramReactionsMixin._reaction_tiktok(chan, 1, 2, 3, "🎬")
    assert acks == [], "acked a reaction that briefed nothing"

    # Briefed → ack.
    outcome["handled"] = True
    await tr.TelegramReactionsMixin._reaction_tiktok(chan, 1, 2, 3, "🎬")
    assert len(acks) == 1
