"""
navig.ui.models — Typed data models for all NAVIG UI components.

All render functions accept these dataclasses. Provides semantic structure
for CLI output — icon, color, severity, etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional

# ── Type aliases ────────────────────────────────────────────────────────────
RenderMode = Literal["rich", "safe"]
Severity = Literal["info", "warn", "critical", "ok"]
Color = Literal["cyan", "green", "yellow", "red", "magenta", "white", "dim"]


# ── Atomic components ────────────────────────────────────────────────────────

@dataclass
class StatusChip:
    """Single status indicator shown in compact header row."""
    icon: str          # Unicode icon (rich mode)
    icon_safe: str     # ASCII fallback (safe mode)
    label: str
    value: Optional[str] = None
    color: Color = "white"


@dataclass
class Metric:
    """Single numeric metric with optional bar + sparkline."""
    label: str
    value: str
    bar_fill: float     # 0.0–1.0
    sparkline: Optional[str] = None
    color: Color = "cyan"


@dataclass
class CauseScore:
    """Confidence-scored root cause entry."""
    confidence: int     # 0–100
    description: str
    severity: Severity = "info"


@dataclass
class Event:
    """Single timeline event with timestamp."""
    timestamp: str
    icon: str
    label: str
    detail: str
    color: Color = "white"


@dataclass
class ActionItem:
    """Numbered recommended action."""
    index: int
    description: str
    estimated_value: Optional[str] = None
    risk: Literal["low", "medium", "high"] = "low"


@dataclass
class DiffLine:
    """Single unified diff line."""
    op: Literal["add", "remove", "context"]
    content: str


@dataclass
class DiffPreview:
    """Grouped diff preview section."""
    title: str
    lines: List[DiffLine] = field(default_factory=list)


@dataclass
class SummaryResult:
    """AI or diagnostic summary output."""
    root_cause: str
    recommendation: str
    confidence: int     # 0–100
    action_prompt: Optional[str] = None
