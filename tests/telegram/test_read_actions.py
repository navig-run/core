"""``/read`` over Telegram — the bedtime reader's door.

The reading engine is the navig-audio plugin and is tested there (real ffmpeg, a fake
provider). What belongs here is everything that makes it reachable and safe from a chat:

* the gestures — ``/read <text>``, a ``.txt`` with the caption ``/read``, ``stop`` / ``go`` /
  ``status`` — and that ``go home now`` is read, not mistaken for ``go``;
* text files in the code page they were saved in (a Russian .txt from an older Windows
  editor is cp1251 — read as UTF-8 it becomes a chapter of ``�``);
* delivery: a rejected send returns ``None`` WITHOUT raising, and must count as a failure;
* the words, in the languages the owner writes — rendered in ru/fr here; that every key exists
  in en/ru/fr is ``test_read_locale_keys`` (split out: it reads the locale files by path, so it
  is wired into the fast gate on its own);
* and that the gate in ``_process_update`` is really there and runs before the generic
  document handling (a unit-tested module nothing calls is the defect this repo keeps
  finding).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from navig.telegram import read_actions as ra


@pytest.fixture
def engine():
    """The real plugin classes (Outcome, StartResult, plans) — skipped, never faked, when
    the plugin is not installed on this machine."""
    pytest.importorskip("navig_audio")
    from navig_audio import reader, reading_job

    return SimpleNamespace(reader=reader, job=reading_job)


class FakeChannel:
    """Records what the bot would send; ``audio_result`` None simulates a REJECTED send."""

    def __init__(self):
        self.sent: list[str] = []
        self.audio: list[dict] = []
        self.audio_result: dict | None = {"message_id": 7}

    async def send_message(self, chat_id, text, parse_mode=None, **kw):
        self.sent.append(text)
        return {"message_id": len(self.sent)}

    async def send_audio(self, chat_id, data, caption=None, filename=None, title=None, performer=None, **kw):
        self.audio.append(
            {"chat_id": chat_id, "bytes": len(data), "caption": caption, "filename": filename,
             "title": title, "performer": performer}
        )
        return self.audio_result


@pytest.fixture
def chan():
    return FakeChannel()


def _plan(engine, text="Once upon a time there was a king. " * 40):
    return engine.reader.plan_reading(text, engine.reader.ReadOptions(lang="en"))


@pytest.fixture
def jobs(engine, monkeypatch):
    """Replace the job layer's entry points with recorders; each returns what the test sets."""
    state = SimpleNamespace(
        begin_calls=[], confirm_calls=[], stop_calls=[], discard_calls=[],
        result=None, stop_result=False, discard_result=False, current=None, pending=None,
    )

    async def begin(key, text, options, **kw):
        state.begin_calls.append({"key": key, "text": text, "options": options, **kw})
        return state.result

    async def confirm(key, **kw):
        state.confirm_calls.append({"key": key, **kw})
        return state.result

    monkeypatch.setattr(engine.job, "begin", begin)
    monkeypatch.setattr(engine.job, "confirm", confirm)
    monkeypatch.setattr(engine.job, "stop", lambda key: (state.stop_calls.append(key), state.stop_result)[1])
    monkeypatch.setattr(engine.job, "discard_pending", lambda key: (state.discard_calls.append(key), state.discard_result)[1])
    monkeypatch.setattr(engine.job, "current", lambda key: state.current)
    monkeypatch.setattr(engine.job, "pending_plan", lambda key: state.pending)
    return state


# ── intake ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        ("/read", True), ("/read go", True), ("/READ@NavigBot", True), ("/book", True),
        ("  /read  ", True), ("/reader", False), ("read", False), ("", False), (None, False),
        ("/courrier", False), ("please /read this", False),
    ],
)
def test_the_caption_gesture(caption, expected):
    assert ra.is_read_caption(caption or "") is expected


def test_a_read_caption_without_a_document_is_not_a_read_intake():
    assert ra.wants_read({"caption": "/read"}) is False
    assert ra.wants_read({"document": {"file_id": "x"}, "caption": "/read"}) is True
    assert ra.wants_read({"document": {"file_id": "x"}, "caption": "/courrier"}) is False
    assert ra.wants_read({"document": {"file_id": "x"}}) is False


def test_the_job_key_is_per_chat():
    assert ra.job_key(42) == "telegram:42"


# ── commands ──────────────────────────────────────────────────────────────────


async def test_bare_read_explains_the_gestures(engine, chan, jobs):
    await ra.handle_command(chan, 42, "")
    help_text = chan.sent[0]
    for gesture in ("/read <text>", "/read stop", "/read go", "caption /read"):
        assert gesture in help_text, f"the help does not mention {gesture!r}"
    assert jobs.begin_calls == []


async def test_a_text_after_read_starts_a_reading_for_that_chat(engine, chan, jobs):
    plan = _plan(engine)
    jobs.result = engine.job.StartResult(engine.job.Outcome.STARTED, plan=plan, spend=plan.credits)
    await ra.handle_command(chan, 42, "Once upon a time there was a king.")

    call = jobs.begin_calls[0]
    assert call["key"] == "telegram:42" and call["text"] == "Once upon a time there was a king."
    assert call["confirmed"] is False
    started = chan.sent[0]
    assert str(plan.part_count) in started and "/read stop" in started


async def test_a_multi_word_text_starting_with_go_is_a_text_not_a_yes(engine, chan, jobs):
    """"go home now" is a sentence to read. Only the bare word `go` confirms."""
    jobs.result = engine.job.StartResult(engine.job.Outcome.STARTED, plan=_plan(engine))
    await ra.handle_command(chan, 42, "go home now, said the king")
    assert jobs.begin_calls and jobs.confirm_calls == []


@pytest.mark.parametrize("word", ["go", "yes", "да", "oui", "OK"])
async def test_a_bare_yes_confirms_the_pending_reading(engine, chan, jobs, word):
    plan = _plan(engine)
    jobs.result = engine.job.StartResult(engine.job.Outcome.STARTED, plan=plan)
    await ra.handle_command(chan, 42, word)
    assert jobs.confirm_calls and jobs.confirm_calls[0]["key"] == "telegram:42"
    assert jobs.begin_calls == [], "a yes must not start a new reading from the word 'go'"


async def test_a_yes_with_nothing_waiting_says_so(engine, chan, jobs):
    jobs.result = engine.job.StartResult(engine.job.Outcome.NOTHING_PENDING)
    await ra.handle_command(chan, 42, "go")
    assert "Nothing is waiting" in chan.sent[0]


@pytest.mark.parametrize("word", ["stop", "cancel", "стоп", "arrête"])
async def test_stop_cancels_a_running_reading(engine, chan, jobs, word):
    jobs.stop_result = True
    await ra.handle_command(chan, 42, word)
    assert jobs.stop_calls == ["telegram:42"] and "Stopping" in chan.sent[0]


async def test_stop_drops_a_reading_that_was_only_waiting_for_a_yes(engine, chan, jobs):
    jobs.stop_result, jobs.discard_result = False, True
    await ra.handle_command(chan, 42, "stop")
    assert "Nothing was spent" in chan.sent[0]


async def test_stop_with_nothing_to_stop_says_so(engine, chan, jobs):
    await ra.handle_command(chan, 42, "stop")
    assert "Nothing is being read" in chan.sent[0]


async def test_status_reports_a_running_reading_a_pending_one_and_nothing(engine, chan, jobs):
    jobs.current = SimpleNamespace(delivered=2, plan=SimpleNamespace(part_count=5))
    await ra.handle_command(chan, 42, "status")
    assert "2 of 5" in chan.sent[-1]
    jobs.current, jobs.pending = None, _plan(engine)
    await ra.handle_command(chan, 42, "status")
    assert "/read go" in chan.sent[-1]
    jobs.pending = None
    await ra.handle_command(chan, 42, "status")
    assert "Nothing is being read" in chan.sent[-1]


# ── every outcome has words ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("outcome", "needle"),
    [
        ("STARTED", "Reading to you"),
        ("NEEDS_CONFIRM", "/read go"),
        ("INSUFFICIENT", "credits"),
        ("BUSY", "already reading"),
        ("EMPTY", "Nothing to read"),
        ("NOTHING_PENDING", "Nothing is waiting"),
        ("UNAVAILABLE", "Could not read"),
    ],
)
async def test_every_outcome_is_answered_in_words(engine, chan, jobs, outcome, needle):
    jobs.result = engine.job.StartResult(
        getattr(engine.job.Outcome, outcome), plan=_plan(engine), spend=40_000, remaining=1_000, detail="boom"
    )
    await ra.handle_command(chan, 42, "some text")
    assert needle in chan.sent[0], chan.sent


async def test_the_quote_names_length_time_and_credits(engine, chan, jobs):
    plan = _plan(engine, "Sentence of the story goes here. " * 800)
    jobs.result = engine.job.StartResult(engine.job.Outcome.NEEDS_CONFIRM, plan=plan, spend=plan.credits)
    await ra.handle_command(chan, 42, "long text")
    quote = chan.sent[0]
    assert ra._money(plan.chars) in quote and ra._money(plan.credits) in quote and "min" in quote


# ── the plugin is optional ────────────────────────────────────────────────────


async def test_without_the_plugin_every_gesture_explains_instead_of_crashing(chan, monkeypatch):
    monkeypatch.setattr(ra, "_engine", lambda: None)
    await ra.handle_command(chan, 42, "some text")
    await ra.handle_command(chan, 42, "stop")
    await ra.handle_document(chan, 42, 1, {"document": {"file_id": "x", "file_name": "a.txt"}, "caption": "/read"})
    assert len(chan.sent) == 3 and all("navig-audio" in s for s in chan.sent)


# ── a text file ───────────────────────────────────────────────────────────────

RU = "Жили-были старик со старухой у самого синего моря. И не было у них ничего. " * 5


def _doc(name="story.txt", size=500, caption="/read"):
    return {"document": {"file_id": "F1", "file_name": name, "file_size": size}, "caption": caption}


@pytest.fixture
def download(monkeypatch):
    box = SimpleNamespace(data=b"hello there, a short story.", error=None, calls=[])

    async def fake(channel, file_id, limit):
        box.calls.append((file_id, limit))
        if box.error:
            raise box.error
        return box.data

    monkeypatch.setattr(ra, "_download_bytes", fake)
    return box


async def test_a_text_file_is_downloaded_decoded_and_read(engine, chan, jobs, download):
    jobs.result = engine.job.StartResult(engine.job.Outcome.STARTED, plan=_plan(engine))
    await ra.handle_document(chan, 42, 1, _doc())
    assert download.calls == [("F1", ra.FILE_LIMIT)]
    assert jobs.begin_calls[0]["text"] == "hello there, a short story."
    assert jobs.begin_calls[0]["confirmed"] is False


@pytest.mark.parametrize("encoding", ["utf-8", "cp1251", "utf-16"])
async def test_a_russian_file_in_any_common_code_page_arrives_intact(engine, chan, jobs, download, encoding):
    """cp1251 is what an older Windows editor writes. Read as UTF-8 it is a chapter of
    replacement characters — which a voice then reads aloud, at a cost per character."""
    download.data = RU.encode(encoding)
    jobs.result = engine.job.StartResult(engine.job.Outcome.STARTED, plan=_plan(engine))
    await ra.handle_document(chan, 42, 1, _doc())
    assert jobs.begin_calls[0]["text"].startswith("Жили-были старик"), jobs.begin_calls[0]["text"][:40]
    assert "�" not in jobs.begin_calls[0]["text"]


async def test_read_go_on_the_caption_confirms_a_big_one_up_front(engine, chan, jobs, download):
    jobs.result = engine.job.StartResult(engine.job.Outcome.STARTED, plan=_plan(engine))
    await ra.handle_document(chan, 42, 1, _doc(caption="/read go"))
    assert jobs.begin_calls[0]["confirmed"] is True


async def test_other_file_types_are_declined_before_any_download(engine, chan, jobs, download):
    await ra.handle_document(chan, 42, 1, _doc(name="book.pdf"))
    assert ".txt" in chan.sent[0] and "not supported" in chan.sent[0], "say what IS accepted"
    assert download.calls == [] and jobs.begin_calls == []


async def test_an_oversize_file_is_declined_before_any_download(engine, chan, jobs, download):
    await ra.handle_document(chan, 42, 1, _doc(size=5 * 1024 * 1024))
    assert "limit" in chan.sent[0] and download.calls == []


async def test_a_failed_download_is_reported_not_swallowed(engine, chan, jobs, download):
    download.error = RuntimeError("HTTP 500")
    await ra.handle_document(chan, 42, 1, _doc())
    assert "could not download" in chan.sent[0] and jobs.begin_calls == []


@pytest.mark.parametrize("name", ["a.TXT", "b.md", "c.markdown", "d.text"])
async def test_the_accepted_extensions(engine, chan, jobs, download, name):
    jobs.result = engine.job.StartResult(engine.job.Outcome.STARTED, plan=_plan(engine))
    await ra.handle_document(chan, 42, 1, _doc(name=name))
    assert jobs.begin_calls, f"{name} should be accepted"


# ── delivery ──────────────────────────────────────────────────────────────────


def _part(engine, tmp_path, index=1, total=3):
    path = tmp_path / f"part{index}.mp3"
    path.write_bytes(b"\x00" * 2048)
    return engine.reader.ReadPart(index=index, total=total, path=path, duration_s=598.0, chars=9000)


async def test_a_part_arrives_as_an_audio_track_titled_and_captioned(engine, chan, tmp_path):
    ok = await ra._deliver_to(chan, 42)(_part(engine, tmp_path, 2, 3))
    sent = chan.audio[0]
    assert ok is True
    assert (sent["chat_id"], sent["bytes"], sent["filename"]) == (42, 2048, "part2.mp3")
    assert sent["title"] == "2/3" and sent["performer"] == "NAVIG"
    assert sent["caption"] == "📖 2/3 · 9:58"


async def test_a_rejected_send_counts_as_a_failure(engine, chan, tmp_path):
    """Telegram's transport returns None WITHOUT raising when it refuses — success there
    would tell the job a part was heard that never arrived."""
    chan.audio_result = None
    assert await ra._deliver_to(chan, 42)(_part(engine, tmp_path)) is False


async def test_only_the_end_of_a_reading_is_announced_not_every_part(engine, chan, tmp_path):
    notify = ra._notify_to(chan, 42)
    part = _part(engine, tmp_path)
    await notify(engine.job.ReadEvent("part", part=part))
    assert chan.sent == [], "the audio itself is the message for a part"
    summary = engine.reader.ReadSummary(out_dir=tmp_path, parts=[part])
    await notify(engine.job.ReadEvent("done", summary=summary))
    assert chan.sent == ["✅ That's all — 1 part(s)."]


@pytest.mark.parametrize(
    ("kind", "needle"),
    [("cancelled", "Stopped after"), ("partial", "cached and free"), ("failed", "Could not read")],
)
def test_the_other_endings_have_words(engine, tmp_path, kind, needle):
    part = _part(engine, tmp_path)
    summary = engine.reader.ReadSummary(out_dir=tmp_path, parts=[part], error="boom")
    text = ra.describe_event(engine.job.ReadEvent(kind, summary=summary, detail="boom"))
    assert needle in text


@pytest.mark.parametrize(("lang", "needle"), [("ru", "Это всё"), ("fr", "C'est tout")])
def test_the_ending_is_said_in_the_owners_language(engine, tmp_path, monkeypatch, lang, needle):
    from navig.core import i18n

    monkeypatch.setattr(i18n, "current_language", lambda: lang)
    i18n.shared().reset()
    try:
        part = _part(engine, tmp_path)
        summary = engine.reader.ReadSummary(out_dir=tmp_path, parts=[part])
        assert needle in ra.describe_event(engine.job.ReadEvent("done", summary=summary))
    finally:
        i18n.shared().reset()


# ── it is wired ───────────────────────────────────────────────────────────────


def test_the_slash_commands_exist_and_route_to_the_handler():
    from navig.gateway.channels import telegram_commands as tc

    entries = {e.command: e for e in tc._SLASH_REGISTRY}
    assert entries["read"].handler == "_handle_read" and entries["read"].visible
    assert entries["book"].handler == "_handle_read" and not entries["book"].visible
    assert hasattr(tc.TelegramCommandsMixin, "_handle_read")


@pytest.mark.parametrize(
    ("text", "argument"),
    [
        ("/read", ""),
        ("/read hello", "hello"),
        ("/read@NavigBot stop", "stop"),
        ("/read\nA long\npasted story", "A long\npasted story"),
        ("/read    spaced   out ", "spaced   out"),
    ],
)
async def test_the_command_hands_the_argument_through_intact(monkeypatch, text, argument):
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    seen = []

    async def fake(channel, chat_id, arg):
        seen.append(arg)

    monkeypatch.setattr(ra, "handle_command", fake)
    await TelegramCommandsMixin._handle_read(SimpleNamespace(), 42, text=text)
    assert seen == [argument]
