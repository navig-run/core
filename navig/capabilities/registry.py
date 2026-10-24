"""Installed commands and plugins, read once and cached by install fingerprint.

The command manifest (``navig.registry.manifest.build_public_manifest``) walks the whole
Typer tree, every entry-point plugin included — about 2,400 commands and several seconds
of imports. That is far too slow for an MCP ``initialize``, and the dev-checkout copy
(``core/generated/commands.json``) is not part of the wheel. So the manifest is built at
most once per *install state* and cached under the config dir; the cache key is the NAVIG
version plus every distribution that contributes ``navig.commands``. Installing, upgrading
or removing a plugin changes the key, so the cache can never describe a different install.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Bumped when the cached row shape changes.
_CACHE_SCHEMA = 1
_CACHE_NAME = "capabilities-commands.json"
_LOCK = threading.Lock()
_MEMO: dict[str, Any] = {}

#: Exact command path grammar: "navig" followed by lowercase words. Nothing a caller
#: supplies is ever executed unless it equals one of the installed paths.
PATH_RE = re.compile(r"^navig(?: [a-z0-9][a-z0-9-]*)+$")


# ---------------------------------------------------------------------------
# Install fingerprint
# ---------------------------------------------------------------------------


#: Entry-point groups through which a distribution plugs into NAVIG. A plugin may use
#: either: navig-social declares both, navig-generate only ``navig.plugins``.
_PLUGIN_GROUPS = ("navig.commands", "navig.plugins")


def _command_distributions() -> dict[str, dict[str, Any]]:
    """dist name -> {version, summary, verbs} for every installed NAVIG plugin distribution."""
    out: dict[str, dict[str, Any]] = {}
    for group in _PLUGIN_GROUPS:
        try:
            eps = metadata.entry_points(group=group)
        except Exception as exc:  # noqa: BLE001 — discovery must never break a caller
            logger.debug("%s discovery failed: %s", group, exc)
            continue
        for ep in eps:
            dist = getattr(ep, "dist", None)
            name = getattr(dist, "name", None) or "unknown"
            if name == "navig":
                continue  # core is not a plugin
            row = out.setdefault(
                name,
                {
                    "version": getattr(dist, "version", "") or "",
                    "summary": _summary(dist),
                    "verbs": [],
                },
            )
            # Only command entry points are CLI verbs; a plugins-group name is an id.
            if group == "navig.commands" and ep.name not in row["verbs"]:
                row["verbs"].append(ep.name)
    for row in out.values():
        row["verbs"].sort()
    return out


def _summary(dist: Any) -> str:
    try:
        return str((dist.metadata or {}).get("Summary") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _navig_version() -> str:
    try:
        from navig import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return "0"


def fingerprint() -> str:
    """Stable hash of the install: NAVIG version + every plugin distribution version."""
    parts = [f"navig={_navig_version()}"]
    parts += sorted(f"{n}={r['version']}" for n, r in _command_distributions().items())
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Manifest cache
# ---------------------------------------------------------------------------


def _cache_path() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "cache" / _CACHE_NAME


def _owner_map() -> dict[str, str]:
    """top-level import package -> distribution name (``navig_social`` -> ``navig-social``)."""
    try:
        mapping = metadata.packages_distributions()
    except Exception:  # noqa: BLE001
        return {}
    return {pkg: dists[0] for pkg, dists in mapping.items() if dists}


def _compact_row(cmd: dict[str, Any], owners: dict[str, str]) -> dict[str, Any] | None:
    path = str(cmd.get("path") or "").strip()
    if not PATH_RE.match(path):
        return None
    module = str(cmd.get("module") or "")
    package = module.split(".", 1)[0]
    plugin = "core" if package in ("navig", "") else owners.get(package, package.replace("_", "-"))
    options = []
    takes_json = False
    for opt in cmd.get("options") or []:
        flags = [str(f) for f in opt.get("flags") or []]
        if "--json" in flags:
            takes_json = True
        options.append({"flags": flags, "takes_value": bool(opt.get("takes_value"))})
    return {
        "path": path,
        "summary": " ".join(str(cmd.get("summary") or "").split()),
        "plugin": plugin,
        "arguments": [
            {"name": str(a.get("name")), "required": bool(a.get("required"))}
            for a in cmd.get("arguments") or []
        ],
        "options": options,
        "json": takes_json,
    }


def _build_rows() -> list[dict[str, Any]]:
    from navig.registry.manifest import build_public_manifest

    manifest = build_public_manifest(validate=False)
    owners = _owner_map()
    rows = [r for c in manifest.get("commands", []) if (r := _compact_row(c, owners))]
    rows.sort(key=lambda r: r["path"])
    return rows


def _read_cache(key: str) -> list[dict[str, Any]] | None:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("schema") != _CACHE_SCHEMA or data.get("fingerprint") != key:
        return None
    rows = data.get("commands")
    return rows if isinstance(rows, list) else None


def _write_cache(key: str, rows: list[dict[str, Any]]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"schema": _CACHE_SCHEMA, "fingerprint": key, "commands": rows}),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:  # a read-only home still works, just without the cache
        logger.debug("capabilities cache not written: %s", exc)


def commands(*, refresh: bool = False) -> list[dict[str, Any]]:
    """Every installed command as a compact row (path, summary, plugin, args, options)."""
    key = fingerprint()
    with _LOCK:
        if not refresh and _MEMO.get("key") == key:
            return _MEMO["rows"]
        rows = None if refresh else _read_cache(key)
        if rows is None:
            rows = _build_rows()
            _write_cache(key, rows)
        _MEMO.update(key=key, rows=rows, index={r["path"]: r for r in rows})
        return rows


def get(path: str) -> dict[str, Any] | None:
    """The installed command whose path equals *path* exactly, else None."""
    path = " ".join(str(path or "").split())
    if not PATH_RE.match(path):
        return None
    commands()
    return _MEMO.get("index", {}).get(path)


# ---------------------------------------------------------------------------
# Plugins and search
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _probe_names() -> frozenset[str]:
    try:
        return frozenset(ep.name for ep in metadata.entry_points(group="navig.capabilities"))
    except Exception:  # noqa: BLE001
        return frozenset()


def installed_plugins(*, with_counts: bool = True) -> list[dict[str, Any]]:
    """One row per installed plugin distribution: verbs, pitch, command count, probe.

    ``with_counts=False`` skips the command manifest entirely (metadata only, a few ms),
    which is what the ``initialize`` digest uses: a cold manifest build takes seconds.
    """
    counts: dict[str, int] = {}
    if with_counts:
        for row in commands():
            counts[row["plugin"]] = counts.get(row["plugin"], 0) + 1
    probes = _probe_names()
    out = []
    for name, row in sorted(_command_distributions().items()):
        short = name.removeprefix("navig-")
        out.append(
            {
                "plugin": name,
                "version": row["version"],
                "verbs": row["verbs"],
                "pitch": row["summary"],
                "commands": counts.get(name, 0) if with_counts else None,
                "probe": short if short in probes else None,
            }
        )
    return out


_WORD = re.compile(r"[a-z0-9]+")
#: Everyday words an agent uses for an operator goal, mapped onto NAVIG's own verbs.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "post": ("publish", "fan-out", "upload", "card"),
    "publish": ("post", "fan-out", "upload"),
    "share": ("post", "publish", "fan-out"),
    "picture": ("image",),
    "photo": ("image",),
    "img": ("image",),
    "clip": ("video",),
    "movie": ("video",),
    "sound": ("audio",),
    "music": ("audio", "score", "beat"),
    "voice": ("audio", "speak", "dub"),
    "mail": ("email",),
    "phone": ("mobile", "ios", "android", "device"),
    "iphone": ("ios", "mobile", "device"),
    "background": ("rembg",),
    "make": ("gen", "generate", "render"),
    "create": ("gen", "generate", "render"),
}


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def find(query: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """Commands that best match an operator goal, best first (simple token scoring)."""
    words = _tokens(query)
    if not words:
        return []
    wanted: dict[str, float] = {}
    for word in words:
        wanted[word] = max(wanted.get(word, 0.0), 1.0)
        for alt in _SYNONYMS.get(word, ()):
            for tok in _tokens(alt):
                wanted[tok] = max(wanted.get(tok, 0.0), 0.6)
    scored = []
    for row in commands():
        path_tokens = set(_tokens(row["path"])) - {"navig"}
        summary_tokens = set(_tokens(row["summary"]))
        score = 0.0
        for tok, weight in wanted.items():
            if tok in path_tokens:
                score += 2.0 * weight
            elif tok in summary_tokens:
                score += 1.0 * weight
        if score:
            # Prefer the specific leaf over a group, and plugins that can say --json.
            score += 0.1 * len(path_tokens) + (0.2 if row["json"] else 0.0)
            scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], item[1]["path"]))
    return [_public(row) for _score, row in scored[: max(1, min(int(limit), 25))]]


def _public(row: dict[str, Any]) -> dict[str, Any]:
    from navig.capabilities.safety import classify

    return {
        "path": row["path"],
        "summary": row["summary"],
        "plugin": row["plugin"],
        "safety": classify(row["path"]),
        "json": row["json"],
    }


def command_help(path: str) -> dict[str, Any] | None:
    """Full usage for one installed command: arguments, options, safety."""
    row = get(path)
    if row is None:
        return None
    from navig.capabilities.safety import classify

    usage = " ".join(
        [row["path"]]
        + [f"<{a['name']}>" if a["required"] else f"[{a['name']}]" for a in row["arguments"]]
    )
    return {
        **row,
        "usage": usage,
        "safety": classify(row["path"]),
        "run_with": "navig_run action=run path=<path> args=[...]",
    }
