"""
navig.tools.trust_boundary — External content tagging and sanitisation.

Any content fetched from the network or an untrusted source MUST be wrapped
before it reaches the LLM context window.  This prevents prompt-injection
attacks where a hostile web page tries to hijack the agent's behaviour.

Wrapping adds clear sentinel tags::

    [EXTERNAL CONTENT: https://evil.com]
    …raw page text…
    [/EXTERNAL CONTENT]

The LLM system prompt is primed to treat everything inside these sentinels as
*untrusted data*, not as instructions.

Usage
-----
    from navig.tools.trust_boundary import wrap_external, is_externally_wrapped

    safe = wrap_external(page_text, source="https://example.com")
    # safe is now opaque to prompt injection

    if is_externally_wrapped(safe):
        inner = unwrap_external(safe)
"""

from __future__ import annotations

import re

__all__ = [
    "wrap_external",
    "unwrap_external",
    "is_externally_wrapped",
    "TrustBoundaryError",
]

# Sentinel tokens — kept distinct from any plausible web content
_OPEN_TMPL = "[EXTERNAL CONTENT: {source}]"
_CLOSE_TAG = "[/EXTERNAL CONTENT]"
_OPEN_RE = re.compile(r"^\[EXTERNAL CONTENT: .+?\]", re.DOTALL)


class TrustBoundaryError(ValueError):
    """Raised when unwrap_external receives content that isn't wrapped."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def wrap_external(content: str, source: str = "unknown") -> str:
    """
    Wrap *content* from *source* with trust-boundary sentinels.

    Idempotent: already-wrapped content is returned unchanged.

    Args:
        content: The raw text fetched from an external source.
        source:  A descriptive label for the source (URL, service name, …).

    Returns:
        A string that starts with ``[EXTERNAL CONTENT: …]`` and ends with
        ``[/EXTERNAL CONTENT]``.
    """
    if is_externally_wrapped(content):
        return content
    open_tag = _OPEN_TMPL.format(source=_sanitise_source(source))
    return f"{open_tag}\n{content}\n{_CLOSE_TAG}"


def unwrap_external(content: str) -> str:
    """
    Strip the trust-boundary sentinels and return the inner text.

    Raises:
        TrustBoundaryError: If *content* is not externally wrapped.
    """
    if not is_externally_wrapped(content):
        raise TrustBoundaryError(
            "unwrap_external() called on content that is not externally wrapped"
        )
    # Remove the first line (open tag) and the last line (close tag)
    lines = content.splitlines()
    # First line is the open tag; last line is the close tag
    inner_lines = lines[1:]
    if inner_lines and inner_lines[-1].strip() == _CLOSE_TAG:
        inner_lines = inner_lines[:-1]
    return "\n".join(inner_lines)


def is_externally_wrapped(content: str) -> bool:
    """Return True if *content* starts with the external-content open sentinel."""
    return bool(_OPEN_RE.match(content.lstrip()))


def extract_source(content: str) -> str | None:
    """
    Extract the source label from a wrapped string.

    Returns ``None`` if the content is not wrapped.
    """
    m = re.match(r"^\[EXTERNAL CONTENT: (.+?)\]", content.lstrip())
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sanitise_source(source: str) -> str:
    """
    Sanitise the source label so it cannot itself inject sentinel syntax.

    Strips ``[`` and ``]`` characters to keep the sentinels parseable.
    """
    return source.replace("[", "(").replace("]", ")")
