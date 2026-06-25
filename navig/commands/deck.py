"""navig deck — the NAVIG control UI (Telegram Mini App / web dashboard).

`open` launches the gateway-served deck in a browser (no Node needed); `dev`
runs the Next dev server against an EXISTING daemon (never spawns a second one);
`deploy` ships the deck to the user's Cloudflare (aliases `navig miniapp deploy`).
"""
import typer

from navig.console_helper import get_console

deck_app = typer.Typer(help="The NAVIG control UI — open · dev · deploy", no_args_is_help=True)
console = get_console()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _resolve_deck_api_key() -> str:
    """The deck bearer api_key from config (deck.api_key), for remote-daemon dev."""
    try:
        from navig.config import get_config_manager

        gc = get_config_manager().global_config or {}
        deck = gc.get("deck", {}) if isinstance(gc, dict) else {}
        return str((deck or {}).get("api_key", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _resolve_deck_url() -> str:
    """The URL where the deck is reachable: cloud public/lighthouse URL, else the
    local gateway (the gateway serves the prebuilt deck SPA)."""
    from navig.commands.cloud import _gateway_port  # noqa: PLC0415
    from navig.config import get_config_manager  # noqa: PLC0415

    try:
        gc = get_config_manager().global_config or {}
        cloud = gc.get("cloud", {}) if isinstance(gc, dict) else {}
        url = str((cloud.get("public_url") or cloud.get("lighthouse_url") or "")).strip().rstrip("/")
        if url:
            return url
    except Exception:  # noqa: BLE001
        pass
    return f"http://127.0.0.1:{_gateway_port()}"


def _npm_runner() -> list[str] | None:
    import shutil  # noqa: PLC0415

    npm = shutil.which("npm")
    return [npm] if npm else None


@deck_app.command("open")
def deck_open(
    print_url: bool = typer.Option(False, "--url", help="Print the URL instead of opening a browser."),
):
    """Open the control deck — the gateway already serves it (no Node, no second daemon)."""
    import webbrowser

    from navig import console_helper as ch

    url = _resolve_deck_url()
    if print_url:
        print(url)  # noqa: T201
        return
    ch.info(f"Opening the deck → {url}")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        ch.info(f"Open it manually: {url}")


@deck_app.command("dev")
def deck_dev(
    daemon: str = typer.Option(
        "", "--daemon", "-d",
        help="Daemon to develop against (default: local gateway). Pass a remote VPS URL to dev against it.",
    ),
):
    """Run the deck dev server against an EXISTING daemon — never spawns a second daemon.

    Default targets the local gateway (the installer's already-running daemon).
    ``--daemon https://my-vps`` develops against a remote daemon; the local
    ``deck.api_key`` is injected so the dev deck is pre-authed cross-origin.
    Requires the navig-deck source + Node (developer path).
    """
    import os
    import subprocess

    from navig import console_helper as ch
    from navig.commands.cloud import _gateway_port
    from navig.commands.miniapp import _find_deck_dir

    deck_dir = _find_deck_dir()
    if deck_dir is None:
        ch.error(
            "navig-deck source not found.",
            details="Set $NAVIG_DECK_DIR or run from the repo (needs the navig-deck package + Node).",
        )
        raise typer.Exit(1)
    runner = _npm_runner()
    if runner is None:
        ch.error("npm/node not found on PATH — install Node to run the deck dev server.")
        raise typer.Exit(1)

    target = (daemon or f"http://127.0.0.1:{_gateway_port()}").rstrip("/")
    env = dict(os.environ)
    env["NAVIG_DAEMON_URL"] = target
    env["NEXT_PUBLIC_DECK_DEV_DAEMON"] = target  # so the offline screen shows the real target

    is_local = target.startswith(("http://127.0.0.1", "http://localhost"))
    if not is_local:
        # A remote/VPS daemon is not loopback → the deck must present a Bearer key.
        # Inject the local deck.api_key for the dev SPA bootstrap to seed.
        key = _resolve_deck_api_key()
        if key:
            env["NEXT_PUBLIC_DECK_DEV_TOKEN"] = key
        else:
            ch.warning(
                "No deck.api_key configured — the remote deck may show a Connect screen.",
                details="Set one with `navig config set deck.api_key <key>` (or run on the daemon host).",
            )

    ch.success("Deck dev — against your existing daemon (no second daemon).", details=f"daemon: {target}")
    ch.info("Dev server: http://localhost:7432")
    try:
        rc = subprocess.run([*runner, "run", "dev"], cwd=str(deck_dir), env=env).returncode
    except KeyboardInterrupt:
        rc = 0
    raise typer.Exit(rc)


# ── Alias: `navig deck deploy` == `navig miniapp deploy` ─────────────────────
# "The deck" is NAVIG's Telegram Mini App / control UI. Reuse the identical
# miniapp deploy command so options, help text, and behaviour stay in lockstep
# with the canonical `navig miniapp deploy` (single source of truth).
from navig.commands.miniapp import miniapp_deploy as _miniapp_deploy  # noqa: E402

deck_app.command("deploy")(_miniapp_deploy)
