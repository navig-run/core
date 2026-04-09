"""Renders ConversationalAgent StatusEvent lifecycle events to the terminal using the rich library."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
)
from rich.text import Text

if TYPE_CHECKING:
    from navig.agent.conv.agent import ConversationalAgent

from navig.agent.conv.status_event import StatusEvent
from navig.console_helper import get_console


class ConsoleStatusRenderer:
    """Renders StatusEvent lifecycle events from ConversationalAgent to the terminal using rich."""

    def __init__(self, agent: ConversationalAgent) -> None:
        self._console = get_console()
        self._progress: Progress | None = None
        self._progress_task_id: TaskID | None = None
        self._task_start_time: datetime | None = None
        self._live: Live | None = None
        self._token_buffer: str = ""
        # Register self as the agent callback (also syncs executor via property setter)
        agent.on_status_update = self  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Callable protocol — async so it satisfies Callable[[StatusEvent], Awaitable[None]]
    # ------------------------------------------------------------------

    async def __call__(self, event: StatusEvent) -> None:
        match event.type:
            case "task_start":
                await self._on_task_start(event)
            case "step_start":
                await self._on_step_start(event)
            case "step_done":
                await self._on_step_done(event)
            case "step_failed":
                await self._on_step_failed(event)
            case "task_done":
                await self._on_task_done(event)
            case "thinking":
                await self._on_thinking(event)
            case "streaming_token":
                await self._on_streaming_token(event)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _stop_live(self) -> None:
        """Stop and discard the active Live display."""
        if self._live is not None:
            self._live.stop()
            self._live = None
            self._token_buffer = ""

    def _active_console(self) -> Console:
        """Return the console associated with the active Progress, or the root console."""
        if self._progress is not None:
            return self._progress.console
        return self._console

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _on_task_start(self, event: StatusEvent) -> None:
        self._stop_live()
        # Guard: stop any progress bar left over from a cancelled/interrupted task.
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._progress_task_id = None
        self._task_start_time = event.timestamp
        self._console.print(
            Panel(event.message, title=f"[bold cyan]Task {event.task_id}[/bold cyan]")
        )
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=self._console,
        )
        self._progress_task_id = None
        self._progress.start()

    async def _on_step_start(self, event: StatusEvent) -> None:
        self._stop_live()
        if self._progress is None:
            return
        # Use explicit None check — step_index=0 is falsy but is a valid index.
        idx = 0 if event.step_index is None else event.step_index
        total = 1 if event.total_steps is None else event.total_steps
        n = idx + 1
        if self._progress_task_id is None:
            self._progress_task_id = self._progress.add_task(
                f"[{n}/{total}] {event.message}", total=total
            )
        else:
            self._progress.update(
                self._progress_task_id,
                description=f"[{n}/{total}] {event.message}",
                completed=float(idx),
            )

    async def _on_step_done(self, event: StatusEvent) -> None:
        if self._progress is not None and self._progress_task_id is not None:
            self._progress.advance(self._progress_task_id, 1)
        self._active_console().print(f"[green]\u2705 {event.message}[/green]")

    async def _on_step_failed(self, event: StatusEvent) -> None:
        error = event.metadata.get("error", "Unknown error")
        is_final: bool = event.metadata.get("is_final", True)
        if is_final:
            # Terminal failure — bold red, clearly visible.
            self._active_console().print(f"[bold red]\u274c {event.message}: {error}[/bold red]")
        else:
            # Intermediate retry — dim, doesn't alarm the user.
            attempt = event.metadata.get("attempt", 0)
            self._active_console().print(
                f"[dim]\u26a0\ufe0f  Attempt {attempt + 1} failed, retrying\u2026 ({error})[/dim]"
            )

    async def _on_task_done(self, event: StatusEvent) -> None:
        self._stop_live()
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._progress_task_id = None
        elapsed = ""
        if self._task_start_time is not None:
            secs = (event.timestamp - self._task_start_time).total_seconds()
            elapsed = f" in {secs:.2f}s"
        self._console.print(
            f"[bold green]\u2705 Task {event.task_id} complete{elapsed}[/bold green]"
        )

    async def _on_thinking(self, event: StatusEvent) -> None:  # noqa: ARG002
        self._stop_live()
        self._active_console().print("[dim]Thinking\u2026[/dim]")

    async def _on_streaming_token(self, event: StatusEvent) -> None:
        token = event.metadata.get("token", "")
        self._token_buffer += token
        if self._live is None:
            self._live = Live(
                Text(self._token_buffer),
                console=self._console,
                refresh_per_second=20,
                auto_refresh=False,
            )
            self._live.start()
        self._live.update(Text(self._token_buffer), refresh=True)
