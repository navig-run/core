"""
navig logs — Structured log tail with severity colouring.

Usage::

    navig logs nginx
    navig logs worker --tail 50 --host prod-01
"""

from __future__ import annotations

import re
import time
from typing import List, Optional, Tuple

import typer

app = typer.Typer(
    name="logs",
    help="Tail and colour-code service logs.",
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# Severity catalogue
# ---------------------------------------------------------------------------

#: Mapping from log-level keyword → (label, ANSI colour attr name)
_SEVERITY_MAP: dict[str, Tuple[str, str]] = {
    "EMERG":    ("EMERG",   "RED"),
    "ALERT":    ("ALERT",   "RED"),
    "CRIT":     ("CRIT",    "RED"),
    "CRITICAL": ("CRIT",    "RED"),
    "ERROR":    ("ERROR",   "RED"),
    "ERR":      ("ERROR",   "RED"),
    "WARN":     ("WARN",    "YELLOW"),
    "WARNING":  ("WARN",    "YELLOW"),
    "NOTICE":   ("NOTICE",  "CYAN"),
    "INFO":     ("INFO",    "GREEN"),
    "DEBUG":    ("DEBUG",   "GREY"),
    "TRACE":    ("TRACE",   "GREY"),
}

# Compiled regex: matches any severity keyword surrounded by word/bracket boundaries
_SEVERITY_RE: re.Pattern[str] = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _SEVERITY_MAP) + r")\b",
    re.IGNORECASE,
)


def _render_log_line(line: str) -> str:
    """Highlight the first severity keyword found in *line* and return it.

    In plain mode the line is returned unchanged.

    Args:
        line: Raw log line text.

    Returns:
        Annotated string ready for ``print()``.
    """
    from navig.core.renderer import _C, _PLAIN_MODE  # noqa: PLC0415

    if _PLAIN_MODE:
        return line

    m = _SEVERITY_RE.search(line)
    if not m:
        return f"{_C.GREY}{line}{_C.RESET}"

    keyword = m.group(1).upper()
    label, colour_attr = _SEVERITY_MAP.get(keyword, ("INFO", "GREY"))
    colour = getattr(_C, colour_attr, _C.GREY)
    highlighted = line.replace(
        m.group(0),
        f"{colour}{_C.BOLD}{m.group(0)}{_C.RESET}",
        1,
    )
    return highlighted


def _fetch_logs(service: str, tail: int, host: str) -> List[str]:
    """Fetch log lines for *service* from *host*.

    .. note::
        Remote log streaming is not yet implemented.  Callers must handle
        an empty return value and emit an actionable message directing the
        user to ``navig file show``.

    Args:
        service: Target service name.
        tail:    Number of lines to fetch.
        host:    Target host.

    Returns:
        An empty list (placeholder until remote log streaming is implemented).
    """
    # TODO: implement remote log streaming via navig run / navig file show
    return []


def run(
    service: str,
    tail: int = 20,
    host: str = "production-01",
) -> None:
    """Fetch and render the last *tail* lines of *service* logs.

    Args:
        service: Service name whose logs to tail.
        tail:    Number of lines to show (default 20).
        host:    Target host name (default ``production-01``).
    """
    from navig.core.renderer import (
        BlockType,
        DIVIDER,
        renderBlock,
        sessionClose,
        sessionOpen,
    )

    sessionOpen(host, f"logs  {service}  --tail {tail}")

    renderBlock(BlockType.FETCH, f"Fetching last {tail} lines of {service} …")
    time.sleep(0.03)

    print(DIVIDER)
    print()

    lines = _fetch_logs(service, tail, host)
    if not lines:
        print(
            f"  Remote log streaming is not yet implemented.\n"
            f"  To tail logs on a remote host use:\n"
            f"\n"
            f"    navig file show /var/log/{service}.log --tail --lines {tail}\n"
            f"  or:\n"
            f"    navig docker logs {service} -n {tail}\n"
        )
        print(DIVIDER)
        sessionClose("0 lines — streaming not implemented")
        return

    for line in lines:
        print(_render_log_line(line))

    print()
    print(DIVIDER)

    sessionClose(f"{len(lines)} line(s)")


@app.command()
def logs_cmd(
    service: str = typer.Argument(..., help="Service whose logs to tail"),
    tail: int = typer.Option(20, "--tail", "-n", help="Number of lines to show"),
    host: Optional[str] = typer.Option(None, "--host", "-H",
                                       help="Target host (default: production-01)"),
) -> None:
    """Tail and colour-code service logs."""
    run(service, tail=tail, host=host or "production-01")
