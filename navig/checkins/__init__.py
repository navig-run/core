"""Check-ins — the bot asks, you answer in one tap (or one number).

One engine, three templates:

* **Weigh-in** (``number``) — the morning weight. Behaviour is the one
  ``navig body checkin`` always had; the record stays ``metrics.csv``.
* **Pills** (``dose``) — per medication and time of day: Taken · Skip · Snooze ·
  Taken earlier. Never auto-marked taken, never advice.
* **Water** (``count``) — glasses against a daily target, ``+1`` / ``+2`` taps.

Not habits on purpose: a habit is done-or-not, a check-in is a *measurement* or a
*dose* — different records, different safety rules.

Layout:

* :mod:`.defs`     — the templates (:class:`CheckinDef`) and configured items
* :mod:`.cadence`  — slot times, the local calendar, DST-safe instants
* :mod:`.state`    — open prompts keyed by (chat, message id), asked/nudged marks
* :mod:`.records`  — ``checkins.jsonl`` (pills + water), adherence and totals
* :mod:`.cards`    — the Telegram text + keyboards
* :mod:`.engine`   — scheduled runs, taps, typed replies, the gateway tick
* :mod:`.jobs`     — the ``checkin:*`` cron jobs and the legacy-job migration

Stdlib-only at import time; every navig import is function-scoped.
"""
