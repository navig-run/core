"""The Telegram monitor cards must reuse the shared collectors.

The "is anything blocking the event loop?" rule now lives in
``test_no_loop_blocking.py`` and covers the WHOLE gateway, not just this file —
that is the check that would have caught the daemon-freezing
``psutil.disk_partitions()`` call before it shipped.

What remains here is the other half of that lesson. The freeze survived so long
because the collection logic existed in *three separate copies* (the monitor
route, the briefing, and these Telegram cards), so hardening one did nothing for
the others. The cards must therefore go through ``navig.commands.monitor``,
which owns the platform quirks (Windows drive-type filtering, timeout-bounded
probes, non-blocking CPU sampling) in one place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "navig"
    / "gateway"
    / "channels"
    / "telegram_commands.py"
)


@pytest.mark.parametrize(
    "collector",
    ["get_cpu_info", "get_services_info", "get_ports_info", "get_disk_info", "get_system_disk"],
)
def test_monitor_cards_use_the_shared_collector(collector: str):
    src = SOURCE.read_text(encoding="utf-8")
    assert f"from navig.commands.monitor import {collector}" in src, (
        f"{collector} is not reused — re-implementing collection here is how the "
        "daemon-freezing disk_partitions() call hid in three copies of this logic"
    )
