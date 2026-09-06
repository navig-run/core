"""Browser reaper — close NAVIG-launched debug browsers nobody is using any more.

**Why a monitor and not a `finally`.** A `finally` only helps the process that opened the
browser, and the leaks come from exactly the cases where that process is gone: browsers are
spawned ``DETACHED_PROCESS`` so they outlive their launcher, cron jobs are killed by a
timeout rather than returning, and a hard-killed daemon runs no cleanup at all. Something
long-lived has to sweep, so it lives next to the other monitors.

**It is not `CDPSessionManager.sweep_idle`.** That evicts the CDP *WebSocket* while, in its
own words, "the remote app keeps running" — after it fires the browser is held by no session
at all, an untracked orphan with a window still on screen. This is the layer underneath.

Every safety rule lives in :func:`navig.browser.targets.reap_idle_browsers` (named profiles,
visible windows, unverifiable PIDs, unknown ages are all refused) so that the CLI and this
loop cannot drift apart. This module only decides *when* to ask.

Configured by ``browser.reap_idle_minutes`` (default 30; ``0`` disables). Opt-in via
``monitors.browser_reaper.enabled`` like every monitor except ``config_incidents``.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("navig.notify")

#: How often to sweep. Deliberately much coarser than the idle threshold: the cost of
#: noticing a leak a few minutes late is a window nobody was looking at anyway, while a
#: tight loop would rewrite the registry file constantly for no benefit.
POLL_S = 300.0

DEFAULT_IDLE_MINUTES = 30


def _idle_seconds() -> float:
    """``browser.reap_idle_minutes`` in seconds, or the default.

    Read fresh each pass so the operator can turn it off without restarting the daemon.
    Any failure yields the default rather than 0: a config hiccup must not silently
    disable a cleanup whose absence is invisible until windows pile up.
    """
    try:
        from navig.config import get_config_manager

        raw = get_config_manager().get("browser.reap_idle_minutes", DEFAULT_IDLE_MINUTES)
        minutes = float(raw)
    except (TypeError, ValueError, AttributeError, OSError):
        minutes = DEFAULT_IDLE_MINUTES
    except Exception:  # noqa: BLE001 — a config read must never stop the sweep
        minutes = DEFAULT_IDLE_MINUTES
    return max(0.0, minutes * 60.0)


def sweep_once() -> dict:
    """One sweep. Returns the reaper's result dict; never raises."""
    try:
        from navig.browser.targets import reap_idle_browsers

        return reap_idle_browsers(_idle_seconds())
    except Exception as exc:  # noqa: BLE001 — a monitor must survive a bad pass
        logger.debug("[browser_reaper] sweep failed: %s", exc)
        return {"reaped": [], "checked": 0, "skipped": {}, "errors": [str(exc)]}


#: `system_alert` is the existing "something happened on your machine" type — the same one
#: the resources monitor uses. Deliberately NOT `config_incident`: that type, and the
#: `navig.core.incidents` log behind it, are specifically the config/identity layer
#: rescuing itself (a wiped config, a re-identified deck key). Filing a browser cleanup
#: there would surface it to the operator under "Config rescues" and dilute the one signal
#: that means their bot is about to go deaf.
NOTIFY_TYPE = "system_alert"


async def _announce(ports: list[int]) -> None:
    """Tell the operator a cleanup happened. Best-effort; never breaks the loop.

    Self-healing that tells nobody is how the original problem hid for weeks — a leaked
    browser is silent by construction, which is why ~24 accumulated unnoticed. After the
    visibility fix a reap should be RARE, so each one is real information: it means some
    path is still leaking. If this ever becomes noisy, that is the signal, not the bug.
    """
    try:
        from navig.notify import dispatch

        plural = "browser" if len(ports) == 1 else "browsers"
        await dispatch(
            NOTIFY_TYPE,
            "Automation browser cleanup",
            f"Closed {len(ports)} idle automation {plural} "
            f"(port{'' if len(ports) == 1 else 's'} {', '.join(str(p) for p in ports)}). "
            f"Named profiles and visible windows are never touched.",
            priority="low",
            data={"source": "browser_reaper", "ports": ports},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[browser_reaper] could not announce: %s", exc)


async def run_browser_reaper(poll_s: float = POLL_S) -> None:
    """Sweep leaked debug browsers forever. Silent on a healthy machine."""
    logger.info("[browser_reaper] started (poll=%ss)", poll_s)
    while True:
        result = sweep_once()
        reaped = result.get("reaped") or []
        if reaped:
            logger.info("[browser_reaper] closed idle browser(s) on port(s): %s", reaped)
            await _announce(list(reaped))
        for err in result.get("errors") or []:
            logger.warning("[browser_reaper] %s", err)
        await asyncio.sleep(poll_s)
