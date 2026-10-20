"""navig presets — reusable, typed presets for Text, Image, Audio and Video.

One catalog for every AI tool (``navig.presets``): builtin and plugin presets
ship as files, the operator's own live in the prompt library database. NAVIG OS
(Studio's preset picker) and ``/api/deck/presets`` read the same catalog.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from navig import console_helper as ch
from navig.console_helper import get_console

presets_app = typer.Typer(
    help="Reusable presets for Text, Image, Audio and Video: list, apply, create, share",
    no_args_is_help=True,
)
console = get_console()

_TOOL_HELP = "text · image · video · audio.generate · audio.enhance · speech"


def _catalog():
    from navig.presets import catalog

    return catalog


def _fail(msg: str, code: int = 1) -> None:
    ch.error(msg)
    raise typer.Exit(code)


def _get_or_fail(pid: str):
    p = _catalog().get_preset(pid)
    if p is None:
        matches = _catalog().list_presets(q=pid)
        hint = f" Did you mean: {', '.join(m.id for m in matches[:4])}?" if matches else ""
        _fail(f"No preset {pid!r}.{hint} See: navig presets list")
    return p


def _parse_sets(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            _fail(f"--set expects key=value, got {pair!r}")
        k, v = pair.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@presets_app.command("list")
def presets_list(
    tool: str = typer.Option("", "--tool", "-t", help=f"Only this tool ({_TOOL_HELP})"),
    layer: str = typer.Option("", "--layer", help="context · style · format · config"),
    query: str = typer.Option("", "--search", "-s", help="Filter by name, id, tag or category"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """List presets (favorites first)."""
    from navig.console_helper import Table

    cat = _catalog()
    items = cat.list_presets(tool=tool, layer=layer, q=query)
    if as_json:
        print(json.dumps([p.to_dict() for p in items], indent=2, ensure_ascii=False))
        return
    defaults = {t: cat.get_default(t) for t in {p.tool for p in items}}
    # The ID is what you type next, so it never truncates; Name is the one wrappable
    # column. Tool and layer share a column, and the description lives in `show` — at 80
    # columns, seven columns squeezed every cell to "Cinematic Cre…" / "audio.enh…".
    table = Table(box=None, show_header=True, padding=(0, 1))
    table.add_column("", no_wrap=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", min_width=12)
    # Filtered to one tool, every row repeats it — show just the layer then.
    table.add_column("Layer" if tool else "Tool · layer", no_wrap=bool(tool), min_width=6)
    table.add_column("Source", min_width=6)
    for p in items:
        mark = "[yellow]★[/yellow]" if p.favorite else ""
        if defaults.get(p.tool) == p.id:
            mark += "[green]●[/green]"
        src = p.source if p.trusted else "[yellow]imported[/yellow]"
        if p.overrides is not None:
            src += " [dim]edited[/dim]"
        table.add_row(mark, p.id, p.name, p.layer if tool else f"{p.tool} [dim]· {p.layer}[/dim]", src)
    if not items:
        ch.info("No presets match. Try `navig presets list` with no filters.")
        return
    console.print(table)
    for err in cat.load_errors():
        ch.warning(f"Skipped malformed preset file {err['path']}: {err['error']}")
    ch.info("★ favorite · ● default for its tool — next: navig presets show <id>")


@presets_app.command("show")
def presets_show(
    pid: str = typer.Argument(..., help="Preset id"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show one preset in full."""
    p = _get_or_fail(pid)
    if as_json:
        print(json.dumps(p.to_dict(), indent=2, ensure_ascii=False))
        return
    lines = [
        f"[bold]{p.name}[/bold]  [dim]{p.id} · v{p.version}[/dim]",
        f"Tool: {p.tool} · layer: {p.layer} · source: {p.source}"
        + ("" if p.trusted else " [yellow](unreviewed — instructions are screened and quoted)[/yellow]"),
    ]
    if p.description:
        lines.append(p.description)
    if p.instructions:
        lines += ["", "[bold]Instructions[/bold]", p.instructions]
    if p.template:
        lines += ["", "[bold]Template[/bold]", p.template]
    if p.params:
        lines += ["", "[bold]Settings[/bold]"] + [f"  {k}: {v}" for k, v in p.params.items()]
    if p.avoid:
        lines += ["", "[bold]Avoid[/bold]"] + [f"  - {a}" for a in p.avoid]
    if p.references:
        lines += ["", "[bold]Context references[/bold]"] + [f"  {r}" for r in p.references]
    if p.variables:
        lines += ["", "[bold]Variables[/bold]"] + [
            f"  {v.key}{' (required)' if v.required else ''}{f' = {v.default}' if v.default else ''}"
            for v in p.variables
        ]
    ch.panel("\n".join(lines), title="Preset")


@presets_app.command("resolve")
def presets_resolve(
    ids: list[str] = typer.Argument(None, help="Preset ids to combine (none = the tool's default)"),
    tool: str = typer.Option(..., "--tool", "-t", help=_TOOL_HELP),
    task: str = typer.Option("", "--task", help="Your request (the subject or the text task)"),
    sets: list[str] = typer.Option(None, "--var", help="Fill a variable: key=value (repeatable)"),
    overrides: list[str] = typer.Option(None, "--set", help="Override a setting: key=value (repeatable)"),
    provider: str = typer.Option("", "--provider", help="Image only: show what this provider receives"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show exactly what a combination of presets sends to a tool — nothing is generated."""
    from navig.presets import PresetError
    from navig.presets.resolve import resolve_ids

    try:
        res = resolve_ids(
            list(ids or []), tool=tool, task=task, values=_parse_sets(sets), overrides=_parse_sets(overrides)
        )
    except KeyError as exc:
        _fail(f"No preset {exc.args[0]!r}. See: navig presets list --tool {tool}")
    except PresetError as exc:
        _fail(str(exc))
    data = res.to_dict()
    if provider and tool == "image":
        from navig.presets.adapters import translate_image

        data["provider_request"] = translate_image(provider, res.prompt, res.params, res.negative).to_dict()
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    applied = ", ".join(f"{p['name']} [dim]({p['layer']})[/dim]" for p in res.presets) or "[dim]none[/dim]"
    body = [f"Applied: {applied}", ""]
    if res.system_blocks:
        body += ["[bold]Instructions[/bold]", res.system_text, ""]
    body += ["[bold]Prompt[/bold]", res.prompt or "[dim](empty — pass --task)[/dim]"]
    if res.negative:
        body += ["", f"[bold]Avoid[/bold] {res.negative}"]
    if res.params:
        body += ["", "[bold]Settings[/bold] " + ", ".join(f"{k}={v}" for k, v in res.params.items())]
    if "provider_request" in data:
        body += ["", f"[bold]{provider} receives[/bold] " + json.dumps(
            {k: v for k, v in data["provider_request"].items() if k not in ("prompt", "notes")})]
        body += [f"  [dim]{n}[/dim]" for n in data["provider_request"].get("notes", [])]
    for c in res.context:
        body.append(f"  context {c.ref}: {c.status}" + (f" ({c.chars} chars, {c.modified})" if c.chars else ""))
    ch.panel("\n".join(body), title=f"Resolved for {tool}")
    for w in res.warnings:
        ch.warning(w)


def _read_body_file(path: Path) -> dict:
    import yaml

    from navig.platform.paths import resolve_user_path

    p = resolve_user_path(path)
    if not p.is_file():
        _fail(f"No such file: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _fail(f"{p} is not valid YAML/JSON: {exc}")
    if not isinstance(raw, dict):
        _fail(f"{p} must contain one preset mapping")
    return raw


@presets_app.command("new")
def presets_new(
    name: str = typer.Option("", "--name", "-n", help="Preset name"),
    tool: str = typer.Option("", "--tool", "-t", help=_TOOL_HELP),
    layer: str = typer.Option("", "--layer", help="context · style · format · config"),
    instructions: str = typer.Option("", "--instructions", "-i", help="Reusable guidance text"),
    overrides: list[str] = typer.Option(None, "--set", help="A typed setting: key=value (repeatable)"),
    from_file: Path = typer.Option(None, "--from", help="Create from a YAML/JSON preset file"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Create a preset (from flags or a file)."""
    from navig.presets import PresetError

    data = _read_body_file(from_file) if from_file else {}
    if data.get("id") and _catalog().get_preset(str(data["id"])) is not None:
        _fail(f"{data['id']!r} already exists — use `navig presets edit {data['id']} --from {from_file}`")
    for key, val in (("name", name), ("tool", tool), ("layer", layer), ("instructions", instructions)):
        if val:
            data[key] = val
    if overrides:
        data["params"] = {**(data.get("params") or {}), **_parse_sets(overrides)}
    try:
        p = _catalog().save_preset(data)
    except PresetError as exc:
        _fail(str(exc))
    if as_json:
        print(json.dumps(p.to_dict(), indent=2, ensure_ascii=False))
        return
    ch.success(f"Created {p.name} ({p.id}). Use it: navig presets resolve {p.id} --tool {p.tool} --task \"…\"")


@presets_app.command("edit")
def presets_edit(
    pid: str = typer.Argument(..., help="Preset id"),
    name: str = typer.Option("", "--name", "-n"),
    instructions: str = typer.Option(None, "--instructions", "-i"),
    description: str = typer.Option(None, "--description", "-d"),
    overrides: list[str] = typer.Option(None, "--set", help="Change a setting: key=value (repeatable)"),
    from_file: Path = typer.Option(None, "--from", help="Replace with the contents of a preset file"),
) -> None:
    """Edit a preset. Editing a builtin saves your own override (undo with `reset`)."""
    from navig.presets import PresetError

    p = _get_or_fail(pid)
    data = _read_body_file(from_file) if from_file else p.to_portable()
    data["id"] = p.id
    if name:
        data["name"] = name
    if instructions is not None:
        data["instructions"] = instructions
    if description is not None:
        data["description"] = description
    if overrides:
        data["params"] = {**(data.get("params") or {}), **_parse_sets(overrides)}
    try:
        saved = _catalog().save_preset(data, source=p.source if p.editable else "user")
    except PresetError as exc:
        _fail(str(exc))
    note = " (your override of the builtin — `navig presets reset` restores it)" if saved.overrides else ""
    ch.success(f"Saved {saved.name} v{saved.version}{note}.")


@presets_app.command("duplicate")
def presets_duplicate(
    pid: str = typer.Argument(..., help="Preset id to copy"),
    name: str = typer.Option("", "--name", "-n", help="Name of the copy"),
) -> None:
    """Copy a preset into a new one you own."""
    _get_or_fail(pid)
    p = _catalog().duplicate_preset(pid, name)
    ch.success(f"Created {p.name} ({p.id}). Next: navig presets edit {p.id}")


@presets_app.command("rename")
def presets_rename(pid: str = typer.Argument(...), name: str = typer.Argument(...)) -> None:
    """Rename a preset (the id stays stable)."""
    from navig.presets import PresetError

    _get_or_fail(pid)
    try:
        p = _catalog().rename_preset(pid, name)
    except PresetError as exc:
        _fail(str(exc))
    ch.success(f"Renamed {p.id} → {p.name}.")


@presets_app.command("rm")
def presets_rm(
    pid: str = typer.Argument(..., help="Preset id"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask"),
) -> None:
    """Delete a preset you created (builtins can be reset, not deleted)."""
    from navig.presets import PresetError

    p = _get_or_fail(pid)
    if not yes and sys.stdin.isatty() and not typer.confirm(f"Delete {p.name} ({p.id})?"):
        raise typer.Exit(1)
    try:
        done = _catalog().delete_preset(pid)
    except PresetError as exc:
        _fail(str(exc))
    if not done:
        _fail(f"{pid!r} was not deleted")
    ch.success(f"Deleted {p.name}.")


@presets_app.command("reset")
def presets_reset(pid: str = typer.Argument(..., help="Builtin/plugin preset id")) -> None:
    """Discard your edits to a builtin preset."""
    from navig.presets import PresetError

    try:
        removed = _catalog().reset_preset(pid)
    except PresetError as exc:
        _fail(str(exc))
    if removed:
        ch.success(f"{pid} is back to its built-in version.")
    else:
        ch.info(f"{pid} had no edits to discard.")


@presets_app.command("fav")
def presets_fav(pid: str = typer.Argument(...), off: bool = typer.Option(False, "--off")) -> None:
    """Star (or --off unstar) a preset."""
    _get_or_fail(pid)
    p = _catalog().set_favorite(pid, not off)
    ch.success(f"{p.name}: {'★ favorite' if p.favorite else 'not a favorite'}.")


@presets_app.command("default")
def presets_default(
    tool: str = typer.Argument(..., help=_TOOL_HELP),
    pid: str = typer.Argument("", help="Preset id (omit to show; --clear to remove)"),
    clear: bool = typer.Option(False, "--clear"),
) -> None:
    """Show or set the preset a tool starts with."""
    from navig.presets import PresetError

    cat = _catalog()
    try:
        if clear:
            cat.set_default(tool, None)
            ch.success(f"{tool} has no default preset now.")
            return
        if not pid:
            cur = cat.get_default(tool)
            ch.info(f"{tool} default: {cur or 'none'}")
            return
        cat.set_default(tool, pid)
    except KeyError:
        _fail(f"No preset {pid!r}.")
    except PresetError as exc:
        _fail(str(exc))
    ch.success(f"{tool} now starts with {pid}.")


@presets_app.command("export")
def presets_export(
    pid: str = typer.Argument(..., help="Preset id"),
    out: Path = typer.Option(None, "--out", "-o", help="Write to this file (default: print)"),
) -> None:
    """Export a preset to share. Secrets and local paths are removed; files are not included."""
    from navig.platform.paths import resolve_user_path

    _get_or_fail(pid)
    doc, warnings = _catalog().export_preset(pid)
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if out is None:
        print(text)
    else:
        dest = resolve_user_path(out)
        dest.write_text(text + "\n", encoding="utf-8")
        ch.success(f"Exported to {dest}")
    for w in warnings:
        ch.warning(w)


@presets_app.command("import")
def presets_import(
    path: Path = typer.Argument(..., help="An exported .json preset"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an imported preset with the same id"),
) -> None:
    """Import a shared preset. It stays marked unreviewed until you run `navig presets trust`."""
    from navig.platform.paths import resolve_user_path
    from navig.presets import PresetError

    src = resolve_user_path(path)
    if not src.is_file():
        _fail(f"No such file: {src}")
    if src.stat().st_size > 256_000:
        _fail(f"{src} is too large to be a preset (limit 256 KB)")
    try:
        p = _catalog().import_preset(src.read_text(encoding="utf-8"), overwrite=overwrite)
    except PresetError as exc:
        _fail(str(exc))
    ch.success(f"Imported {p.name} ({p.id}) as unreviewed. Review it: navig presets show {p.id}")


@presets_app.command("trust")
def presets_trust(pid: str = typer.Argument(..., help="An imported preset you have reviewed")) -> None:
    """Mark an imported preset as reviewed, so its instructions apply unquoted."""
    _get_or_fail(pid)
    p = _catalog().trust_preset(pid)
    ch.success(f"{p.name} is now trusted.")
