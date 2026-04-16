"""
LLM Cost Tracker — per-session token and USD cost accumulation.

Ported from .lab/claude/cost-tracker.ts (MIT, Anthropic).  Adapted to
Python idioms; all pricing driven by ``config/defaults.yaml`` so no USD
figure is hardcoded in this module.

Usage::

    from navig.cost_tracker import get_session_tracker

    tracker = get_session_tracker()
    tracker.record(model="gpt-4o", input_tokens=150, output_tokens=80)
    print(tracker.format_summary())          # rich table
    tracker.save()                           # flush to ~/.navig/session_costs.jsonl

The tracker is a process-wide singleton keyed to the current session ID.
Call ``reset_session_tracker()`` in tests to clear it.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from navig.platform.paths import config_dir

logger = logging.getLogger("navig.cost_tracker")

# ---------------------------------------------------------------------------
# Module-level constants — all overridable via config; no USD literals here
# ---------------------------------------------------------------------------
_HISTORY_FILE_NAME: str = "session_costs.jsonl"
_HISTORY_ROTATION_SENTINEL: str = "__rotate__"
_DEFAULT_PRICING_KEY: str = "default"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ModelUsage:
    """Accumulated token / cost counters for one model."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    request_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ModelUsage":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclasses.dataclass
class SessionSnapshot:
    """Serialisable snapshot of a completed session."""

    session_id: str
    started_at: str          # ISO-8601
    ended_at: str            # ISO-8601
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    model_usage: dict[str, dict[str, Any]]   # model_name → ModelUsage.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# SessionCostTracker
# ---------------------------------------------------------------------------


class SessionCostTracker:
    """
    Accumulates LLM token usage and USD cost across a single CLI session.

    Thread-safe: all mutations go through ``_lock``.
    """

    def __init__(
        self,
        session_id: str,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.session_id = session_id
        self._cfg = config or {}
        self._started_at = time.monotonic()
        self._started_wall = datetime.datetime.now(datetime.timezone.utc)
        self._model_usage: dict[str, ModelUsage] = {}
        self._api_duration_s: float = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        api_duration_s: float = 0.0,
    ) -> None:
        """
        Record one LLM API response.

        Args:
            model:             Model name as returned by the provider.
            input_tokens:      Prompt / user tokens billed.
            output_tokens:     Completion / assistant tokens billed.
            cache_read_tokens: Prompt-cache read tokens (billed at reduced rate).
            api_duration_s:    Wall-clock seconds spent waiting for the API.
        """
        if not self._cfg.get("enabled", True):
            return

        pricing = self._get_pricing(model)
        per_k = 1_000.0
        cost = (
            (input_tokens / per_k) * pricing["input"]
            + (output_tokens / per_k) * pricing["output"]
            + (cache_read_tokens / per_k) * pricing["cache_read"]
        )

        with self._lock:
            entry = self._model_usage.setdefault(model, ModelUsage())
            entry.input_tokens += input_tokens
            entry.output_tokens += output_tokens
            entry.cache_read_tokens += cache_read_tokens
            entry.cost_usd += cost
            entry.request_count += 1
            self._api_duration_s += api_duration_s

    def total_cost_usd(self) -> float:
        """Return cumulative USD cost for the session."""
        with self._lock:
            return sum(u.cost_usd for u in self._model_usage.values())

    def total_tokens(self) -> tuple[int, int, int]:
        """Return (input, output, cache_read) totals."""
        with self._lock:
            inp = sum(u.input_tokens for u in self._model_usage.values())
            out = sum(u.output_tokens for u in self._model_usage.values())
            crd = sum(u.cache_read_tokens for u in self._model_usage.values())
            return inp, out, crd

    def format_summary(self) -> str:
        """
        Return a human-readable Rich markup summary string.

        Designed for printing to a console via ``console_helper.print_markup()``.
        Falls back to plain text if Rich is unavailable.
        """
        with self._lock:
            usages = dict(self._model_usage)
            api_dur = self._api_duration_s

        if not usages:
            return "[dim]No LLM calls recorded this session.[/dim]"

        total_in = sum(u.input_tokens for u in usages.values())
        total_out = sum(u.output_tokens for u in usages.values())
        total_crd = sum(u.cache_read_tokens for u in usages.values())
        total_cost = sum(u.cost_usd for u in usages.values())
        elapsed = time.monotonic() - self._started_at

        lines: list[str] = [
            "\n[bold cyan]Session Cost Summary[/bold cyan]",
            f"[dim]Session:[/dim]   {self.session_id}",
            f"[dim]Elapsed:[/dim]   {elapsed:.1f}s wall  /  {api_dur:.1f}s API",
            f"[dim]Tokens:[/dim]    {total_in:,} in  +  {total_out:,} out"
            + (f"  +  {total_crd:,} cache-read" if total_crd else ""),
            f"[bold green]Total cost:[/bold green] ${total_cost:.6f} USD",
        ]

        if len(usages) > 1:
            lines.append("\n[dim]── Per-model breakdown ──[/dim]")
            for model_name, u in sorted(usages.items()):
                lines.append(
                    f"  [cyan]{model_name}[/cyan]  "
                    f"{u.input_tokens:,}in / {u.output_tokens:,}out  "
                    f"→ [green]${u.cost_usd:.6f}[/green]  "
                    f"[dim]({u.request_count} req)[/dim]"
                )

        return "\n".join(lines)

    def save(self) -> None:
        """
        Append a session snapshot to ``~/.navig/session_costs.jsonl``.
        Creates the file and parent directory if absent.
        Rotates (keeps last ``history_keep`` entries) automatically.
        """
        if not self._cfg.get("persist", True):
            return

        snapshot = self._build_snapshot()
        history_file = self._history_path()

        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(history_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(snapshot.to_dict()) + "\n")
            self._rotate(history_file)
        except OSError as exc:
            logger.debug("cost_tracker.save failed: %s", exc)

    # ------------------------------------------------------------------
    # Class / static helpers
    # ------------------------------------------------------------------

    @classmethod
    def load_session(cls, session_id: str) -> "SessionSnapshot | None":
        """
        Read the most-recent snapshot for *session_id* from the history file.
        Returns ``None`` if not found or on read error.
        """
        history_file = cls._history_path_static()
        if not history_file.is_file():
            return None

        try:
            with open(history_file, encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        d = json.loads(raw)
                        if d.get("session_id") == session_id:
                            return SessionSnapshot(**d)
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError as exc:
            logger.debug("cost_tracker.load_session failed: %s", exc)

        return None

    @classmethod
    def load_history(cls, last_n: int = 10) -> list[SessionSnapshot]:
        """Return up to *last_n* most-recent snapshots, newest first."""
        history_file = cls._history_path_static()
        if not history_file.is_file():
            return []

        snapshots: list[SessionSnapshot] = []
        try:
            with open(history_file, encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        d = json.loads(raw)
                        snapshots.append(SessionSnapshot(**d))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError as exc:
            logger.debug("cost_tracker.load_history failed: %s", exc)

        return list(reversed(snapshots[-last_n:]))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_pricing(self, model: str) -> dict[str, float]:
        """
        Look up per-1K-token pricing for *model* from config.

        Searches for the longest prefix match in ``cost_tracker.model_pricing``.
        Falls back to the ``default`` key (all zeros for local models).
        """
        pricing_table: dict[str, Any] = self._cfg.get("model_pricing", {})
        if not pricing_table:
            return {"input": 0.0, "output": 0.0, "cache_read": 0.0}

        # Exact match first, then prefix scan
        if model in pricing_table:
            return self._normalise_pricing(pricing_table[model])

        for key in sorted(pricing_table, key=len, reverse=True):
            if key == _DEFAULT_PRICING_KEY:
                continue
            if model.startswith(key):
                return self._normalise_pricing(pricing_table[key])

        default = pricing_table.get(_DEFAULT_PRICING_KEY, {})
        return self._normalise_pricing(default)

    @staticmethod
    def _normalise_pricing(raw: Any) -> dict[str, float]:
        if not isinstance(raw, dict):
            return {"input": 0.0, "output": 0.0, "cache_read": 0.0}
        return {
            "input": float(raw.get("input", 0.0)),
            "output": float(raw.get("output", 0.0)),
            "cache_read": float(raw.get("cache_read", 0.0)),
        }

    def _build_snapshot(self) -> SessionSnapshot:
        with self._lock:
            usages = {k: v.to_dict() for k, v in self._model_usage.items()}
            total_in, total_out, total_crd = (
                sum(u.input_tokens for u in self._model_usage.values()),
                sum(u.output_tokens for u in self._model_usage.values()),
                sum(u.cache_read_tokens for u in self._model_usage.values()),
            )
            total_cost = sum(u.cost_usd for u in self._model_usage.values())

        return SessionSnapshot(
            session_id=self.session_id,
            started_at=self._started_wall.isoformat(),
            ended_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            total_cost_usd=total_cost,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            total_cache_read_tokens=total_crd,
            model_usage=usages,
        )

    def _history_path(self) -> Path:
        return self._history_path_static()

    @staticmethod
    def _history_path_static() -> Path:
        try:
            from navig.workspace_ownership import USER_WORKSPACE_DIR

            return USER_WORKSPACE_DIR / _HISTORY_FILE_NAME
        except ImportError:
            return config_dir() / _HISTORY_FILE_NAME

    def _rotate(self, path: Path) -> None:
        """Keep only the last ``history_keep`` entries."""
        keep = int(self._cfg.get("history_keep", 100))
        try:
            with open(path, encoding="utf-8") as fh:
                lines = [ln for ln in fh if ln.strip()]
            if len(lines) > keep:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.writelines(lines[-keep:])
        except OSError as exc:
            logger.debug("cost_tracker._rotate failed: %s", exc)


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_tracker_lock = threading.Lock()
_active_tracker: SessionCostTracker | None = None


def get_session_tracker() -> SessionCostTracker:
    """
    Return (or create) the process-wide ``SessionCostTracker``.

    The session ID is derived from the current process PID + start time so
    each CLI invocation gets a distinct session ID without requiring explicit
    wiring.
    """
    global _active_tracker  # noqa: PLW0603

    with _tracker_lock:
        if _active_tracker is not None:
            return _active_tracker

        cfg = _load_tracker_config()
        if not cfg.get("enabled", True):
            # Disabled — return a no-op tracker so call-sites never branch.
            tracker = SessionCostTracker(
                session_id="disabled",
                config={"enabled": False, "persist": False},
            )
            _active_tracker = tracker
            return tracker

        import os

        session_id = f"s{os.getpid()}-{int(time.time())}"
        _active_tracker = SessionCostTracker(session_id=session_id, config=cfg)
        return _active_tracker


def reset_session_tracker() -> None:
    """Reset the singleton — call in tests between cases."""
    global _active_tracker  # noqa: PLW0603

    with _tracker_lock:
        _active_tracker = None


def _load_tracker_config() -> dict[str, Any]:
    """Load ``cost_tracker:`` section from config.yaml with graceful fallback."""
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        raw = cm.get("cost_tracker")
        if isinstance(raw, dict):
            return raw
    except Exception as exc:  # noqa: BLE001
        logger.debug("cost_tracker config load failed: %s", exc)
    return {}
