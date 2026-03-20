"""
navig.ui.renderer — Unified public API for all NAVIG CLI output.

This is the ONLY import command modules need:

    from navig.ui import renderer
    renderer.render_primary_state(...)
    renderer.render_status_header(...)

All functions are safe — they catch all exceptions internally and fall
back to plain print(). They never raise.

4-Layer Output Contract
-----------------------
  Layer 1  render_status_header      compact chip row
  Layer 2  render_primary_state      main state line + detail
  Layer 3  render_explanation        cause analysis
           render_metrics_panel      numeric signals
           render_findings_table     severity table
           render_fleet_table        node/peer table
           render_event_timeline     timestamped events
           render_metric_bars        bar charts
           render_sparklines         trend sparklines
           render_diff_preview       diff preview (debug only)
  Layer 4  render_actions            recommended actions
           render_fallback           degraded-path message
           render_action_queue       pending queue
           render_kv_diagnostics     key-value pairs
           render_command_row        padded command line
           render_next_step          ⚑ failure closer
           render_summary            AI/diagnostic summary
           render_ai_response        freeform AI output
           render_keymap_footer      key-binding footer
           render_action_approval    y/n approval prompt
           render_section_divider    thin horizontal rule
"""
from __future__ import annotations

# ── Layer 4 ──────────────────────────────────────────────────────────────
from navig.ui.actions import render_action_queue, render_actions, render_fallback
from navig.ui.bars import render_metric_bars, render_sparklines
from navig.ui.diff import render_diff_preview
from navig.ui.formatters import render_command_row, render_kv_diagnostics, render_section_divider

# ── Icons ─────────────────────────────────────────────────────────────────
from navig.ui.icons import icon

# ── Models (re-exported for convenience) ─────────────────────────────────
from navig.ui.models import (
    ActionItem,
    CauseScore,
    DiffLine,
    DiffPreview,
    Event,
    Metric,
    StatusChip,
    SummaryResult,
)

# ── Layer 2 ──────────────────────────────────────────────────────────────
from navig.ui.panels import (
    render_explanation,
    render_metrics_panel,
    render_primary_state,
)
from navig.ui.prompts import render_action_approval, render_keymap_footer

# ── Layer 1 ──────────────────────────────────────────────────────────────
from navig.ui.status import render_status_header
from navig.ui.summary import render_ai_response, render_next_step, render_summary

# ── Layer 3 ──────────────────────────────────────────────────────────────
from navig.ui.tables import render_findings_table, render_fleet_table

# ── Theme helpers ─────────────────────────────────────────────────────────
from navig.ui.theme import RENDER_MODE, SAFE_MODE, console
from navig.ui.timeline import render_event_timeline

__all__ = [
    # Layer 1
    "render_status_header",
    # Layer 2
    "render_primary_state",
    "render_explanation",
    "render_metrics_panel",
    # Layer 3
    "render_findings_table",
    "render_fleet_table",
    "render_event_timeline",
    "render_metric_bars",
    "render_sparklines",
    "render_diff_preview",
    # Layer 4
    "render_actions",
    "render_fallback",
    "render_action_queue",
    "render_kv_diagnostics",
    "render_command_row",
    "render_section_divider",
    "render_next_step",
    "render_summary",
    "render_ai_response",
    "render_keymap_footer",
    "render_action_approval",
    # Models
    "StatusChip",
    "Metric",
    "CauseScore",
    "Event",
    "ActionItem",
    "DiffLine",
    "DiffPreview",
    "SummaryResult",
    # Theme
    "SAFE_MODE",
    "RENDER_MODE",
    "console",
    # Icons
    "icon",
]
