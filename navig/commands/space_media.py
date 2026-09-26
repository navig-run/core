"""``navig space media`` — keep a space's code tree attached to its media tree.

A space or project stays lean by keeping heavy assets in a separate media tree
and attaching them through a ``.media`` directory link. This group verifies and
repairs those links; the engine lives in :mod:`navig.spaces.media_links`.

Mounted onto ``space_app`` at the tail of :mod:`navig.commands.space`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer

from navig import console_helper as ch
from navig.console_helper import get_console
from navig.platform.paths import resolve_user_path
from navig.spaces.media_links import (
    Finding,
    MediaConfig,
    MediaConfigError,
    Report,
    ScanRoot,
    ensure_ignored,
    is_link,
    load_config,
    make_link,
    remove_link,
    save_config,
    scan,
)

media_app = typer.Typer(
    name="media",
    help="Verify and repair the code/media split (<project>/.media -> <media root>).",
    no_args_is_help=True,
)

_console = get_console()

_STYLES: dict[Finding, str] = {
    Finding.BROKEN: "bold red",
    Finding.NOT_A_LINK: "bold red",
    Finding.FOREIGN: "bold red",
    Finding.UNLINKED: "yellow",
    Finding.TRACKED: "bold yellow",
    Finding.UNIGNORED: "yellow",
    Finding.ORPHAN: "cyan",
}


def _Table(*args, **kwargs):
    from rich.table import Table  # noqa: PLC0415

    return Table(*args, **kwargs)


def _load() -> MediaConfig:
    try:
        return load_config()
    except MediaConfigError as exc:
        ch.error(
            "No media layout configured.",
            details=str(exc),
        )
        raise typer.Exit(2) from exc


def _findings_text(findings: list[Finding]) -> str:
    return ", ".join(f"[{_STYLES.get(f, 'white')}]{f.value}[/]" for f in findings)


def _render(report: Report, cfg: MediaConfig, *, show_ok: bool) -> None:
    rows = report.entries if show_ok else report.problems
    if rows:
        table = _Table(title=f"media links — {cfg.media_root}", expand=False)
        table.add_column("state", no_wrap=True)
        table.add_column("project", no_wrap=True)
        table.add_column("link -> target")
        table.add_column("detail", overflow="fold")
        for entry in rows:
            state = "[green]ok[/]" if entry.ok else _findings_text(entry.findings)
            target = str(entry.target) if entry.target else "—"
            table.add_row(
                state, entry.name, f"{entry.link}\n  -> {target}", entry.detail
            )
        _console.print(table)

    if report.orphans:
        orphan_table = _Table(title="media folders nothing points at", expand=False)
        orphan_table.add_column("state", no_wrap=True)
        orphan_table.add_column("folder")
        for path in report.orphans:
            orphan_table.add_row("[cyan]orphan[/]", str(path))
        _console.print(orphan_table)

    counts = report.to_dict()["counts"]
    _console.print(
        f"\nchecked [bold]{counts['checked']}[/] links · "
        f"[green]{counts['ok']} ok[/] · "
        f"[yellow]{counts['problems']} with findings[/] "
        f"([red]{counts['severe']} severe[/]) · "
        f"[cyan]{counts['orphans']} orphan folder(s)[/]"
    )


@media_app.command("verify")
def media_verify(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
    show_ok: bool = typer.Option(False, "--all", "-a", help="List healthy links too."),
    no_git: bool = typer.Option(False, "--no-git", help="Skip the git checks."),
    strict: bool = typer.Option(
        False, "--strict", help="Exit non-zero on any finding, not just severe ones."
    ),
) -> None:
    """Check every media link across the configured trees.

    Exits 1 when something is actually broken — a dangling link, media sitting
    inside a repo, or a link pointing outside the media root. ``--strict`` also
    fails on the tidiness findings, which is what a scheduled check wants.

    A dangling link is the case worth having a command for: it still reports as a
    directory and lists as empty, so it reads as "no media yet" rather than as
    breakage.
    """
    cfg = _load()
    report = scan(cfg, check_git=not no_git)

    if json_output:
        _console.print_json(json.dumps(report.to_dict()))
    else:
        _render(report, cfg, show_ok=show_ok)

    if report.problems if strict else report.severe:
        raise typer.Exit(1)


@media_app.command("list")
def media_list(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show every media link and its target, healthy ones included."""
    cfg = _load()
    report = scan(cfg, check_git=False)
    if json_output:
        _console.print_json(json.dumps(report.to_dict()))
    else:
        _render(report, cfg, show_ok=True)


@media_app.command("link")
def media_link(
    project: Path = typer.Argument(..., help="The project directory to attach."),
    name: str | None = typer.Option(
        None, "--name", "-n", help="Media folder name (default: the project's own)."
    ),
    create: bool = typer.Option(
        True, "--create/--no-create", help="Create the media folder if absent."
    ),
) -> None:
    """Attach one project to its media folder, creating the folder if needed."""
    cfg = _load()
    target_project = resolve_user_path(project)
    if not target_project.is_dir():
        ch.error(f"Not a directory: {target_project}")
        raise typer.Exit(2)

    media_dir = cfg.media_dir_for(name or target_project.name)
    link = target_project / cfg.link_name

    if link.exists() or is_link(link):
        ch.warning(f"{link} already exists — nothing to do.")
        raise typer.Exit(1)

    if not media_dir.is_dir():
        if not create:
            ch.error(f"No media folder at {media_dir}", details="Pass --create to make it.")
            raise typer.Exit(1)
        media_dir.mkdir(parents=True)
        ch.info(f"Created {media_dir}")

    make_link(link, media_dir)
    ch.success(f"Linked {link} -> {media_dir}")
    if ensure_ignored(target_project, cfg):
        ch.info(f"Added {cfg.link_name}/ to .gitignore")


@media_app.command("fix")
def media_fix(
    dry_run: bool = typer.Option(
        True, "--dry-run/--apply", help="Preview by default; --apply to write."
    ),
    move_media: bool = typer.Option(
        False,
        "--move-media",
        help="Also move a real .media directory into the media tree and link it. "
        "This MOVES FILES, so it is opt-in.",
    ),
) -> None:
    """Repair what can be repaired without a judgement call.

    Creates missing media folders, attaches unlinked projects and adds the
    missing ignore rule. It will not repoint a link aimed outside the media root
    — only a human knows where that media went — it will not untrack committed
    media, because that is a commit, and it moves files only with
    ``--move-media``.
    """
    cfg = _load()
    report = scan(cfg)
    verb = "would" if dry_run else ""
    done = 0

    for entry in report.problems:
        for finding in entry.findings:
            if finding is Finding.BROKEN and entry.target is not None:
                _console.print(f"{verb} create missing media folder {entry.target}")
                if not dry_run:
                    entry.target.mkdir(parents=True, exist_ok=True)
                done += 1

            elif finding is Finding.UNLINKED and entry.target is not None:
                _console.print(f"{verb} link {entry.link} -> {entry.target}")
                if not dry_run:
                    make_link(entry.link, entry.target)
                done += 1

            elif finding is Finding.UNIGNORED:
                _console.print(
                    f"{verb} add {cfg.link_name}/ to {entry.path}/.gitignore"
                )
                if not dry_run:
                    ensure_ignored(entry.path, cfg)
                done += 1

            elif finding is Finding.NOT_A_LINK:
                if not move_media:
                    _console.print(
                        f"[yellow]skip[/] {entry.link} holds real media "
                        f"({entry.detail}) — rerun with --move-media to relocate it"
                    )
                    continue
                media_dir = cfg.media_dir_for(entry.name)
                _console.print(
                    f"{verb} move {entry.link} -> {media_dir} and replace with a link"
                )
                if not dry_run:
                    media_dir.mkdir(parents=True, exist_ok=True)
                    for child in entry.link.iterdir():
                        shutil.move(str(child), str(media_dir / child.name))
                    entry.link.rmdir()
                    make_link(entry.link, media_dir)
                    ensure_ignored(entry.path, cfg)
                done += 1

            elif finding is Finding.FOREIGN:
                _console.print(
                    f"[yellow]manual[/] {entry.link} points outside {cfg.media_root} "
                    f"({entry.target}) — repoint it with `navig space media relink`"
                )

            elif finding is Finding.TRACKED:
                # Untracking is a commit against the project's repo. Adding an
                # ignore rule instead would change nothing, so say so and stop.
                _console.print(
                    f"[yellow]manual[/] {entry.link}: {entry.detail}. Untrack it and "
                    "commit — an ignore rule alone cannot take effect on a tracked path."
                )

    if not done:
        ch.success("Nothing to fix.")
    elif dry_run:
        _console.print(f"\n[bold]{done}[/] change(s) available — rerun with --apply")
    else:
        ch.success(f"Applied {done} change(s).")


@media_app.command("relink")
def media_relink(
    project: Path = typer.Argument(..., help="The project whose link is wrong."),
    target: Path = typer.Argument(..., help="The media folder it should point at."),
) -> None:
    """Repoint one media link. Removes the link only — never the media."""
    cfg = _load()
    target_project = resolve_user_path(project)
    media_dir = resolve_user_path(target)
    link = target_project / cfg.link_name

    if not media_dir.is_dir():
        ch.error(f"No such media folder: {media_dir}")
        raise typer.Exit(2)

    if link.exists() or is_link(link):
        if not is_link(link):
            ch.error(
                f"{link} is a real directory, not a link.",
                details="Use `navig space media fix --move-media` so its contents are kept.",
            )
            raise typer.Exit(2)
        remove_link(link)

    make_link(link, media_dir)
    ch.success(f"Relinked {link} -> {media_dir}")


@media_app.command("config")
def media_config(
    init: bool = typer.Option(False, "--init", help="Write the layout."),
    media_root: Path | None = typer.Option(
        None, "--media-root", help="The media tree everything links into."
    ),
    projects_root: list[Path] = typer.Option(
        None,
        "--projects-root",
        help="A code tree holding <category>/<project>. Repeatable.",
    ),
    space_root: list[Path] = typer.Option(
        None,
        "--space-root",
        help="A tree scanned for links at any depth, with no project check. Repeatable.",
    ),
) -> None:
    """Show the media layout, or write it with ``--init``.

    Stored under ``space.media`` in ``~/.navig/config.yaml``, so no path is baked
    into navig itself. ``NAVIG_MEDIA_ROOT`` overrides the media root per run.
    """
    if not init:
        try:
            cfg = load_config()
        except MediaConfigError as exc:
            ch.error("No media layout configured.", details=str(exc))
            raise typer.Exit(2) from exc
        _console.print_json(json.dumps(cfg.to_dict()))
        return

    if media_root is None:
        ch.error("--init needs --media-root.")
        raise typer.Exit(2)

    roots = [
        ScanRoot(path=resolve_user_path(p), project_depth=2, max_depth=2)
        for p in (projects_root or [])
    ]
    roots += [
        ScanRoot(path=resolve_user_path(p), project_depth=0, max_depth=3)
        for p in (space_root or [])
    ]
    if not roots:
        ch.error(
            "--init needs at least one root.",
            details="Pass --projects-root and/or --space-root.",
        )
        raise typer.Exit(2)

    written = save_config(
        MediaConfig(media_root=resolve_user_path(media_root), roots=tuple(roots))
    )
    ch.success("Wrote the media layout.", details=str(written))
