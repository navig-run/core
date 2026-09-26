"""The desktop app's dev CDP port must not sit in a band NAVIG allocates from.

**CROSS-LANGUAGE.** The port is a TypeScript literal in ``apps/os/scripts/tauri-dev.ts``;
the bands it must avoid are Python constants in ``navig.browser.targets`` and
``navig.browser.profiles``. Nothing compares them, and nothing can: the pytest selection
matches changed CORE MODULES against test names, so a guard living here is reachable only
from the core side unless it is wired into ``INVARIANT_GUARDS`` (it is).

**What went wrong.** The default was ``9222`` — the head of ``DEFAULT_SCAN_PORTS`` and the
default ``--port`` for ``cdp launch`` — so a bare ``navig cdp screenshot`` during a dev
session drove the DESKTOP APP's window instead of a browser. The comment above the literal
had reasoned carefully about the in-app browser panes (9333+) and concluded "they never
collide", which was true and about the wrong neighbour.

**The profiles/panes overlap is PAID.** Profiles (9280-9339) used to overlap the panes
(9333-9340); it was recorded here as debt rather than fixed, because moving a band is a
decision. The decision was made on 2026-09-15: the pane base moved to 9450 (the panes are
per-launch, not stable ports anyone holds; the profiles kept theirs). Also the pane base is
now RESOLVED at runtime (``NAVIG_PANE_CDP_PORT`` override, fallbacks when the range is
reserved) — the literal parsed here is the default, which is what the band map documents.
``test_profile_pane_overlap_is_gone`` keeps it gone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TAURI_DEV = REPO / "apps" / "os" / "scripts" / "tauri-dev.ts"


def _os_dev_cdp_port() -> int:
    """The default port `apps/os` gives its main window in dev.

    Parsed from source rather than imported: it is TypeScript, and this guard must not
    need a bun/node toolchain to run inside the Python gate.
    """
    src = TAURI_DEV.read_text(encoding="utf-8")
    m = re.search(r"NAVIG_OS_CDP_PORT\s*\?\?\s*[\"'](\d+)[\"']", src)
    assert m, (
        f"could not find the NAVIG_OS_CDP_PORT default in {TAURI_DEV.name}. If the "
        "expression changed shape, update this parser — do not delete the guard, or the "
        "port silently stops being checked."
    )
    return int(m.group(1))


def _navig_bands() -> dict[str, range]:
    """Every port band NAVIG hands out, all of which grow UPWARD from their base."""
    from navig.browser import profiles as p
    from navig.browser import targets as t

    scan = t.DEFAULT_SCAN_PORTS
    return {
        "cdp DEFAULT_SCAN_PORTS": range(min(scan), max(scan) + 1),
        # find_free_port(start=9222, count=50) — its preferred window before it falls
        # through to an OS-assigned port.
        "cdp new free-port search": range(9222, 9222 + 50),
        "named profiles": range(p.PROFILE_PORT_BASE, p.PROFILE_PORT_BASE + p.PROFILE_PORT_COUNT),
    }


def test_the_source_is_where_we_think_it_is() -> None:
    """Anti-vacuity: a parser pointed at a missing file would 'pass' by never running."""
    assert TAURI_DEV.is_file(), f"{TAURI_DEV} not found — this guard is checking nothing"


def test_os_dev_cdp_port_is_outside_every_navig_band() -> None:
    port = _os_dev_cdp_port()
    clashes = [name for name, band in _navig_bands().items() if port in band]
    assert not clashes, (
        f"apps/os dev CDP port {port} falls inside {clashes}. A bare `navig cdp <verb>` "
        f"would then drive the DESKTOP APP window instead of a browser. Move it above "
        f"every band (they all grow upward) and update the comment in "
        f"{TAURI_DEV.name}."
    )


def test_os_dev_cdp_port_avoids_the_in_app_panes() -> None:
    """The panes own PANE_CDP_PORT..+SLOT_MAX; the main window must not land there."""
    rs = (REPO / "apps" / "os" / "src-tauri" / "src" / "webview_pane.rs").read_text(
        encoding="utf-8"
    )
    m = re.search(r"PANE_CDP_PORT_DEFAULT:\s*u16\s*=\s*(\d+)", rs)
    assert m, "could not find PANE_CDP_PORT_DEFAULT — update this parser rather than dropping it"
    base = int(m.group(1))
    port = _os_dev_cdp_port()
    assert port not in range(base, base + 16), (
        f"apps/os dev CDP port {port} collides with the in-app browser panes "
        f"({base}+slot)."
    )


def test_profile_pane_overlap_is_gone() -> None:
    """The panes must not share a port with the named profiles — ever again.

    Named profiles run 9280-9339 and the panes used to start at 9333, so the top 7
    profile ports were also pane ports (a `navig cdp profile` and the in-app pane could
    claim the same port). This was pinned as recorded debt; the pane base then moved to
    9450 and the debt is paid. Any future overlap between the two bands — from either
    side — is a regression, not a decision.
    """
    from navig.browser import profiles as p

    rs = (REPO / "apps" / "os" / "src-tauri" / "src" / "webview_pane.rs").read_text(
        encoding="utf-8"
    )
    base = int(re.search(r"PANE_CDP_PORT_DEFAULT:\s*u16\s*=\s*(\d+)", rs).group(1))
    profile_band = range(p.PROFILE_PORT_BASE, p.PROFILE_PORT_BASE + p.PROFILE_PORT_COUNT)
    overlap = [port for port in range(base, base + 8) if port in profile_band]
    assert overlap == [], (
        f"the in-app pane band ({base}+slot) overlaps the named-profile band "
        f"({p.PROFILE_PORT_BASE}-{p.PROFILE_PORT_BASE + p.PROFILE_PORT_COUNT - 1}): {overlap}"
    )


@pytest.mark.parametrize("band_name", sorted(_navig_bands()))
def test_every_band_is_non_empty(band_name: str) -> None:
    """A band that resolved to an empty range would make the collision check vacuous."""
    assert len(_navig_bands()[band_name]) > 0
