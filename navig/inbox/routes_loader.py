"""
navig.inbox.routes_loader — Load and validate per-space routes.yaml config.

Schema supported:
  spaces_root: <path>          # optional: parent dir to scan for sibling spaces
  channels:                    # existing channel routing (unchanged)
    - id, name, agents, priority, sla?, keywords?, description?
  exclude:                     # list of rules — content matching these should leave this space
    - keywords: [...]           # word list; case-insensitive; partial match
      min_hits: 1               # how many keywords must match (default: 1)
      action: find_best_space   # only supported action for now
      dry_run: true             # true = log only; false = execute migration
      on_conflict: rename       # rename | skip | overwrite
      on_error: log_and_skip
  defaults:
    sla_hours: 48
    unrouted_fallback: <agent>

No required keys — every field is optional and has a safe default.
Never raises on missing or malformed YAML — logs a warning and returns None.

Legacy shapes are accepted and normalised onto the schema above. They were written by
space scaffolding that predates this loader, and on a real install they are the MAJORITY
of routes.yaml files — read literally, 13 of 16 loaded as an empty config, so every one
of those spaces routed nothing while appearing configured. Accepted aliases:

  routes:                      ->  channels:
    - channel: "#lyrics"       ->      id:
      owner:    lyricist       ->      agents:   (also `handler:`, `handlers:`,
      handlers: [lyricist]                        `multi_agent:`)
      triggers: [{keyword: [...]}]  ->  keywords:  (also `tags:`, and `pattern:`
      pattern:  "a|b|c"                            split on `|`)
      default:  true           ->  defaults.unrouted_fallback (that channel's first agent)
  fallback:
    channel: "#ideas"          ->  defaults.unrouted_fallback

`formation:` is deliberately NOT mapped to `agents:` — a formation is a group, not a
handler, and conflating them would dispatch to a name no agent answers to.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.inbox.routes_loader")


# ── Typed dataclasses ─────────────────────────────────────────


@dataclass
class ChannelConfig:
    id: str
    name: str
    agents: list[str] = field(default_factory=list)
    priority: str = "normal"
    sla: str | None = None
    keywords: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class ExcludeRule:
    keywords: list[str] = field(default_factory=list)
    min_hits: int = 1
    action: str = "find_best_space"
    dry_run: bool = True
    on_conflict: str = "rename"
    on_error: str = "log_and_skip"


@dataclass
class RoutesDefaults:
    sla_hours: int = 48
    unrouted_fallback: str = ""


@dataclass
class RoutesConfig:
    channels: list[ChannelConfig] = field(default_factory=list)
    exclude: list[ExcludeRule] = field(default_factory=list)
    defaults: RoutesDefaults = field(default_factory=RoutesDefaults)
    spaces_root: Path | None = None
    source_path: Path | None = None  # the routes.yaml file this was loaded from


# ── Loader ────────────────────────────────────────────────────


def load(space_root: Path) -> RoutesConfig | None:
    """
    Load `<space_root>/.navig/inbox/routes.yaml`.

    Returns a RoutesConfig on success.
    Returns None (with a logged warning) on missing file or any parse error.
    Never raises.
    """
    routes_path = space_root / ".navig" / "inbox" / "routes.yaml"
    if not routes_path.exists():
        logger.debug("No routes.yaml at %s — skipping", routes_path)
        return None

    try:
        import yaml  # soft dep — only needed at runtime  # noqa: PLC0415
    except ImportError:
        logger.warning("PyYAML not installed — cannot load routes.yaml at %s", routes_path)
        return None

    try:
        raw: Any = yaml.safe_load(routes_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse routes.yaml at %s: %s", routes_path, exc)
        return None

    if not isinstance(raw, dict):
        logger.warning("routes.yaml at %s is not a mapping — skipped", routes_path)
        return None

    config = RoutesConfig(source_path=routes_path)

    # spaces_root
    sr = raw.get("spaces_root")
    if sr:
        config.spaces_root = Path(str(sr))
    else:
        # Default: parent of parent of space_root — i.e. D:\spaces if space_root = D:\spaces\human
        parent = space_root.parent
        if parent != space_root:
            config.spaces_root = parent

    # channels (accepting the legacy `routes:` spelling and field aliases)
    fallback_agent = ""
    for ch in raw.get("channels", raw.get("routes", [])) or []:
        if not isinstance(ch, dict):
            continue
        cid = ch.get("id") or ch.get("channel") or ""
        cname = ch.get("name") or cid
        if not cid:
            continue
        agents = _channel_agents(ch)
        config.channels.append(
            ChannelConfig(
                id=cid,
                name=cname,
                agents=agents,
                priority=str(ch.get("priority", "normal")),
                sla=ch.get("sla"),
                keywords=_channel_keywords(ch),
                description=str(ch.get("description", "")),
            )
        )
        # `default: true` marks the channel unrouted content falls back to.
        if ch.get("default") and not fallback_agent and agents:
            fallback_agent = agents[0]

    # `fallback: {channel: ...}` — the other legacy spelling of the same idea.
    if not fallback_agent and isinstance(raw.get("fallback"), dict):
        fallback_agent = str(raw["fallback"].get("channel") or "")

    # exclude
    for rule in raw.get("exclude", []) or []:
        if not isinstance(rule, dict):
            continue
        config.exclude.append(
            ExcludeRule(
                keywords=_coerce_list(rule.get("keywords")),
                min_hits=int(rule.get("min_hits", 1)),
                action=str(rule.get("action", "find_best_space")),
                dry_run=bool(rule.get("dry_run", True)),
                on_conflict=str(rule.get("on_conflict", "rename")),
                on_error=str(rule.get("on_error", "log_and_skip")),
            )
        )

    # defaults — an explicit `defaults:` block always wins over an inferred fallback.
    if isinstance(raw.get("defaults"), dict):
        d = raw["defaults"]
        config.defaults = RoutesDefaults(
            sla_hours=int(d.get("sla_hours", 48)),
            unrouted_fallback=str(d.get("unrouted_fallback", "") or fallback_agent),
        )
    elif fallback_agent:
        config.defaults = RoutesDefaults(unrouted_fallback=fallback_agent)

    return config


def scan_sibling_spaces(spaces_root: Path) -> list[Path]:
    """
    Return all subdirectories of `spaces_root` that look like a valid space
    (i.e. contain a `.navig/inbox/` subdirectory).

    The folder name is irrelevant — only structure matters.
    Returns an empty list if the directory does not exist or is unreadable.
    """
    result: list[Path] = []
    if not spaces_root.is_dir():
        return result
    try:
        for entry in spaces_root.iterdir():
            if not entry.is_dir():
                continue
            if (entry / ".navig" / "inbox").is_dir():
                result.append(entry)
    except PermissionError as exc:
        logger.warning("Cannot scan spaces root %s: %s", spaces_root, exc)
    return result


# ── Helpers ───────────────────────────────────────────────────


def _coerce_list(val: Any) -> list[str]:
    """Normalise a YAML value to a list of strings."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v) for v in val]
    return [str(val)]


def _dedupe(values: list[str]) -> list[str]:
    """Order-preserving de-duplication of non-empty strings."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _channel_agents(ch: dict[str, Any]) -> list[str]:
    """Agents for a channel, across every spelling seen in the wild.

    `formation:` is intentionally excluded — see the module docstring.
    """
    agents: list[str] = []
    for key in ("agents", "handlers", "handler", "owner", "multi_agent"):
        agents.extend(_coerce_list(ch.get(key)))
    return _dedupe(agents)


def _channel_keywords(ch: dict[str, Any]) -> list[str]:
    """Match terms for a channel, across every spelling seen in the wild.

    Handles `triggers: [{keyword: [...]}, {formation: ...}]` (only the `keyword`
    entries are terms) and `pattern: "a|b|c"` (a regex alternation, not a list).
    """
    words: list[str] = []
    words.extend(_coerce_list(ch.get("keywords")))
    words.extend(_coerce_list(ch.get("tags")))

    triggers = ch.get("triggers")
    if isinstance(triggers, list):
        for trig in triggers:
            if isinstance(trig, dict):
                words.extend(_coerce_list(trig.get("keyword")))
            elif trig is not None:
                words.append(str(trig))

    pattern = ch.get("pattern")
    if isinstance(pattern, str) and pattern:
        words.extend(pattern.split("|"))

    return _dedupe(words)
