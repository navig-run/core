"""Open NAVIG OS on this machine: get a one-time launch code, start the browser.

The local NAVIG OS server hands out single-use, 60-second launch codes over a unix
socket (``NAVIG_LAUNCH_SOCKET``; see ``apps/os/packages/server-core/src/webui/launch.ts``).
Being able to connect to that socket is the authorization — it lives in a directory
owned by the ``navig-launch`` group — so whoever runs this never needs the server token
or the web password. The browser opens ``/launch#c=<code>``, which trades the code for
the ordinary session cookie.

Two callers:

* the kiosk session (``/usr/lib/navig-os/kiosk-session``) runs
  ``python -m navig.os_host.launch --kiosk`` as the restricted ``navig-kiosk`` user;
* the owner runs ``navig os open`` (an app window) from their own desktop.

This module imports nothing else from navig on purpose: the kiosk user must not trigger
onboarding or grow a ``~/.navig``, and the session restarts the browser in a loop, so
start-up cost is paid often.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import sys
from collections.abc import Sequence

DEFAULT_SOCKET = "/run/navig-os/launch.sock"
DEFAULT_PORT = 9100

# Chromium first: the Debian package depends on it. Google Chrome works the same way.
BROWSERS = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome")

# What the server mints: 32 random bytes, base64url, no padding (43 chars). Accept a
# little range so a future length change on the server does not need a lockstep release.
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")

_MAX_REPLY = 512


class LaunchError(RuntimeError):
    """A launch that cannot happen, with a message a person can act on."""


def socket_path() -> str:
    return os.environ.get("NAVIG_LAUNCH_SOCKET", "").strip() or DEFAULT_SOCKET


def server_port() -> int:
    raw = os.environ.get("NAVIG_OS_PORT", "").strip()
    try:
        port = int(raw) if raw else DEFAULT_PORT
    except ValueError:
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def mint_code(path: str | None = None, timeout: float = 5.0) -> str:
    """Connect to the launch socket and read the code it hands out."""
    path = path or socket_path()
    if not hasattr(socket, "AF_UNIX"):
        raise LaunchError("Launch codes need a unix socket, which this platform does not have.")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
        chunks: list[bytes] = []
        size = 0
        while size <= _MAX_REPLY:
            chunk = sock.recv(_MAX_REPLY)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except FileNotFoundError as exc:
        raise LaunchError(
            f"NAVIG OS is not running here (no launch socket at {path}). "
            "Start it with: systemctl start navig-os-server@<owner>"
        ) from exc
    except PermissionError as exc:
        raise LaunchError(
            f"This account may not open NAVIG OS ({path} is limited to the navig-launch group)."
        ) from exc
    except ConnectionRefusedError as exc:
        raise LaunchError(
            f"NAVIG OS is not accepting launches (nothing is listening on {path})."
        ) from exc
    except TimeoutError as exc:
        raise LaunchError(f"NAVIG OS did not answer on {path} within {timeout:g}s.") from exc
    finally:
        sock.close()

    code = b"".join(chunks).decode("ascii", errors="replace").strip()
    if not _CODE_RE.fullmatch(code):
        raise LaunchError(f"NAVIG OS answered on {path} with something that is not a launch code.")
    return code


def launch_url(code: str, port: int | None = None) -> str:
    """The page that redeems ``code``. The code rides in the FRAGMENT, never a query."""
    return f"http://127.0.0.1:{port or server_port()}/launch#c={code}"


def find_browser(explicit: str = "") -> str | None:
    if explicit:
        return shutil.which(explicit) or (explicit if os.path.isfile(explicit) else None)
    for name in BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def browser_argv(browser: str, url: str, kiosk: bool) -> list[str]:
    """The browser command. Kiosk = full screen with no chrome; otherwise an app window."""
    common = [
        "--no-first-run",
        "--no-default-browser-check",
        # The kiosk user has no keyring; without this Chromium blocks on a keyring prompt.
        "--password-store=basic",
        "--disable-features=Translate",
        "--ozone-platform-hint=auto",
    ]
    if kiosk:
        return [browser, "--kiosk", "--noerrdialogs", "--disable-session-crashed-bubble", *common, url]
    return [browser, f"--app={url}", *common]


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="navig-os-open",
        description="Open NAVIG OS on this machine with a one-time launch code.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--kiosk", action="store_true", help="full screen, no browser chrome")
    mode.add_argument("--app", action="store_true", help="an app window (the default)")
    p.add_argument("--port", type=int, default=0, help=f"NAVIG OS port (default {DEFAULT_PORT})")
    p.add_argument("--socket", default="", help=f"launch socket (default {DEFAULT_SOCKET})")
    p.add_argument("--browser", default="", help="browser executable (default: Chromium)")
    p.add_argument(
        "--print-url",
        action="store_true",
        help="print the one-time URL instead of opening a browser (it works once, for 60 s)",
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    try:
        code = mint_code(args.socket or None)
    except LaunchError as exc:
        print(f"navig-os-open: {exc}", file=sys.stderr)
        return 1

    url = launch_url(code, args.port or None)
    if args.print_url:
        print(url)
        return 0

    browser = find_browser(args.browser)
    if browser is None:
        wanted = args.browser or " / ".join(BROWSERS)
        print(f"navig-os-open: no browser found ({wanted}). Install chromium.", file=sys.stderr)
        return 1

    argv_ = browser_argv(browser, url, kiosk=args.kiosk)
    # Replace this process: the kiosk session supervises the BROWSER, and a Python parent
    # in between would outlive a browser crash and hide it from the session's restart loop.
    os.execv(argv_[0], argv_)
    return 0  # pragma: no cover — execv does not return


if __name__ == "__main__":
    sys.exit(main())
