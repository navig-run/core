"""``navig os`` — run NAVIG OS as a web app on this machine.

The desktop build is not released yet, but the same server already serves the whole
interface to a browser. Reaching it took four environment variables and knowing that
``NAVIG_SERVER_TOKEN`` signs the login session, so in practice nobody did. This turns it
into one verb.

The architecture is the point: the server runs **here**, and the browser — this machine's,
your phone's over a tunnel — is just a window onto it. That is why it can open your files
and spaces at all, and why nothing of yours transits navig.run.

Credentials live in the vault, not in config: the token signs session cookies and the
password is what stands between a tunnel and the open internet.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from navig import console_helper as ch

app = typer.Typer(help="Run NAVIG OS in a browser, served by this machine")

DEFAULT_PORT = 9100

VAULT_PROVIDER = "navig-os-server"
_VAULT_LABEL = "NAVIG OS web server (token + password)"


# ---------------------------------------------------------------------------
# Locating the app
# ---------------------------------------------------------------------------

def find_os_dir(explicit: str = "") -> Path | None:
    """The ``apps/os`` checkout, or ``None``.

    An explicitly named directory (``explicit`` or ``$NAVIG_OS_DIR``) is the ONLY thing
    consulted when one is given — valid or not. Otherwise resolution walks up from where
    the operator actually stood, then looks relative to this installed package for an
    editable install.

    The walk starts at :func:`navig.platform.paths.invocation_cwd` rather than
    ``Path.cwd()`` — navig chdirs into the active space, so ``Path.cwd()`` would look for
    the monorepo underneath whatever space happens to be active and quietly miss it.
    """
    from navig.platform.paths import invocation_cwd, resolve_user_path

    # An explicitly named directory is AUTHORITATIVE: if it does not hold an apps/os
    # checkout, that is an answer, not a reason to go looking elsewhere. Falling through
    # would silently run a different directory than the one the operator named — they
    # would be told the server started and be looking at the wrong tree.
    explicit_path = ""
    if explicit:
        explicit_path = str(resolve_user_path(explicit))
    elif os.environ.get("NAVIG_OS_DIR"):
        explicit_path = str(Path(os.environ["NAVIG_OS_DIR"]).expanduser())
    if explicit_path:
        named = Path(explicit_path)
        return named.resolve() if _is_os_checkout(named) else None

    candidates: list[Path] = []
    cur = invocation_cwd()
    for parent in [cur, *cur.parents]:
        candidates.append(parent / "apps" / "os")

    here = Path(__file__).resolve()
    for up in (3, 4, 5):
        if len(here.parents) > up:
            candidates.append(here.parents[up] / "apps" / "os")

    for c in candidates:
        if _is_os_checkout(c):
            return c.resolve()
    return None


# Where the Debian package (`navig-os-server`) puts the prebuilt server tree.
INSTALLED_SERVER_DIRS: tuple[Path, ...] = (Path("/opt/navig-os/server"),)


def find_installed_server(explicit: str = "") -> Path | None:
    """A prebuilt server tree (``build-server.ts`` output), or ``None``.

    This is how NAVIG OS runs on a machine with no source checkout and no bun: the tree
    carries its own runtimes and, built with ``--with-webui``, its own web UI. A named
    directory (``explicit`` / ``$NAVIG_OS_DIR``) is authoritative here exactly as it is
    in :func:`find_os_dir`.
    """
    from navig.platform.paths import resolve_user_path

    named = ""
    if explicit:
        named = str(resolve_user_path(explicit))
    elif os.environ.get("NAVIG_OS_DIR"):
        named = str(Path(os.environ["NAVIG_OS_DIR"]).expanduser())
    if named:
        path = Path(named)
        return path.resolve() if _is_installed_tree(path) else None
    for candidate in INSTALLED_SERVER_DIRS:
        if _is_installed_tree(candidate):
            return candidate
    return None


def _is_installed_tree(path: Path) -> bool:
    """The launch manifest AND its launcher — what ``build-server.ts`` always writes."""
    try:
        return (path / "launch.json").is_file() and (path / "bin" / "navig-server").is_file()
    except OSError:
        return False


def _stdout_is_the_journal() -> bool:
    """True when stdout IS the systemd journal (``JOURNAL_STREAM``'s documented test).

    The variable alone is not enough: it is inherited by children whose stdout is a pipe,
    and leaks into terminal shells on some desktops. Its ``dev:inode`` must match stdout.
    """
    dev, _, ino = os.environ.get("JOURNAL_STREAM", "").partition(":")
    if not (dev and ino):
        return False
    try:
        st = os.fstat(sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        return False
    return str(st.st_dev) == dev and str(st.st_ino) == ino


def _output_is_a_log() -> bool:
    """True when stdout goes to a log rather than a person — a systemd service.

    ``serve`` shows a new password and a link carrying the server token ONCE, at the
    terminal, because the operator needs them. Under a unit those same lines land in the
    system journal, which other accounts can read. navig's own units also say so outright
    (``NAVIG_SERVICE=1``). A TTY check is the wrong test: a pipe or a test runner is not a
    journal, and must still see the link.
    """
    return os.environ.get("NAVIG_SERVICE") == "1" or _stdout_is_the_journal()


def _is_os_checkout(path: Path) -> bool:
    """The package.json AND the server entry point.

    A bare package.json would also match an unrelated directory called ``apps/os``, which
    would then be handed to ``bun run server:prod`` and fail in a way that points nowhere
    near the actual mistake.

    An INSTALLED server tree has both files too — it is an assembled source tree run by
    its own bundled bun — so it is excluded explicitly. Taken for a checkout, it was handed
    to `bun run server:prod` and the packaged server crash-looped on "bun is not
    installed" (measured on a real Debian install).
    """
    try:
        return (
            (path / "package.json").is_file()
            and (path / "packages" / "server" / "src" / "index.ts").is_file()
            and not _is_installed_tree(path)
        )
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def _vault():
    from navig.vault import get_vault

    return get_vault()


def load_credentials() -> dict[str, str]:
    """The stored ``{"token", "password"}``, or ``{}`` when there is nothing stored."""
    try:
        vault = _vault()
        if vault is None:
            return {}
        cred = vault.get(VAULT_PROVIDER, caller="os.serve")
        if cred is None:
            return {}
        data = cred.data or {}
        return {
            "token": str(data.get("token") or "").strip(),
            "password": str(data.get("password") or "").strip(),
        }
    except Exception:  # noqa: BLE001 — an unreadable vault must not be a crash
        return {}


def save_credentials(token: str, password: str) -> bool:
    """Store the pair. Returns False when the vault refused — the caller must say so.

    A silent failure here is the bad case: the server would start with credentials that
    are not persisted, so the next start would mint different ones and every saved
    bookmark would stop working with no explanation.
    """
    try:
        vault = _vault()
        if vault is None:
            return False
        data = {"token": token, "password": password}
        existing = vault.get(VAULT_PROVIDER, caller="os.serve")
        if existing is not None:
            vault.update(existing.id, data=data)
        else:
            vault.add(
                provider=VAULT_PROVIDER,
                credential_type="api_key",
                data=data,
                profile_id="default",
                label=_VAULT_LABEL,
            )
        return True
    except Exception:  # noqa: BLE001
        return False


def generate_token() -> str:
    """A 256-bit hex token. It signs the web session cookie."""
    return secrets.token_hex(32)


def generate_password() -> str:
    """A password a person can retype once but nobody will guess (~95 bits)."""
    return secrets.token_urlsafe(12)


HOSTED_APP_URL = "https://os.navig.run/"


def hosted_link(token: str, port: int, host: str = "127.0.0.1") -> str:
    """The os.navig.run link that opens NAVIG OS already connected to this server.

    Server and token ride in the URL FRAGMENT: browsers never send a fragment in a
    request, so the token reaches the page's own script and not os.navig.run's logs, and
    the page strips it from the address bar before doing anything else. A wildcard bind
    is not an address a browser can dial, so it is rendered as loopback.
    """
    from urllib.parse import urlencode

    dial = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    if ":" in dial and not dial.startswith("["):
        dial = f"[{dial}]"
    return f"{HOSTED_APP_URL}#" + urlencode({"server": f"http://{dial}:{port}", "token": token})


def build_env(token: str, password: str, port: int, host: str) -> dict[str, str]:
    """The environment `bun run server:prod` needs, as a plain dict.

    Pure, so the dry run prints exactly what the real run will use — a `--print-env` that
    reconstructs the values separately would be able to disagree with the thing it claims
    to describe.
    """
    return {
        "NAVIG_SERVER_TOKEN": token,
        "NAVIG_WEBUI_PASSWORD": password,
        "NAVIG_RPC_PORT": str(port),
        "NAVIG_RPC_HOST": host,
        # Announce this server to the NAVIG brain (<config>/run/os-server.json), so a
        # phone paired in Settings → Remote access can reach it through the relay.
        "NAVIG_OS_ANNOUNCE": "1",
    }


_SECRET_KEYS = frozenset({"NAVIG_SERVER_TOKEN", "NAVIG_WEBUI_PASSWORD"})


def redact_env(env: dict[str, str]) -> dict[str, str]:
    """The same environment with the secrets replaced — for printing and for logs."""
    return {k: ("<hidden>" if k in _SECRET_KEYS and v else v) for k, v in env.items()}


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@app.command("serve")
def serve(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Port to serve on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (leave as loopback unless you know why)"),
    password: str = typer.Option("", "--password", help="Set the web login password (stored in the vault)"),
    new_password: bool = typer.Option(False, "--new-password", help="Generate and store a fresh password"),
    directory: str = typer.Option("", "--dir", help="Path to the apps/os checkout"),
    print_env: bool = typer.Option(False, "--print-env", help="Show what would run, and exit"),
) -> None:
    """Serve NAVIG OS to a browser from this machine."""
    # A source checkout wins when there is one (that is a developer, who wants their own
    # tree); otherwise the prebuilt tree a package installed.
    os_dir = find_os_dir(directory)
    installed = find_installed_server(directory) if os_dir is None else None
    if os_dir is None and installed is None:
        named = directory or os.environ.get("NAVIG_OS_DIR", "")
        if named:
            # Say which directory was rejected and why. "Could not find it" would be a lie
            # when they told us exactly where to look.
            ch.error(
                f"Not a NAVIG OS checkout or server install: {named}",
                "Expected packages/server/src/index.ts (a checkout) or launch.json + "
                "bin/navig-server (an installed server) inside it.",
            )
        else:
            ch.error(
                "Could not find NAVIG OS (no apps/os checkout, no installed server).",
                "On Debian: sudo apt install navig-os-server. From source: point at the "
                "checkout with --dir <path> or $NAVIG_OS_DIR.",
            )
        raise typer.Exit(1)

    if installed is not None:
        argv = [str(installed / "bin" / "navig-server")]
        run_dir = installed
    else:
        if shutil.which("bun") is None:
            ch.error("bun is not installed.", "NAVIG OS is built with bun — install it from https://bun.sh")
            raise typer.Exit(1)
        argv = ["bun", "run", "server:prod"]
        run_dir = os_dir

    stored = load_credentials()
    token = stored.get("token") or generate_token()

    fresh_password = ""
    if password:
        chosen = password
    elif new_password or not stored.get("password"):
        chosen = generate_password()
        fresh_password = chosen
    else:
        chosen = stored["password"]

    if not save_credentials(token, chosen):
        # Not fatal — the server can still run — but the operator has to know that the
        # next start will mint different credentials and invalidate their bookmarks.
        ch.warning(
            "Could not store the credentials in the vault.",
            "This session will work, but the next start will generate new ones. "
            "Check `navig doctor` — it reports whether the vault can be opened.",
        )

    env = build_env(token, chosen, port, host)

    if host not in ("127.0.0.1", "localhost", "::1"):
        ch.warning(
            f"Binding to {host} exposes this server to the network.",
            "The server refuses a non-loopback bind without TLS unless it is told otherwise. "
            "A tunnel is the safer way to reach it from elsewhere — see below.",
        )

    if print_env:
        ch.info(f"Would run: {' '.join(argv)}", f"in {run_dir}")
        table = ch.create_table("Environment", [
            {"name": "Variable", "style": "cyan"},
            {"name": "Value", "style": "white"},
        ])
        for k, v in redact_env(env).items():
            table.add_row(k, v)
        ch.print_table(table)
        if fresh_password:
            ch.warning("A password would be generated and stored. Nothing was started.")
        return

    secrets_to_log = _output_is_a_log()
    if fresh_password and secrets_to_log:
        ch.info(
            "A web login password was generated and stored in the vault.",
            "It is not printed here because this output goes to the system log. "
            "Set your own: navig os serve --password <yours>",
        )
    elif fresh_password:
        # Shown ONCE, at the terminal, because it is the credential the operator needs to
        # log in with. It is never written to a log.
        ch.panel(
            f"Web login password:  {fresh_password}\n\n"
            "Stored in your vault. Write it down now — it is not shown again.\n"
            "Change it any time with: navig os serve --password <yours>",
            title="New password",
        )

    ch.success(f"Starting NAVIG OS at http://{host}:{port}")
    ch.info("From this machine", f"http://127.0.0.1:{port}")
    # From anywhere else, pairing is the way: it goes through the user's own Lighthouse
    # relay, so there is no address to type and no token to copy.
    ch.info("From your phone or anywhere", "run `navig os pair` and scan the code")
    # The link carries this server's token — anyone holding it has this machine. It is
    # printed to the operator's own terminal only (like the password above), never logged.
    if not secrets_to_log:
        ch.dim(f"Same network only: {hosted_link(token, port, host)}")
        ch.dim("Stop it with Ctrl-C.")

    run_env = {**os.environ, **env}
    if installed is not None and os.name == "posix" and secrets_to_log:
        # Under a unit, BECOME the server: systemd then supervises the process that
        # actually serves (its exit status, its restarts) instead of a Python parent that
        # only waits on it.
        os.chdir(run_dir)
        os.execve(argv[0], argv, run_env)
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv,
            cwd=str(run_dir),
            env=run_env,
            check=False,
        )
    except KeyboardInterrupt:
        raise typer.Exit(0) from None
    except OSError as exc:
        ch.error("Could not start the server.", str(exc))
        raise typer.Exit(1) from exc

    # The child's exit status IS this command's result. Swallowing it would report success
    # for a server that died on startup.
    if completed.returncode != 0:
        raise typer.Exit(completed.returncode)


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------

@app.command("open")
def open_cmd(
    kiosk: bool = typer.Option(False, "--kiosk", help="Full screen, no browser chrome"),
    port: int = typer.Option(0, "--port", "-p", help=f"NAVIG OS port (default {DEFAULT_PORT})"),
    socket_path: str = typer.Option("", "--socket", help="Launch socket (default /run/navig-os/launch.sock)"),
    browser: str = typer.Option("", "--browser", help="Browser executable (default: Chromium)"),
    print_url: bool = typer.Option(
        False,
        "--print-url",
        help="Print the URL instead of opening it — the one-time launch URL when launch codes "
        "are available (it dies in 60 s), otherwise the login page",
    ),
) -> None:
    """Open NAVIG OS from this machine's local server — no password to type.

    Launch codes (a Linux install whose server hands out codes over its unix socket, and
    an account in the navig-launch group) are the passwordless path. Windows has no unix
    socket and a bare `navig os serve` has no launch socket either — so when there is no
    code but the server IS serving, open its login page instead (the password lives in the
    vault: `navig os serve --new-password` sets one). Only when the server is down does
    this fail, with the reason the launch actually failed.
    """
    from navig.os_host import launch

    server_port = port or launch.server_port()
    try:
        code = launch.mint_code(socket_path or None)
        open_url = launch.launch_url(code, port or None)
        outcome = "Opened NAVIG OS" + (" (kiosk)" if kiosk else "")
    except launch.LaunchError as exc:
        if not probe(server_port).get("reachable"):
            # The launch reason is about a socket; what actually blocked the operator is that
            # the server itself is not answering — observed 2026-10-21: Windows printed
            # "Launch codes need a unix socket" while the real fact was "your web server is
            # down". Lead with the actionable one, keep the launch reason so nothing hides.
            ch.error(
                "Could not open NAVIG OS.",
                f"The NAVIG OS web server is not answering on port {server_port} — "
                f"start it with: navig os serve   (no launch code: {exc})",
            )
            raise typer.Exit(1) from exc
        # The server answered: the app is reachable, only the passwordless shortcut is not.
        open_url = f"http://127.0.0.1:{server_port}/login"
        outcome = "Opened the NAVIG OS login"

    if print_url:
        # Printed to the caller's own terminal; a launch URL's code is single-use (60 s).
        typer.echo(open_url)
        return

    found = launch.find_browser(browser)
    if found is None:
        ch.error(
            "No browser found.",
            f"Looked for {browser or ', '.join(launch.BROWSERS)}. Install chromium, or pass --browser.",
        )
        raise typer.Exit(1)

    argv = launch.browser_argv(found, open_url, kiosk=kiosk)
    try:
        # Detached: an app window outlives this command, and the terminal stays usable.
        subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        ch.error("Could not start the browser.", str(exc))
        raise typer.Exit(1) from exc
    ch.success(outcome)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def probe(port: int, timeout: float = 2.0) -> dict[str, object]:
    """Ask the server's unauthenticated /health endpoint whether it is up.

    Returns a dict with ``reachable`` and, when it answered, ``status``. A timeout and a
    refused connection are reported as themselves rather than collapsed into "down": one
    means nothing is listening, the other that something is and did not answer.
    """
    import json
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as res:  # noqa: S310 — loopback, fixed scheme
            body = json.loads(res.read().decode("utf-8") or "{}")
            return {"reachable": True, "status": str(body.get("status") or "unknown")}
    except urllib.error.HTTPError as exc:
        # 503 is the server saying it is unhealthy — it IS reachable.
        return {"reachable": True, "status": f"unhealthy (HTTP {exc.code})"}
    except TimeoutError:
        return {"reachable": False, "error": "timed out"}
    except OSError as exc:
        return {"reachable": False, "error": str(exc)}
    except ValueError as exc:
        return {"reachable": True, "status": f"unreadable response ({exc})"}


@app.command("status")
def status(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Port to check"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Is the web server up, and is it set up?"""
    os_dir = find_os_dir()
    stored = load_credentials()
    result = probe(port)

    payload = {
        "port": port,
        "url": f"http://127.0.0.1:{port}",
        "source": str(os_dir) if os_dir else None,
        "credentials_stored": bool(stored.get("token") and stored.get("password")),
        **result,
    }

    if as_json:
        ch.emit_json(payload)
        return

    table = ch.create_table("NAVIG OS — web server", [
        {"name": "Check", "style": "cyan"},
        {"name": "State", "style": "white"},
    ])
    if result.get("reachable"):
        table.add_row("Server", f"[green]● up[/green] · {result.get('status', '?')}")
        table.add_row("Address", f"http://127.0.0.1:{port}")
    else:
        table.add_row("Server", f"[dim]○ not running[/dim] · {result.get('error', '')}")
        table.add_row("Address", f"[dim]http://127.0.0.1:{port}[/dim]")
    table.add_row("Source", str(os_dir) if os_dir else "[yellow]apps/os not found[/yellow]")
    table.add_row(
        "Credentials",
        "[green]stored[/green]" if payload["credentials_stored"] else "[dim]none yet[/dim]",
    )
    ch.print_table(table)

    if not result.get("reachable"):
        ch.dim("Start it with: navig os serve")



# ---------------------------------------------------------------------------
# pair / devices — remote NAVIG OS through the Lighthouse relay
# ---------------------------------------------------------------------------
#
# These talk to the RUNNING daemon over loopback (its /api/deck/os/* routes), never to the
# store directly: the daemon owns the relay, so only it can say whether pairing will work
# (relay online, new enough) and only it can end a revoked device's live session.


class DaemonUnavailable(RuntimeError):
    """The NAVIG daemon did not answer on loopback."""


def _http(method: str, url: str, body: dict | None, timeout: float = 10.0) -> tuple[int, dict]:
    """One loopback HTTP call: (status, JSON body). Raises DaemonUnavailable if nothing answers."""
    import requests

    try:
        res = requests.request(method, url, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise DaemonUnavailable(str(exc)) from exc
    try:
        data = res.json()
    except ValueError:
        data = {}
    return res.status_code, data if isinstance(data, dict) else {"data": data}


def _daemon(path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    """Call a daemon route as a local caller (no credential: the loopback path), or exit
    clearly when NAVIG is not running.

    Shaped for tests/quality/test_gateway_routes_called_exist.py (``path`` + ``method``
    parameters, URL built from ``gateway_base_url``): every caller passes the literal route
    and its verb, so a typo in either fails the build instead of 404ing at runtime.
    """
    from navig.gateway_client import gateway_base_url

    try:
        return _http(method, f"{gateway_base_url()}{path}", body)
    except DaemonUnavailable:
        ch.error("NAVIG is not running on this computer.", "Start it with: navig start")
        raise typer.Exit(1) from None


def terminal_qr(data: str) -> str | None:
    """The QR code as half-block text, or None when the optional `qrcode` package is absent."""
    try:
        import qrcode  # type: ignore[import-not-found]
    except ImportError:
        return None
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    if len(matrix) % 2:
        matrix.append([False] * len(matrix[0]))
    lines = []
    for top, bottom in zip(matrix[0::2], matrix[1::2]):
        # Dark modules print as spaces on a light background, so the code scans on any
        # terminal theme: invert, and draw two rows per line with half blocks.
        line = ""
        for t, b in zip(top, bottom):
            line += {(False, False): "█", (True, False): "▄", (False, True): "▀", (True, True): " "}[(t, b)]
        lines.append(line)
    return "\n".join(lines)


@app.command("pair")
def pair(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Pair a phone or browser to use NAVIG OS here, through your own relay."""
    status_code, data = _daemon("/api/deck/os/pair", method="POST")
    if status_code != 200:
        message = str(data.get("message") or data.get("error") or f"HTTP {status_code}")
        if as_json:
            ch.emit_json({"ok": False, "error": data.get("error", "failed"), "message": message})
        else:
            ch.error("Could not start pairing.", message)
        raise typer.Exit(1)

    link = str(data.get("link") or "")
    if as_json:
        ch.emit_json({"ok": True, "link": link, "expires_at": data.get("expires_at")})
        return

    qr = terminal_qr(link)
    if qr:
        ch.console.print(qr, highlight=False, markup=False)
    ch.success("Scan this with the phone's camera, or open the link on the other device:")
    ch.console.print(link, highlight=False, markup=False, soft_wrap=True)
    ttl = int(data.get("ttl_s") or 120)
    ch.dim(f"It works once, for {ttl // 60} minutes. The device appears in: navig os devices")
    if qr is None:
        ch.dim("No QR here (the optional qrcode package is not installed). "
               "NAVIG OS → Settings → Remote access shows one.")


@app.command("link")
def link(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Let ANOTHER NAVIG OS use this computer as its brain, through your own relay.

    Prints a one-time link. Paste it into the other machine's NAVIG OS
    (Settings → Connect to my NAVIG). It gets its own key, never this computer's, and
    you can remove it any time: navig os devices revoke <id>
    """
    status_code, data = _daemon("/api/deck/os/link", method="POST")
    if status_code != 200:
        _fail(as_json, data, status_code, "Could not make a link.")

    url = str(data.get("link") or "")
    if as_json:
        ch.emit_json({"ok": True, "link": url, "expires_at": data.get("expires_at")})
        return
    ch.success("Paste this into the other NAVIG OS (Settings → Connect to my NAVIG):")
    ch.console.print(url, highlight=False, markup=False, soft_wrap=True)
    ttl = int(data.get("ttl_s") or 600)
    ch.dim(f"It works once, for {ttl // 60} minutes. Treat it like a password until it is used.")
    ch.dim("The machine appears in: navig os devices")


devices_app = typer.Typer(help="Phones, browsers and other NAVIG OS machines that use this computer")
app.add_typer(devices_app, name="devices")


def _ago(stamp: object) -> str:
    from datetime import datetime, timezone

    if not stamp:
        return "never"
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return str(stamp)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    minutes = int((datetime.now(timezone.utc) - then).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return then.date().isoformat()


@devices_app.callback(invoke_without_command=True)
def devices(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """List paired devices."""
    if ctx.invoked_subcommand is not None:
        return
    _, data = _daemon("/api/deck/os/devices", method="GET")
    rows = data.get("devices") or []
    if as_json:
        ch.emit_json({"devices": rows})
        return
    if not rows:
        ch.info("No paired devices.", "Pair a phone with: navig os pair · link a NAVIG OS with: navig os link")
        return
    table = ch.create_table("Paired devices", [
        {"name": "ID", "style": "cyan"},
        {"name": "Device", "style": "white"},
        {"name": "Uses", "style": "white"},
        {"name": "Paired", "style": "white"},
        {"name": "Last seen", "style": "white"},
        {"name": "Today", "style": "white", "justify": "right"},
        {"name": "State", "style": "white"},
    ])
    for d in rows:
        table.add_row(
            str(d.get("id", "")),
            str(d.get("name", "")),
            "this brain" if d.get("kind") == "brain" else "NAVIG OS here",
            _ago(d.get("created_at")),
            _ago(d.get("last_seen")),
            f"{int(d.get('msgs_today') or 0):,} msgs",
            "[green]● connected[/green]" if d.get("connected") else "[dim]○ idle[/dim]",
        )
    ch.print_table(table)
    ch.dim(f"{len(rows)} paired · remove one with: navig os devices revoke <id>")


@devices_app.command("revoke")
def devices_revoke(
    device_id: str = typer.Argument(..., help="The device ID from `navig os devices`"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Remove a device now. Its live session ends; it must pair again to come back."""
    from urllib.parse import quote

    status_code, data = _daemon(f"/api/deck/os/devices/{quote(device_id, safe='')}", method="DELETE")
    if status_code == 404:
        if as_json:
            ch.emit_json({"ok": False, "error": "not_found"})
        else:
            ch.error(f"No paired device with ID {device_id}.", "See them with: navig os devices")
        raise typer.Exit(1)
    if status_code != 200:
        if as_json:
            ch.emit_json({"ok": False, "error": data.get("error", "failed")})
        else:
            ch.error("Could not remove the device.", str(data.get("message") or f"HTTP {status_code}"))
        raise typer.Exit(1)
    if as_json:
        ch.emit_json({"ok": True, "sessions_ended": data.get("sessions_ended", 0)})
        return
    ended = int(data.get("sessions_ended") or 0)
    ch.success(f"Removed {device_id}." + (" Its live session was ended." if ended else ""))


account_app = typer.Typer(help="Link this computer to your navig.run account, so any browser can sign in to it")
app.add_typer(account_app, name="account")


def _fail(as_json: bool, data: dict, status_code: int, headline: str) -> None:
    message = str(data.get("message") or data.get("error") or f"HTTP {status_code}")
    if as_json:
        ch.emit_json({"ok": False, "error": data.get("error", "failed"), "message": message})
    else:
        ch.error(headline, message)
    raise typer.Exit(1)


@account_app.callback(invoke_without_command=True)
def account(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Show which navig.run account this computer is linked to."""
    if ctx.invoked_subcommand is not None:
        return
    status_code, data = _daemon("/api/deck/os/account", method="GET")
    if status_code != 200:
        _fail(as_json, data, status_code, "Could not read the account link.")
    if as_json:
        ch.emit_json(data)
        return
    state, email = data.get("state"), data.get("email", "")
    if state == "linked":
        ch.success(f"Linked to {email}.")
        ch.dim("Sign in at https://os.navig.run on any device; each new one asks you here or in Telegram.")
    elif state == "waiting":
        ch.warning(f"Waiting for {email} to confirm — click the link in the email from navig.run.")
        if not data.get("reachable"):
            ch.dim("navig.run could not be reached just now, so this is not confirmed either way.")
    else:
        ch.info("Not linked to a navig.run account.", "Link it with: navig os account link <email>")


@account_app.command("link")
def account_link(
    email: str = typer.Argument(..., help="The email you use for navig.run"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Link this computer to a navig.run email; nothing is linked until you click the emailed link."""
    status_code, data = _daemon("/api/deck/os/account", method="POST", body={"email": email})
    if status_code != 200:
        _fail(as_json, data, status_code, "Could not link this computer.")
    if as_json:
        ch.emit_json({"ok": True, "email": data.get("email"), "dev_confirm_url": data.get("devConfirmUrl")})
        return
    ch.success(f"Check {data.get('email')} and click the link to finish.")
    ch.dim("Then sign in at https://os.navig.run on any device. Check the link with: navig os account")


@account_app.command("unlink")
def account_unlink(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Unlink this computer. Paired devices keep working; new ones need a QR code again."""
    status_code, data = _daemon("/api/deck/os/account", method="DELETE")
    if status_code != 200:
        _fail(as_json, data, status_code, "Could not unlink this computer.")
    if as_json:
        ch.emit_json({"ok": True, "navig_run_updated": bool(data.get("navig_run_updated"))})
        return
    ch.success("Unlinked. No new device can sign in to this computer through navig.run.")
    if not data.get("navig_run_updated"):
        ch.dim("navig.run could not be told just now; it will still list this computer until it can.")


requests_app = typer.Typer(help="Devices that signed in and wait for your approval")
app.add_typer(requests_app, name="requests")


@requests_app.callback(invoke_without_command=True)
def requests_list(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """List devices waiting for your approval."""
    if ctx.invoked_subcommand is not None:
        return
    _, data = _daemon("/api/deck/os/requests", method="GET")
    rows = data.get("requests") or []
    if as_json:
        ch.emit_json({"requests": rows})
        return
    if not rows:
        ch.info("No device is waiting for approval.")
        return
    table = ch.create_table("Waiting for your approval", [
        {"name": "ID", "style": "cyan"},
        {"name": "Device", "style": "white"},
        {"name": "Signed in as", "style": "white"},
        {"name": "Asked", "style": "white"},
    ])
    for r in rows:
        table.add_row(str(r.get("id", "")), str(r.get("name", "")), str(r.get("email", "")), _ago(r.get("created_at")))
    ch.print_table(table)
    ch.dim("Allow one with: navig os requests allow <id> · refuse with: navig os requests deny <id>")


def _decide(request_id: str, approve: bool, as_json: bool) -> None:
    from urllib.parse import quote

    rid = quote(request_id, safe="")
    if approve:
        status_code, data = _daemon(f"/api/deck/os/requests/{rid}/approve", method="POST")
    else:
        status_code, data = _daemon(f"/api/deck/os/requests/{rid}/deny", method="POST")
    if status_code != 200:
        _fail(as_json, data, status_code, "That request could not be answered.")
    if as_json:
        ch.emit_json({"ok": True, "approved": approve, "name": data.get("name")})
        return
    name = data.get("name") or "The device"
    if approve:
        ch.success(f"{name} can now use NAVIG OS. Remove it any time with: navig os devices revoke <id>")
    else:
        ch.success(f"{name} was denied.")


@requests_app.command("allow")
def requests_allow(
    request_id: str = typer.Argument(..., help="The request ID from `navig os requests`"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Let this device use NAVIG OS. Only allow a sign-in you made yourself, just now."""
    _decide(request_id, True, as_json)


@requests_app.command("deny")
def requests_deny(
    request_id: str = typer.Argument(..., help="The request ID from `navig os requests`"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Refuse this device."""
    _decide(request_id, False, as_json)
