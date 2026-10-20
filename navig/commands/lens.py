"""``navig lens`` — manage Lenses (Tampermonkey-compatible site augmentations).

A Lens is a ``.user.js``: a ``==UserScript==`` header (``@match``, ``@run-at``, ``@grant``…)
plus code. Lenses live in ``<config_dir>/lenses/`` and run in every NAVIG browser host —
the NAVIG OS browser pane, ``navig cdp`` browsers, and the Chrome extension's web bar.

    navig lens list
    navig lens add ./hide-shorts.user.js        (or an https:// URL)
    navig lens show hide-shorts --url https://www.youtube.com/
    navig lens disable hide-shorts
    navig lens export hide-shorts -o hide-shorts.user.js
    navig lens remove hide-shorts
    navig lens review [hide-shorts]              what the agent proposed / an update changes
    navig lens approve hide-shorts | reject hide-shorts
    navig lens update [hide-shorts]              fetch install links; stage, never apply
"""

from __future__ import annotations

import typer

from navig import console_helper as ch

lens_app = typer.Typer(
    help="Manage Lenses — Tampermonkey-compatible scripts that augment websites in every "
    "NAVIG browser.",
    no_args_is_help=True,
)


def _esc(text: object) -> str:
    """User content (Lens names, errors) must never be read as Rich markup."""
    from rich.markup import escape

    return escape(str(text))


def _emit_source(source: str) -> None:
    """The Lens source, byte-for-byte. NOT ch.raw_print: that redacts secret-looking
    `key=value` text, which would silently change the code being exported."""
    typer.echo(source, nl=not source.endswith("\n"))


def _store():
    from navig.lenses import LensStore

    return LensStore()


def _where(manifest) -> str:
    pats = list(manifest.matches) + list(manifest.includes)
    if not pats:
        return "nowhere"
    head = pats[0]
    return head if len(pats) == 1 else f"{head} +{len(pats) - 1}"


@lens_app.command("list")
def lens_list(
    as_json: bool = typer.Option(False, "--json", help="Emit the Lenses as JSON."),
) -> None:
    """List installed Lenses."""
    records = _store().records()
    if as_json:
        ch.emit_json([r.to_dict() for r in records])
        return
    if not records:
        ch.info("No Lenses yet. Add one with: navig lens add <file.user.js | https://…>")
        return

    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Lens", style="bold", no_wrap=True)
    table.add_column("Ver", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Runs at", no_wrap=True)
    table.add_column("From", no_wrap=True)
    table.add_column("Sites")  # the one free-text column — wraps on a narrow terminal
    on = 0
    for r in records:
        m = r.manifest
        if r.error or m is None:
            state = "[red]✗ broken[/red]"
        elif r.needs_review and not r.enabled:
            state = "[yellow]◐ review[/yellow]"
        elif r.enabled:
            state = "[green]● on[/green]"
            on += 1
        else:
            state = "[dim]○ off[/dim]"
        origin = "[yellow]agent[/yellow]" if r.origin == "agent" else r.origin
        table.add_row(
            _esc(r.id),
            _esc(m.version) if m else "—",
            state,
            m.run_at.replace("document-", "") if m else "—",
            origin,
            _esc(r.error) if r.error else _esc(_where(m)),
        )
    ch.console.print(table)
    waiting = sum(1 for r in records if r.needs_review)
    nudge = f"{on}/{len(records)} on"
    if waiting:
        nudge += f" · {waiting} waiting for your review — navig lens review"
    else:
        nudge += " · toggle one with navig lens enable|disable <id>"
    ch.dim(nudge)


@lens_app.command("show")
def lens_show(
    lens_id: str = typer.Argument(..., help="Lens id (see navig lens list)."),
    url: str = typer.Option(None, "--url", help="Also say whether this Lens runs on URL."),
    source: bool = typer.Option(False, "--source", help="Print the full source."),
    as_json: bool = typer.Option(False, "--json", help="Emit the Lens as JSON."),
) -> None:
    """Show a Lens: where it runs, what it may use, warnings."""
    try:
        rec = _store().get(lens_id)
    except ValueError as exc:
        ch.error(_esc(exc))
        raise typer.Exit(1) from None
    if rec is None:
        ch.error(f"No Lens '{_esc(lens_id)}'.", "See: navig lens list")
        raise typer.Exit(1)
    m = rec.manifest
    runs = None
    if url and m is not None:
        target = url.split("#")[0]
        runs = any(r.test(target) for r in m.include_res) and not any(
            r.test(target) for r in m.exclude_res
        )
    if as_json:
        out = rec.to_dict(with_source=source)
        if url:
            out["runs_on_url"] = runs
        ch.emit_json(out)
        return
    if m is None:
        ch.error(f"{_esc(rec.id)} is broken: {_esc(rec.error)}")
        raise typer.Exit(1)

    from navig.console_helper import Table

    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column("k", style="dim", no_wrap=True)
    t.add_column("v")
    t.add_row("Lens", f"[bold]{_esc(m.name)}[/bold] {_esc(m.version)}  ({_esc(rec.id)})")
    if m.description:
        t.add_row("About", _esc(m.description))
    t.add_row("State", "[green]● on[/green]" if rec.enabled else "[dim]○ off[/dim]")
    t.add_row("From", _esc(rec.origin + (f" · {rec.install_url}" if rec.install_url else "")))
    t.add_row("Runs at", m.run_at + (" · top frame only" if m.noframes else " · all frames"))
    for p in m.matches:
        t.add_row("Match", _esc(p))
    for p in m.includes:
        t.add_row("Include", _esc(p))
    for p in m.excludes + m.exclude_matches:
        t.add_row("Exclude", _esc(p))
    if m.grants:
        t.add_row("Grants", _esc(", ".join(m.grants)))
    for req in rec.requires:
        t.add_row("Requires", _esc(req.get("url", "")))
    if m.capability:
        t.add_row("Harbor", _esc(m.capability))
    t.add_row("Syntax", "checked" if rec.syntax == "ok" else "[yellow]not checked[/yellow] (install node)")
    if runs is not None:
        verdict = "[green]runs here[/green]" if runs else "[dim]does not run here[/dim]"
        t.add_row("URL", f"{_esc(url)} → {verdict}")
    ch.console.print(t)
    for w in m.warnings:
        ch.warning(_esc(w))
    if source:
        _emit_source(rec.source)
    if rec.origin == "agent" and not rec.enabled:
        ch.dim(f"Proposed by the agent. Read it, then turn it on: navig lens enable {rec.id}")


@lens_app.command("add")
def lens_add(
    target: str = typer.Argument(..., help="A .user.js file, or an https:// URL to one."),
    disabled: bool = typer.Option(False, "--disabled", help="Install switched off."),
) -> None:
    """Install (or update) a Lens from a file or an https URL."""
    from navig.lenses import LensFetchError, LensFormatError, fetch_text
    from navig.platform.paths import resolve_user_path

    install_url = None
    try:
        if target.lower().startswith(("https://", "http://")):
            source = fetch_text(target)
            install_url = target
        else:
            path = resolve_user_path(target)
            if not path.is_file():
                ch.error(f"No such file: {_esc(path)}")
                raise typer.Exit(1)
            source = path.read_text(encoding="utf-8")
        before = _store().get(_store().preview(source).id)
        rec = _store().save(
            source,
            origin="url" if install_url else "user",
            install_url=install_url,
            enabled=False if disabled else None,
        )
    except (LensFormatError, LensFetchError) as exc:
        ch.error("Not installed.", _esc(exc))
        raise typer.Exit(1) from None
    except OSError as exc:
        ch.error("Not installed.", _esc(exc))
        raise typer.Exit(1) from None
    m = rec.manifest
    verb = "Updated" if before is not None else "Installed"
    ch.success(
        f"{verb} {_esc(rec.name)} ({_esc(rec.id)}) — {'on' if rec.enabled else 'off'}",
        f"runs on {_esc(_where(m))} at {m.run_at}" if m else None,
    )
    for w in (m.warnings if m else []):
        ch.warning(_esc(w))
    if rec.syntax != "ok":
        ch.dim("Syntax not checked (node not found) — a broken Lens only breaks itself.")


def _toggle(lens_id: str, on: bool) -> None:
    try:
        rec = _store().set_enabled(lens_id, on)
    except KeyError:
        ch.error(f"No Lens '{_esc(lens_id)}'.", "See: navig lens list")
        raise typer.Exit(1) from None
    except ValueError as exc:
        ch.error(_esc(exc))
        raise typer.Exit(1) from None
    ch.success(f"{_esc(rec.name)} is {'on' if on else 'off'}.")


@lens_app.command("enable")
def lens_enable(lens_id: str = typer.Argument(..., help="Lens id.")) -> None:
    """Turn a Lens on."""
    _toggle(lens_id, True)


@lens_app.command("disable")
def lens_disable(lens_id: str = typer.Argument(..., help="Lens id.")) -> None:
    """Turn a Lens off (it stays installed)."""
    _toggle(lens_id, False)


@lens_app.command("remove")
def lens_remove(
    lens_id: str = typer.Argument(..., help="Lens id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation."),
) -> None:
    """Delete a Lens and its cached @require files."""
    store = _store()
    try:
        rec = store.get(lens_id)
    except ValueError as exc:
        ch.error(_esc(exc))
        raise typer.Exit(1) from None
    if rec is None:
        ch.error(f"No Lens '{_esc(lens_id)}'.", "See: navig lens list")
        raise typer.Exit(1)
    if not yes and not ch.confirm_action(f"Delete Lens '{_esc(rec.name)}'?", default=False):
        ch.dim("Kept.")
        raise typer.Exit(1)
    store.remove(lens_id)
    ch.success(f"Removed {_esc(rec.name)}.")


@lens_app.command("export")
def lens_export(
    lens_id: str = typer.Argument(..., help="Lens id."),
    output: str = typer.Option(None, "--output", "-o", help="Write to this file (default: stdout)."),
) -> None:
    """Export a Lens's original .user.js (works in Tampermonkey / Violentmonkey too)."""
    from navig.platform.paths import resolve_user_path

    try:
        rec = _store().get(lens_id)
    except ValueError as exc:
        ch.error(_esc(exc))
        raise typer.Exit(1) from None
    if rec is None or not rec.source:
        ch.error(f"No Lens '{_esc(lens_id)}'.", "See: navig lens list")
        raise typer.Exit(1)
    if not output:
        _emit_source(rec.source)
        return
    path = resolve_user_path(output)
    path.write_text(rec.source, encoding="utf-8")
    ch.success(f"Wrote {_esc(path)}")


def _review_target(lens_id: str) -> dict:
    try:
        return _store().review_details(lens_id)
    except KeyError:
        ch.error(f"'{_esc(lens_id)}' has nothing waiting for review.", "See: navig lens review")
        raise typer.Exit(1) from None
    except ValueError as exc:
        ch.error(_esc(exc))
        raise typer.Exit(1) from None


_KIND = {
    "proposed": "[yellow]new — proposed by the agent[/yellow]",
    "agent": "[yellow]change by the agent[/yellow]",
    "update": "[cyan]update from its link[/cyan]",
}


@lens_app.command("review")
def lens_review(
    lens_id: str = typer.Argument(None, help="Show the diff for this Lens (omit to list)."),
    as_json: bool = typer.Option(False, "--json", help="Emit as JSON."),
) -> None:
    """What is waiting on you: agent proposals and staged updates, with the diff."""
    if lens_id is None:
        records = _store().reviews()
        if as_json:
            ch.emit_json([r.to_dict() for r in records])
            return
        if not records:
            ch.success("Nothing waiting for review.")
            return
        from navig.console_helper import Table

        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Lens", style="bold", no_wrap=True)
        table.add_column("What", no_wrap=True)
        table.add_column("Version", no_wrap=True)
        table.add_column("Next step")
        for r in records:
            pending = r.pending or {}
            kind = pending.get("kind") or "proposed"
            version = pending.get("version") or (r.manifest.version if r.manifest else "—")
            table.add_row(
                _esc(r.id), _KIND.get(kind, kind), _esc(version), f"→ navig lens review {_esc(r.id)}"
            )
        ch.console.print(table)
        ch.dim(f"{len(records)} waiting · decide with navig lens approve|reject <id>")
        return

    details = _review_target(lens_id)
    if as_json:
        ch.emit_json(details)
        return
    lens = details["lens"]
    kind = _KIND.get(details["kind"], details["kind"])
    ch.console.print(f"[bold]{_esc(lens['name'])}[/bold] ({_esc(lens['id'])}) — {kind}")
    widens = [c for c in details["changes"] if c["broadens"]]
    for change in details["changes"]:
        mark = "[yellow]▲[/yellow]" if change["broadens"] else "[dim]·[/dim]"
        parts = [f"+ {_esc(v)}" for v in change["added"]]
        parts += [f"− {_esc(v)}" for v in change["removed"]]
        ch.console.print(f"  {mark} {_esc(change['label'])}: {', '.join(parts)}")
    if details["diff"]:
        from rich.syntax import Syntax

        ch.console.print(Syntax(details["diff"], "diff", theme="ansi_dark", word_wrap=True))
    ch.dim(
        ("It would reach further than before (▲). " if widens else "")
        + f"Decide: navig lens approve {lens['id']}  ·  navig lens reject {lens['id']}"
    )


@lens_app.command("approve")
def lens_approve(lens_id: str = typer.Argument(..., help="Lens id.")) -> None:
    """Apply a staged change, or turn on a Lens the agent proposed."""
    from navig.lenses import LensFetchError, LensFormatError

    try:
        rec = _store().approve(lens_id)
    except KeyError:
        ch.error(f"'{_esc(lens_id)}' has nothing waiting for review.", "See: navig lens review")
        raise typer.Exit(1) from None
    except (ValueError, LensFormatError, LensFetchError) as exc:
        ch.error("Not approved.", _esc(exc))
        raise typer.Exit(1) from None
    version = f" {_esc(rec.manifest.version)}" if rec.manifest else ""
    ch.success(f"Approved {_esc(rec.name)}{version} — {'on' if rec.enabled else 'off'}.")


@lens_app.command("reject")
def lens_reject(lens_id: str = typer.Argument(..., help="Lens id.")) -> None:
    """Discard a staged change (the Lens stays as it was), or delete an agent's proposal."""
    try:
        kept = _store().reject(lens_id)
    except KeyError:
        ch.error(f"'{_esc(lens_id)}' has nothing waiting for review.", "See: navig lens review")
        raise typer.Exit(1) from None
    except ValueError as exc:
        ch.error(_esc(exc))
        raise typer.Exit(1) from None
    if kept:
        ch.success(f"Discarded the staged change — {_esc(lens_id)} stays as it was.")
    else:
        ch.success(f"Deleted the proposed Lens {_esc(lens_id)}.")


_STATUS = {
    "staged": "[yellow]◐ staged for review[/yellow]",
    "current": "[green]● up to date[/green]",
    "pending": "[yellow]◐ already waiting[/yellow]",
    "rejected": "[dim]○ you rejected this version[/dim]",
    "older": "[dim]○ link has an older version[/dim]",
    "error": "[red]✗ could not check[/red]",
}


@lens_app.command("update")
def lens_update(
    lens_id: str = typer.Argument(None, help="Only this Lens (default: every Lens with a link)."),
    as_json: bool = typer.Option(False, "--json", help="Emit the results as JSON."),
) -> None:
    """Check install links for new versions. Stages them for review — applies nothing."""
    rows = _store().check_updates(only=lens_id)
    if as_json:
        ch.emit_json(rows)
    elif not rows:
        ch.info("No Lens was installed from a link, so there is nothing to check.")
    else:
        from navig.console_helper import Table

        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Lens", style="bold", no_wrap=True)
        table.add_column("Result", no_wrap=True)
        table.add_column("Detail")
        for row in rows:
            detail = row.get("error") or row.get("version") or row.get("url") or ""
            table.add_row(_esc(row["id"]), _STATUS.get(row["status"], row["status"]), _esc(detail))
        ch.console.print(table)
        staged = sum(1 for r in rows if r["status"] == "staged")
        ch.dim(f"{staged} staged · review with navig lens review" if staged else "Nothing new to review.")
    if any(r["status"] == "error" for r in rows):
        raise typer.Exit(1)
