"""navig move — move this NAVIG to another machine, and back.

    navig move out [-o FILE]        on the old machine: pack everything into one encrypted
                                    file, then stand this NAVIG down
    navig move in FILE [--replace]  on the new machine: unpack it; it becomes this NAVIG
    navig move status               has this NAVIG moved away?
    navig move undo                 bring it back on the old machine

The identity moves, it is not copied: ``deck.api_key`` is the relay tenant the Telegram
bot is reached through, so only one machine may answer as it. ``move out`` stops the
daemon, packs (with the daemon stopped, so its databases are consistent), verifies the
file opens, and only then writes the "moved" marker that keeps this NAVIG from starting
again. While no machine is connected the relay queues Telegram updates and delivers them
to the new one. See ``navig.move`` for the format and what is (and is not) carried.
"""

from __future__ import annotations

import os
import socket
import sys
import time

import typer

from navig import console_helper as ch

move_app = typer.Typer(
    help="Move this NAVIG to another machine (one encrypted file), and back",
    no_args_is_help=True,
)

_PASSPHRASE_ENV = "NAVIG_MOVE_PASSPHRASE"


def _size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _daemon_running() -> bool:
    try:
        from navig.daemon.supervisor import NavigDaemon

        return bool(NavigDaemon.is_running())
    except Exception:  # noqa: BLE001 — no supervisor module/state: nothing of ours is running
        return False


def _stop_daemon() -> None:
    """Stop through the one stop path (`navig service stop`): stop flag, task disabled,
    graceful shutdown. It raises typer.Exit itself when the daemon will not stop."""
    from navig.commands.service import service_stop

    service_stop(admin=False)


def _vault():
    from navig.vault.core import get_vault

    return get_vault()


def _interactive() -> bool:
    """A person at a terminal (the stdin the command sees, not the process's fd 0)."""
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def _vault_readable(vault) -> bool:
    """Can this process decrypt the vault? A vault protected by a passphrase cannot until
    it is unlocked in-process (there is no CLI unlock; onboarding does the same)."""
    try:
        items = vault.list()
        if items:
            vault.get_bytes(items[0].label)
        return True
    except Exception:  # noqa: BLE001 — any failure means "not readable yet"
        return False


def _unlock_if_needed(vault) -> None:
    if _vault_readable(vault):
        return
    if not _interactive():
        ch.error(
            "Your vault is protected by a passphrase, so its secrets cannot be packed.",
            "Run navig move out in a terminal: it asks for the vault passphrase.",
        )
        raise typer.Exit(1)
    passphrase = typer.prompt("Vault passphrase", hide_input=True)
    try:
        vault.unlock(passphrase=passphrase.encode("utf-8"))
    except Exception:  # noqa: BLE001 — reported below as a plain refusal
        pass
    if not _vault_readable(vault):
        ch.error("That passphrase did not unlock the vault. Nothing changed.")
        raise typer.Exit(1)


def _fresh_vault():
    # Not the singleton: `move in` renames the config dir (and the vault in it) aside, so a
    # Vault opened earlier in this process would point at the old one.
    from navig.vault.core import Vault

    return Vault()


@move_app.command("out")
def move_out(
    output: str = typer.Option("", "--output", "-o", help="Where to write the bundle (default: here)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before stopping NAVIG here"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output (includes the passphrase)"),
) -> None:
    """Pack this NAVIG into one encrypted file and stand it down here."""
    from navig.move import marker, state
    from navig.move.bundle import BundleError, new_passphrase
    from navig.platform.paths import invocation_cwd, resolve_user_path

    if marker.read_moved() is not None:
        ch.error("This NAVIG has already moved away.", "Bring it back first with: navig move undo")
        raise typer.Exit(1)

    roots = state.default_roots()
    vault = _vault()
    _unlock_if_needed(vault)
    s = state.survey(roots, vault)
    stamp = time.strftime("%Y%m%d-%H%M")
    out = resolve_user_path(output) if output else invocation_cwd() / f"navig-{socket.gethostname()}-{stamp}.navigmove"
    if out.is_dir():
        out = out / f"navig-{socket.gethostname()}-{stamp}.navigmove"

    if not as_json:
        table = ch.create_table("What moves", [
            {"name": "Part", "style": "cyan"},
            {"name": "Files", "style": "white", "justify": "right"},
            {"name": "Size", "style": "white", "justify": "right"},
        ])
        labels = {"navig": "NAVIG (config, spaces, memory…)", "navig-data": "NAVIG data", "navig-os": "NAVIG OS settings"}
        for prefix, (files, size) in s.by_root.items():
            table.add_row(labels.get(prefix, prefix), f"{files:,}", _size(size))
        table.add_row("Vault secrets", f"{s.vault_items:,}", "—")
        ch.print_table(table)
        ch.dim(f"→ {out}")
        ch.warning(
            "NAVIG stops here and stays stopped: the new machine becomes this NAVIG "
            "(same Telegram bot, same relay). Undo any time with: navig move undo"
        )
    if not yes:
        if not _interactive():
            ch.error("Refusing to stand NAVIG down without confirmation.", "Run in a terminal, or pass --yes")
            raise typer.Exit(1)
        if not typer.confirm("Pack it and stop NAVIG here?", default=False):
            ch.info("Nothing changed.")
            raise typer.Exit(0)

    was_running = _daemon_running()
    if was_running:
        _stop_daemon()
    passphrase = new_passphrase()
    try:
        state.pack(out, passphrase, roots, vault, progress=(None if as_json else lambda p: ch.dim(f"packing {p}…")))
        manifest = state.verify(out, passphrase)
    except (state.MoveError, BundleError, OSError) as exc:
        ch.error(f"Nothing moved: {exc}")
        if was_running:
            ch.info("NAVIG was stopped for packing. Start it again with: navig service start")
        raise typer.Exit(1) from None

    marker.write_moved({"bundle": str(out), "to": ""})
    if as_json:
        ch.emit_json({
            "ok": True,
            "bundle": str(out),
            "passphrase": passphrase,
            "files": manifest.get("files", 0),
            "vault_items": manifest.get("vault_items", 0),
            "os_sign_ins": (manifest.get("os_sign_ins") or {}).get("carried", 0),
            "os_connections_to_sign_in": manifest.get("os_connections_to_sign_in", []),
        })
        return
    os_carried = (manifest.get("os_sign_ins") or {}).get("carried", 0)
    ch.success(
        f"Packed {manifest.get('files', 0):,} files, {manifest.get('vault_items', 0)} secrets"
        f" and {os_carried} NAVIG OS sign-ins."
    )
    os_note = (manifest.get("os_sign_ins") or {}).get("note")
    if os_note:
        ch.warning(os_note)
    ch.console.print(f"\n  Bundle:      {out}", highlight=False, markup=False)
    ch.console.print(f"  Passphrase:  {passphrase}\n", highlight=False, markup=False)
    ch.warning("Write the passphrase down now. It is shown once, and the file is useless without it.")
    ch.info("On the new machine:  navig move in <the file>")
    ch.dim("NAVIG is stopped here. Telegram messages wait at your relay until the new machine connects.")


@move_app.command("in")
def move_in(
    bundle: str = typer.Argument(..., help="The .navigmove file from `navig move out`"),
    replace: bool = typer.Option(False, "--replace", help="Replace the NAVIG identity this machine already has"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before unpacking"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Unpack a moved NAVIG here. Whatever was here before is kept as a backup."""
    from navig.move import state
    from navig.move.bundle import BundleError, read_header
    from navig.platform.paths import resolve_user_path

    path = resolve_user_path(bundle)
    if not path.is_file():
        ch.error(f"No such file: {path}")
        raise typer.Exit(1)
    try:
        header = read_header(path)
    except BundleError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from None
    if _daemon_running():
        ch.error("NAVIG is running on this machine.", "Stop it first: navig service stop")
        raise typer.Exit(1)
    source = header.get("source") or {}
    if not as_json:
        ch.info(f"Bundle from {source.get('hostname', '?')}, made {source.get('created_at', '?')}.")
    if not yes and _interactive() and not typer.confirm("Unpack it here?", default=True):
        ch.info("Nothing changed.")
        raise typer.Exit(0)
    passphrase = os.environ.get(_PASSPHRASE_ENV) or typer.prompt("Passphrase", hide_input=True)
    try:
        result = state.unpack(path, passphrase, state.default_roots(), _fresh_vault, replace=replace)
    except (state.MoveError, BundleError, OSError) as exc:
        ch.error(f"Nothing changed: {exc}")
        raise typer.Exit(1) from None

    signins = result.os_to_sign_in
    if as_json:
        ch.emit_json({
            "ok": True,
            "from": source.get("hostname", ""),
            "vault_items": result.vault_restored,
            "backups": [str(b) for b in result.backups],
            "paths_rewritten": result.paths_rewritten,
            "os_sign_ins": result.os_sign_ins_restored,
            "os_sign_ins_note": result.os_sign_ins_note,
            "os_connections_to_sign_in": signins,
        })
        return
    ch.success(f"This machine is now the NAVIG from {source.get('hostname', 'the old machine')}.")
    ch.dim(
        f"{result.vault_restored} secrets and {result.os_sign_ins_restored} NAVIG OS sign-ins"
        " re-sealed for this machine."
    )
    for b in result.backups:
        ch.dim(f"What was here before is kept at {b}")
    if result.os_sign_ins_note:
        ch.warning(result.os_sign_ins_note)
    if signins:
        ch.warning("Sign in to these again in NAVIG OS: " + ", ".join(signins))
    ch.info("Start it:  navig service start")


@move_app.command("status")
def move_status(as_json: bool = typer.Option(False, "--json", help="Machine-readable output")) -> None:
    """Has this NAVIG moved to another machine?"""
    from navig.move import marker

    info = marker.read_moved()
    if as_json:
        ch.emit_json({"moved": info is not None, **(info or {})})
        return
    if info is None:
        ch.info("This NAVIG lives here (it has not moved away).")
        return
    ch.warning(marker.moved_message(info))


@move_app.command("undo")
def move_undo(yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask")) -> None:
    """Bring a moved NAVIG back on this (old) machine."""
    from navig.daemon.service_manager import clear_stop_flag
    from navig.move import marker

    if marker.read_moved() is None:
        ch.info("Nothing to undo: this NAVIG has not moved away.")
        return
    ch.warning(
        "If the new machine is already running as this NAVIG, stop it there first "
        "(navig service stop): two machines must never answer as the same NAVIG."
    )
    if not yes:
        if not _interactive():
            ch.error("Refusing without confirmation.", "Run in a terminal, or pass --yes")
            raise typer.Exit(1)
        if not typer.confirm("Bring NAVIG back on this machine?", default=False):
            ch.info("Nothing changed.")
            raise typer.Exit(0)
    marker.clear_moved()
    clear_stop_flag()
    ch.success("NAVIG lives here again.")
    ch.info("Start it:  navig service start")


__all__ = ["move_app"]
