from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from navig.registry.meta import CommandMeta, get_meta_for_callback


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().splitlines()[0].strip()


def _click_params(callback: Any) -> list[Any] | None:
    """The command's parameters as Typer itself builds them for the real CLI, or ``None``.

    The manifest used to read the handler's raw signature, which is wrong three ways at once:
    under ``from __future__ import annotations`` every annotation is a STRING, so no flag ever
    looked boolean (730 options recorded as taking a value); a parameter declared
    ``Annotated[bool, typer.Option(...)] = False`` has no ``OptionInfo`` default, so it was
    skipped outright (863 options missing — whole plugins' worth); and a bare ``name: str`` /
    ``dry_run: bool`` was skipped too although the CLI accepts it. Typer already resolves all
    of that to click parameters, so ask it: register the callback on a throwaway one-command
    app (no completion options) and read the click command it builds. ``None`` when Typer
    cannot build it, and the caller falls back to the signature.
    """
    import inspect

    if inspect.isbuiltin(callback) or not callable(callback):
        return None  # a C callable has no Python signature for Typer to read
    try:
        import typer
        from typer.main import get_command

        app = typer.Typer(add_completion=False)
        app.command()(callback)
        return list(get_command(app).params)
    except Exception:  # noqa: BLE001 — a command Typer cannot build still gets a manifest row
        return None


def _flags_of(option: Any) -> list[str]:
    """``["--play/--no-play", "-p"]`` — the shape the manifest has always used for a switch."""
    opts = list(getattr(option, "opts", []) or [])
    secondary = list(getattr(option, "secondary_opts", []) or [])
    if opts and secondary:
        return [f"{opts[0]}/{secondary[0]}", *opts[1:], *secondary[1:]]
    return opts + secondary


def _extract_arguments(callback: Any) -> list[dict[str, Any]]:
    """Positional CLI arguments of a Typer command, in declaration order.

    Read off the handler's own signature: a parameter whose default is a Typer
    ``ArgumentInfo`` is a positional argument (``typer.Option`` params are flags and a
    ``typer.Context`` param carries no default of either kind, so both are skipped).
    Used to render a real usage hint — ``navig db query <query>`` — instead of a bare
    ``navig db query`` on every command surface (help, the deck, navig.run/commands).

    Returns ``[{name, required, variadic}]``. ``required`` is true when the argument has
    no default (Typer stores ``...``); ``variadic`` is true for a ``list[...]`` argument
    (Typer's ``nargs=-1``). Never raises — a signature it can't read yields ``[]``.
    """
    import inspect
    import typing

    built = _click_params(callback)
    if built is not None:
        return [
            {
                "name": str(p.name).replace("_", "-"),
                "required": bool(p.required),
                "variadic": p.nargs == -1,
            }
            for p in built
            if type(p).__name__ in ("Argument", "TyperArgument") and not getattr(p, "hidden", False)
        ]

    try:
        params = inspect.signature(callback).parameters
    except (TypeError, ValueError):  # builtins / C callables have no signature
        return []

    args: list[dict[str, Any]] = []
    for name, param in params.items():
        default = param.default
        # Typer's ArgumentInfo is the only thing that marks a positional argument.
        if type(default).__name__ != "ArgumentInfo":
            continue
        required = getattr(default, "default", ...) is ...
        variadic = typing.get_origin(param.annotation) is list
        args.append(
            {"name": name.replace("_", "-"), "required": required, "variadic": variadic}
        )
    return args


def _extract_options(callback: Any) -> list[dict[str, Any]]:
    """Option flags of a Typer command, in declaration order.

    The counterpart to :func:`_extract_arguments`: a parameter whose default is a Typer
    ``OptionInfo`` is a ``--flag``. Completes the command schema so a consumer (an agent
    driving navig, the deck, `navig help`) sees the full interface — that ``navig db
    query`` accepts ``--json`` — not just its positional ``<query>``.

    Returns ``[{flags, takes_value}]`` where ``flags`` is every declared spelling
    (``["--json"]``, ``["--container", "-c"]``) and ``takes_value`` is false for a
    boolean switch. Falls back to ``--<name>`` when Typer records no explicit flag.
    Never raises — a signature it can't read yields ``[]``.
    """
    import inspect

    built = _click_params(callback)
    if built is not None:
        return [
            {
                "flags": _flags_of(p),
                # A switch (is_flag) or a counter (-vvv) is given without a value.
                "takes_value": not (getattr(p, "is_flag", False) or getattr(p, "count", False)),
            }
            for p in built
            if type(p).__name__ in ("Option", "TyperOption")
            and not getattr(p, "hidden", False)
            and _flags_of(p)
        ]

    try:
        params = inspect.signature(callback).parameters
    except (TypeError, ValueError):  # builtins / C callables have no signature
        return []

    opts: list[dict[str, Any]] = []
    for name, param in params.items():
        default = param.default
        if type(default).__name__ != "OptionInfo":
            continue
        declared = getattr(default, "param_decls", None) or ()
        flags = [d for d in declared if isinstance(d, str) and d.startswith("-")]
        if not flags:
            flags = ["--" + name.replace("_", "-")]
        # A bool param (or an explicit is_flag) is a switch that takes no value.
        takes_value = param.annotation is not bool and getattr(default, "is_flag", None) is not True
        opts.append({"flags": flags, "takes_value": bool(takes_value)})
    return opts


def _runs_bare(typer_app: Any) -> bool:
    """True when the group executes its callback with no subcommand.

    Typer keeps unset settings as ``DefaultPlaceholder`` objects (truthy), so only a
    literal ``True`` — on the ``Typer(...)`` info or the ``@app.callback(...)`` — counts.
    """
    for holder in (getattr(typer_app, "info", None), getattr(typer_app, "registered_callback", None)):
        if getattr(holder, "invoke_without_command", None) is True:
            return True
    return False


def _iter_typer_commands(
    typer_app: Any,
    prefix: list[str],
    *,
    top_hidden: bool = False,
    nested_hidden: bool = False,
    depth: int = 0,
):
    """Walk the Typer tree, keeping the TWO reasons a command can be hidden apart.

    ``top_hidden``    — the top-level ``navig <group>`` is hidden from ``--help``. That
                        says nothing about whether the command is public: an alias is
                        hidden *and* private, while an unlisted-but-real group (``navig
                        cost``) is hidden *and* public. :func:`_public_status` decides.
    ``nested_hidden`` — this command, or a sub-group above it, is itself hidden. That is
                        always genuinely internal, whatever the top-level group is.

    Collapsing the two is what deleted `navig ai` / `brain` / `cost` from the manifest.
    """
    for cmd_info in getattr(typer_app, "registered_commands", []):
        callback = getattr(cmd_info, "callback", None)
        if callback is None:
            continue

        name = cmd_info.name or callback.__name__.replace("_", "-")
        own_hidden = bool(getattr(cmd_info, "hidden", False))
        help_text = _first_line(getattr(cmd_info, "help", None)) or _first_line(
            getattr(callback, "__doc__", None)
        )
        path_parts = [*prefix, name]
        yield {
            "path_parts": path_parts,
            "path": " ".join(path_parts),
            "callback": callback,
            "help": help_text,
            "top_hidden": top_hidden,
            "nested_hidden": bool(nested_hidden or own_hidden),
        }

    for group_info in getattr(typer_app, "registered_groups", []):
        group_name = getattr(group_info, "name", None)
        group_typer = getattr(group_info, "typer_instance", None)
        if not group_name or group_typer is None:
            continue

        this_hidden = bool(getattr(group_info, "hidden", False))
        if depth == 0:
            # A top-level group that RUNS bare (`navig doctor`, `navig cost`, …) is a verb:
            # its callback is a real command. The recursion below yields only the group's
            # SUBCOMMANDS, which is how `navig doctor` stayed runnable for years while
            # missing from commands.md — `help --schema` and the website's /commands page
            # had no page for the most-used verb.
            cb_info = getattr(group_typer, "registered_callback", None)
            cb = getattr(cb_info, "callback", None) or (
                cb_info if callable(cb_info) else None
            )
            # `__navig_platform_stub__` marks a callback registered ONLY to refuse on a
            # platform the feature does not support (`navig desktop` off Windows). Such a
            # group is not a command of the product: the shipped manifest is generated on
            # Windows, where no callback exists, so emitting this row made every Linux
            # checkout call `generated/commands.json` stale (2026-10-30, outpost).
            if cb is not None and not getattr(cb, "__navig_platform_stub__", False):
                # typer stores `.help` as a DefaultPlaceholder when unset — never hand
                # that object to _first_line (no .splitlines); a string only.
                cb_help = getattr(cb_info, "help", None)
                grp_help = getattr(group_info, "help", None)
                help_text = (
                    (_first_line(cb_help) if isinstance(cb_help, str) else None)
                    or _first_line(getattr(cb, "__doc__", None))
                    or (_first_line(grp_help) if isinstance(grp_help, str) else None)
                )
                yield {
                    "path_parts": [*prefix, group_name],
                    "path": " ".join([*prefix, group_name]),
                    "callback": cb,
                    "help": help_text,
                    "top_hidden": this_hidden,
                    "nested_hidden": False,
                }
            # A top-level `navig <group>`.
            yield from _iter_typer_commands(
                group_typer,
                [*prefix, group_name],
                top_hidden=this_hidden,
                nested_hidden=False,
                depth=1,
            )
        else:
            # A sub-group that RUNS bare (`invoke_without_command=True`: `navig repo lock`,
            # `navig wiki inbox`, …) is a command too — the same rule as the top level, but
            # only for groups that say so: a nested callback that merely holds options is
            # not a verb. Measured 2026-10-25: 33 such commands ran but had no manifest row,
            # no completion and no commands.md page.
            cb_info = getattr(group_typer, "registered_callback", None)
            cb = getattr(cb_info, "callback", None)
            if cb is not None and _runs_bare(group_typer):
                cb_help = getattr(cb_info, "help", None)
                grp_help = getattr(group_info, "help", None)
                yield {
                    "path_parts": [*prefix, group_name],
                    "path": " ".join([*prefix, group_name]),
                    "callback": cb,
                    "help": (
                        (_first_line(cb_help) if isinstance(cb_help, str) else None)
                        or _first_line(getattr(cb, "__doc__", None))
                        or (_first_line(grp_help) if isinstance(grp_help, str) else None)
                    ),
                    "top_hidden": top_hidden,
                    "nested_hidden": bool(nested_hidden or this_hidden),
                }
            # A sub-group. Hidden here means internal, never merely "unlisted".
            yield from _iter_typer_commands(
                group_typer,
                [*prefix, group_name],
                top_hidden=top_hidden,
                nested_hidden=bool(nested_hidden or this_hidden),
                depth=depth + 1,
            )


def _public_status(item: dict[str, Any]) -> str:
    """Is this command part of the public API?

    Hidden-from-``--help`` is a readability choice; public-API is a contract. Only an
    ALIAS (a duplicate name whose canonical group is documented) leaves the manifest.
    """
    if item.get("nested_hidden"):
        return "hidden"  # the command / sub-group is itself internal
    if not item.get("top_hidden"):
        return "stable"

    from navig.cli.registration import _UNLISTED_COMMANDS  # noqa: PLC0415 — avoid a cycle

    parts = item.get("path_parts") or []
    group = parts[1] if len(parts) > 1 else ""
    return "stable" if group in _UNLISTED_COMMANDS else "hidden"


def _is_alias_path(item: dict[str, Any]) -> bool:
    """True when this path sits under a top-level ALIAS (or superseded) group.

    An alias registers the SAME callbacks under a second name, so it inherits the
    canonical command's ``CommandMeta`` too — and `meta.status` used to win over the
    hidden flag, which is how `navig database list` / `navig database query` shipped in
    the public manifest as duplicates of `navig db list` / `navig db query`. An alias is
    never public, meta or not: the canonical group already documents every subcommand.
    A superseded group (``_RETIRED_COMMANDS``) is treated the same way: it still answers,
    but its replacement is what the manifest documents.
    """
    from navig.cli.registration import _NOT_PUBLISHED  # noqa: PLC0415 — avoid a cycle

    parts = item.get("path_parts") or []
    return len(parts) > 1 and parts[1] in _NOT_PUBLISHED


def _entry_from_command(item: dict[str, Any]) -> dict[str, Any]:
    callback = item["callback"]
    meta: CommandMeta | None = get_meta_for_callback(callback)

    summary = (
        meta.summary
        if meta is not None
        else _first_line(item.get("help"))
        or _first_line(getattr(callback, "__doc__", None))
        or f"Run {item['path']}"
    )
    if _is_alias_path(item):
        status = "hidden"
    elif meta is not None:
        status = meta.status
    else:
        status = _public_status(item)
    # A command's `since` is authoritative ONLY when its author declared it via
    # @command_meta(since=...). For a meta-less command we do NOT know when it was
    # introduced, and stamping the CURRENT __version__ is actively wrong: it rewrote
    # `since` for every meta-less command on each rebuild, so a version bump churned
    # ~1300 rows in generated/commands.json and left the CI freshness gate permanently
    # red (it could only pass on a manifest regenerated at the exact committed version).
    # "" is honest and, crucially, STABLE across rebuilds. Consumers already tolerate it
    # (validate_manifest requires the field present but not non-empty; the website does
    # `cmd.since || null`; help renders "" as "—").
    since = meta.since if meta is not None else ""
    tags = list(meta.tags) if meta is not None else []
    aliases = list(meta.aliases) if meta is not None else []
    examples = list(meta.examples) if (meta is not None and meta.examples) else [item["path"]]

    entry: dict[str, Any] = {
        "path": item["path"],
        "summary": summary,
        "module": callback.__module__,
        "handler": callback.__name__,
        "status": status,
        "since": since,
        "aliases": aliases,
        "tags": tags,
        "examples": examples,
        "arguments": _extract_arguments(callback),
        "options": _extract_options(callback),
        "_has_explicit_meta": meta is not None,
    }

    if meta is not None and meta.deprecated is not None:
        entry["deprecated"] = {
            "since": meta.deprecated.since,
            "remove_after": meta.deprecated.remove_after,
            "replaced_by": meta.deprecated.replaced_by,
            "note": meta.deprecated.note,
        }

    return entry


def _prefer_new_entry(new: dict[str, Any], current: dict[str, Any]) -> bool:
    if bool(new.get("_has_explicit_meta")) and not bool(current.get("_has_explicit_meta")):
        return True
    if bool(new.get("_has_explicit_meta")) == bool(current.get("_has_explicit_meta")):
        return len(new.get("summary", "")) > len(current.get("summary", ""))
    return False


def _plugin_mount() -> Any | None:
    """A throwaway Typer carrying every BUILTIN PluginManager plugin app, or ``None``.

    ``_register_external_commands`` only walks the CLI app's OWN sub-apps. A plugin
    mounted by ``navig.main.load_plugins_into_app`` never passes through that seam —
    that is how the builtin plugin host reaches the CLI (``navig mini status``), and
    none of those commands reached ``generated/commands.json``: no completion, no
    markdown reference, no deck CLI schema.

    The mount is rebuilt here on a FRESH Typer rather than on the process-global
    ``navig.cli.app``, so building a manifest has no side effect on a CLI run that
    shares the process, and calling it twice cannot register a group twice. It mirrors
    the runtime loop exactly (``add_typer(plugin_app, name=name)``), so every path
    recorded is the path a real invocation parses.

    ``None`` when the plugin host cannot be consulted or loads nothing — the exporter
    then records core commands only, which a caller can still validate.

    Scoped to ``source == "builtin"``: this is a SHIPPED artifact, so it may only document
    commands a navig install actually contains. The host also discovers the operator's own
    ``~/.navig/plugins`` and ``<space>/.navig/plugins`` — mounting those here would bake a
    private, unshipped command into ``generated/commands.json`` on the next regen, which is
    the phantom-command class the freshness gate exists to catch.
    """
    try:
        from navig.plugins import get_plugin_manager  # noqa: PLC0415 — avoid a cycle

        manager = get_plugin_manager()
        discovered = manager.discover_plugins()
        manager.load_all_plugins(silent=True)
        shipped = {info.name for info in discovered.values() if info.source == "builtin"}
        plugin_apps = {
            name: app
            for name, app in manager.get_loaded_apps().items()
            if name in shipped
        }
    except Exception:  # noqa: BLE001 — a broken plugin host must not break the export
        return None
    if not plugin_apps:
        return None

    import typer  # noqa: PLC0415 — heavy-ish; only paid for when plugins exist

    mount = typer.Typer(add_completion=False)
    for name, plugin_app in sorted(plugin_apps.items()):
        try:
            mount.add_typer(plugin_app, name=name)
        except Exception:  # noqa: BLE001 — one bad app must not drop the others
            continue
    return mount


def _build_manifest(include_hidden: bool) -> dict[str, Any]:
    import navig.cli as cli_mod

    cli_mod._register_external_commands(register_all=True)
    roots: list[Any] = [cli_mod.app]
    plugin_mount = _plugin_mount()
    if plugin_mount is not None:
        roots.append(plugin_mount)

    by_path: dict[str, dict[str, Any]] = {}
    for root in roots:
        for cmd in _iter_typer_commands(root, ["navig"]):
            entry = _entry_from_command(cmd)
            status = str(entry.get("status", "stable"))

            if not include_hidden and status in {"hidden", "internal"}:
                continue

            key = entry["path"]
            existing = by_path.get(key)
            if existing is None or _prefer_new_entry(entry, existing):
                by_path[key] = entry

    commands = []
    for entry in by_path.values():
        cleaned = dict(entry)
        cleaned.pop("_has_explicit_meta", None)
        commands.append(cleaned)

    commands.sort(key=lambda c: c.get("path", ""))

    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(commands),
        "commands": commands,
        "groups": _group_help(roots, {c["path"].split()[1] for c in commands if len(c["path"].split()) > 1}),
    }


def _group_help(apps: list[Any], present: set[str]) -> dict[str, str]:
    """``navig <group>`` → the group's OWN one-line help (additive key, schema 1.0.0).

    Every command carries a summary, but a group's description lived only in its
    ``typer.Typer(help=…)`` — so every consumer that lists groups (navig.run/commands)
    fell back to "the first command's summary": `navig blackbox` read "Record and replay
    an operations session" and `navig vault` read "Set a credential as the active…".
    Only groups that contribute public commands are listed; aliases never are.

    Takes the SAME roots ``_build_manifest`` walks — the CLI app plus the plugin mount —
    so a group contributed only by a plugin (``navig mini``) gets its own description
    instead of being silently absent from ``groups``.
    """
    from navig.cli.registration import _NOT_PUBLISHED  # noqa: PLC0415 — avoid a cycle

    out: dict[str, str] = {}
    for app in apps:
        for info in getattr(app, "registered_groups", []):
            name = getattr(info, "name", None)
            if not name or name not in present or name in _NOT_PUBLISHED:
                continue
            typer_instance = getattr(info, "typer_instance", None)
            inner = getattr(getattr(typer_instance, "info", None), "help", None)
            text = _first_line(getattr(info, "help", None)) or _first_line(inner)
            if text and name not in out:
                out[name] = text
    return dict(sorted(out.items()))


def build_public_manifest(validate: bool = False) -> dict[str, Any]:
    manifest = _build_manifest(include_hidden=False)
    if validate:
        validate_manifest(manifest)
    return manifest


def build_full_manifest(validate: bool = False) -> dict[str, Any]:
    manifest = _build_manifest(include_hidden=True)
    if validate:
        validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    required_fields = [
        "path",
        "summary",
        "module",
        "handler",
        "status",
        "since",
        "aliases",
        "tags",
        "examples",
        "arguments",
        "options",
    ]
    valid_status = {
        "stable",
        "beta",
        "experimental",
        "deprecated",
        "hidden",
        "internal",
    }

    errors: list[str] = []
    commands = manifest.get("commands", [])
    if not isinstance(commands, list):
        raise ValueError("manifest.commands must be a list")

    for command in commands:
        path = command.get("path", "<unknown>")
        for field in required_fields:
            if field not in command:
                errors.append(f"{path}: missing required field '{field}'")

        status = str(command.get("status", ""))
        if status not in valid_status:
            errors.append(f"{path}: invalid status '{status}'")

        summary = str(command.get("summary", ""))
        if not summary:
            errors.append(f"{path}: summary must not be empty")
        elif len(summary) > 100:
            errors.append(f"{path}: summary exceeds 100 chars")

        examples = command.get("examples", [])
        if not isinstance(examples, list) or not examples:
            errors.append(f"{path}: examples must be a non-empty list")

        arguments = command.get("arguments")
        if not isinstance(arguments, list):
            errors.append(f"{path}: arguments must be a list")
        else:
            for arg in arguments:
                if not isinstance(arg, dict) or "name" not in arg or "required" not in arg:
                    errors.append(f"{path}: each argument needs 'name' and 'required'")
                    break

        options = command.get("options")
        if not isinstance(options, list):
            errors.append(f"{path}: options must be a list")
        else:
            for opt in options:
                if not isinstance(opt, dict) or not opt.get("flags"):
                    errors.append(f"{path}: each option needs a non-empty 'flags' list")
                    break

        if status == "deprecated":
            deprecated = command.get("deprecated")
            if not isinstance(deprecated, dict):
                errors.append(f"{path}: deprecated command must include deprecated block")
            else:
                for key in ("since", "remove_after", "replaced_by", "note"):
                    if key not in deprecated or not str(deprecated.get(key, "")).strip():
                        errors.append(f"{path}: deprecated.{key} is required")

    if errors:
        raise ValueError("\n".join(errors))


def deprecations_report(
    manifest: dict[str, Any], *, include_superseded: bool = True
) -> dict[str, Any]:
    """Deprecated commands: those the manifest marks ``deprecated`` (``@command_meta``)
    plus, by default, the superseded-verb table in :mod:`navig.cli.superseded`.

    The table matters because a superseded verb is hidden and therefore never IN the
    public manifest — without it the report could not name a single one of them. A
    manifest row wins over a table row for the same path (it carries more detail).
    """
    rows = []
    for command in manifest.get("commands", []):
        if command.get("status") != "deprecated":
            continue
        dep = command.get("deprecated", {})
        rows.append(
            {
                "path": command.get("path"),
                "since": dep.get("since"),
                "remove_after": dep.get("remove_after"),
                "replaced_by": dep.get("replaced_by"),
                "note": dep.get("note", ""),
            }
        )

    if include_superseded:
        from navig.cli.superseded import report_rows  # noqa: PLC0415 — avoid a cycle

        seen = {str(row.get("path")) for row in rows}
        rows.extend(row for row in report_rows() if row["path"] not in seen)

    rows.sort(key=lambda row: str(row.get("path", "")))
    return {
        "generated_at": manifest.get("generated_at"),
        "total_deprecated": len(rows),
        "deprecated_commands": rows,
    }


def render_markdown(manifest: dict[str, Any]) -> str:
    lines: list[str] = [
        "# NAVIG Command Reference",
        "",
        f"_Generated {manifest.get('generated_at', '')}_",
        "",
    ]

    for command in manifest.get("commands", []):
        path = command.get("path", "")
        status = command.get("status", "")
        since = command.get("since", "")
        summary = command.get("summary", "")

        lines.extend(
            [
                f"## `{path}`",
                f"**Status:** `{status}` · **Since:** {since}",
                summary,
            ]
        )

        deprecated = command.get("deprecated")
        if isinstance(deprecated, dict):
            lines.append(
                "> ⚠️ Deprecated since "
                f"`{deprecated.get('since', '')}`. Use "
                f"`{deprecated.get('replaced_by', '')}` instead. Removed after "
                f"`{deprecated.get('remove_after', '')}`."
            )

        examples = command.get("examples", []) or []
        if examples:
            lines.append("**Examples:**")
            for example in examples:
                lines.append("```sh")
                lines.append(str(example))
                lines.append("```")

        lines.append("")

    return "\n".join(lines)


def topic_index_from_manifest(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    topics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for command in manifest.get("commands", []):
        path = str(command.get("path", "")).strip()
        parts = path.split()
        if len(parts) < 2 or parts[0] != "navig":
            continue
        topic = parts[1]
        topics[topic].append(command)

    for _topic, rows in topics.items():
        rows.sort(key=lambda row: str(row.get("path", "")))

    return dict(sorted(topics.items()))
