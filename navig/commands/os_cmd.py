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


def _is_os_checkout(path: Path) -> bool:
    """The package.json AND the server entry point.

    A bare package.json would also match an unrelated directory called ``apps/os``, which
    would then be handed to ``bun run server:prod`` and fail in a way that points nowhere
    near the actual mistake.
    """
    try:
        return (path / "package.json").is_file() and (
            path / "packages" / "server" / "src" / "index.ts"
        ).is_file()
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
    os_dir = find_os_dir(directory)
    if os_dir is None:
        named = directory or os.environ.get("NAVIG_OS_DIR", "")
        if named:
            # Say which directory was rejected and why. "Could not find it" would be a lie
            # when they told us exactly where to look.
            ch.error(
                f"Not a NAVIG OS checkout: {named}",
                "Expected a package.json and packages/server/src/index.ts inside it.",
            )
        else:
            ch.error(
                "Could not find the NAVIG OS source (apps/os).",
                "The desktop build is not released yet, so this needs the monorepo checkout. "
                "Point at it with --dir <path> or $NAVIG_OS_DIR.",
            )
        raise typer.Exit(1)

    if shutil.which("bun") is None:
        ch.error("bun is not installed.", "NAVIG OS is built with bun — install it from https://bun.sh")
        raise typer.Exit(1)

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
        ch.info("Would run: bun run server:prod", f"in {os_dir}")
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

    if fresh_password:
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
    ch.info(
        "From your phone or another machine",
        f"cloudflared tunnel --url http://127.0.0.1:{port}",
        no_wrap=True,
    )
    ch.dim("The server detects the tunnel and configures the browser connection itself.")
    ch.dim("Stop it with Ctrl-C.")

    run_env = {**os.environ, **env}
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["bun", "run", "server:prod"],
            cwd=str(os_dir),
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
