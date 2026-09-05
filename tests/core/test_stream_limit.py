"""STREAM_LIMIT teeth — a real subprocess emitting one >64 KiB line.

Proves the mechanism the #692/#695 class rests on, with no mocking: asyncio's DEFAULT pipe
limit (64 KiB) makes ``readline()`` raise on a longer line, and ``limit=STREAM_LIMIT`` makes
the same read succeed. The build guard (``tests/quality/test_subprocess_stream_limit.py``)
asserts every line-reading spawn passes the limit; this asserts the limit actually matters.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from navig.core.aio_subprocess import STREAM_LIMIT

# One line well over asyncio's 64 KiB default, then a newline. Exactly the shape of a
# `navig … --json` payload or a big JSON-RPC response.
_BIG = 100_000
_CHILD = f"import sys; sys.stdout.write('x' * {_BIG} + chr(10)); sys.stdout.flush()"


async def _read_one_line(*, limit: int | None) -> bytes:
    kwargs = {} if limit is None else {"limit": limit}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _CHILD,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        **kwargs,
    )
    try:
        return await asyncio.wait_for(proc.stdout.readline(), timeout=30)
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


async def test_default_limit_raises_on_a_long_line():
    """The bug: with asyncio's default 64 KiB limit, a longer line RAISES instead of
    returning — which is what killed the MCP reader loops and the deck CLI pump."""
    with pytest.raises((ValueError, asyncio.LimitOverrunError)):
        await _read_one_line(limit=None)


async def test_stream_limit_reads_the_long_line_whole():
    """The fix: with limit=STREAM_LIMIT the same line comes back intact."""
    line = await _read_one_line(limit=STREAM_LIMIT)
    assert line.rstrip(b"\r\n") == b"x" * _BIG


def test_stream_limit_is_generously_above_the_default():
    assert STREAM_LIMIT >= 8 * 1024 * 1024  # 64 KiB default is the footgun this replaces
