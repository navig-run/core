"""Doctor and heal as awaitables — the one seam every engine-control surface uses.

The deck's ``/api/deck/engine/*`` routes (NAVIG OS + the Mini App) and Telegram's
``/doctor`` card all run the health check and the heal through here, so a verdict
can never differ by which surface asked.

* ``collect_doctor`` is ``navig doctor --json`` in-process (``collect_report``), off
  the event loop, bounded.
* ``run_heal`` is ``navig doctor --heal --json`` in a subprocess. Heal never restarts
  a LIVE daemon (that remediation is report-only), so running it from inside the
  daemon's tree is safe — unlike a restart, which must leave the tree.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

DOCTOR_TIMEOUT_S = 180.0
HEAL_TIMEOUT_S = 420.0


async def collect_doctor(timeout: float = DOCTOR_TIMEOUT_S) -> dict[str, Any]:
    """The doctor report. Raises ``asyncio.TimeoutError`` past *timeout*."""
    from navig.commands.doctor import collect_report

    return await asyncio.wait_for(
        asyncio.to_thread(collect_report, None, False, quiet=True), timeout=timeout
    )


async def run_heal(timeout: float = HEAL_TIMEOUT_S) -> dict[str, Any]:
    """The heal result document. Raises ``asyncio.TimeoutError`` or ``ValueError``
    (no JSON — an older CLI, or a crash before the document was printed)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "navig", "doctor", "--heal", "--json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError(f"doctor --heal printed nothing (exit {proc.returncode})")
    return json.loads(text)
