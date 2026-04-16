"""navig/cli/selector.py
========================

Three-tier interactive command selector with zero NAVIG-internal imports.

Tier resolution order:
  1. **fzf**  — when fzf is in ``$PATH`` and stdin/stdout are TTYs.
  2. **readchar** arrow-key selector — when ``readchar`` is installed.
  3. **Numbered prompt** — stdlib only; always available.

``Ctrl+C`` / ``EOFError`` always return ``None`` from whichever tier is active.
``SystemExit`` is never raised here — callers own that responsibility.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

# One-time hints already shown this process lifetime (avoids tip spam).
_hints_shown: set[str] = set()


def _hint(key: str, message: str) -> None:
    """Emit *message* to stderr at most once per session (only in TTY contexts)."""
    if key in _hints_shown or not sys.stderr.isatty():
        return
    _hints_shown.add(key)
    print(f"  │ {message}", file=sys.stderr)


@dataclass
class CommandEntry:
    """A single selectable command in the launcher."""

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
    """Return the user-selected :class:`CommandEntry`, or ``None`` on cancel.

    Tries each tier in order, falling through on failure.

    Args:
        commands: Non-empty sequence of selectable entries.
        prompt:   Prompt string shown to the user.
    """
    if not commands:
        return None

    # Build aligned display strings: name column (26 chars) + description.
    display = [f"{cmd.name:<26} {cmd.description}" for cmd in commands]

    # Map stripped display strings back to entries.  fzf strips trailing
    # whitespace from its output, so a direct dict(zip(display, commands))
    # would miss entries where description is empty.
    index_map: dict[str, CommandEntry] = {
        d.strip(): cmd for d, cmd in zip(display, commands)
    }

    # ── Tier 1: fzf ────────────────────────────────────────────────────────
    if shutil.which("fzf") and sys.stdin.isatty():
        try:
            proc = subprocess.run(
                [
                    "fzf",
                    "--prompt", prompt,
                    "--ansi",
                    "--height=40%",
                    "--reverse",
                    "--border=rounded",
                    "--info=inline",
                ],
                input="\n".join(display),
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            if proc.returncode == 0:
                return index_map.get(proc.stdout.strip())
            # returncode 130 = Ctrl+C / ESC inside fzf — clean cancel.
            return None
        except Exception as exc:  # noqa: BLE001
            print(f"[navig] fzf error ({exc}), falling back.", file=sys.stderr)

    # ── Tier 2: readchar arrow-key selector ────────────────────────────────
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
    """Arrow-key selector.  Falls through to :func:`_numbered_prompt` when
    ``readchar`` is absent.
    """
    try:
        import readchar
    except ImportError:
        _hint(
            "readchar",
            "Tip: pip install navig[interactive]  to enable arrow-key navigation",
        )
        return _numbered_prompt(commands, prompt)

    import os

    idx = 0
    try:
        while True:
            # Clear the screen before each redraw.
            subprocess.run(
                ["cmd", "/c", "cls"] if os.name == "nt" else ["clear"],
                check=False,
            )
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
    """Stdlib-only fallback — no external dependencies required."""
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
