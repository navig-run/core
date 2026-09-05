"""No caller may hand the message router a "replace the system prompt" key.

`telegram_commands._handle_explain` used to route `/explain_ai` with

    metadata={"system_override": "You are an expert explainer. ..."}

**Nothing ever read that key.** It appeared exactly once in the whole tree — at the
line that set it — so the persona never applied and the command always answered in
the ordinary agent voice. A knob with a writer and no reader.

The reason this is a guard and not just a deletion is what the obvious "fix" would
have been. Wiring `system_override` through `channel_router.route()` looks like
completing an unfinished feature; it would in fact create a **per-message channel for
substituting the system prompt** — precisely what the guardrail floor exists to
prevent (#1119: a surface speaking as someone else with no floor; #1129: an
operator-authored personality replacing the whole prompt, floor included). Anything a
command wants to tell the model belongs in the USER turn, where it cannot displace
the floor.

Scanned on the AST: only a real ``metadata={...}`` keyword argument counts, so prose
describing this rule — including this file and the comment left at the removal site —
cannot trip it, and cannot satisfy it either.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_CORE = Path(__file__).resolve().parents[2]
_NAVIG_ROOT = _CORE / "navig"

#: Key names that mean "use this instead of the assembled system prompt".
_OVERRIDE_KEY = re.compile(
    r"system_override|override_system|system_prompt_override|override_prompt",
    re.I,
)


def _metadata_dict_keys() -> list[tuple[str, int, str]]:
    """(file, line, key) for every literal key in a ``metadata={...}`` argument."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(_NAVIG_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(_CORE).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "metadata" or not isinstance(kw.value, ast.Dict):
                    continue
                for key in kw.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        found.append((rel, key.lineno, key.value))
    return found


def test_no_caller_passes_a_system_prompt_override() -> None:
    offenders = [
        (f, line, key) for f, line, key in _metadata_dict_keys() if _OVERRIDE_KEY.search(key)
    ]
    assert not offenders, (
        "these call sites pass a system-prompt override through message metadata:\n  "
        + "\n  ".join(f"{f}:{line}  {key!r}" for f, line, key in offenders)
        + "\n\nA per-message system-prompt override is a floor-bypass channel: the "
        "guardrail floor is assembled once, and a key that replaces it lets any caller "
        "hand the model an identity with no boundaries. Put the instruction in the USER "
        "message instead, where it cannot displace the floor.\n"
        "If you are ADDING this because the key looked unread and unfinished — it was "
        "unread on purpose; see this file's docstring."
    )


def test_the_scan_actually_reads_metadata_arguments() -> None:
    """Anti-vacuity: an AST walk that matches nothing passes the check above forever."""
    keys = _metadata_dict_keys()
    assert len(keys) >= 5, (
        f"only {len(keys)} literal metadata={{...}} keys found across navig/ — the idiom "
        "changed or the walk is reading the wrong node, so the check above is silently "
        "passing. It found dozens when written."
    )
