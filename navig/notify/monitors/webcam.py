"""Webcam-in-use monitor (Windows).

Detects when an application is actively using the camera by reading Windows'
Capability Access Manager ConsentStore — the same data the OS uses to show the
camera privacy indicator. For every consumer Windows records a per-app key with
``LastUsedTimeStart`` / ``LastUsedTimeStop`` (FILETIME QWORDs); a session is
**live** while ``Start != 0 and Stop == 0``.

  HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager
       \\ConsentStore\\webcam\\<PFN-or-"NonPackaged"\\<exe-key>>

The poll loop dispatches ``webcam_on`` on a debounced rising edge and
``webcam_off`` on release, into the Privacy category. Opt-in: the gateway only
spawns it when ``monitors.webcam.enabled`` is true.

The transition logic (``WebcamMonitor``) is pure and unit-tested with a fake
scan; only ``scan_active_apps`` touches the registry and is Windows-only.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass

from navig.notify.delivery import all_channels_failed

logger = logging.getLogger("navig.notify")

_CONSENT_PATH = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion"
    r"\CapabilityAccessManager\ConsentStore\webcam"
)

# Debounce: a session must persist this long AND span ≥2 polls before we announce
# it — defeats sub-second probes (Windows Hello, driver enumeration). Release is
# announced only after the app is absent for this many consecutive polls.
MIN_ON_SECONDS = 3.0
MIN_ON_POLLS = 2
OFF_POLLS = 2

POLL_IDLE_S = 5.0
POLL_ACTIVE_S = 2.0


# ── Registry scan (Windows-only) ──────────────────────────────────────────────


def _friendly_name(key_name: str, *, nonpackaged: bool) -> str:
    """Turn a ConsentStore subkey into a human label."""
    if nonpackaged:
        # NonPackaged keys encode the exe path with '#' for '\'.
        path = key_name.replace("#", "\\")
        base = path.rsplit("\\", 1)[-1]
        return base or path
    # Packaged consumers use the PFN: "Microsoft.WindowsCamera_8wekyb3d8bbwe".
    return key_name.split("_", 1)[0]


def _read_session(winreg, root, subpath: str) -> bool:
    """True if the app at *subpath* is currently holding the camera."""
    try:
        with winreg.OpenKey(root, subpath) as k:
            start, _ = winreg.QueryValueEx(k, "LastUsedTimeStart")
            stop, _ = winreg.QueryValueEx(k, "LastUsedTimeStop")
    except (FileNotFoundError, OSError):
        return False
    return bool(start) and not stop


def scan_active_apps() -> dict[str, str]:
    """Return ``{app_key: friendly_name}`` for every app currently using the camera.

    Reads HKCU + HKLM ConsentStore (HKLM covers service/SYSTEM consumers). Never
    raises — a registry hiccup yields an empty/partial map. No-op off Windows.
    """
    if sys.platform != "win32":
        return {}
    import winreg  # stdlib, Windows-only

    out: dict[str, str] = {}
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            base = winreg.OpenKey(root, _CONSENT_PATH)
        except (FileNotFoundError, OSError):
            continue
        with base:
            for name in _iter_subkeys(winreg, base):
                if name.lower() == "nonpackaged":
                    np_path = f"{_CONSENT_PATH}\\{name}"
                    try:
                        np = winreg.OpenKey(root, np_path)
                    except (FileNotFoundError, OSError):
                        continue
                    with np:
                        for sub in _iter_subkeys(winreg, np):
                            if _read_session(winreg, root, f"{np_path}\\{sub}"):
                                out[f"np:{sub}"] = _friendly_name(sub, nonpackaged=True)
                else:
                    if _read_session(winreg, root, f"{_CONSENT_PATH}\\{name}"):
                        out[name] = _friendly_name(name, nonpackaged=False)
    return out


def _iter_subkeys(winreg, key):
    i = 0
    while True:
        try:
            yield winreg.EnumKey(key, i)
        except OSError:
            return
        i += 1


# ── Pure transition logic (unit-tested) ───────────────────────────────────────


@dataclass
class _Session:
    name: str
    first_seen: float
    polls: int = 1
    announced: bool = False
    missing: int = 0


class WebcamMonitor:
    """Stateful debouncer turning poll snapshots into on/off events.

    ``step(current, now)`` takes ``{app_key: name}`` of live sessions and the
    monotonic clock, and returns events ``[(notify_type, app_name)]`` to dispatch.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    def step(self, current: dict[str, str], now: float) -> list[tuple[str, str, str]]:
        """Return ``(event_type, session_key, app_name)`` transitions to dispatch.

        The session *key* is surfaced (not just the name) so the loop can roll a
        ``webcam_on`` back to un-announced via :meth:`reset_announced` when the
        dispatch didn't deliver — two apps can share a friendly name.
        """
        events: list[tuple[str, str, str]] = []

        for key, name in current.items():
            s = self._sessions.get(key)
            if s is None:
                self._sessions[key] = _Session(name=name, first_seen=now)
                continue
            s.polls += 1
            s.missing = 0
            s.name = name
            if (
                not s.announced
                and s.polls >= MIN_ON_POLLS
                and (now - s.first_seen) >= MIN_ON_SECONDS
            ):
                s.announced = True
                events.append(("webcam_on", key, s.name))

        for key in list(self._sessions):
            if key in current:
                continue
            s = self._sessions[key]
            s.missing += 1
            if s.missing >= OFF_POLLS:
                if s.announced:
                    events.append(("webcam_off", key, s.name))
                del self._sessions[key]

        return events

    def reset_announced(self, key: str) -> None:
        """Un-latch a session's ``announced`` flag so the next poll re-fires
        ``webcam_on`` — the loop calls this when the dispatch didn't deliver."""
        s = self._sessions.get(key)
        if s is not None:
            s.announced = False

    @property
    def has_live_sessions(self) -> bool:
        return bool(self._sessions)


# ── Async poll loop ───────────────────────────────────────────────────────────


async def run_webcam_monitor() -> None:
    """Background task: poll the ConsentStore and dispatch Privacy notifications."""
    if sys.platform != "win32":
        logger.info("webcam monitor: not supported on %s — idle", sys.platform)
        return

    from navig.notify import dispatch as notify_dispatch

    monitor = WebcamMonitor()
    loop = asyncio.get_event_loop()
    logger.info("webcam monitor started")
    try:
        while True:
            try:
                current = await asyncio.to_thread(scan_active_apps)
            except Exception:  # noqa: BLE001 — never let a scan error kill the loop
                logger.debug("webcam scan failed", exc_info=True)
                current = {}

            # A bad poll (dispatch bug, transient error) must NOT kill the monitor —
            # else it dies silently while the deck still shows it enabled.
            try:
                await _handle_events(monitor, monitor.step(current, loop.time()), notify_dispatch)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad poll must not kill the monitor
                logger.warning("webcam monitor: poll failed, continuing", exc_info=True)

            await asyncio.sleep(POLL_ACTIVE_S if current else POLL_IDLE_S)
    except asyncio.CancelledError:
        logger.info("webcam monitor stopped")
        raise


async def _handle_events(
    monitor: WebcamMonitor, events: list[tuple[str, str, str]], dispatch_fn
) -> None:
    """Dispatch each webcam transition, settling on VERIFIED delivery.

    A ``webcam_on`` whose alert reached ZERO channels because every one FAILED
    (``all_channels_failed`` — an empty list is an intentional mute) is rolled back
    to un-announced so the next poll re-fires it, instead of silently losing the
    privacy alert until the app releases and re-acquires the camera. A failed
    ``webcam_off`` (informational) isn't retried — its session is already gone.
    """
    for event_type, key, app in events:
        if event_type == "webcam_on":
            outcome = await dispatch_fn(
                "webcam_on",
                "Webcam in use",
                f"{app} started using your camera",
                priority="high",
                data={"app": app},
            )
            if all_channels_failed(outcome):
                monitor.reset_announced(key)
        else:
            await dispatch_fn(
                "webcam_off",
                "Webcam released",
                f"{app} stopped using your camera",
                priority="normal",
                data={"app": app},
            )
