"""navig/cli/selector.py

Three-tier interactive selector with zero navig internal imports.

Tier resolution order:
  1. fzf  — when fzf is in $PATH and stdin is a TTY
  2. readchar arrow-key selector  — when ``readchar`` is installed
  3. Numbered prompt  — stdlib only, always available

``Ctrl+C`` / ``EOFError`` always return ``None`` from whichever tier is active.
``SystemExit`` is never raised here — callers own that responsibility.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from collections.abc import Sequence

# Track which one-time hints have already been shown this process lifetime.
_hints_shown: set[str] = set()


def _hint(key: str, message: str) -> None:
    """Print *message* to stderr at most once per session (TTY only)."""
    if key in _hints_shown or not sys.stderr.isatty():
        return
    _hints_shown.add(key)
    print(f"  │ {message}", file=sys.stderr)


@dataclass
class CommandEntry:
    """Represents one selectable command in the launcher."""

    name: str
    description: str
    domain: str = field(default="")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fzf_or_fallback(
    commands: Sequence[CommandEntry],
    prompt: str = "> ",
) -> CommandEntry | None:
    """Return the selected :class:`CommandEntry`, or ``None`` if the user cancelled.

    Tries each tier in order, falling through on failure.

    Args:
        commands: Non-empty list of selectable entries.
        prompt:   Prompt string shown to the user.
    """
    if not commands:
        return None

    # Build display entries (aligned name + description columns)
    entries = [f"{cmd.name:<26} {cmd.description}" for cmd in commands]
    # Use stripped keys so fzf's stripped output (no trailing spaces) resolves correctly.
    # When description is empty the padded entry ends with whitespace; fzf returns the
    # line stripped, so a plain dict(zip(entries, commands)) would always miss.
    index_map: dict[str, CommandEntry] = {e.strip(): cmd for e, cmd in zip(entries, commands)}

    # ------------------------------------------------------------------
    # Tier 1 — fzf
    # ------------------------------------------------------------------
    if shutil.which("fzf") and sys.stdin.isatty():
        try:
            proc = subprocess.run(
                [
                    "fzf",
                    "--prompt",
                    prompt,
                    "--ansi",
                    "--height=40%",
                    "--reverse",
                    "--border=rounded",
                    "--info=inline",
                ],
                input="\n".join(entries),
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            if proc.returncode == 0:
                chosen = proc.stdout.strip()
                return index_map.get(chosen)
            # returncode 130 = Ctrl+C / ESC inside fzf — clean cancel
            return None
        except Exception as exc:  # noqa: BLE001
            print(f"[navig] fzf error ({exc}), falling back.", file=sys.stderr)

    # ------------------------------------------------------------------
    # Tier 2 — readchar arrow-key selector
    # ------------------------------------------------------------------
    if sys.stdin.isatty():
        _hint(
            "fzf",
            "Tip: install fzf for the best picker — "
            "winget install junegunn.fzf  /  brew install fzf  /  apt install fzf",
        )
    return _arrow_selector(commands, prompt)


# ---------------------------------------------------------------------------
# Internal tiers
# ---------------------------------------------------------------------------


def _arrow_selector(
    commands: Sequence[CommandEntry],
    prompt: str,
) -> CommandEntry | None:
    """Arrow-key selector using *readchar*.

    Falls through to :func:`_numbered_prompt` when ``readchar`` is absent.
    """
    try:
        import readchar  # noqa: PLC0415
    except ImportError:
        _hint(
            "readchar",
            "Tip: pip install navig[interactive]  to enable arrow-key navigation",
        )
        return _numbered_prompt(commands, prompt)

    import os  # noqa: PLC0415

    idx = 0
    try:
        while True:
            subprocess.run(["cmd", "/c", "cls"] if os.name == "nt" else ["clear"], check=False)
            print(f"\n  {prompt}\n")
            for i, cmd in enumerate(commands):
                marker = "▶" if i == idx else " "
                print(f"  {marker} {cmd.name:<26} {cmd.description}")
            print("\n  ↑↓ navigate   Enter select   q quit")

            key = readchar.readkey()
            if key == readchar.key.UP:
                idx = (idx - 1) % len(commands)
            elif key == readchar.key.DOWN:
                idx = (idx + 1) % len(commands)
            elif key in (readchar.key.ENTER, "\r", "\n"):
                return commands[idx]
            elif key in (readchar.key.ESC, "q", "Q"):
                return None
    except KeyboardInterrupt:
        return None


def _numbered_prompt(
    commands: Sequence[CommandEntry],
    prompt: str,
) -> CommandEntry | None:
    """Stdlib-only last resort.  No external dependencies."""
    print()
    for i, cmd in enumerate(commands, 1):
        print(f"  {i:>2}.  {cmd.name:<26} {cmd.description}")
    print()
    try:
        raw = input(f"  {prompt}").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(commands):
            return commands[idx]
    return None
