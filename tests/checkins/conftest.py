"""Shared isolation for the check-in tests.

Every test gets its own config dir, data dir and health space, a fixed zone
(Europe/Paris — it has DST, which is the point), and a fake Telegram that
records every call. Nothing here can reach the live daemon or the operator's real
``~/.navig``: the config dir is a tmp dir (no ``gateway.json`` → the job store is
a detached service over the tmp dir), and the scheduler helpers are stubbed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

PARIS = ZoneInfo("Europe/Paris")
CHAT = 4242


class FakeTelegram:
    """Records every Bot API call; ``sendMessage`` returns a fresh Message id.

    Shaped like ``TelegramChannel._api_call``: the Message dict on success, None
    when ``reject`` names the method.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.next_id = 100
        self.reject: set[str] = set()
        self.raise_on: set[str] = set()

    async def __call__(self, method: str, payload: dict[str, Any]) -> Any:
        self.calls.append((method, dict(payload)))
        if method in self.raise_on:
            raise RuntimeError(f"{method} exploded")
        if method in self.reject:
            return None
        if method == "sendMessage":
            self.next_id += 1
            return {
                "message_id": self.next_id,
                "chat": {"id": payload["chat_id"], "type": "private"},
            }
        return (
            {"ok": True} if method != "editMessageText" else {"message_id": payload["message_id"]}
        )

    # channel-shaped access, for code that takes a channel
    async def _api_call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        return await self(method, payload or {})

    async def send_message(self, chat_id: int, text: str, **_: Any) -> dict:
        return await self("sendMessage", {"chat_id": chat_id, "text": text})

    def sent(self, method: str = "sendMessage") -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == method]


@pytest.fixture
def tg() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def ci_env(tmp_path, monkeypatch):
    """Isolated config + data + health space, Paris time, no live scheduler."""
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    monkeypatch.setattr(ConfigSingleton, "_instance", None, raising=False)

    space = tmp_path / "health"
    space.mkdir()
    metrics = space / "metrics.csv"
    metrics.write_text(
        "date,weight_kg,body_fat_pct,resting_hr,sleep_hours,steps,mood_1_10,hrv,notes\n",
        encoding="utf-8",
    )
    from navig.spaces import body_metrics as bm

    bm.remember_target(CHAT, metrics)

    from navig.checkins import cadence

    monkeypatch.setattr(cadence, "local_tz", lambda: PARIS)

    # The scheduler is not under test here: record what would be written.
    from navig.checkins import jobs

    written: dict[str, list] = {"sync": [], "removed": [], "toggled": []}
    monkeypatch.setattr(
        jobs,
        "sync_jobs",
        lambda item, chat: written["sync"].append((item.id, chat)) or jobs.job_specs(item, chat),
    )
    monkeypatch.setattr(
        jobs, "remove_jobs", lambda item_id: written["removed"].append(item_id) or 0
    )
    monkeypatch.setattr(
        jobs,
        "set_jobs_enabled",
        lambda item_id, enabled: written["toggled"].append((item_id, enabled)) or 0,
    )

    yield {"space": space, "metrics": metrics, "records": space / "checkins.jsonl", "jobs": written}
    reset_config_manager()


def at(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    """An aware Paris datetime."""
    return datetime(y, mo, d, h, mi, tzinfo=PARIS)


@pytest.fixture
def add_pills(ci_env):
    def _add(name: str = "Metformin", dose: str = "500 mg", times: str = "08:00,20:00", **kw):
        from navig.checkins import defs as d

        kw.setdefault("created", "2026-01-01")
        return d.save_item(d.validate("pills", {"name": name, "dose": dose, "times": times, **kw}))

    return _add


@pytest.fixture
def add_water(ci_env):
    def _add(**kw):
        from navig.checkins import defs as d

        kw.setdefault("created", "2026-01-01")
        return d.save_item(d.validate("water", kw))

    return _add
