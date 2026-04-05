from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from navig import __version__
from navig.registry.meta import CommandMeta, get_meta_for_callback


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().splitlines()[0].strip()


def _iter_typer_commands(typer_app: Any, prefix: list[str], group_hidden: bool = False):
    for cmd_info in getattr(typer_app, "registered_commands", []):
        callback = getattr(cmd_info, "callback", None)
        if callback is None:
            continue

        name = cmd_info.name or callback.__name__.replace("_", "-")
        hidden = bool(group_hidden or getattr(cmd_info, "hidden", False))
        help_text = _first_line(getattr(cmd_info, "help", None)) or _first_line(
            getattr(callback, "__doc__", None)
        )
        path_parts = [*prefix, name]
        yield {
            "path_parts": path_parts,
            "path": " ".join(path_parts),
            "callback": callback,
            "help": help_text,
            "hidden": hidden,
        }

    for group_info in getattr(typer_app, "registered_groups", []):
        group_name = getattr(group_info, "name", None)
        group_typer = getattr(group_info, "typer_instance", None)
        if not group_name or group_typer is None:
            continue

        nested_hidden = bool(group_hidden or getattr(group_info, "hidden", False))
        yield from _iter_typer_commands(group_typer, [*prefix, group_name], nested_hidden)


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
    status = meta.status if meta is not None else ("hidden" if item.get("hidden") else "stable")
    since = meta.since if meta is not None else __version__
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


def _build_manifest(include_hidden: bool) -> dict[str, Any]:
    import navig.cli as cli_mod

    cli_mod._register_external_commands(register_all=True)
    app = cli_mod.app

    by_path: dict[str, dict[str, Any]] = {}
    for cmd in _iter_typer_commands(app, ["navig"]):
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
    }


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


def deprecations_report(manifest: dict[str, Any]) -> dict[str, Any]:
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
