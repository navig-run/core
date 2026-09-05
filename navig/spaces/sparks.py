"""
Sparks — the short lines a tracker sends between reminders.

A reminder tells you to do a thing. A spark tells you why it was worth doing,
and it is the only message in the day that asks for nothing back. That is the
whole design: a notification with a task attached is a debt, and a day made of
debts is one you start avoiding by lunchtime.

Two rules the pool exists to enforce:

**Chosen, not random.** A generic quote at 15:30 is wallpaper. The same line
arriving on the afternoon the walk is still unmarked is a nudge — so selection
reads the tracker first and only falls back to the general pool when the day has
nothing to say. `pick()` is pure and takes the day's state as an argument, so the
logic is testable without a clock or a chat.

**Never twice in a fortnight.** A repeated line is worse than no line: it proves
nobody is home. Callers pass what was recently sent and the pool excludes it.

The lines live in the SPACE, not here — ``sparks.txt`` beside ``habits.csv`` —
because they are the owner's words to himself, and editing them must not mean
editing navig. The built-ins below are a working default, not the canon.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date
from pathlib import Path

SPARKS_FILE = "sparks.txt"

#: Category → when it fires. Order matters: the first match wins, so the
#: situation-specific buckets sit above the ambient ones.
CONTEXT_ORDER = ("recover", "walk", "ship", "weekend")
AMBIENT = ("body", "focus", "affirm")


def _key(line: str) -> str:
    """A short stable id for a line, so 'recently sent' survives file edits."""
    return hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:10]


# ── The default pool ──────────────────────────────────────────────────────────
#
# Russian on purpose. Every label on the card is English because a label is
# something you decode in a second at 22:15; a line meant to land emotionally is
# not a label, and it lands in the language you think in.

BUILTIN: dict[str, tuple[str, ...]] = {
    # Yesterday was empty. The one message that matters is that today is not.
    "recover": (
        "Вчера был ноль. Один ноль — это случайность. Два подряд — это уже привычка.",
        "Пропущенный день не ломает серию. Ломает второй.",
        "Сегодня не надо навёрстывать. Надо просто не пропустить второй раз.",
        "Ты не начинаешь заново. Ты продолжаешь с того места, где остановился.",
        "Плохой день — это не провал. Провал — это перестать записывать.",
    ),
    # The walk is still unmarked and the day is running out.
    "walk": (
        "Выйди на 30 минут. Не за продуктами. Просто выйди.",
        "Дом — это место, где ты работаешь. Не давай ему стать местом, где ты живёшь целиком.",
        "Прогулка — это не спорт. Это единственный способ, чтобы сегодня в дне были люди.",
        "Тридцать минут на улице сделают больше, чем ещё один час за экраном.",
        "Если день разваливается — сделай одно: выйди из дома. Остальное обычно возвращается.",
    ),
    # A weekday, the ship block hasn't happened, there's still time.
    "ship": (
        "Девяносто минут. Одна строка на бумаге: что будет ГОТОВО.",
        "Выпускается версия, за которую стыдно на 20%. Не на 80. Но и не идеальная.",
        "Идеальная версия не существует. Ожидание её — это способ не выпускать ничего.",
        "Сначала своё, потом клиентское. Наоборот не работает — клиентское не кончается.",
        "Готовый продукт — это не про код. Это про то, чтобы кто-то чужой смог им пользоваться.",
    ),
    # Saturday and Sunday: the day with no workday to hang itself on.
    "weekend": (
        "У выходного нет рабочего дня, за который можно зацепиться. Зацепись за прогулку в 10:00.",
        "45 минут, которые ты сделаешь, лучше 90, которые пропустишь.",
        "Выходной — это день для людей. Позвони маме. Напиши другу конкретное: день, время, место.",
        "Суббота без единого человека — это и есть настоящий пропуск недели.",
        "Три дела и всё. Встал, вышел, поработал 45 минут. Остальное — твоё.",
    ),
    # Body: the goal is fat loss without losing muscle at 40.
    "body": (
        "В 40 мышцы уходят вместе с жиром, если не есть белок. Вес падает, вид — нет.",
        "Главный жиросжигатель — это не тренировка. Это ежедневная прогулка.",
        "Больно в спине на следующий день — снимай 30% веса. Это не слабость, это техника.",
        "48 часов между силовыми. В 40 восстановление медленнее — это физиология, а не мотивация.",
        "Талия честнее весов. Весы врут каждый день, сантиметр — раз в месяц.",
        "Штанга подождёт до дня 31. Травма ждать не будет — она остановит весь план целиком.",
    ),
    # Focus: the named saboteur is night + home + being alone.
    "focus": (
        "Ночная ясность ощущается как сила и работает как ловушка.",
        "После 22:00 работа не начинается. Особенно если «пошло».",
        "Телефон в другой комнате — это не дисциплина. Это отсутствие выбора.",
        "Ты не прокрастинируешь. Ты прячешься в часах, где будущее не задаёт вопросов.",
        "Один блок в 90 минут стоит четырёх часов, разорванных уведомлениями.",
        "Скажи вслух: «на сегодня всё». Это то, что выключает прокручивание в голове ночью.",
    ),
    # Affirmations. Stated as facts he can check, not as wishes.
    "affirm": (
        "Ты доводишь проекты до конца. Проблема никогда не была в дисциплине.",
        "Ты умеешь зарабатывать. Нужно не больше денег, а один повторяющийся источник.",
        "Сорок — это не поздно. Это первый возраст, когда ты знаешь, чего не хочешь.",
        "Неопределённость лечится не режимом. Она лечится тем, что появляется в календаре и работает без тебя.",
        "Ты не сломан. Ты не собран — а это чинится другим способом.",
        "Серия важнее идеальности. Всегда.",
        "То, что ты сегодня отметил галочку, — это уже больше, чем весь апрель.",
    ),
}


# ── Loading the owner's own pool ──────────────────────────────────────────────


def sparks_path(space: str | None = None) -> Path:
    """Where the owner's lines live — beside habits.csv, in the space."""
    from navig.spaces.habit_tracker import tracker_path  # noqa: PLC0415

    if space:
        p = Path(space).expanduser()
        return p if p.suffix.lower() == ".txt" else p / SPARKS_FILE
    return tracker_path().parent / SPARKS_FILE


def parse(text: str) -> dict[str, tuple[str, ...]]:
    """Parse the plain-text pool format.

    ::

        # focus
        One line per spark.
        Another line.

    Deliberately not JSON or YAML: this file is edited by a human on a tired
    evening, and a missing comma must never cost him the whole pool. Anything
    before the first heading, and any unknown heading, is kept — an unrecognised
    category is a line the owner wrote, not an error to discard.
    """
    out: dict[str, list[str]] = {}
    current = "affirm"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            name = line.lstrip("#").strip().lower()
            if name:
                current = name
            continue
        out.setdefault(current, []).append(line)
    return {k: tuple(v) for k, v in out.items() if v}


def load(space: str | None = None) -> dict[str, tuple[str, ...]]:
    """The owner's pool if the space has one, else the built-ins.

    A file that exists but parses to nothing falls back rather than going silent:
    an empty pool would simply stop sending, which looks exactly like the daemon
    being down — the failure mode this whole system keeps having to design out.
    """
    try:
        text = sparks_path(space).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return BUILTIN
    parsed = parse(text)
    return parsed or BUILTIN


# ── Choosing one ──────────────────────────────────────────────────────────────


def category_for(
    day: date,
    today_done: set[str],
    yesterday_done: set[str],
    hour: int,
) -> str:
    """Which bucket this moment calls for. Pure — no clock, no files, no chat.

    Falls through the situation-specific buckets in order and lands on an ambient
    one when the day is going fine. "Going fine" deliberately includes a day that
    is merely young: nagging about a walk at 11:00 trains you to ignore the nag.
    """
    if not yesterday_done:
        return "recover"
    if day.weekday() >= 5:
        return "weekend"
    if hour >= 15 and "out" not in today_done:
        return "walk"
    if hour < 17 and "ship" not in today_done:
        return "ship"
    return ""


def pick(
    pool: dict[str, tuple[str, ...]],
    *,
    day: date,
    today_done: set[str],
    yesterday_done: set[str],
    hour: int,
    recent: list[str] | None = None,
    rng: random.Random | None = None,
) -> tuple[str, str]:
    """Return ``(category, line)``.

    ``recent`` holds keys already sent lately; those lines are excluded so the
    same words never arrive twice in a fortnight. If exclusion empties the chosen
    category, the whole pool is used before repeating — a repeat is the last
    resort, not the first.
    """
    r = rng or random.Random()
    seen = set(recent or [])

    wanted = category_for(day, today_done, yesterday_done, hour)
    order = ([wanted] if wanted else []) + [c for c in AMBIENT if c in pool]
    order += [c for c in pool if c not in order]

    for category in order:
        fresh = [ln for ln in pool.get(category, ()) if _key(ln) not in seen]
        if fresh:
            return category, r.choice(fresh)

    # Everything has been sent recently — repeat rather than say nothing.
    every = [ln for lines in pool.values() for ln in lines]
    if not every:
        return "", ""
    return "", r.choice(every)


def key_of(line: str) -> str:
    """The id a caller stores to remember a line was sent."""
    return _key(line)
