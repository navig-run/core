"""A scheduled digest card must wait until the reply it would interrupt is done.

Incident 2026-10-22: the review card arrived at 08:30 while the operator's 08:29
question was still being answered at 08:32 — a scheduled message visually replaced
the answer. The channel marks a chat busy for the duration of an inbound message;
digest (SUMMARY) cards are parked and drained when the turn ends.
"""

from __future__ import annotations

from navig.telegram import cards, turn_state


def _screen(title: str = "NAVIG · 2 task(s) due", *, kind: str = cards.SUMMARY) -> cards.Screen:
    return cards.Screen(
        title=title,
        body="two overdue tasks",
        kind=kind,
        rows=((cards.CardButton("Done", action="done"),),),
    )


def test_a_marked_chat_is_in_flight_until_it_finishes():
    turn_state.mark_started(424242)
    try:
        assert turn_state.in_flight(424242) is True
    finally:
        turn_state.mark_finished(424242)
    assert turn_state.in_flight(424242) is False


def test_a_stale_marker_from_a_crashed_worker_is_ignored():
    turn_state.mark_started(424243, now=1000.0)
    try:
        assert turn_state.in_flight(424243, now=1000.0 + turn_state.TURN_STALE_S + 1) is False
    finally:
        turn_state.mark_finished(424243)


def test_a_deferred_card_round_trips_through_the_state_file():
    screen = _screen()
    context = cards.Context("nav", "review")
    chat = 424244

    assert turn_state.defer_summary(chat, screen, context) == 1
    pending = turn_state.take_deferred(chat)
    assert len(pending) == 1

    rebuilt_screen, rebuilt_context = turn_state.build_card(pending[0])
    assert rebuilt_screen.title == screen.title
    assert rebuilt_screen.kind == cards.SUMMARY
    assert rebuilt_context.prefix == "nav" and rebuilt_context.key == "review"
    assert rebuilt_screen.rows[0][0].text == "Done"
    assert rebuilt_screen.rows[0][0].action == "done"
    assert turn_state.take_deferred(chat) == []


def test_the_pending_queue_is_bounded():
    chat = 424245
    for index in range(12):
        turn_state.defer_summary(chat, _screen(title=f"review {index}"), cards.Context("nav", "review"))
    pending = turn_state.take_deferred(chat)
    assert len(pending) == 10
    assert pending[-1]["title"] == "review 11"


def test_a_corrupt_state_file_costs_nothing(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(turn_state, "_path", lambda: state / "turn_state.json")
    state.mkdir(parents=True)
    (state / "turn_state.json").write_text("{not json", encoding="utf-8")

    assert turn_state.in_flight(1) is False
    assert turn_state.take_deferred(1) == []
