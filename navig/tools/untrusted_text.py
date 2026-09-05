"""navig.tools.untrusted_text — rendering text you did not write into a prompt a human trusts.

An approval card is the one place where the operator's judgement is the last line of
defence, and it is built out of Markdown assembled from three sources with three very
different levels of trust:

* NAVIG's own prose — trusted;
* a **remote server's** tool name and description — chosen by whoever runs that server;
* the **agent's** arguments — chosen by a model that may be acting on injected input.

Left alone, either untrusted source can close a code fence and continue in the prompt's
own voice, or open a heading that outranks NAVIG's, and argue its own case to the person
deciding whether to allow it. The functions here neutralise that: fences are defused,
headings and quote markers are stripped, backticks cannot escape a code span, everything
is capped, and untrusted prose is block-quoted so it is visibly *reported* rather than
*asserted*.

Ported from Cloudflare OS (`packages/mcp-shared/src/tools.ts`), which states the rule
this module exists to honour: the agent's arguments get the same treatment as the
server's text, because **the agent is who this prompt protects the user from**.

Deliberately stdlib-only and free of ``navig.*`` imports: :mod:`navig.tools.approval`
sits on the hot tool-dispatch path and must be able to import this without dragging in
``navig.mcp`` (which chains registry -> client -> transport).
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "defuse_fences",
    "quote_untrusted",
    "code_span",
    "plain_inline",
    "describe_call",
    "MAX_DESCRIPTION",
    "MAX_ARGUMENTS",
    "MAX_INLINE_TEXT",
]

#: Longest server-supplied tool description reproduced in an approval prompt.
MAX_DESCRIPTION = 600

#: Longest rendering of a tool call's arguments reproduced in an approval prompt.
MAX_ARGUMENTS = 4000

#: Longest server-chosen name or endpoint shown inline in a prompt.
MAX_INLINE_TEXT = 120

_FENCE_RE = re.compile(r"`{3,}")
_LEADING_MARKUP_RE = re.compile(r"^[ \t]*[#>]+[ \t]*", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")
_STRUCTURE_CHARS_RE = re.compile(r"[`*_\[\]()#>|]")


def defuse_fences(text: str) -> str:
    """Neutralise Markdown fences in text about to be placed inside one.

    Without this a value can close the fence and continue in the prompt's own voice.
    """
    return _FENCE_RE.sub("'''", text)


def quote_untrusted(text: str, max_chars: int = MAX_DESCRIPTION) -> str:
    """Render untrusted prose safely inside the approval prompt.

    Left alone, a tool description can write its own ``Endpoint:`` line and argue the
    server's case in the prompt's voice. Fences and headings are neutralised, the text
    is capped, and the remainder is block-quoted so it reads as reported speech.

    The heading strip is applied **repeatedly**: one pass over ``##`` leaves ``#``,
    which is still a heading, at heading weight, in the prompt the approver reads.
    """
    cleaned = defuse_fences(text)
    while True:
        stripped = _LEADING_MARKUP_RE.sub("", cleaned)
        if stripped == cleaned:
            break
        cleaned = stripped
    cleaned = cleaned.strip()

    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "…"
    return "\n".join(f"> {line}" for line in cleaned.split("\n"))


def code_span(text: str, max_chars: int = MAX_INLINE_TEXT) -> str:
    """Render server-chosen text inside a Markdown code span.

    Tool names and endpoints are shown in backticks so the approver sees them exactly
    as sent — but a name is as server-controlled as a description, and one containing a
    backtick closes the span, after which everything is prose the server wrote in the
    prompt's own voice. Backticks are dropped and whitespace flattened, so what is
    rendered can never be more than one inline span.
    """
    cleaned = _WHITESPACE_RE.sub(" ", text.replace("`", "")).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "…"
    return f"`{cleaned or '(unnamed)'}`"


def plain_inline(text: str, max_chars: int = MAX_INLINE_TEXT) -> str:
    """Render server-chosen text as inline prose with structure characters removed.

    Used where the value appears outside a code span — a title, a one-line summary —
    and so must not be able to forge emphasis, links or headings.
    """
    cleaned = _WHITESPACE_RE.sub(" ", _STRUCTURE_CHARS_RE.sub("", text)).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "…"
    return cleaned or "(unnamed)"


def describe_call(
    *,
    server_name: str,
    endpoint: str,
    tool_name: str,
    description: str,
    arguments: dict[str, Any] | None,
    mode: str,
    classified_by: str,
) -> tuple[str, str]:
    """Render one external tool call as the Markdown an approver reads before deciding.

    Returns ``(title, description)``. The title is plain text rather than Markdown, but
    it is still server-chosen and appears in the pending list, so it gets the same
    flattening and cap.
    """
    try:
        rendered = defuse_fences(json.dumps(arguments or {}, indent=2, default=repr))
    except (TypeError, ValueError):
        rendered = "(arguments could not be displayed)"
    if len(rendered) > MAX_ARGUMENTS:
        rendered = rendered[:MAX_ARGUMENTS] + "\n... (truncated)"

    if mode == "read":
        provenance = (
            "The server declares this tool read-only, so it runs without approval. "
            "That claim comes from the server itself."
            if classified_by == "server-annotation"
            else "Treated as read-only by this deployment."
        )
    else:
        provenance = (
            "Treated as an action because the server did not declare it read-only. "
            "Nothing has been sent yet."
        )

    body = "\n".join(
        [
            f"**{plain_inline(server_name)}** → {code_span(tool_name)}",
            "",
            quote_untrusted(description)
            if description
            else "_The server provided no description for this tool._",
            "",
            "Arguments:",
            "```json",
            rendered,
            "```",
            "",
            f"Endpoint: {code_span(endpoint)}",
            "",
            provenance,
        ]
    )
    return f"{plain_inline(server_name)}: {plain_inline(tool_name)}", body
