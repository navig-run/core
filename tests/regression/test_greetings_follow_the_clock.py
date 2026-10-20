"""A message that can fire at any hour must not name an hour.

Reported 2026-09-27, in the evening: *"why does he say this — it's not day,
it's evening already"*. Three messages, none of them time-aware:

* 20:47 — "Доброе утро, навигатор…" — a BOOT greeting. The boot pool is picked
  on every boot whatever the hour, and one of its ten lines said good morning.
* 21:18 — "Системы в строю…" — another boot greeting, from another restart.
  daemon.log shows EIGHT boots that day, mostly agents deploying fixes; the
  cross-process de-dupe window was five minutes, so each boot spaced wider than
  that greeted the operator.
* 19:33 — "🟡 ☀️ NAVIG — Приветствие" — an engagement greeting whose header
  emoji came from the `routine` type, which was ☀️ at every hour.

The guard below scans every pool that is chosen WITHOUT looking at the clock,
in every locale plus the English fallback, so a time-of-day word cannot come
back through a translation.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from navig import boot_messages
from navig.gateway.notifications import Notification, routine_emoji

LOCALES = Path(boot_messages.__file__).parent / "locales"

#: Pools picked with no regard for the time. The engagement.greeting.<part-of-day>
#: pools are exempt by construction — the clock chose them.
TIMELESS = (
    "boot.messages",
    "engagement.greeting.returning",
    "engagement.greeting.default",
    "engagement.checkin",
    "engagement.idle_nudge",
    "engagement.feedback_ask",
    "engagement.title.",
)

TIME_WORDS = re.compile(
    r"\b(good morning|morning|evening|tonight|afternoon|good ?night|"
    r"утр\w*|вечер\w*|ноч\w*|днём|добрый день|спокойной|"
    r"bonjour|bonsoir|matin\w*|soir\w*|nuit)\b",
    re.I,
)


def _timeless_lines():
    for path in sorted(LOCALES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in data.items():
            if not key.startswith(TIMELESS):
                continue
            for line in value if isinstance(value, list) else [value]:
                yield path.name, key, str(line)
    for line in boot_messages.NAVIG_BOOT_MESSAGES:
        yield "boot_messages.py", "NAVIG_BOOT_MESSAGES", line


def test_the_scan_reads_real_pools():
    """A guard that finds no pools passes having checked nothing."""
    lines = list(_timeless_lines())
    locales = {name for name, _, _ in lines if name.endswith(".json")}
    assert len(locales) >= 3, f"expected en/fr/ru at least, read {sorted(locales)}"
    boot = [ln for _, key, ln in lines if key == "boot.messages"]
    assert len(boot) >= 3 * 10, f"boot pools look empty: {len(boot)} lines"


def test_no_any_hour_message_names_a_time_of_day():
    offenders = [
        f"{name} {key}: {line}" for name, key, line in _timeless_lines() if TIME_WORDS.search(line)
    ]
    assert not offenders, "picked at any hour, but names one:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(5, "🌙"), (6, "☀️"), (12, "☀️"), (17, "☀️"), (18, "🌙"), (19, "🌙"), (23, "🌙"), (0, "🌙")],
)
def test_the_routine_emoji_follows_the_hour(hour, expected):
    assert routine_emoji(hour) == expected


def test_an_evening_greeting_is_not_headed_by_a_sun():
    """The 19:33 message, rebuilt."""
    note = Notification(
        type="routine",
        title="NAVIG — Приветствие",
        message="Снова в деле. Я приглядывал — всё чисто.",
        created_at=datetime(2026, 9, 27, 19, 33),
    )
    header = note.to_telegram_message().splitlines()[0]
    assert "☀️" not in header, header
    assert "🌙" in header, header


def test_a_day_of_restarts_greets_once(tmp_path, monkeypatch):
    """Eight boots on 2026-09-27; the first greets, a restart 30 minutes later
    stays quiet, and a boot after a night off greets again."""
    monkeypatch.setattr(boot_messages, "_marker_path", lambda: tmp_path / "boot_greeting.claim")
    monkeypatch.setattr(boot_messages, "_dedupe_sec", lambda: boot_messages.DEFAULT_DEDUPE_SEC)
    t0 = 1_790_000_000.0

    assert boot_messages.claim_boot_greeting(now=t0) is True
    assert boot_messages.claim_boot_greeting(now=t0 + 30 * 60) is False, "a restart is not news"
    assert boot_messages.claim_boot_greeting(now=t0 + 5 * 3600) is False
    assert boot_messages.claim_boot_greeting(now=t0 + 7 * 3600) is True, (
        "after a night off, say hello"
    )
