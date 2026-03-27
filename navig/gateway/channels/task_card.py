import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StepState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    PAUSED = "paused"


STATE_ICON = {
    StepState.DONE: "🟩",
    StepState.ACTIVE: "🟨",
    StepState.FAILED: "🟥",
    StepState.PAUSED: "🟪",
    StepState.PENDING: "▫️",
}

STATE_WEIGHT = {
    StepState.DONE: 1.0,
    StepState.ACTIVE: 0.5,
    StepState.PAUSED: 0.5,
    StepState.FAILED: 0.5,
    StepState.PENDING: 0.0,
}

THROTTLE_SECONDS = 0.8


@dataclass
class TaskStep:
    key: str
    label: str
    state: StepState = StepState.PENDING
    detail: str | None = None


@dataclass
class TaskView:
    title: str = "🤖 Working on it..."
    steps: list[TaskStep] = field(default_factory=list)
    expanded: bool = True
    done: bool = False
    running: bool = True
    percent: int = 0
    message_id: int | None = None
    _last_edit: float = field(default=0.0, repr=False, compare=False)

    def recompute_percent(self) -> None:
        if not self.steps:
            self.percent = 0
            return
        total = sum(STATE_WEIGHT[s.state] for s in self.steps)
        self.percent = min(100, int((total / len(self.steps)) * 100))

    def set_step(self, key: str, state: StepState, detail: str | None = None) -> None:
        for step in self.steps:
            if step.key == key:
                step.state = state
                if detail is not None:
                    step.detail = detail
                return
        raise KeyError(f"Step key not found: {key!r}")

    @property
    def active_step(self) -> TaskStep | None:
        return next((s for s in self.steps if s.state == StepState.ACTIVE), None)

    @property
    def throttle_ready(self) -> bool:
        return (time.monotonic() - self._last_edit) >= THROTTLE_SECONDS

    def mark_edited(self) -> None:
        self._last_edit = time.monotonic()


def _progress_bar(percent: int, slots: int = 8) -> str:
    filled = round((percent / 100) * slots)
    return "▰" * filled + "▱" * (slots - filled)


def render_compact(view: TaskView) -> str:
    active = view.active_step
    label = active.label if active else ("Done" if view.done else "…")
    icon = STATE_ICON[active.state] if active else "🟩"
    return f"{icon} {view.percent}% — {label}"


def render_big(view: TaskView) -> str:
    lines: list[str] = [
        f"<b>{view.title}</b>",
        f"{_progress_bar(view.percent)} {view.percent}%",
        "",
    ]
    visible = (
        view.steps
        if view.expanded
        else ([view.active_step] if view.active_step else ([view.steps[0]] if view.steps else []))
    )
    for step in visible:
        if step is None:
            continue
        line = f"{STATE_ICON[step.state]} {step.label}"
        if step.detail:
            line += f"  <i>— {step.detail}</i>"
        lines.append(line)
    return "\n".join(lines)


def render(view: TaskView) -> str:
    return render_big(view) if view.expanded else render_compact(view)


def build_keyboard(view: TaskView) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Hide details" if view.expanded else "Show details",
                    "callback_data": "task:toggle_details",
                },
                {
                    "text": "Pause" if view.running else "Resume",
                    "callback_data": "task:toggle_pause",
                },
            ],
            [
                {"text": "Stop", "callback_data": "task:stop"},
                {"text": "Refresh", "callback_data": "task:refresh"},
            ],
        ]
    }


def make_task(steps: list[tuple[str, str]], title: str = "🤖 Working on it...") -> TaskView:
    return TaskView(
        title=title,
        steps=[TaskStep(key=k, label=l) for k, l in steps],
    )


async def send_task_card(channel: Any, chat_id: int, view: TaskView) -> int | None:
    """Send initial status card via NAVIG Telegram channel"""
    await channel._api_call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    res = await channel.send_message(
        chat_id,
        render(view),
        parse_mode="HTML",
        reply_markup=build_keyboard(view),
        disable_web_page_preview=True,
    )
    if res and res.get("ok"):
        view.mark_edited()
        view.message_id = res.get("result", {}).get("message_id")
        return view.message_id
    return None


async def update_task_card(channel: Any, chat_id: int, view: TaskView, force: bool = False) -> None:
    """Edit the live status card via NAVIG Telegram channel"""
    if not view.message_id:
        return
    if not force and not view.throttle_ready:
        return
    try:
        await channel._api_call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": view.message_id,
                "text": render(view),
                "parse_mode": "HTML",
                "reply_markup": build_keyboard(view),
                "disable_web_page_preview": True,
            },
        )
        view.mark_edited()
    except Exception as exc:
        if "not modified" not in str(exc).lower():
            pass  # Could log
