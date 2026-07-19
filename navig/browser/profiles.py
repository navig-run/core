"""Named browser profiles — different Chrome identities for different projects, cases, accounts.

A **profile** is a persistent, isolated, logged-in browser identity:
  - its own Chrome ``user-data-dir`` (``~/.navig/cdp-profiles/named/<name>``) → isolated
    cookies/logins at the browser level,
  - a **stable** debug port (assigned once, remembered — you never chase a dynamic port),
  - metadata (note/purpose, app, optional project link, timestamps).

An **active profile** pointer lets ``navig do`` and ``navig cdp`` default to one profile: set it
once with ``navig cdp profile use <name>`` and everything targets it — no ``--port`` juggling, no
close/reopen.

Registry file: ``~/.navig/cdp-profiles.json``. Profile ports come from a NAVIG-reserved band
(9280+) kept clear of the ad-hoc ``cdp new`` range and the 9222–9229 discovery scan, and are
probed for a live foreign browser before being claimed (avoids collisions).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger

__all__ = [
    "Profile",
    "PROFILE_PORT_BASE",
    "registry_path",
    "list_profiles",
    "get_profile",
    "create_profile",
    "create_real_profile",
    "remove_profile",
    "touch_profile",
    "set_default_account",
    "set_profile_proxy",
    "allocate_port",
    "set_active",
    "get_active",
    "resolve_active",
    "detect_real_chrome_profiles",
    "real_user_data_dir",
]

# Reserved band for profile debug ports — clear of the 9222–9229 discovery scan
# and the `cdp new` free-port search (9222+50).
PROFILE_PORT_BASE = 9280
PROFILE_PORT_COUNT = 60


@dataclass
class Profile:
    name: str
    port: int
    user_data_dir: str
    app: str = "chrome"
    note: str = ""
    project: str | None = None
    profile_directory: str | None = None  # a named profile inside a real user-data-dir
    real: bool = False                     # True → points at the user's real Chrome data
    default_account: str | None = None     # default Gmail account (email/index) for this profile
    proxy: str | None = None               # per-profile proxy URL (overrides the shared pool)
    created: int = 0
    last_used: int = 0


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------


def registry_path() -> Path:
    from navig.platform.paths import config_dir  # noqa: PLC0415

    return config_dir() / "cdp-profiles.json"


def _read() -> dict:
    path = registry_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        raise ValueError("registry root is not a JSON object")
    except Exception as exc:  # noqa: BLE001
        # Do NOT silently discard a corrupt registry (the next write would clobber
        # it, losing every profile). Move it aside so it can be recovered.
        logger.warning("[cdp.profiles] registry unreadable ({}) — backing up to .corrupt", exc)
        try:
            path.replace(path.with_name(path.name + ".corrupt"))
        except Exception:  # noqa: BLE001
            pass
        return {}


def _write(data: dict) -> bool:
    """Persist the registry. Returns True on success, False on failure (surfaced to callers)."""
    path = registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[cdp.profiles] write failed: {}", exc)
        return False


def _profiles_node(data: dict) -> dict:
    node = data.get("profiles")
    if not isinstance(node, dict):
        node = {}
        data["profiles"] = node
    return node


# ---------------------------------------------------------------------------
# Port allocation (stable + collision-avoiding)
# ---------------------------------------------------------------------------


def allocate_port(data: dict | None = None) -> int:
    """Return a free port in the reserved band, not already assigned or serving CDP."""
    from navig.browser import targets as t  # noqa: PLC0415

    data = data if data is not None else _read()
    taken = {int(p.get("port", 0)) for p in _profiles_node(data).values()}
    # Scan the reserved band and keep going upward if it's full — always avoiding
    # ports already assigned to a profile OR served by a live foreign browser, so
    # the fallback can never hand back a colliding port.
    for port in range(PROFILE_PORT_BASE, PROFILE_PORT_BASE + PROFILE_PORT_COUNT + 200):
        if port in taken:
            continue
        try:
            if t.probe_port(port, timeout=0.3) is not None:
                continue
        except Exception:  # noqa: BLE001
            pass
        return port
    return PROFILE_PORT_BASE + PROFILE_PORT_COUNT + 500  # practically unreachable


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def list_profiles() -> list[Profile]:
    node = _profiles_node(_read())
    out: list[Profile] = []
    for name, rec in node.items():
        try:
            out.append(Profile(name=name, **{k: v for k, v in rec.items() if k != "name"}))
        except TypeError:
            # tolerate older/extra keys
            out.append(Profile(
                name=name, port=int(rec.get("port", 0)),
                user_data_dir=rec.get("user_data_dir", ""), app=rec.get("app", "chrome"),
                note=rec.get("note", ""), project=rec.get("project"),
                profile_directory=rec.get("profile_directory"), real=bool(rec.get("real", False)),
                default_account=rec.get("default_account"), proxy=rec.get("proxy"),
                created=int(rec.get("created", 0)), last_used=int(rec.get("last_used", 0)),
            ))
    return sorted(out, key=lambda p: p.name)


def get_profile(name: str) -> Profile | None:
    rec = _profiles_node(_read()).get(name)
    if not rec:
        return None
    return Profile(name=name, port=int(rec.get("port", 0)),
                   user_data_dir=rec.get("user_data_dir", ""), app=rec.get("app", "chrome"),
                   note=rec.get("note", ""), project=rec.get("project"),
                   profile_directory=rec.get("profile_directory"), real=bool(rec.get("real", False)),
                   default_account=rec.get("default_account"), proxy=rec.get("proxy"),
                   created=int(rec.get("created", 0)), last_used=int(rec.get("last_used", 0)))


def create_profile(name: str, *, app: str = "chrome", note: str = "",
                   project: str | None = None) -> Profile:
    """Create (or return the existing) named automation profile with a stable port."""
    data = _read()
    node = _profiles_node(data)
    if name in node:
        return get_profile(name)  # idempotent

    from navig.browser import targets as t  # noqa: PLC0415

    port = allocate_port(data)
    user_data_dir = t.new_session_profile_dir(name)  # persistent ~/.navig/cdp-profiles/named/<name>
    now = int(time.time())
    prof = Profile(name=name, port=port, user_data_dir=user_data_dir, app=app,
                   note=note, project=project, created=now, last_used=now)
    node[name] = {k: v for k, v in asdict(prof).items() if k != "name"}
    # The first profile becomes active, so a single-profile user never hits
    # "no profile selected" from navig do / navig gmail.
    if not data.get("active"):
        data["active"] = name
    _write(data)
    logger.info("[cdp.profiles] created profile '{}' on port {}", name, port)
    return prof


def create_real_profile(name: str, directory: str, *, app: str = "chrome", note: str = "",
                        project: str | None = None) -> Profile | None:
    """Register a profile that points at the user's REAL browser data + a named profile dir.

    Advanced path: opening it relaunches the real browser (must be quit first; M136 may block
    the *default* profile). Returns None if the real User Data dir can't be found.
    """
    base = real_user_data_dir(app)
    if base is None:
        return None
    data = _read()
    node = _profiles_node(data)
    if name in node:
        return get_profile(name)
    port = allocate_port(data)
    now = int(time.time())
    prof = Profile(name=name, port=port, user_data_dir=str(base), app=app, note=note,
                   project=project, profile_directory=directory, real=True,
                   created=now, last_used=now)
    node[name] = {k: v for k, v in asdict(prof).items() if k != "name"}
    _write(data)
    logger.info("[cdp.profiles] created REAL profile '{}' → {} / {}", name, base, directory)
    return prof


def set_default_account(name: str, account: str) -> bool:
    """Remember the default Gmail account (email or index) for a profile."""
    data = _read()
    node = _profiles_node(data)
    if name not in node:
        return False
    node[name]["default_account"] = account
    return _write(data)


def set_profile_proxy(name: str, proxy: str | None) -> bool:
    """Assign (or clear, with None) a per-profile proxy URL that overrides the shared pool."""
    data = _read()
    node = _profiles_node(data)
    if name not in node:
        return False
    if proxy:
        node[name]["proxy"] = proxy
    else:
        node[name].pop("proxy", None)
    return _write(data)


def touch_profile(name: str) -> None:
    data = _read()
    node = _profiles_node(data)
    if name in node:
        node[name]["last_used"] = int(time.time())
        _write(data)


def remove_profile(name: str) -> bool:
    """Remove a profile from the registry (caller deletes the on-disk dir separately)."""
    data = _read()
    node = _profiles_node(data)
    if node.pop(name, None) is None:
        return False
    if data.get("active") == name:
        data["active"] = None
    return _write(data)


# ---------------------------------------------------------------------------
# Active profile
# ---------------------------------------------------------------------------


def set_active(name: str) -> bool:
    data = _read()
    if name not in _profiles_node(data):
        return False
    data["active"] = name
    return _write(data)  # False if the pointer couldn't be persisted


def get_active() -> str | None:
    return _read().get("active")


def resolve_active(name: str | None) -> Profile | None:
    """Resolve the profile to use: explicit *name* → active pointer → the sole profile.

    The sole-profile fallback means a user with exactly one profile never has to run
    ``profile use`` before ``navig do`` / ``navig gmail`` work.
    """
    if name:
        return get_profile(name)
    active = get_active()
    if active:
        prof = get_profile(active)
        if prof is not None:
            return prof
    profs = list_profiles()
    return profs[0] if len(profs) == 1 else None


# ---------------------------------------------------------------------------
# Real (system) Chrome profile detection — read-only
# ---------------------------------------------------------------------------


def real_user_data_dir(app: str = "chrome") -> Path | None:
    """Best-effort path to the user's REAL browser User Data dir for *app*."""
    import sys  # noqa: PLC0415

    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        table = {
            "chrome": local / "Google" / "Chrome" / "User Data",
            "edge": local / "Microsoft" / "Edge" / "User Data",
            "brave": local / "BraveSoftware" / "Brave-Browser" / "User Data",
        }
    elif sys.platform == "darwin":
        sup = home / "Library" / "Application Support"
        table = {
            "chrome": sup / "Google" / "Chrome",
            "edge": sup / "Microsoft Edge",
            "brave": sup / "BraveSoftware" / "Brave-Browser",
        }
    else:
        cfg = home / ".config"
        table = {
            "chrome": cfg / "google-chrome",
            "edge": cfg / "microsoft-edge",
            "brave": cfg / "BraveSoftware" / "Brave-Browser",
        }
    path = table.get(app)
    return path if path and path.exists() else None


def detect_real_chrome_profiles(app: str = "chrome") -> list[dict]:
    """List the user's real browser profiles (directory → display name), read-only.

    Parsed from the browser's ``Local State`` (``profile.info_cache``). Returns [] if the
    browser isn't installed / the file is unreadable.
    """
    base = real_user_data_dir(app)
    if base is None:
        return []
    ls = base / "Local State"
    try:
        data = json.loads(ls.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    cache = (data.get("profile") or {}).get("info_cache") or {}
    out = []
    for directory, info in cache.items():
        out.append({
            "directory": directory,
            "name": (info or {}).get("name") or directory,
            "user_data_dir": str(base),
            "app": app,
        })
    return sorted(out, key=lambda p: p["name"].lower())
