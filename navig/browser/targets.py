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
# channel/version, and command-line flags aren't visible to page JS (no stealth cost). Do NOT
# fold a `--disable-features=…` into this list — Chrome honours only the LAST such switch, which
# would clobber the extension loader's own `--disable-features` (see cdp_actions).
CHROMIUM_QUIET_ARGS: tuple[str, ...] = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-search-engine-choice-screen",
)

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
                out = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {name}.exe"],
                    capture_output=True, text=True, timeout=5,
                )
                return name.lower() in out.stdout.lower()
            out = subprocess.run(["pgrep", "-fi", name], capture_output=True, text=True, timeout=5)
            return out.returncode == 0
        except Exception:  # noqa: BLE001
            return False


def terminate_app(app_id: str) -> bool:
    """Terminate a running instance of a known app so it can be relaunched.

    Destructive-ish (closes the user's app) — callers MUST warn/confirm first.
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
    return args


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
    # it) instead of every Chrome on the machine.
    record_launched(port, proc.pid, app if not os.path.isabs(app) else exe, user_data_dir)

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
            real = _debug_browser_pids(port, user_data_dir)
            if real and real[0] != proc.pid:
                logger.debug("[cdp.targets] port %d: launcher pid %d → real browser pid %d",
                             port, proc.pid, real[0])
                record_launched(port, real[0], app if not os.path.isabs(app) else exe,
                                user_data_dir)
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


def record_launched(port: int, pid: int, app: str, user_data_dir: str | None) -> None:
    """Remember a browser NAVIG launched (keyed by debug port)."""
    data = _read_launched()
    data[str(port)] = {"pid": pid, "app": app, "user_data_dir": user_data_dir,
                       "started": int(time.time())}
    _write_launched(data)


def get_launched() -> dict:
    """Return the map of NAVIG-launched debug browsers ``{port: {...}}``."""
    return _read_launched()


def remove_launched(port: int) -> None:
    data = _read_launched()
    if data.pop(str(port), None) is not None:
        _write_launched(data)


def _terminate_pid(pid: int) -> bool:
    """Terminate a process (and its children) by PID. Best-effort, cross-platform."""
    try:
        import psutil  # type: ignore

        proc = psutil.Process(pid)
        for child in proc.children(recursive=True):
            try:
                child.terminate()
            except Exception:  # noqa: BLE001
                pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        return True
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
    except Exception as exc:  # noqa: BLE001 (psutil NoSuchProcess etc.)
        logger.debug("[cdp.targets] pid %s already gone: %s", pid, exc)
        return True


def _debug_browser_pids(port: int, user_data_dir: str | None) -> list[int]:
    """PIDs of the REAL browser processes serving *port*.

    Selection is by ``--remote-debugging-port=<port>`` (and the unique
    ``--user-data-dir`` when we recorded one) — never by process name. The
    operator's own Chrome/Edge carries neither flag, so it can never be selected
    here. Renderer/GPU/utility children carry ``--type=`` and die with their
    parent, so only the MAIN processes are returned.
    """
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
            if needle_dir and needle_dir not in cmd:
                continue
            if any(a.startswith("--type=") for a in argv):
                continue  # a child (renderer/gpu/utility) — reaped with its parent
            pids.append(int(proc.info["pid"]))
        except Exception:  # noqa: BLE001 — a process can vanish mid-iteration
            continue
    return pids


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

    So: kill the tracked PID (harmless if it is the corpse), then kill the processes
    that are genuinely serving this debug port, then VERIFY by probing the port.
    ``closed`` now means closed.
    """
    data = _read_launched()
    entry = data.get(str(port))
    if not entry:
        return {"ok": False, "error": f"NAVIG did not launch a browser on port {port}"}

    user_data_dir = entry.get("user_data_dir")

    try:
        _terminate_pid(int(entry["pid"]))
    except (KeyError, TypeError, ValueError):  # a malformed registry entry must not block the kill
        pass

    for pid in _debug_browser_pids(port, user_data_dir):
        _terminate_pid(pid)

    # The only honest signal: is the debug port still answering?
    closed = probe_port(port, timeout=1.0) is None
    if closed:
        remove_launched(port)   # keep the entry on failure so the user can retry
    else:
        logger.warning(
            "[cdp.targets] browser on port %d is STILL serving CDP after stop — not closed", port
        )

    result = {"ok": closed, "port": port, "app": entry.get("app"), "closed": closed}
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
    failed: list[int] = []
    for port_str in list(data.keys()):
        res = stop_launched(int(port_str))
        (closed if res.get("ok") else failed).append(int(port_str))

    # Reclaim the leaks the old `stop` created: it deleted the registry entry but
    # left the browser running, so those processes are unreachable by port and
    # `cdp stop --all` could never clean them up. Now it can.
    orphans = 0
    for pid in _orphan_debug_browser_pids():
        if _terminate_pid(pid):
            orphans += 1

    out: dict = {"ok": not failed, "closed_ports": closed}
    if orphans:
        out["orphans_reclaimed"] = orphans
    if failed:
        out["failed_ports"] = failed
        out["error"] = f"still serving CDP after stop: {failed}"
    return out


def find_free_port(start: int = 9222, count: int = 50) -> int | None:
    """Find a localhost port with nothing bound AND no live CDP endpoint.

    Used by `cdp new` to spin up an isolated browser without clobbering an
    existing debug session.
    """
    for port in range(start, start + count):
        # Skip ports already serving CDP.
        if probe_port(port, timeout=0.4) is not None:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


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
