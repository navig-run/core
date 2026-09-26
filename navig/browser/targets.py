"""
CDP target discovery + Chromium/Electron app launch.

Two jobs, both localhost-only:

1. **Discover** already-running CDP endpoints — any Chrome/Edge/Brave or Electron
   app (Discord, Notion, Slack, VS Code) started with ``--remote-debugging-port``
   answers HTTP on ``/json/version`` and ``/json/list``. We scan candidate ports.

2. **Launch** a known app (or an explicit binary) *with* a debug port so we can
   attach. Electron apps hold a single-instance lock, so a normally-running
   instance ignores the flag — we must quit it first and relaunch (the caller is
   responsible for warning/confirming; :func:`terminate_app` is exposed for that).

This module is deliberately synchronous (discovery is a fast loopback GET, launch
is a ``Popen``). The attach + action layer is async (``CDPBridge``); the session
manager bridges the two.

Security: CDP grants full control of the target, so we only ever talk to
loopback (127.0.0.1 / ::1). :func:`is_loopback_endpoint` guards any external URL.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlparse

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# Default ports we scan when discovering running CDP targets. 9222 is Chrome's
# convention; 9223-9229 leave room for several apps launched side by side.
DEFAULT_SCAN_PORTS: tuple[int, ...] = (9222, 9223, 9224, 9225, 9226, 9227, 9228, 9229)

# Flags that keep a navig-launched automation Chromium QUIET on startup: no first-run /
# welcome tab, no "make Chrome your default" nag, and no EU "choose a search engine" choice
# screen (Chrome 120+). Chrome ignores flags it doesn't recognise, so these are safe on every
# channel/version, and command-line flags aren't visible to page JS (no stealth cost).
# `--disable-features=…` does NOT belong in this list: Chrome honours only the LAST such
# switch, so two callers each adding their own silently cancel each other. Features go in
# CHROMIUM_DISABLED_FEATURES below and are folded into ONE switch by `merge_disable_features`.
CHROMIUM_QUIET_ARGS: tuple[str, ...] = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-search-engine-choice-screen",
    # Every NAVIG close leaves `Preferences → profile.exit_type = "Crashed"` — measured on
    # both a headless AND a headed profile after a clean CDP `Browser.close` (2026-09-18):
    # Chrome writes "Crashed" at startup and only rewrites "Normal" on a shutdown path that
    # `Browser.close` does not take. On the next HEADED launch that state raises the
    # "Chrome didn't shut down correctly — Restore pages?" bubble, an infobar that shifts
    # the page and can intercept the first click. This is Chrome's own switch for it.
    "--hide-crash-restore-bubble",
)

# Chrome features that make an automation profile download Chrome's on-device AI model
# (`OptGuideOnDeviceModel` — Gemini Nano, ~4 GB) plus optimization hints into EVERY
# user-data-dir it is launched with. Measured on the operator's machine (2026-09-15): two
# named profiles held **4,072 MB each** of the identical model, against **6 MB and 1 MB** of
# actual login state — 8.4 GB of an 11.7 GB "profile" footprint was Chrome's model, not
# profiles. Nothing navig drives uses on-device AI. Playwright disables exactly these four by
# default; the raw launch never did, which is why its profiles bloat and Playwright's don't.
# Chrome ignores feature names it doesn't know, so this is safe across channels/versions.
CHROMIUM_DISABLED_FEATURES: tuple[str, ...] = (
    "OptimizationGuideModelDownloading",
    "OptimizationHintsFetching",
    "OptimizationTargetPrediction",
    "OptimizationHints",
)


def merge_disable_features(args: list[str], base: tuple[str, ...] | list[str] = ()) -> list[str]:
    """Collapse every ``--disable-features=`` in *args* (plus *base*) into ONE switch.

    Chrome keeps only the last ``--disable-features`` it sees. The extension loader adds
    ``DisableLoadExtensionCommandLineSwitch`` (without it ``--load-extension`` is silently
    dropped on Stable 137+); a base list adding its own switch would have clobbered that —
    which is why the base list carried none, and why every profile downloaded a 4 GB model.
    Order-preserving union, de-duplicated, emitted once at the end. Switches other than
    ``--disable-features`` pass through untouched, in their original order.
    """
    feats: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        for name in value.split(","):
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                feats.append(name)

    for name in base:
        _add(name)
    out: list[str] = []
    for arg in args:
        if arg.startswith("--disable-features="):
            _add(arg[len("--disable-features="):])
        else:
            out.append(arg)
    if feats:
        out.append("--disable-features=" + ",".join(feats))
    return out

# How long to wait for a freshly launched app's debug port to come up.
LAUNCH_WAIT_S = 15.0
LAUNCH_POLL_INTERVAL_S = 0.4


@dataclass
class CDPTab:
    """One CDP page/target inside a browser or Electron app."""

    id: str
    title: str
    url: str
    type: str
    ws_url: str


@dataclass
class CDPTarget:
    """A reachable CDP endpoint (a browser or Electron app) on a debug port."""

    port: int
    browser: str  # e.g. "Chrome/120.0" or "Electron/28.0" (from /json/version)
    endpoint: str  # "http://127.0.0.1:<port>"
    tabs: list[CDPTab] = field(default_factory=list)
    app: str | None = None  # known app id if we launched/recognised it
    kind: str = "browser"  # "browser" | "node" | "other" (see classify_kind)

    @property
    def attachable(self) -> bool:
        """True for Chromium/Electron surfaces Playwright can drive (not Node)."""
        return self.kind == "browser"

    def to_dict(self) -> dict:
        return {
            "port": self.port,
            "browser": self.browser,
            "endpoint": self.endpoint,
            "app": self.app,
            "kind": self.kind,
            "attachable": self.attachable,
            "tabs": [
                {"id": t.id, "title": t.title, "url": t.url, "type": t.type}
                for t in self.tabs
            ],
        }


def classify_kind(browser: str, tabs: list[CDPTab]) -> str:
    """Classify a CDP endpoint so we don't try to drive a Node inspector.

    Chromium/Electron expose ``page`` targets and a Chrome/Electron ``Browser``
    string; Node/wrangler inspectors expose ``node`` targets and a node.js
    ``Browser`` string. Playwright's connect_over_cdp only works on the former.
    """
    b = browser.lower()
    if any(k in b for k in ("chrome", "chromium", "edg", "brave", "electron", "headless")):
        return "browser"
    if "node" in b or "wrangler" in b or any(t.type == "node" for t in tabs):
        return "node"
    if any(t.type == "page" for t in tabs):
        return "browser"
    return "other"


# ────────────────────────── loopback guard ──────────────────────────


def is_loopback_endpoint(url: str) -> bool:
    """True only if *url* is http(s) pointing at loopback (127.0.0.0/8, ::1)."""
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        addr = info[4][0]
        if not (addr.startswith("127.") or addr == "::1"):
            return False
    return bool(infos)


# ────────────────────────── discovery ──────────────────────────


def _http_get_json(url: str, timeout: float = 1.5) -> object | None:
    """GET *url* and parse JSON. Returns None on any failure (port closed, etc.)."""
    if not is_loopback_endpoint(url):
        logger.warning("[cdp.targets] Refusing non-loopback CDP URL: %s", url)
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (loopback-guarded)
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def probe_port(port: int, timeout: float = 1.5) -> CDPTarget | None:
    """Probe one localhost port for a live CDP endpoint. None if nothing there."""
    endpoint = f"http://127.0.0.1:{port}"
    version = _http_get_json(f"{endpoint}/json/version", timeout=timeout)
    if not isinstance(version, dict):
        return None
    browser = str(version.get("Browser", "unknown"))
    tabs: list[CDPTab] = []
    listing = _http_get_json(f"{endpoint}/json/list", timeout=timeout)
    if isinstance(listing, list):
        for item in listing:
            if not isinstance(item, dict):
                continue
            tabs.append(
                CDPTab(
                    id=str(item.get("id", "")),
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    type=str(item.get("type", "")),
                    ws_url=str(item.get("webSocketDebuggerUrl", "")),
                )
            )
    return CDPTarget(port=port, browser=browser, endpoint=endpoint, tabs=tabs,
                     kind=classify_kind(browser, tabs))


def discover_targets(ports: tuple[int, ...] | list[int] = DEFAULT_SCAN_PORTS) -> list[CDPTarget]:
    """Scan *ports* on localhost and return every live CDP endpoint found."""
    found: list[CDPTarget] = []
    for port in ports:
        target = probe_port(port)
        if target is not None:
            found.append(target)
    return found


def attachable_targets(
    ports: tuple[int, ...] | list[int] = DEFAULT_SCAN_PORTS,
) -> list[CDPTarget]:
    """Live endpoints Playwright can actually drive (browsers/Electron, not Node)."""
    return [t for t in discover_targets(ports) if t.attachable]


def list_page_targets(port: int = 9222, timeout: float = 1.5) -> list[dict]:
    """Full inventory of page targets on *port* straight from raw ``/json/list``.

    This is the authoritative "what is open in the browser" list — it includes
    every tab, even ones Playwright's connect_over_cdp might group differently.
    Returns ``[{index, id, title, url, type}]`` (page-type targets only).
    """
    listing = _http_get_json(f"http://127.0.0.1:{port}/json/list", timeout=timeout)
    out: list[dict] = []
    if isinstance(listing, list):
        idx = 0
        for item in listing:
            if not isinstance(item, dict) or item.get("type") != "page":
                continue
            out.append({
                "index": idx,
                "id": str(item.get("id", "")),
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "type": str(item.get("type", "")),
            })
            idx += 1
    return out


# ────────────────────────── known apps ──────────────────────────

# app id → per-OS list of candidate executable paths (glob patterns allowed).
# Chromium browsers and Electron apps both accept --remote-debugging-port.
_ENV = os.environ.get
_LOCALAPPDATA = _ENV("LOCALAPPDATA", "")
_PROGRAMFILES = _ENV("ProgramFiles", r"C:\Program Files")
_PROGRAMFILES_X86 = _ENV("ProgramFiles(x86)", r"C:\Program Files (x86)")

KNOWN_APPS: dict[str, dict[str, list[str]]] = {
    "chrome": {
        "win32": [
            rf"{_PROGRAMFILES}\Google\Chrome\Application\chrome.exe",
            rf"{_PROGRAMFILES_X86}\Google\Chrome\Application\chrome.exe",
        ],
        "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        "linux": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    },
    "edge": {
        "win32": [rf"{_PROGRAMFILES_X86}\Microsoft\Edge\Application\msedge.exe",
                  rf"{_PROGRAMFILES}\Microsoft\Edge\Application\msedge.exe"],
        "darwin": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
        "linux": ["microsoft-edge", "microsoft-edge-stable"],
    },
    "brave": {
        "win32": [rf"{_LOCALAPPDATA}\BraveSoftware\Brave-Browser\Application\brave.exe",
                  rf"{_PROGRAMFILES}\BraveSoftware\Brave-Browser\Application\brave.exe"],
        "darwin": ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"],
        "linux": ["brave-browser", "brave"],
    },
    "discord": {
        # Discord installs into versioned app-* folders; pick the newest.
        "win32": [rf"{_LOCALAPPDATA}\Discord\app-*\Discord.exe"],
        "darwin": ["/Applications/Discord.app/Contents/MacOS/Discord"],
        "linux": ["discord", "/usr/bin/discord", "/opt/discord/Discord"],
    },
    "notion": {
        "win32": [rf"{_LOCALAPPDATA}\Programs\Notion\Notion.exe"],
        "darwin": ["/Applications/Notion.app/Contents/MacOS/Notion"],
        "linux": ["notion-app", "notion"],
    },
    "slack": {
        "win32": [rf"{_LOCALAPPDATA}\slack\slack.exe",
                  rf"{_PROGRAMFILES}\Slack\slack.exe"],
        "darwin": ["/Applications/Slack.app/Contents/MacOS/Slack"],
        "linux": ["slack", "/usr/bin/slack"],
    },
    "vscode": {
        "win32": [rf"{_LOCALAPPDATA}\Programs\Microsoft VS Code\Code.exe"],
        "darwin": ["/Applications/Visual Studio Code.app/Contents/MacOS/Electron"],
        "linux": ["code", "/usr/bin/code"],
    },
}

# Process image names used to detect/terminate a running instance (for relaunch).
_APP_PROCESS_NAMES: dict[str, str] = {
    "chrome": "chrome",
    "edge": "msedge",
    "brave": "brave",
    "discord": "Discord",
    "notion": "Notion",
    "slack": "slack",
    "vscode": "Code",
}

# Chromium *browsers* (as opposed to Electron apps). Modern Chrome/Edge/Brave
# (M136+) refuse remote debugging on the DEFAULT user-data-dir for security, so
# we launch browsers with a dedicated NAVIG debug profile by default. Electron
# apps are NOT in this set: they must use their own (logged-in) profile.
BROWSER_APPS: frozenset[str] = frozenset({"chrome", "edge", "brave"})


def default_debug_profile_dir(app: str) -> str:
    """Dedicated user-data-dir for a browser's NAVIG debugging profile.

    Using a separate dir is what makes remote debugging work on modern Chrome.
    The profile persists, so logins done here stick across sessions.
    """
    from navig.platform.paths import config_dir

    path = config_dir() / "cdp-profiles" / app
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def resolve_executable(app_id: str) -> str | None:
    """Resolve a known app id to an existing executable path (or None).

    Globs are expanded and the lexically-greatest match wins (newest app-* dir).
    Bare command names are resolved via PATH (``shutil.which``).
    """
    spec = KNOWN_APPS.get(app_id)
    if not spec:
        return None
    candidates = spec.get(sys.platform, [])
    for candidate in candidates:
        if any(ch in candidate for ch in "*?["):
            matches = sorted(glob.glob(candidate))
            if matches:
                return matches[-1]
            continue
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return None


def known_app_ids() -> list[str]:
    """List the app ids this build knows how to launch."""
    return sorted(KNOWN_APPS.keys())


# ────────────────────────── launch / terminate ──────────────────────────


def is_running(app_id: str) -> bool:
    """Best-effort check whether a known app currently has a running process."""
    name = _APP_PROCESS_NAMES.get(app_id)
    if not name:
        return False
    try:
        import psutil  # type: ignore

        needle = name.lower()
        for proc in psutil.process_iter(["name"]):
            pname = (proc.info.get("name") or "").lower()
            if pname == needle or pname == f"{needle}.exe":
                return True
        return False
    except ImportError:
        # No psutil — fall back to platform process listing.
        try:
            if sys.platform == "win32":
                # BYTES, not text=True. tasklist writes the OEM code page (866 here) while
                # text=True decodes with the ANSI one (cp1251) — and its output is not pure
                # ASCII: measured 85 non-ASCII bytes in 6710 for a single filtered query,
                # from the localized header. The mismatch is harmless for an ASCII needle,
                # but the obvious "fix" of encoding="utf-8" RAISES on those bytes, which
                # this except: would swallow into "the process is not running".
                # An image name is ASCII, so compare without decoding at all and the
                # question of which code page it is never arises.
                out = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {name}.exe"],
                    capture_output=True, timeout=5,
                )
                return name.lower().encode() in out.stdout.lower()
            out = subprocess.run(["pgrep", "-fi", name], capture_output=True, text=True, timeout=5)
            return out.returncode == 0
        except Exception:  # noqa: BLE001
            return False


def terminate_app(app_id: str) -> bool:
    """Terminate a running instance of a known app so it can be relaunched.

    ⚠ **This is an IMAGE-NAME kill — `taskkill /IM chrome.exe /F` — not a scoped one.** For
    an Electron app (Discord, Notion, Slack) that is the point: the running instance holds a
    single-instance lock, and it must go before the app can reopen with a debug port. For a
    **browser** it means every window of that browser dies, including the operator's own
    everyday session and all its tabs.

    It is deliberately NOT scoped to NAVIG-launched PIDs. Scoping it would break the only
    thing it does: by definition it targets an app NAVIG did not launch. So the safety lives
    in the caller — ``force_restart`` is opt-in, the CLI confirms first, and the MCP
    ``cdp_launch`` tool is classified ``dangerous``. Callers MUST warn/confirm, and the
    warning must say that a browser loses ALL its windows, not "the current window".

    For a browser you almost never need this: Chrome's single-instance lock is per
    ``--user-data-dir``, so ``navig cdp new`` (its own profile) launches alongside the
    operator's browser without touching it.

    Returns True if a terminate command was issued.
    """
    name = _APP_PROCESS_NAMES.get(app_id)
    if not name:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/IM", f"{name}.exe", "/F"],
                           capture_output=True, timeout=10)
        else:
            subprocess.run(["pkill", "-i", name], capture_output=True, timeout=10)
        # Give the OS a moment to release the single-instance lock.
        time.sleep(1.0)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[cdp.targets] terminate_app(%s) failed: %s", app_id, exc)
        return False


def _build_launch_args(exe: str, app: str, port: int, user_data_dir: str | None,
                       profile_directory: str | None,
                       extra_args: list[str] | None) -> list[str]:
    """Build the launch argv. Browser apps also get the quiet first-run flags; Electron
    apps don't (they have no first-run/search-engine screens)."""
    args = [exe, f"--remote-debugging-port={port}"]
    if user_data_dir:
        args.append(f"--user-data-dir={user_data_dir}")
    if profile_directory:
        # Pick a specific named profile inside the user-data-dir (real-Chrome path).
        args.append(f"--profile-directory={profile_directory}")
    if app in BROWSER_APPS:
        # no first-run/welcome tab, no default-browser nag, no EU search-engine choice screen
        args.extend(CHROMIUM_QUIET_ARGS)
    if extra_args:
        args.extend(extra_args)
    # ONE --disable-features, or Chrome keeps only the last. Browsers also get the base set
    # that stops the 4 GB on-device-model download; Electron apps are not Chrome-the-browser
    # and never fetch it, so they only get their own switches merged.
    return merge_disable_features(args, CHROMIUM_DISABLED_FEATURES if app in BROWSER_APPS else ())


def launch_with_cdp(
    app: str,
    port: int = 9222,
    *,
    user_data_dir: str | None = None,
    profile_directory: str | None = None,
    extra_args: list[str] | None = None,
    wait: bool = True,
) -> CDPTarget | None:
    """Launch a known app id (or an explicit executable path) with a debug port.

    Args:
        app: A known app id (``chrome``/``discord``/``notion``/…) or an absolute
            path to an executable.
        port: Debug port to expose (``--remote-debugging-port``).
        user_data_dir: Optional profile dir. Chromium browsers accept it for an
            isolated automation profile; leave None for Electron apps so they use
            the user's logged-in profile.
        extra_args: Extra CLI args passed to the executable.
        wait: Poll until the debug port answers before returning.

    Returns:
        The attached :class:`CDPTarget` if the port came up, else None.
    """
    exe = app if os.path.isabs(app) else resolve_executable(app)
    if not exe or not os.path.exists(exe):
        logger.warning("[cdp.targets] Could not resolve executable for %r", app)
        return None

    # Browsers need a dedicated user-data-dir (modern Chrome refuses remote
    # debugging on the default profile). Electron apps keep their own profile.
    if user_data_dir is None and app in BROWSER_APPS:
        user_data_dir = default_debug_profile_dir(app)

    args = _build_launch_args(exe, app, port, user_data_dir, profile_directory, extra_args)

    logger.info("[cdp.targets] Launching %s on CDP port %d", exe, port)
    try:
        creationflags = 0
        if sys.platform == "win32":
            # DETACHED so the app outlives this CLI process.
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
        proc = subprocess.Popen(  # noqa: S603 (resolved local executable)
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=(sys.platform != "win32"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[cdp.targets] Launch failed: %s", exc)
        return None

    # Track what WE launched so `stop` can close exactly this browser (and only
    # it) instead of every Chrome on the machine. `headless` is read off the argv we are
    # about to run rather than taken as a parameter, so it records what was actually
    # launched and cannot drift from the command line. The idle reaper uses it: a browser
    # with a VISIBLE window was asked for by a human, and is never reaped for idleness.
    _headless = any(str(a).startswith("--headless") for a in args)
    record_launched(port, proc.pid, app if not os.path.isabs(app) else exe, user_data_dir,
                    headless=_headless)

    if not wait:
        return CDPTarget(port=port, browser="pending", endpoint=f"http://127.0.0.1:{port}",
                         app=app if not os.path.isabs(app) else None)

    deadline = time.monotonic() + LAUNCH_WAIT_S
    while time.monotonic() < deadline:
        target = probe_port(port)
        if target is not None:
            target.app = app if not os.path.isabs(app) else None
            # The PID we recorded above is the one we spawned — but on Windows that
            # is a LAUNCHER that has already exited, handing off to the real browser
            # under a different PID. Now that the port answers, resolve the process
            # actually serving it and record THAT, so `cdp launched` (and anything
            # else reading the registry) reports a PID that exists.
            # exe_hint matters for Electron apps: they carry no navig user-data-dir, so
            # without it this would resolve ANY process on the port — and record it as ours.
            real = _debug_browser_pids(port, user_data_dir, exe_hint=exe)
            if real and real[0] != proc.pid:
                logger.debug("[cdp.targets] port %d: launcher pid %d → real browser pid %d",
                             port, proc.pid, real[0])
                record_launched(port, real[0], app if not os.path.isabs(app) else exe,
                                user_data_dir, headless=_headless)
            logger.info("[cdp.targets] %s is up on port %d (%s)", app, port, target.browser)
            return target
        time.sleep(LAUNCH_POLL_INTERVAL_S)

    logger.warning("[cdp.targets] %s did not expose CDP on port %d within %.0fs",
                   app, port, LAUNCH_WAIT_S)
    return None


def platform_name() -> str:
    """Human-readable platform label for status output."""
    return f"{platform.system()} {platform.release()}"


# ────────────────────────── launched-process registry ──────────────────────────
#
# Persisted so `navig cdp stop` (a separate CLI process) can close exactly the
# browser NAVIG started — by PID — rather than killing every Chrome by image name.


def _launched_registry_path():
    from navig.platform.paths import config_dir

    return config_dir() / "cdp-launched.json"


def _read_launched() -> dict:
    path = _launched_registry_path()
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _write_launched(data: dict) -> None:
    path = _launched_registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[cdp.targets] Could not persist launched registry: %s", exc)


def record_launched(port: int, pid: int, app: str, user_data_dir: str | None,
                    *, headless: bool | None = None) -> None:
    """Remember a browser NAVIG launched (keyed by debug port).

    ``headless`` and ``last_used`` are ADDITIVE — an entry written by an older navig
    carries neither, and every reader here treats their absence as "unknown" rather than
    as a value. That matters because the reaper's decisions are all of the form "act only
    on what I can prove", so an unknown must never read as a licence to close something.
    """
    data = _read_launched()
    now = int(time.time())
    entry = {"pid": pid, "app": app, "user_data_dir": user_data_dir,
             "started": now, "last_used": now}
    if headless is not None:
        entry["headless"] = bool(headless)
    data[str(port)] = entry
    _write_launched(data)


def touch_launched(port: int) -> None:
    """Mark the browser on *port* as used just now (best-effort).

    The registry file is shared by every navig process, so a CLI verb, an MCP tool call
    and the daemon all keep the SAME entry warm. That is what makes "idle" mean "nobody
    anywhere has touched this", rather than "this one process has not".
    """
    data = _read_launched()
    entry = data.get(str(port))
    if not isinstance(entry, dict):
        return
    entry["last_used"] = int(time.time())
    _write_launched(data)


def get_launched() -> dict:
    """Return the map of NAVIG-launched debug browsers ``{port: {...}}``."""
    return _read_launched()


def remove_launched(port: int) -> None:
    data = _read_launched()
    if data.pop(str(port), None) is not None:
        _write_launched(data)


def _is_named_profile(user_data_dir: str | None) -> bool:
    """Is this profile a PERSISTENT named one (``cdp-profiles/named/<name>``)?

    Named profiles hold real logins the operator did by hand, which is exactly what they
    are for — so they are never reaped for idleness. Compared on normalised path parts
    rather than a substring, so a session profile that merely happens to contain the word
    "named" somewhere is not misread.
    """
    if not user_data_dir:
        return False
    parts = os.path.normpath(user_data_dir).replace("\\", "/").lower().split("/")
    return "named" in parts and "cdp-profiles" in parts


# A genuine recorded process was created BEFORE record_launched() ran, so this only has to
# absorb clock skew between psutil's create_time and time.time() — not a real elapsed window.
_PID_REUSE_SLACK_SECONDS = 60.0


def _pid_is_still_the_recorded_process(pid: int, entry: dict) -> bool:
    """Is *pid* still the process we recorded at launch — or one that RECYCLED its number?

    ``stop_launched`` terminates the tracked PID **and its whole process tree**, and the PID it
    tracks is, by this module's own admission, usually a corpse: on Windows the ``chrome.exe`` we
    ``Popen`` is a launcher that exits within ~100 ms. A dead PID is immediately available for
    reuse, ``cdp-launched.json`` lives in the config dir and outlives reboots, and nothing here
    checked identity — so ``navig cdp stop`` could terminate an arbitrary unrelated process tree
    that merely inherited the number. Verified against a plain ``python -c "sleep(60)"``: killed,
    reported ``True``. That breaks the promise ``cdp_actions.stop`` prints in its own docstring —
    "it never kills unrelated browsers".

    Two independent signals, both required:
      * **create_time** — the decisive one. A recycled PID belongs to a process that necessarily
        started *after* ours died, hence after we recorded it.
      * **identity** — it must still look like what we launched (our profile dir, the executable
        we recorded, or a debug-port flag), in case the clock is untrustworthy.

    Refusing is safe and is the default for anything unreadable: ``stop_launched`` still sweeps
    the processes genuinely serving the port and still verifies by probing it, so a PID we
    decline to kill costs nothing — while a PID we kill wrongly costs the operator a live process.
    """
    try:
        import psutil  # type: ignore  # noqa: PLC0415
    except ImportError:
        return False  # cannot identify -> must not kill

    try:
        proc = psutil.Process(pid)
        created = proc.create_time()
        name = proc.name()
        cmdline = " ".join(proc.cmdline() or [])
    except Exception:  # noqa: BLE001 - gone, or unreadable (access denied): either way, not ours to kill
        return False

    started = entry.get("started")
    if not isinstance(started, (int, float)):
        return False  # no launch timestamp -> identity cannot be proven
    if created > started + _PID_REUSE_SLACK_SECONDS:
        logger.warning(
            "[cdp.targets] refusing to kill pid %s: created after we recorded it (%s > %s) — "
            "the PID was recycled and now belongs to %r",
            pid, int(created), int(started), name,
        )
        return False

    user_data_dir = entry.get("user_data_dir") or ""
    if user_data_dir and user_data_dir in cmdline:
        return True
    if "--remote-debugging-port" in cmdline:
        return True
    app = entry.get("app") or ""
    if app and _exe_matches(name, app):
        return True

    logger.warning(
        "[cdp.targets] refusing to kill pid %s (%r): it no longer matches the browser we "
        "recorded for this port", pid, name,
    )
    return False


def _terminate_pid(pid: int) -> bool:
    """Terminate a process **and its children** by PID. Returns whether it is actually GONE.

    The return value is consumed as a fact — ``stop_all_launched`` reports it to the operator as
    ``orphans_reclaimed`` — so it must mean "gone", not "we tried". It used to return ``True``
    from three places that verified nothing: after ``proc.kill()`` with no second wait, after only
    ``terminate()``-ing the children (one that ignores SIGTERM outlives the sweep), and from the
    catch-all handler, which read an access-denied failure as "already gone".
    """
    try:
        import psutil  # type: ignore  # noqa: PLC0415
    except ImportError:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            else:
                os.kill(pid, 15)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cdp.targets] terminate pid %s failed: %s", pid, exc)
            return False

    try:
        proc = psutil.Process(pid)
        children = proc.children(recursive=True)
    except psutil.NoSuchProcess:
        return True  # genuinely gone
    except Exception as exc:  # noqa: BLE001 - access denied etc: we could not even look
        logger.warning("[cdp.targets] cannot inspect pid %s: %s", pid, exc)
        return False

    # Snapshot taken above, before anything is signalled: once the parent exits its tree can no
    # longer be walked, so a child killed later has to be reached through a handle we already hold.
    for child in children:
        try:
            child.terminate()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass

    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001 - did not go quietly; escalate the WHOLE tree, not just it
        for child in children:
            try:
                child.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass

    try:
        gone = not proc.is_running()
    except Exception:  # noqa: BLE001 - the handle is unusable, which means the process is gone
        gone = True
    if not gone:
        logger.warning("[cdp.targets] pid %s survived terminate and kill", pid)
    return gone


def _exe_matches(argv0: str, hint: str) -> bool:
    """Whether *argv0* is the executable *hint* names.

    *hint* is what the registry stored: either a short app id (``"discord"``) or an absolute
    executable path. Compared on basename, case-insensitively — Windows argv carries
    ``…\\Discord.exe`` for the id ``discord``.
    """
    name = os.path.basename(argv0).lower()
    wanted = os.path.basename(hint).lower()
    if not name or not wanted:
        return False
    return name == wanted or os.path.splitext(name)[0] == os.path.splitext(wanted)[0]


def _debug_browser_pids(
    port: int, user_data_dir: str | None, *, exe_hint: str | None = None
) -> list[int]:
    """PIDs of the REAL browser processes serving *port* THAT WE CAN ATTRIBUTE TO OURSELVES.

    Selection is by ``--remote-debugging-port=<port>`` plus a second, identifying signal —
    never by process name alone, and never by the port alone:

    * the unique ``--user-data-dir`` we recorded, when there is one; or
    * *exe_hint* — the executable we launched — when there is not.

    That second signal is not optional. ``--user-data-dir`` is only defaulted for
    ``BROWSER_APPS`` (chrome/edge/brave); an **Electron** app (discord/notion/slack/vscode) keeps
    its own profile, so it is recorded with ``user_data_dir=None``. Matching on the port alone
    then selected ANY process serving that port — including another tool's deliberately-debugged
    browser that happens to sit there — and ``stop_launched`` terminates whatever this returns.
    That is precisely what ``navig cdp status`` promises never happens ("NAVIG never touches a
    browser it did not launch"). With neither signal available we return nothing: refusing to
    kill costs a leaked browser we report honestly; killing a stranger costs the operator their
    session.

    Renderer/GPU/utility children carry ``--type=`` and die with their parent, so only the MAIN
    processes are returned.
    """
    if not user_data_dir and not exe_hint:
        return []  # unattributable — never sweep a port we cannot claim

    try:
        import psutil  # type: ignore
    except ImportError:
        return []

    needle_port = f"--remote-debugging-port={port}"
    needle_dir = f"--user-data-dir={user_data_dir}" if user_data_dir else None

    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            argv = proc.info.get("cmdline") or []
            if not argv:
                continue
            cmd = " ".join(argv)
            if needle_port not in cmd:
                continue
            if needle_dir:
                if needle_dir not in cmd:
                    continue
            elif not _exe_matches(argv[0], exe_hint or ""):
                continue  # serving our port, but it is not the app we launched
            if any(a.startswith("--type=") for a in argv):
                continue  # a child (renderer/gpu/utility) — reaped with its parent
            pids.append(int(proc.info["pid"]))
        except Exception:  # noqa: BLE001 — a process can vanish mid-iteration
            continue
    return pids


#: How long a graceful `Browser.close` gets before the kill path takes over. Chrome flushes
#: cookies/site data and exits in well under a second when idle; a page mid-navigation or a
#: hung renderer can take longer, and past this we stop waiting rather than hang a `stop`.
GRACEFUL_CLOSE_S = 5.0


def request_browser_close(port: int, *, timeout: float = GRACEFUL_CLOSE_S) -> bool:
    """Ask the browser on *port* to shut down CLEANLY via CDP ``Browser.close``, and wait
    for its debug port to go dark. Returns whether it did.

    Every close path in this module used to be a process kill — and Chrome records a kill
    as a crash (``Preferences → profile.exit_type = "Crashed"``). Two consequences, both
    seen on the operator's own profiles: the next headed launch shows the "Chrome didn't
    shut down correctly — restore pages?" bubble, and **a login can be lost**. Chrome
    persists cookies with periodic flushes (~30 s) plus a final one on clean shutdown; a
    kill inside that window discards whatever the last page wrote — which for
    ``navig cdp login`` followed by ``stop`` is precisely the cookie the login produced.

    ``Browser.close`` is Chrome's own orderly shutdown: it flushes and exits. This function
    never kills anything; it asks, then waits. The caller falls through to the existing
    identity-checked kill when the answer is no — that path is unchanged and still the thing
    that PROVES closure. Best-effort by design: no browser, no ``aiohttp``, a refused
    socket, a hung renderer — all return ``False`` and cost at most *timeout* seconds.
    """
    target = probe_port(port, timeout=1.0)
    if target is None:
        return True  # nothing is serving; there is nothing to ask
    endpoint = getattr(target, "endpoint", None)
    if not isinstance(endpoint, str) or not endpoint.startswith("http"):
        return False  # a probe result we cannot address is one we cannot ask
    # The BROWSER-level socket lives in /json/version; CDPTarget only carries the tabs' urls.
    try:
        with urllib.request.urlopen(f"{endpoint}/json/version", timeout=1.0) as resp:
            ws_url = str(json.loads(resp.read().decode("utf-8")).get("webSocketDebuggerUrl") or "")
    except (OSError, ValueError, urllib.error.URLError):
        return False
    if not ws_url.startswith("ws://"):
        return False
    try:
        import aiohttp  # noqa: PLC0415 — a core dependency, but keep `navig help` fast

        from navig.browser import cdp_runtime  # noqa: PLC0415
    except ImportError:
        return False

    async def _ask() -> None:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=2.0, max_msg_size=0) as ws:
                await ws.send_json({"id": 1, "method": "Browser.close"})
                # Chrome may answer, or may just drop the socket as it exits. Either is
                # "asked"; the proof is the port going dark below, not this reply.
                try:
                    await asyncio.wait_for(ws.receive(), timeout=1.0)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    pass

    try:
        cdp_runtime.run(_ask(), timeout=3.0)
    except Exception as exc:  # noqa: BLE001 — asking is best-effort; the kill path is the guarantee
        logger.debug("[cdp.targets] Browser.close on port %d not delivered: %r", port, exc)
        return False

    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        if probe_port(port, timeout=0.4) is None:
            return True
        time.sleep(0.2)
    return probe_port(port, timeout=0.4) is None


def _is_throwaway_session_dir(user_data_dir: str | None) -> bool:
    """Is *user_data_dir* one of ours under ``cdp-profiles/sessions/``? Named profiles live
    under ``named/`` and hold logins; only ``sessions/`` is disposable. A path outside the
    profile root — a temp dir, the operator's real Chrome — is never ours to delete."""
    if not user_data_dir:
        return False
    try:
        p = os.path.normcase(os.path.abspath(str(user_data_dir)))
        root = os.path.normcase(os.path.abspath(os.path.join(_profile_root(), "sessions")))
    except (OSError, ValueError):
        return False
    return p.startswith(root + os.sep) and os.path.basename(p) != ""


def _reap_throwaway_dir(port: int, user_data_dir: str, *, exe_hint: str | None,
                        wait_s: float = 5.0) -> bool:
    """Delete a closed browser's throwaway profile dir, once its processes are gone.

    "Port dark" precedes "files released" by a second or two — Chrome closes its debug
    socket first and its renderer/GPU children after. Deleting under a process that still
    holds the directory fails on Windows, so wait (bounded) for the pids serving this port
    with this profile dir to disappear, then remove. Best-effort: a dir that still will not
    go is left for ``navig cdp profile prune``, which sweeps sessions nothing is using.
    """
    deadline = time.monotonic() + max(0.0, wait_s)
    while time.monotonic() < deadline:
        if not _debug_browser_pids(port, user_data_dir, exe_hint=exe_hint):
            break
        time.sleep(0.25)
    try:
        shutil.rmtree(user_data_dir)
        return True
    except OSError as exc:
        logger.debug("[cdp.targets] throwaway profile %s not removed yet: %s", user_data_dir, exc)
        return False


def stop_launched(port: int) -> dict:
    """Close the NAVIG-launched debug browser on *port* — and PROVE it closed.

    The tracked PID is not enough, and trusting it silently leaked a browser on
    every single call on Windows:

      * The ``chrome.exe`` we ``Popen`` is a **launcher**. It starts the real
        browser as a SEPARATE process and exits within ~100 ms — so the PID we
        recorded is a corpse long before anyone calls ``stop``.
      * ``_terminate_pid`` reports a vanished PID as success ("already gone"), which
        is right for a process that really died — but here it meant we killed a dead
        launcher, returned ``{"closed": true}``, and left the actual browser running
        forever. Every leak was silent, and each one holds a window and a profile dir.

    So: kill the tracked PID **only if it is still the process we recorded**, then kill
    the processes genuinely serving this debug port, then VERIFY by probing the port.
    ``closed`` now means closed.

    That first condition is not caution, it is the fix to a second bug. This docstring
    used to justify the tracked-PID kill as "harmless if it is the corpse" — but a
    corpse's PID is *reusable*, and this registry outlives reboots, so the number could
    belong to anything by now; ``_terminate_pid`` would take it and its whole process
    tree. See :func:`_pid_is_still_the_recorded_process`. Do not reintroduce the
    unconditional kill on the grounds that the PID is probably dead — that IS the hazard.
    """
    data = _read_launched()
    entry = data.get(str(port))
    if not entry:
        return {"ok": False, "error": f"NAVIG did not launch a browser on port {port}"}

    user_data_dir = entry.get("user_data_dir")

    # Ask before killing. A clean `Browser.close` flushes cookies and site data and leaves
    # `exit_type = "Normal"`; a kill leaves "Crashed" and can drop the last page's writes.
    # If the browser complies, the kill path below finds nothing to kill and the port probe
    # at the end proves closure exactly as it always has. If it does not, nothing changes.
    # Was anything there to close? Decided ONCE, up front, and reported: the reaper used to
    # count an entry whose browser had already died as "reaped" and announce "closed idle
    # browser on port N" for a close nobody performed. Clearing the dead entry is right;
    # calling it a close is not.
    already_closed = probe_port(port, timeout=1.0) is None

    if request_browser_close(port):
        logger.debug("[cdp.targets] browser on port %d closed cleanly via Browser.close", port)

    try:
        tracked_pid = int(entry["pid"])
    except (KeyError, TypeError, ValueError):  # a malformed registry entry must not block the kill
        pass
    else:
        # Only if it is STILL that process. The tracked PID is usually a dead launcher (above),
        # and a dead PID gets recycled — killing it blind takes out whatever inherited the
        # number, and its whole tree with it. See _pid_is_still_the_recorded_process.
        if _pid_is_still_the_recorded_process(tracked_pid, entry):
            _terminate_pid(tracked_pid)

    # entry["app"] is the executable (or short id) we recorded at launch — the only thing that
    # identifies an Electron app's process, which carries no navig --user-data-dir.
    for pid in _debug_browser_pids(port, user_data_dir, exe_hint=entry.get("app")):
        _terminate_pid(pid)

    # The only honest signal: is the debug port still answering?
    closed = probe_port(port, timeout=1.0) is None
    if closed:
        remove_launched(port)   # keep the entry on failure so the user can retry
        # A throwaway session profile is disposable BY DEFINITION, and nothing deleted it:
        # every `cdp new` left ~130 MB under cdp-profiles/sessions/ forever once its browser
        # closed — five of them in one half-hour of another session's harness, 517 MB. The
        # same silent-growth class as the 4 GB model, one order of magnitude down.
        if _is_throwaway_session_dir(user_data_dir):
            _reap_throwaway_dir(port, user_data_dir, exe_hint=entry.get("app"))
    else:
        logger.warning(
            "[cdp.targets] browser on port %d is STILL serving CDP after stop — not closed", port
        )

    result = {"ok": closed, "port": port, "app": entry.get("app"), "closed": closed,
              "already_closed": already_closed}
    if closed:
        # Say which of the two things happened — a close, or a dead entry cleared.
        result["note"] = (
            f"port {port} was already closed — cleared its registry entry"
            if already_closed else f"closed {entry.get('app') or 'browser'} on port {port}"
        )
    if not closed:
        result["error"] = (
            f"the browser on port {port} is still answering CDP — it was not closed. "
            f"Kill it by PID if it is stuck."
        )
    return result


def _profile_root() -> str:
    """Where NAVIG keeps its debug profiles — the marker that a browser is ours."""
    from navig.platform.paths import config_dir

    return str(config_dir() / "cdp-profiles")


def list_debug_browsers() -> list[dict]:
    """Every browser on this machine running with a remote-debugging port.

    A leaked debug browser is INVISIBLE: it renders no page (the harness opens its
    content in a tab it then closes), so all you see is a blank window — or, when
    headless, nothing at all. Nothing looked for them, which is how ~24 of them once
    piled up unnoticed. Port scanning cannot find them either: a browser launched
    with ``--remote-debugging-port=0`` takes an ephemeral port nobody knows. The only
    reliable way to see them is to look at the processes.

    Each entry is classified, because the three kinds must be treated differently:

      tracked — ours, and in the launched registry: a live NAVIG session. Leave it.
      orphan  — ours, but NOT in the registry: leaked. `navig cdp stop --all` reclaims it.
      foreign — someone else's harness (a hand-rolled spawn, Playwright, a test
                runner). NAVIG reports it and NEVER touches it: it is not ours to
                kill, and it may still be driving a live session.

    The operator's normal Chrome/Edge carries no debug port at all, so it can never
    appear here.
    """
    try:
        import psutil  # type: ignore
    except ImportError:
        return []

    registry_ports = {int(p) for p in _read_launched()}
    root = _profile_root().lower()
    out: list[dict] = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            argv = proc.info.get("cmdline") or []
            if not argv:
                continue
            if not any(a.startswith("--remote-debugging-port") for a in argv):
                continue
            if any(a.startswith("--type=") for a in argv):
                continue  # renderer/gpu/utility child — dies with its parent

            port = 0
            profile = ""
            for a in argv:
                if a.startswith("--remote-debugging-port="):
                    try:
                        port = int(a.split("=", 1)[1])
                    except ValueError:
                        port = 0
                elif a.startswith("--user-data-dir="):
                    profile = a.split("=", 1)[1].strip('"')

            ours = bool(profile) and profile.lower().startswith(root)
            if not ours:
                kind = "foreign"
            elif port and port in registry_ports:
                kind = "tracked"
            else:
                kind = "orphan"

            out.append({
                "pid": int(proc.info["pid"]),
                "port": port,
                "profile": profile,
                "app": proc.info.get("name") or "",
                "kind": kind,
                "headless": any(a.startswith("--headless") for a in argv),
            })
        except Exception:  # noqa: BLE001 — a process can vanish mid-iteration
            continue
    return out


def _orphan_debug_browser_pids() -> list[int]:
    """NAVIG-launched debug browsers no longer in the registry — i.e. leaked.

    These exist because the old ``stop`` removed the registry entry while failing to
    kill the browser, so they can no longer be found by port. They ARE still
    identifiable by their profile, which is what :func:`list_debug_browsers`
    classifies — so derive from it rather than re-implementing the match, and a
    `foreign` browser can never be selected here by accident.
    """
    return [b["pid"] for b in list_debug_browsers() if b["kind"] == "orphan"]


def stop_all_launched() -> dict:
    """Close every NAVIG-launched debug browser — registry entries AND orphans."""
    data = _read_launched()
    closed: list[int] = []
    already_gone: list[int] = []
    failed: list[int] = []
    for port_str in list(data.keys()):
        res = stop_launched(int(port_str))
        if not res.get("ok"):
            failed.append(int(port_str))
        elif res.get("already_closed"):
            already_gone.append(int(port_str))  # a dead entry cleared, not a browser closed
        else:
            closed.append(int(port_str))

    # Reclaim the leaks the old `stop` created: it deleted the registry entry but
    # left the browser running, so those processes are unreachable by port and
    # `cdp stop --all` could never clean them up. Now it can.
    orphans = 0
    for pid in _orphan_debug_browser_pids():
        if _terminate_pid(pid):
            orphans += 1

    out: dict = {"ok": not failed, "closed_ports": closed}
    if already_gone:
        out["already_gone_ports"] = already_gone
    if orphans:
        out["orphans_reclaimed"] = orphans
    if failed:
        out["failed_ports"] = failed
        out["error"] = f"still serving CDP after stop: {failed}"
    else:
        # One honest line, built from what actually happened — never "closed N" for
        # entries whose browser had already died.
        parts = []
        if closed:
            parts.append(f"closed {len(closed)} browser(s) on port(s) {', '.join(map(str, closed))}")
        if already_gone:
            parts.append(f"cleared {len(already_gone)} dead registry entr{'y' if len(already_gone) == 1 else 'ies'}")
        if orphans:
            parts.append(f"reclaimed {orphans} orphaned browser(s)")
        out["note"] = "; ".join(parts) if parts else "nothing to close — no NAVIG-launched browser is running"
    return out


def reap_idle_browsers(idle_seconds: float, *, now: float | None = None) -> dict:
    """Close NAVIG-launched debug browsers nobody has touched for *idle_seconds*.

    Teardown here used to be pure discipline — the docs said "run ``navig cdp stop --all``
    when you're done" — and discipline is exactly what an unattended cron job does not
    have. Browsers are spawned ``DETACHED_PROCESS`` so they outlive the process that
    started them; nothing closed them, and ~24 once piled up unnoticed.

    ``CDPSessionManager.sweep_idle`` looks like it already did this and does NOT: it evicts
    the *WebSocket*, leaving "the remote app running". After it fires the browser is held
    by no session at all — an untracked orphan with a window still on screen. This is the
    missing layer underneath it.

    **What it will not touch**, because closing someone's browser is not a recoverable
    mistake and every rule below is "act only on what I can prove":

    * anything not in NAVIG's own launched registry — a ``foreign`` browser (another
      harness, Playwright, the operator's own debug session) is not ours;
    * a **named profile** (``cdp-profiles/named/<name>``) — those exist to hold logins the
      operator did by hand, so idleness there is the normal state, not a leak;
    * a browser launched with a **visible window** — a human asked to see it (``navig do``,
      ``cdp login``). After the visibility fix, agent and cron launches are headless, so
      the leaking population is precisely the reapable one;
    * an entry whose recorded PID fails :func:`_pid_is_still_the_recorded_process` — that
      check is applied inside :func:`stop_launched`, which this delegates to rather than
      re-implementing a second kill path;
    * an entry with no usable timestamp at all. An unknown age is not "old".

    Args:
        idle_seconds: Idle threshold. ``<= 0`` disables the sweep entirely.
        now: Injectable clock for tests (epoch seconds).

    Returns:
        ``{"reaped": [ports], "checked": n, "skipped": {reason: n}, "errors": [...]}``.
    """
    result: dict = {"reaped": [], "checked": 0, "skipped": {}, "errors": []}
    if idle_seconds <= 0:
        result["skipped"]["disabled"] = 1
        return result

    def _skip(reason: str) -> None:
        result["skipped"][reason] = result["skipped"].get(reason, 0) + 1

    stamp = time.time() if now is None else now
    for port_str, entry in list(_read_launched().items()):
        result["checked"] += 1
        if not isinstance(entry, dict):
            _skip("malformed")
            continue
        if _is_named_profile(entry.get("user_data_dir")):
            _skip("named_profile")
            continue
        # `headless is False` and not `not headless`: an entry written before this field
        # existed has no opinion, and "unknown" must not read as "the human wanted a
        # window" any more than it reads as "safe to close". It falls through to the age
        # check, where stop_launched's identity verification is still the final gate.
        if entry.get("headless") is False:
            _skip("visible_window")
            continue
        last = entry.get("last_used") or entry.get("started")
        if not isinstance(last, (int, float)):
            _skip("no_timestamp")
            continue
        if stamp - float(last) <= idle_seconds:
            _skip("still_fresh")
            continue
        try:
            port = int(port_str)
        except (TypeError, ValueError):
            _skip("malformed")
            continue
        try:
            res = stop_launched(port)
        except Exception as exc:  # noqa: BLE001 — a reaper must not take the daemon down
            result["errors"].append(f"port {port}: {exc}")
            continue
        if res.get("ok") and res.get("already_closed"):
            # The browser was gone before we arrived; the registry entry is now cleared.
            # Housekeeping, not a reap — it must not be announced as one.
            _skip("already_gone")
        elif res.get("ok"):
            result["reaped"].append(port)
        else:
            result["errors"].append(f"port {port}: {res.get('error', 'stop failed')}")
    return result


def _os_assigned_port() -> int | None:
    """Ask the OS for any free localhost port, or None if even that fails.

    Deliberately no SO_REUSEADDR: port 0 must yield a genuinely unused port, and on
    Windows SO_REUSEADDR permits binding an address another socket already holds.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
        except OSError:
            return None


def port_is_bindable(port: int) -> bool:
    """Can a listener actually take *port* on loopback right now?

    **"Nothing is listening" is NOT the same as "usable."** Windows reserves port ranges
    for Hyper-V / WSL / Docker, and `bind()` inside a reserved range raises
    ``PermissionError(13)`` with nothing listening and nothing to see — no process owns it,
    `netstat` shows it free, and only
    ``netsh interface ipv4 show excludedportrange protocol=tcp`` reveals it. The
    reservations MOVE across reboots, so a port that worked yesterday can be dead today.

    This lived inline in :func:`find_free_port` and nowhere else, which is exactly how
    ``profiles.allocate_port`` — the OTHER port allocator — came to hand out reserved
    ports. Measured on the operator's machine: the reservation was **9181-9280**, and
    ``PROFILE_PORT_BASE`` is **9280**, so the FIRST profile ever created got a port that
    can never bind. Their ``navig-epic`` profile held it, which is why the scheduled Epic
    claim could not open a browser at all.

    ``SO_REUSEADDR`` mirrors the original inline check: it asks "is this port usable by a
    new listener", not "is it perfectly idle".
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port(start: int = 9222, count: int = 50) -> int | None:
    """Find a localhost port with nothing bound AND no live CDP endpoint.

    Used by `cdp new` to spin up an isolated browser without clobbering an
    existing debug session.

    The `start..start+count` window is a PREFERENCE, not the search space. Windows
    RESERVES port ranges for Hyper-V / WSL / Docker, and `bind()` inside a reserved
    range raises PermissionError(13) even though nothing is listening — so a window
    can be entirely unusable on a machine with tens of thousands of free ports.
    Measured on the operator's own box: `netsh interface ipv4 show excludedportrange
    protocol=tcp` reserves **9181-9280**, which swallows the whole default 9222..9271
    window, and `navig cdp new` answered "no free debug port available" every time —
    the browser automation this repo mandates for all browser work simply did not run.
    So when the window is exhausted we fall back to an OS-assigned ephemeral port,
    which is also what CDP itself recommends (`--remote-debugging-port=0`).
    """
    for port in range(start, start + count):
        # Skip ports already serving CDP.
        if probe_port(port, timeout=0.4) is not None:
            continue
        if port_is_bindable(port):
            return port
    # A port the OS just handed us from bind(0) had nothing bound to it, so it cannot
    # already be serving CDP — no probe needed.
    return _os_assigned_port()


def new_session_profile_dir(name: str | None = None) -> str:
    """Profile dir for a fresh isolated browser.

    *name* → a persistent named profile under ``cdp-profiles/named/<name>``.
    None   → a unique throwaway profile under ``cdp-profiles/sessions/<ts>``.
    """
    from navig.platform.paths import config_dir

    base = config_dir() / "cdp-profiles"
    if name:
        path = base / "named" / name
    else:
        path = base / "sessions" / f"s{int(time.time() * 1000)}"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)
