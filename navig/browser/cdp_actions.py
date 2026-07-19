"""
CDP action layer — the single implementation behind both `navig cdp` (CLI) and
the ``cdp_*`` MCP tools.

Every function attaches (or reuses) a live session via
:func:`~navig.browser.session_manager.get_session_manager` and drives the target
through the public :class:`~navig.browser.controller.BrowserController` API. Both
surfaces call these, so there is exactly one code path — no duplication.

Return values are plain JSON-able dicts with an ``ok`` flag, so the CLI can
pretty-print and MCP can hand them straight to the agent.
"""

from __future__ import annotations

import os
import re
from typing import Any

from navig.browser.session_manager import get_session_manager
from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


async def _bridge(port: int, tab_index: int = 0):
    return await get_session_manager().get(port=port, tab_index=tab_index)


async def _try_bridge(port: int, tab_index: int = 0):
    """Return a live bridge, or None if nothing is attachable on *port*."""
    try:
        return await _bridge(port, tab_index)
    except Exception as exc:  # noqa: BLE001 (attach failure → fall back to OS)
        logger.info("[cdp.actions] No CDP target on port %d (%s); OS fallback", port, exc)
        return None


def _os_adapter():
    """Platform desktop-automation adapter (mouse/keyboard), or None."""
    import sys

    try:
        if sys.platform == "win32":
            from navig.adapters.automation.ahk import AHKAdapter

            return AHKAdapter()
        if sys.platform == "linux":
            from navig.adapters.automation.linux import LinuxAdapter

            return LinuxAdapter()
        if sys.platform == "darwin":
            from navig.adapters.automation.macos import MacOSAdapter

            return MacOSAdapter()
    except Exception:  # noqa: BLE001
        return None
    return None


async def _select(bridge, tab: int | None, url: str | None) -> None:
    """Make a specific open tab active before an action, if tab/url is given."""
    if tab is not None or url:
        await bridge.switch_to(index=tab, url=url)


# ────────────────────────── discovery / launch (sync core, async wrappers) ──


def targets(ports=None) -> dict:
    """List live CDP endpoints on localhost."""
    from navig.browser import targets as t

    scan = tuple(ports) if ports else t.DEFAULT_SCAN_PORTS
    found = t.discover_targets(scan)
    return {"ok": True, "targets": [x.to_dict() for x in found]}


def _extension_args(load_extension: str | None) -> list[str]:
    """Chrome flags to load unpacked extension(s) for testing/automation.

    Accepts one directory path or a comma-separated list. Also disables all other
    extensions so the loaded one(s) run in isolation (right for a fresh automation
    profile). Raises ValueError with a clear message if a path is missing or has no
    ``manifest.json`` — so a typo fails loudly instead of silently loading nothing.

    Also sets ``--disable-features=DisableLoadExtensionCommandLineSwitch``: Chrome 137+
    ignores ``--load-extension`` on the Stable channel unless that feature is disabled,
    so without it the flag is silently dropped and nothing loads.

    Caveat: an enterprise-managed Chrome ("managed by your organization") can still
    refuse unpacked extensions regardless of these flags — the extension simply never
    appears in ``chrome://extensions`` (count 0). That is a policy limit on the machine,
    not a bug here; use a userscript manager or an unmanaged Chrome to test in that case.
    """
    if not load_extension:
        return []
    paths: list[str] = []
    for raw in load_extension.split(","):
        raw = raw.strip()
        if not raw:
            continue
        p = os.path.abspath(os.path.expanduser(raw))
        if not os.path.isdir(p):
            raise ValueError(f"extension path is not a directory: {p}")
        if not os.path.exists(os.path.join(p, "manifest.json")):
            raise ValueError(f"no manifest.json in extension path: {p}")
        paths.append(p)
    if not paths:
        return []
    joined = ",".join(paths)
    return [
        f"--load-extension={joined}",
        f"--disable-extensions-except={joined}",
        # Chrome 137+ ignores --load-extension on the Stable channel unless this
        # feature is disabled (the switch was gated behind
        # DisableLoadExtensionCommandLineSwitch). Without it the flag is silently
        # dropped and the unpacked extension never loads.
        "--disable-features=DisableLoadExtensionCommandLineSwitch",
    ]


def _window_size_args(window_size: str | None) -> list[str]:
    """Chrome ``--window-size=W,H`` from a ``WxH`` string (e.g. ``"1440x900"``).

    Returns ``[]`` when *window_size* is None/empty. Raises ``ValueError`` with a
    clear message on a malformed value so a typo (``"1440"``, ``"axb"``) fails loudly
    *before* any browser launches, rather than silently launching at the wrong size.

    Why it also matters headless: with ``--headless=new`` the OS window is gone, but
    Chrome still honours ``--window-size`` for the **initial rendering viewport** — so
    a headless screenshot comes out at exactly these dimensions. That is what makes a
    pixel baseline portable across machines (otherwise the shot inherits whatever the
    launcher's default window width happens to be on that box).
    """
    if not window_size:
        return []
    raw = window_size.strip().lower()
    m = re.fullmatch(r"(\d+)\s*x\s*(\d+)", raw)
    if not m:
        raise ValueError(
            f"invalid --window-size {window_size!r}: expected WIDTHxHEIGHT, e.g. 1440x900"
        )
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        raise ValueError(
            f"invalid --window-size {window_size!r}: width and height must both be > 0"
        )
    return [f"--window-size={w},{h}"]


def launch(app: str, port: int = 9222, *, force_restart: bool = False,
           user_data_dir: str | None = None, load_extension: str | None = None) -> dict:
    """Launch a known app (or explicit path) with a debug port.

    When *force_restart* is True and a running instance holds the single-instance
    lock, the existing instance is terminated first (caller must have confirmed).
    *load_extension* loads unpacked Chrome extension(s) in isolation (a folder path,
    or a comma-separated list) — for testing an extension end-to-end over CDP.
    """
    from navig.browser import targets as t

    try:
        extra = _extension_args(load_extension)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    already = t.probe_port(port)
    if already is not None:
        return {"ok": True, "relaunched": False, "target": already.to_dict(),
                "note": f"port {port} already serving CDP"}

    is_known = app in t.known_app_ids()
    if is_known and force_restart and t.is_running(app):
        t.terminate_app(app)

    target = t.launch_with_cdp(app, port=port, user_data_dir=user_data_dir, extra_args=extra or None)
    if target is None:
        running = is_known and t.is_running(app)
        return {
            "ok": False,
            "error": (
                f"{app} did not expose CDP on port {port}."
                + (" It is already running — pass force_restart to relaunch it "
                   "with the debug port (this closes the current window)." if running else "")
            ),
        }
    return {"ok": True, "relaunched": True, "target": target.to_dict()}


def new(app: str = "chrome", port: int | None = None, profile: str | None = None,
        load_extension: str | None = None, headless: bool = False,
        window_size: str | None = None) -> dict:
    """Open a **completely fresh, isolated** browser session.

    Always uses its own profile dir and its own debug port, so it never touches
    your everyday browser or the default debug profile.

    Args:
        app: chrome|edge|brave (or a browser executable path).
        port: Debug port; auto-allocated (first free from 9222) when omitted.
        profile: Named persistent profile (reusable). Omit for a throwaway
            session profile that is unique each time.
        load_extension: Unpacked extension folder(s) to load in isolation (a path,
            or a comma-separated list) — for end-to-end testing an extension over CDP.
        headless: Launch without a visible window (``--headless=new``). Opt-in —
            the default (False) keeps the historical behaviour of a visible window.
            Unblocks display-less/CI environments. Mirrors ``profile_open``.
        window_size: Pin the window (and, headless, the rendering viewport) to
            ``"WxH"`` (e.g. ``"1440x900"``) via Chrome ``--window-size=W,H``. None
            (default) keeps Chrome's own default sizing. Malformed input is rejected
            before launch (see :func:`_window_size_args`).
    """
    from navig.browser import targets as t

    try:
        ext_args = _extension_args(load_extension)
        size_args = _window_size_args(window_size)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    extra = [*ext_args, *size_args]
    if headless:
        # Same mechanism cdp open / profile_open use for a windowless launch.
        extra.append("--headless=new")

    chosen_port = port or t.find_free_port()
    if chosen_port is None:
        return {"ok": False, "error": "no free debug port available"}
    profile_dir = t.new_session_profile_dir(profile)
    target = t.launch_with_cdp(app, port=chosen_port, user_data_dir=profile_dir, extra_args=extra or None)
    if target is None:
        return {"ok": False, "error": f"could not start a new {app} session on port {chosen_port}"}
    result = {
        "ok": True,
        "port": chosen_port,
        "profile": profile_dir,
        "profile_kind": "named" if profile else "throwaway",
        "app": app,
        "headless": headless,
        "target": target.to_dict(),
        "hint": f"target it with --port {chosen_port}",
    }
    if ext_args:
        result["loaded_extension"] = load_extension
    if window_size:
        result["window_size"] = window_size
    return result


def stop(port: int | None = None, all_ports: bool = False) -> dict:
    """Close a NAVIG-launched debug browser (disables the debug port).

    Releases the live CDP session first, then terminates exactly the browser
    process NAVIG started (by tracked PID) — it never kills unrelated browsers.
    """
    from navig.browser import targets as t
    from navig.browser.cdp_runtime import run as _rt
    from navig.browser.session_manager import get_session_manager

    mgr = get_session_manager()
    if all_ports:
        _rt(mgr.release_all())
        return t.stop_all_launched()
    target_port = port or 9222
    _rt(mgr.release(target_port))
    return t.stop_launched(target_port)


def detach(port: int | None = None, all_ports: bool = False) -> dict:
    """Disconnect NAVIG's session but LEAVE the browser running (port stays open)."""
    from navig.browser.cdp_runtime import run as _rt
    from navig.browser.session_manager import get_session_manager

    mgr = get_session_manager()
    if all_ports:
        _rt(mgr.release_all())
        return {"ok": True, "detached": "all"}
    target_port = port or 9222
    _rt(mgr.release(target_port))
    return {"ok": True, "detached": target_port}


def browsers() -> dict:
    """Every debug browser running on this machine, classified.

    A leaked debug browser renders no page (the harness opens its content in a tab it
    then closes), so it shows up as a blank window — or nothing at all when headless.
    Nothing ever looked for them, which is how ~24 once piled up unnoticed. Port
    scanning cannot find them either: `--remote-debugging-port=0` takes an ephemeral
    port nobody knows. Only a process scan sees them.
    """
    from navig.browser import targets as t

    found = t.list_debug_browsers()
    return {
        "ok": True,
        "browsers": found,
        "orphans": sum(1 for b in found if b["kind"] == "orphan"),
        "foreign": sum(1 for b in found if b["kind"] == "foreign"),
    }


def launched() -> dict:
    """List debug browsers NAVIG launched (and whether the port is still live)."""
    from navig.browser import targets as t

    reg = t.get_launched()
    out = []
    for port_str, entry in reg.items():
        port = int(port_str)
        out.append({
            "port": port,
            "app": entry.get("app"),
            "pid": entry.get("pid"),
            "profile": entry.get("user_data_dir"),
            "live": t.probe_port(port, timeout=0.4) is not None,
        })
    return {"ok": True, "launched": out}


# ────────────────────────── named browser profiles ──────────────────────────
#
# Different persistent, logged-in Chrome identities for different projects/cases/
# accounts, each on a STABLE port. See navig.browser.profiles.


def profile_list(include_real: bool = False, app: str = "chrome") -> dict:
    """List NAVIG automation profiles (+ optionally the user's real browser profiles)."""
    from navig.browser import profiles as p
    from navig.browser import targets as t

    effective = p.resolve_active(None)  # explicit pointer, or the sole profile
    active = effective.name if effective else None
    rows = []
    for prof in p.list_profiles():
        rows.append({
            "name": prof.name,
            "active": prof.name == active,
            "port": prof.port,
            "app": prof.app,
            "note": prof.note,
            "project": prof.project,
            "real": prof.real,
            "running": t.probe_port(prof.port, timeout=0.3) is not None,
            "last_used": prof.last_used,
        })
    out = {"ok": True, "active": active, "profiles": rows}
    if include_real:
        out["real_chrome_profiles"] = p.detect_real_chrome_profiles(app)
    return out


def profile_new(name: str, *, app: str = "chrome", note: str = "",
                project: str | None = None, real_directory: str | None = None,
                gmail: str | None = None) -> dict:
    """Create a named profile (automation by default; real-Chrome when *real_directory* is set).

    *gmail* optionally binds a default Gmail account (email) to the profile.
    """
    from navig.browser import profiles as p

    if p.get_profile(name) is not None:
        return {"ok": False, "error": f"profile '{name}' already exists"}
    if real_directory:
        prof = p.create_real_profile(name, real_directory, app=app, note=note, project=project)
        if prof is None:
            return {"ok": False, "error": f"could not find {app} User Data on this machine"}
        if p.get_profile(name) is None:  # write didn't persist (disk full / permissions)
            return {"ok": False, "error": "profile could not be saved (disk full or permissions?)"}
        return {"ok": True, "name": name, "port": prof.port, "app": app, "real": True,
                "profile_directory": real_directory,
                "note": f"real profile '{name}' registered (advanced — quit {app} before opening)"}
    prof = p.create_profile(name, app=app, note=note, project=project)
    if p.get_profile(name) is None:  # write didn't persist
        return {"ok": False, "error": "profile could not be saved (disk full or permissions?)"}
    if gmail:
        p.set_default_account(name, gmail)
    return {"ok": True, "name": name, "port": prof.port, "app": app,
            "user_data_dir": prof.user_data_dir, "gmail": gmail,
            "note": f"profile '{name}' created on port {prof.port} · open it: navig cdp open {name}"}


def profile_open(name: str, *, headless: bool = False) -> dict:
    """Open (or REUSE if already running) a named profile's visible browser on its stable port."""
    from navig.browser import profiles as p
    from navig.browser import targets as t

    prof = p.get_profile(name)
    if prof is None:
        return {"ok": False,
                "error": f"no profile '{name}' — create it: navig cdp profile new {name}"}

    # Reuse if the stable port is already serving CDP (no relaunch, no reopen).
    if t.probe_port(prof.port, timeout=0.4) is not None:
        p.touch_profile(name)
        p.set_active(name)  # opening a profile makes it the one navig do / gmail use
        return {"ok": True, "reused": True, "name": name, "port": prof.port, "app": prof.app,
                "note": f"profile '{name}' already open on port {prof.port}"}

    # Real profile → preflight: refuse while the real browser holds the profile lock.
    if prof.real and t.is_running(prof.app):
        return {"ok": False, "name": name, "port": prof.port,
                "error": (f"quit {prof.app} first — your real profile is locked by the running "
                          f"browser. (Newest Chrome may also block debugging on the default "
                          f"profile.) Or use an automation profile: navig cdp profile new <name>")}

    # first-run/welcome + search-engine-choice suppression is applied by launch_with_cdp
    # for every browser launch; here we only add the headless switch when asked.
    extra = ["--headless=new"] if headless else []
    target = t.launch_with_cdp(prof.app, port=prof.port, user_data_dir=prof.user_data_dir,
                               profile_directory=prof.profile_directory, extra_args=extra)
    if target is None:
        return {"ok": False, "name": name, "port": prof.port,
                "error": f"could not open profile '{name}' on port {prof.port}"}
    p.touch_profile(name)
    p.set_active(name)  # opening a profile makes it the one navig do / gmail use
    return {"ok": True, "reused": False, "name": name, "port": prof.port, "app": prof.app,
            "real": prof.real, "target": target.to_dict(),
            "note": f"profile '{name}' open on port {prof.port}"}


def profile_use(name: str) -> dict:
    """Set the active profile (subsequent `navig do` / `cdp` default to it)."""
    from navig.browser import profiles as p

    if p.get_profile(name) is None:
        return {"ok": False, "error": f"no profile '{name}' — create it: navig cdp profile new {name}"}
    if not p.set_active(name):  # exists but the write failed
        return {"ok": False, "error": "could not persist the active pointer (disk full or permissions?)"}
    return {"ok": True, "active": name, "note": f"active profile → {name}"}


def profile_close(name: str | None = None, all_profiles: bool = False) -> dict:
    """Close a running profile's browser (by its stable port), or all of them."""
    from navig.browser import profiles as p

    if all_profiles:
        closed = [prof.name for prof in p.list_profiles() if stop(port=prof.port).get("ok")]
        return {"ok": True, "closed": closed,
                "note": (f"closed: {', '.join(closed)}" if closed
                         else "no NAVIG-launched profiles were running")}
    if not name:
        return {"ok": False, "error": "give a profile name or --all"}
    prof = p.get_profile(name)
    if prof is None:
        return {"ok": False, "error": f"no profile '{name}'"}
    r = stop(port=prof.port)
    return {"ok": True, "name": name,
            "note": f"closed '{name}'" if r.get("ok") else f"'{name}' was not a NAVIG-launched browser"}


def profile_remove(name: str, *, delete_data: bool = False) -> dict:
    """Remove a profile from the registry (closing it first); optionally delete its on-disk data."""
    import shutil

    from navig.browser import profiles as p
    from navig.browser import targets as t

    prof = p.get_profile(name)
    if prof is None:
        return {"ok": False, "error": f"no profile '{name}'"}
    stop(port=prof.port)  # best-effort close
    p.remove_profile(name)
    deleted = False
    if delete_data and not prof.real and prof.user_data_dir:
        # Never rmtree a profile whose browser is still up — Windows locks the files
        # and a half-deleted profile results. Refuse if the port is still live.
        if t.probe_port(prof.port, timeout=0.3) is not None:
            return {"ok": True, "removed": name, "data_deleted": False,
                    "note": f"removed '{name}' from the registry — its browser is still running, "
                            f"so on-disk data was NOT deleted (close it, then re-run with --delete-data)"}
        try:
            shutil.rmtree(prof.user_data_dir, ignore_errors=True)
            deleted = True
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "removed": name, "data_deleted": deleted,
            "note": f"removed profile '{name}'" + (" (data deleted)" if deleted else "")}


# ────────────────────────── page actions ──────────────────────────


async def snapshot(port: int = 9222, tab: int | None = None, url: str | None = None) -> dict:
    """Accessibility snapshot with numeric refs for element selection."""
    b = await _bridge(port)
    await _select(b, tab, url)
    text, ref_map = await b.get_a11y_snapshot_with_refs()
    refs = [
        {"ref": rid, "role": node.get("role", ""), "name": node.get("name", "")}
        for rid, node in ref_map.items()
    ]
    return {"ok": True, "active_url": b._page.url if b._page else None, "snapshot": text, "refs": refs}


async def screenshot(port: int = 9222, out: str | None = None,
                     full_page: bool = False, as_base64: bool = False,
                     tab: int | None = None, url: str | None = None) -> dict:
    """Capture the current page — to a file (default) or as base64.

    Falls back to a full-screen OS capture when no CDP target is attached.
    """
    b = await _try_bridge(port)
    if b is not None:
        await _select(b, tab, url)
        if as_base64:
            return {"ok": True, "via": "cdp", "base64": await b.screenshot_base64()}
        path = await b.screenshot(name=out, full_page=full_page)
        return {"ok": True, "via": "cdp", "path": path}
    # OS fallback — full-screen capture.
    try:
        import base64 as _b64
        import io
        from datetime import datetime
        from pathlib import Path

        from navig.adapters.automation.screenshot import capture_full_screen
        from navig.platform.paths import config_dir

        img, backend = capture_full_screen()
        if as_base64:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return {"ok": True, "via": "os-automation", "backend": backend,
                    "base64": _b64.b64encode(buf.getvalue()).decode()}
        name = out or f"cdp_screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        if not name.endswith(".png"):
            name += ".png"
        dest = Path(config_dir()) / "screenshots"
        dest.mkdir(parents=True, exist_ok=True)
        path = str(dest / name)
        img.save(path)
        return {"ok": True, "via": "os-automation", "backend": backend, "path": path}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"no CDP target and OS screenshot failed: {exc}"}


async def click(port: int = 9222, ref: int | None = None,
                x: float | None = None, y: float | None = None,
                button: str = "left", tab: int | None = None, url: str | None = None) -> dict:
    """Click by a11y ref, or at viewport/screen coordinates (x, y).

    Ref clicks require CDP. Coordinate clicks fall back to OS mouse when no
    CDP target is attached.
    """
    b = await _try_bridge(port)
    if b is not None:
        await _select(b, tab, url)
        if ref is not None:
            _text, ref_map = await b.get_a11y_snapshot_with_refs()
            result = await b.click_by_ref(ref, ref_map)
            return {"ok": bool(result.get("ok")), "via": "cdp", **result}
        if x is not None and y is not None:
            await b.click_xy(x, y, button=button)
            return {"ok": True, "via": "cdp", "clicked": {"x": x, "y": y, "button": button}}
        return {"ok": False, "error": "provide either ref or (x, y)"}
    # OS fallback (coordinates only).
    if ref is not None:
        return {"ok": False, "error": "ref click requires an attached CDP target"}
    if x is None or y is None:
        return {"ok": False, "error": "provide either ref or (x, y)"}
    adapter = _os_adapter()
    if adapter is None:
        return {"ok": False, "error": "no CDP target and no OS automation adapter"}
    res = adapter.click(int(x), int(y), button=button)
    return {"ok": bool(getattr(res, "success", False)), "via": "os-automation",
            "clicked": {"x": x, "y": y, "button": button}}


async def type_text(port: int = 9222, text: str = "", tab: int | None = None,
                    url: str | None = None) -> dict:
    """Type text into the focused element (CDP), or via OS keyboard as fallback."""
    b = await _try_bridge(port)
    if b is not None:
        await _select(b, tab, url)
        await b._page.keyboard.type(text)  # noqa: SLF001 (same-package public intent)
        return {"ok": True, "via": "cdp", "typed": len(text)}
    adapter = _os_adapter()
    if adapter is None:
        return {"ok": False, "error": "no CDP target and no OS automation adapter"}
    res = adapter.type_text(text)
    return {"ok": bool(getattr(res, "success", False)), "via": "os-automation", "typed": len(text)}


async def key(port: int = 9222, combo: str = "Enter", tab: int | None = None,
              url: str | None = None) -> dict:
    """Press a key / combination (CDP), or via OS keyboard as fallback."""
    b = await _try_bridge(port)
    if b is not None:
        await _select(b, tab, url)
        await b.key_press(combo)
        return {"ok": True, "via": "cdp", "key": combo}
    adapter = _os_adapter()
    if adapter is None or not hasattr(adapter, "send_keys"):
        return {"ok": False, "error": "no CDP target and no OS automation adapter"}
    res = adapter.send_keys(combo)
    return {"ok": bool(getattr(res, "success", False)), "via": "os-automation", "key": combo}


async def scroll(port: int = 9222, delta_y: float = 300, delta_x: float = 0,
                 tab: int | None = None, url: str | None = None) -> dict:
    """Scroll by a wheel delta (positive delta_y scrolls down)."""
    b = await _bridge(port)
    await _select(b, tab, url)
    await b.scroll_wheel(delta_x, delta_y)
    return {"ok": True, "scrolled": {"x": delta_x, "y": delta_y}}


async def move(port: int = 9222, x: float = 0, y: float = 0,
               tab: int | None = None, url: str | None = None) -> dict:
    """Move the mouse to viewport coordinates (x, y)."""
    b = await _bridge(port)
    await _select(b, tab, url)
    await b.move_mouse(x, y)
    return {"ok": True, "moved": {"x": x, "y": y}}


async def eval_js(port: int = 9222, expression: str = "",
                  tab: int | None = None, url: str | None = None) -> dict:
    """Evaluate a JavaScript expression and return the result."""
    b = await _bridge(port)
    await _select(b, tab, url)
    try:
        result: Any = await b.eval_js(expression)
        return {"ok": True, "active_url": b._page.url if b._page else None, "result": result}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def navigate(port: int = 9222, url: str = "", tab: int | None = None,
                   select_url: str | None = None) -> dict:
    """Navigate a page to *url*. Optionally pick which tab first (tab/select_url)."""
    b = await _bridge(port)
    await _select(b, tab, select_url)
    info = await b.navigate(url)
    return {"ok": True, **info}


async def tabs(port: int = 9222) -> dict:
    """List every open page in the browser (authoritative, from raw /json/list).

    This is the full inventory of what is open — so NAVIG knows everything in the
    browser, even before/without attaching Playwright.
    """
    from navig.browser import targets as t

    pages = t.list_page_targets(port)
    if not pages:
        # Port unreachable or no pages — say so rather than silently empty.
        if t.probe_port(port) is None:
            return {"ok": False, "error": f"no CDP target on port {port}"}
    return {"ok": True, "count": len(pages), "tabs": pages}


async def switch(port: int = 9222, tab: int | None = None, url: str | None = None) -> dict:
    """Make a specific open tab the active target for subsequent actions.

    Select by *tab* index (from `tabs`) or *url* substring. The choice sticks on
    the persistent session, so the agent can `switch` once then act repeatedly.
    """
    b = await _bridge(port)
    return await b.switch_to(index=tab, url=url)


async def login(port: int = 9222, domain: str | None = None, username: str | None = None,
                open_url: str | None = None, tab: int | None = None, url: str | None = None,
                allow_insecure: bool = False, auto_submit: bool = True) -> dict:
    """Auto-login on the attached page using a vaulted website credential.

    Session-first: restores a saved authenticated session if one exists, else
    fills the login form heuristically. Strictly origin-bound and https-only
    (see :mod:`navig.browser.origin_match`). The password is injected
    server-side and is NEVER returned in the result.
    """
    from navig.browser.autofill import auto_login as _auto_login

    b = await _bridge(port)
    await _select(b, tab, url)
    if open_url:
        await b.navigate(open_url)
        await b.wait_for_stable()
    return await _auto_login(b, domain=domain, username=username,
                             allow_insecure=allow_insecure, auto_submit=auto_submit)


async def bring_to_front(port: int = 9222, tab: int | None = None, url: str | None = None) -> dict:
    """Raise the attached browser window to the foreground (so the user sees it)."""
    b = await _try_bridge(port)
    if b is None:
        return {"ok": False, "error": f"no CDP target on port {port}"}
    await _select(b, tab, url)
    return {"ok": bool(await b.bring_to_front())}


async def gmail_compose(port: int = 9222, *, to: str = "", subject: str = "", body: str = "",
                        cc: str = "", bcc: str = "", send: bool = False, account: int | str = 0,
                        tab: int | None = None, url: str | None = None) -> dict:
    """Compose (and optionally send) a Gmail message on the attached profile via the deep-link.

    Requires the profile to be signed into Gmail. Sending is off unless *send* is True.
    *account* selects which Gmail account (email address or index) for a multi-account profile.
    """
    from navig.browser.recipes import gmail as _gmail

    b = await _bridge(port)
    await _select(b, tab, url)
    return await _gmail.compose(b, to=to, subject=subject, body=body, cc=cc, bcc=bcc,
                                send=send, account=account)


async def inject_script(port: int = 9222, script: str = "", tab: int | None = None,
                        url: str | None = None) -> dict:
    """Register a persistent *userscript* on the attached browser.

    Uses CDP ``Page.addScriptToEvaluateOnNewDocument`` (via Playwright
    ``add_init_script``) so the script re-runs at document-start on every
    navigation, in current and future pages — a real userscript, unlike the
    one-shot :func:`eval_js`. Also runs once on the current page so it takes
    effect immediately.
    """
    b = await _bridge(port)
    await _select(b, tab, url)
    if not (script or "").strip():
        return {"ok": False, "error": "empty script"}
    await b.add_init_script(script)
    try:
        await b._page.evaluate("() => {" + script + "}")  # immediate run on current page
    except Exception as exc:  # noqa: BLE001
        return {"ok": True, "persisted": True, "immediate_run_error": str(exc)}
    return {"ok": True, "persisted": True, "active_url": b._page.url if b._page else None}
