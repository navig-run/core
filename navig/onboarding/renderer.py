"""
UX Renderer — pure functions of engine state.

Zero side effects in display logic.
All terminal output is produced here and only here.
The engine never prints. The renderer never executes business logic.

Color policy: encodes state ONLY, never decoration.
  green   = success / completed
  yellow  = skipped / warning
  red     = failure / error
  cyan    = in-progress / active
  dim     = secondary / metadata
"""

from __future__ import annotations

import os
import re
import sys

from .engine import StepRecord

# ── ANSI helpers ───────────────────────────────────────────────────────────


def _tc() -> bool:
    """True if terminal supports 24-bit color."""
    ct = os.environ.get("COLORTERM", "").lower()
    return "truecolor" in ct or "24bit" in ct


def _uni() -> bool:
    """True if terminal supports Unicode (checks actual encoding, not just color depth)."""
    term = os.environ.get("TERM", "")
    if term in ("dumb", "unknown"):
        return False
    # PYTHONUTF8=1 forces UTF-8 mode
    if os.environ.get("PYTHONUTF8", "") == "1":
        return True
    if os.environ.get("PYTHONIOENCODING", "").lower().startswith("utf"):
        return True
    enc = (getattr(sys.stdout, "encoding", None) or "ascii").lower().replace("-", "")
    return enc in ("utf8", "utf8bom")


# Build color codes once at module import
_TRUECOLOR = _tc()
_UNICODE = _uni()


def _rgb(r: int, g: int, b: int) -> str:
    if _TRUECOLOR:
        return f"\x1b[38;2;{r};{g};{b}m"
    return ""  # fall through to named codes


# Named codes (work on all 16-color+ terminals)
_G = "\x1b[32m"  # green
_Y = "\x1b[33m"  # yellow
_R = "\x1b[1;31m"  # bold red
_C = "\x1b[36m"  # cyan
_B = "\x1b[34m"  # blue
_M = "\x1b[35m"  # magenta
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_CLEAR = "\x1b[2K\r"  # clear current line, carriage return
_UP1 = "\x1b[1A"  # cursor up 1 line
_HIDE = "\x1b[?25l"  # hide cursor
_SHOW = "\x1b[?25h"  # show cursor
_SAVE = "\x1b[s"
_REST = "\x1b[u"

# Accent: electric cyan for primary UI chrome
_ACCENT = _rgb(34, 211, 238) or _C

# Truecolor named pairs (r g b, fallback ANSI code)
_GREEN_TC = _rgb(74, 222, 128) or _G
_RED_TC = _rgb(244, 63, 94) or _R
_AMBER_TC = _rgb(251, 191, 36) or _Y
_DIM_CYAN = _rgb(103, 232, 249) or _C

# Icons — Unicode when supported, ASCII fallback
if _UNICODE:
    _ICON_OK = "✓"
    _ICON_SKIP = "⊘"
    _ICON_FAIL = "✗"
    _ICON_DOT = "·"
    _ICON_ARROW = "→"
    _ICON_BULLET = "●"
    _ICON_DIAMOND = "◆"
    _ICON_SPARK = "✦"
    _BAR_FULL = "█"
    _BAR_HALF = "▓"
    _BAR_EMPTY = "░"
    _SEP = "═"
    _SEP_THIN = "─"
else:
    _ICON_OK = "+"
    _ICON_SKIP = "-"
    _ICON_FAIL = "x"
    _ICON_DOT = "."
    _ICON_ARROW = "->"
    _ICON_BULLET = "*"
    _ICON_DIAMOND = "<>"
    _ICON_SPARK = "*"
    _BAR_FULL = "#"
    _BAR_HALF = "="
    _BAR_EMPTY = "-"
    _SEP = "="
    _SEP_THIN = "-"


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[^m]*m", "", s)


def _pad_to(content: str, width: int, fill: str = " ") -> str:
    """Pad `content` to visual `width`, accounting for ANSI escape codes."""
    visual_len = len(_strip_ansi(content))
    return content + fill * max(0, width - visual_len)


# ── Step label & detail helpers ────────────────────────────────────────────

_STEP_LABELS: dict[str, str] = {
    # Phase 1 — bootstrap
    "workspace-init": "workspace",
    "workspace-templates": "identity files",
    "config-file": "config",
    "configure-ssh": "ssh key",
    "verify-network": "network",
    "sigil-genesis": "sigil",
    "core-navig": "core",
    # Phase 2 — configuration
    "ai-provider": "ai provider",
    "vault-init": "vault",
    "web-search-provider": "web search",
    "first-host": "first host",
    "matrix": "matrix",
    "telegram-bot": "telegram",
    "email": "email",
    "social-networks": "social",
    "runtime-secrets": "secrets",
    "skills-activation": "skills",
    "review": "review",
}

_LABEL_W = 18  # visual column width for step labels


def _label(step_id: str, fallback: str) -> str:
    return _STEP_LABELS.get(step_id, fallback[:_LABEL_W])


def _format_detail(step_id: str, output: dict, error: str | None = None) -> str:
    """Return a compact one-liner describing what a step did."""
    from pathlib import Path as _P

    home = str(_P.home())

    def _sh(p: str) -> str:
        return p.replace(home, "~") if p and p.startswith(home) else p

    if step_id == "workspace-init":
        p = output.get("workspacePath", "")
        return _sh(p) if p else ""

    if step_id == "workspace-templates":
        return f"SOUL.md  {_ICON_DOT}  IDENTITY.md  {_ICON_DOT}  AGENTS.md  {_ICON_DOT}  USER.md"

    if step_id == "config-file":
        p = output.get("configPath", "")
        if not p:
            # Step was soft-skipped but path is deterministic; show it anyway
            p = str(_P.home() / ".navig" / "config.yaml")
        return _sh(p)

    if step_id == "configure-ssh":
        reason = output.get("reason", "")
        if reason:
            return f"{_DIM}{reason}{_RESET}"
        p = _sh(output.get("keyPath", ""))
        kt = output.get("keyType", "")
        return f"{p}  {_DIM}({kt}){_RESET}" if kt else p

    if step_id == "verify-network":
        return "online" if output.get("networkReachable") == "true" else f"{_DIM}offline{_RESET}"

    if step_id == "ai-provider":
        provider = output.get("provider", "")
        if provider and provider not in ("none", "unconfigured", ""):
            src = output.get("keySource", "")
            return f"{provider}  ({src})" if src else provider
        reason = output.get("reason", "")
        return f"{_DIM}{reason or 'configure later with: navig config ai'}{_RESET}"

    if step_id == "vault-init":
        if output.get("existing") == "true":
            return f"{_DIM}already initialised{_RESET}"
        vp = _sh(output.get("vaultPath", ""))
        return vp or f"{_DIM}secure credential store ready{_RESET}"

    if step_id == "first-host":
        count = output.get("hostCount", "")
        if count:
            return f"{_DIM}{count} host{'s' if count != '1' else ''} configured{_RESET}"
        return f"{_DIM}{output.get('reason', 'run navig host add')}{_RESET}"

    if step_id == "telegram-bot":
        return (
            f"{_DIM}bot token saved{_RESET}"
            if output.get("configured") == "true"
            else f"{_DIM}{output.get('reason', 'optional — skipped')}{_RESET}"
        )

    if step_id == "skills-activation":
        packs = output.get("activatedPacks", "")
        if packs:
            return f"{_DIM}{packs}{_RESET}"
        return f"{_DIM}{output.get('reason', 'configure with: navig skills activate')}{_RESET}"

    if error:
        return error[:70]

    vals = [
        str(v)
        for v in list(output.values())[:2]
        if v and str(v) not in ("true", "false", "1", "True")
    ]
    return "  ".join(vals)


# ── Progress indicator ─────────────────────────────────────────────────────


def render_progress(
    current: int,
    total: int,
    title: str,
    step_id: str = "",
    elapsed_s: float = 0.0,
) -> str:
    """
    Pending-step line written WITHOUT a trailing newline.
    The result functions use _CLEAR to overwrite it in-place.

      ·  workspace          …
    """
    label = _label(step_id, title)
    return f"{_CLEAR}  {_DIM}{_ICON_DOT}  {label:<{_LABEL_W}}  …{_RESET}"


# ── Step results (all use _CLEAR to overwrite the progress line) ────────────


def render_step_success(record: StepRecord) -> str:
    """✓  workspace          ~/.navig/workspace/"""
    label = _label(record.id, record.title)
    detail = _format_detail(record.id, record.output)
    dur = f"  {_DIM}({record.duration_ms}ms){_RESET}" if record.duration_ms > 500 else ""
    detail_s = f"  {detail}" if detail else ""
    return f"{_CLEAR}  {_GREEN_TC}{_ICON_OK}{_RESET}  {label:<{_LABEL_W}}{detail_s}{dur}"


def render_step_skipped(record: StepRecord) -> str:
    """·  ai provider        set NAVIG_LLM_PROVIDER to enable"""
    label = _label(record.id, record.title)
    detail = _format_detail(record.id, record.output, record.error)
    return f"{_CLEAR}  {_DIM}{_ICON_DOT}  {label:<{_LABEL_W}}  {detail}{_RESET}"


def render_step_already_done(record: StepRecord) -> str:
    """Dim line for steps skipped because the artifact shows them done."""
    label = _label(record.id, record.title)
    return f"  {_DIM}{_ICON_DOT}  {label:<{_LABEL_W}}  done{_RESET}"


def render_step_failure(record: StepRecord, fix_hint: str = "") -> str:
    """✗  ssh key            Permission denied\n       fix:  navig init --step configure-ssh"""
    label = _label(record.id, record.title)
    error = (record.error or "unknown error")[:80]
    fix = fix_hint or f"navig init --step {record.id}"
    return (
        f"{_CLEAR}  {_RED_TC}{_ICON_FAIL}{_RESET}  {label:<{_LABEL_W}}  {_RED_TC}{error}{_RESET}\n"
        f"       {_DIM}fix:{_RESET}  {_AMBER_TC}{fix}{_RESET}"
    )


def render_step_in_progress(title: str) -> str:
    """Compat alias."""
    return f"{_CLEAR}  {_DIM}{_ICON_DOT}  {title}…{_RESET}"
