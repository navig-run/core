"""The orphan "💭 Thinking…" bubble — it must be deleted, never left stuck.

Measured 2026-10: every deep answer left a permanent "💭 Thinking…" bubble
above the reply. The deep path renders a progress bubble; when streaming also
created its own placeholder, the STREAM bubble was finalized into the answer
(`_ph_id` is not None → the progress bubble is never reused) and nothing ever
deleted the progress bubble.

The cleanup in `_handle_message_common` hangs on one pure decision —
`_orphan_progress_id(progress, _ph_id)` — and these tests pin every branch of
it: reuse must NOT delete (that would delete the answer), a different id MUST,
and a missing/absent bubble is a no-op.
"""

from __future__ import annotations

from types import SimpleNamespace

from navig.gateway.channels.telegram import _orphan_progress_id


def test_no_progress_bubble_is_a_noop():
    assert _orphan_progress_id(None, 111) is None


def test_progress_bubble_that_was_never_sent_is_a_noop():
    """The renderer creates no message until the first thinking beat."""
    progress = SimpleNamespace(message_id=None)
    assert _orphan_progress_id(progress, None) is None


def test_reused_progress_bubble_is_the_answer_and_must_not_be_deleted():
    """The common case: streaming never created its own placeholder, so the
    progress bubble WAS reused as the answer (`_ph_id == progress.message_id`).
    Deleting it here would delete the reply."""
    progress = SimpleNamespace(message_id=111)
    assert _orphan_progress_id(progress, 111) is None


def test_a_separate_stream_placeholder_makes_the_progress_bubble_an_orphan():
    """THE bug: streaming created its own placeholder (finalized into the
    answer), the progress bubble was never reused nor deleted — it is an
    orphan and must be cleaned up."""
    progress = SimpleNamespace(message_id=111)
    assert _orphan_progress_id(progress, 222) == 111


def test_orphan_is_returned_even_without_a_stream_placeholder():
    """No response at all: the elif branch deletes `_ph_id` (the progress
    bubble itself when reused), but a separate progress bubble alongside a
    missing stream placeholder must still be reported."""
    progress = SimpleNamespace(message_id=111)
    assert _orphan_progress_id(progress, None) == 111


def test_message_id_is_returned_as_int():
    """Telegram ids may arrive as str from a mock/serializer; the caller
    hands the value straight to delete_message, so normalize once here."""
    progress = SimpleNamespace(message_id="111")
    assert _orphan_progress_id(progress, 222) == 111
