"""Capability index — a rebuildable derivative over NAVIG's existing registries.

Answers "what can NAVIG already do, how is it invoked, what does it need, is it
healthy" **without inventing a second knowledge store**: every entry is read from
the authoritative source that already owns it (command manifest, ToolMeta, hub
store, module registry, skills loader, agent tool registry) and the snapshot
under ``data_dir()/creative/capability_index.json`` is a disposable cache —
``invalidate()`` + rebuild, never a source of truth.

Provenance (the mission's three-way distinction, bounded to two on entries):
- ``source``   — availability answered from the DECLARATION only (commands from
  the gated manifest; nothing was probed this build).
- ``runtime``  — availability probed from live state this build (ToolMeta
  status, store wire states, module enablement, agent-tool check fns).
- The third mission category, *AI assumption*, never appears on an index entry;
  it lives only on proposals (``proposal.assumptions``) as "names the AI used
  that the index could not confirm".

Readers are best-effort: one failing subsystem is recorded in ``errors`` and the
build still succeeds (a partial index beats none; ``status()`` reports honestly).
No LLM imports — guard-tested.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

from navig.platform.paths import data_dir

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
#: Snapshot freshness window. Deliberately NOT a committed/gated artifact — a
#: derived cache rots, so it expires instead of being policed.
DEFAULT_TTL_S = 3600

__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_TTL_S",
    "build_index",
    "ensure_index",
    "load_index",
    "invalidate",
    "find",
    "status",
    "index_path",
]


def index_path() -> Path:
    """Where the snapshot lives. Under ``data_dir()`` so ``NAVIG_DATA_DIR`` isolates it."""
    return data_dir() / "creative" / "capability_index.json"


# ── readers ──────────────────────────────────────────────────────────────────
# Each returns (entries, fingerprint_count). Kept as module-level functions so
# tests inject fakes without patching internals of build_index.


def _read_commands() -> tuple[list[dict[str, Any]], int]:
    from navig.cli.registry import _build_runtime_manifest, _load_generated_manifest

    manifest = _load_generated_manifest() or _build_runtime_manifest()
    built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entries: list[dict[str, Any]] = []
    for cmd in manifest.get("commands", []) or []:
        path = cmd.get("path") or ""
        if not path:
            continue
        status = str(cmd.get("status") or "stable")
        entries.append(
            {
                "id": f"command:{path}",
                "kind": "command",
                "name": path,
                "summary": str(cmd.get("summary") or ""),
                "how_to_invoke": {"type": "cli", "ref": path},
                "inputs": {
                    "arguments": cmd.get("arguments") or [],
                    "options": cmd.get("options") or [],
                },
                "outputs": None,
                "safety": "unknown",
                "requires": {"config": [], "plugins": [], "entitlement": None},
                "declared": {"origin": "commands.json", "status": status},
                # Declared availability (provenance=source says so): hidden /
                # internal / deprecated commands are not offered as usable.
                "observed": {
                    "state": "disabled" if status in ("hidden", "internal", "deprecated") else "available",
                    "degraded": False,
                    "checked_at": built_at,
                    "error": None,
                },
                "provenance": "source",
                "tags": list(cmd.get("tags") or []),
                "provider": None,
                "source_ref": str(cmd.get("module") or ""),
            }
        )
    return entries, len(entries)


def _read_tools() -> tuple[list[dict[str, Any]], int]:
    from navig.tools.router import get_tool_registry

    built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state_map = {
        "available": "available",
        "unavailable": "unwired",
        "disabled": "disabled",
        "error": "broken",
    }
    entries: list[dict[str, Any]] = []
    for tool in get_tool_registry().list_tools(available_only=False):
        status = getattr(tool.status, "value", str(tool.status))
        safety = getattr(tool.safety, "value", str(tool.safety))
        domain = getattr(tool.domain, "value", str(tool.domain))
        entries.append(
            {
                "id": f"tool:{tool.name}",
                "kind": "tool",
                "name": tool.name,
                "summary": str(tool.description or ""),
                "how_to_invoke": {"type": "agent_tool", "ref": tool.name},
                "inputs": tool.parameters_schema,
                "outputs": tool.output_schema,
                "safety": str(safety).lower(),
                "requires": {
                    "config": list(tool.required_config or []),
                    "plugins": [],
                    "entitlement": None,
                },
                "declared": {"origin": "toolmeta", "status": status},
                "observed": {
                    "state": state_map.get(str(status), "unknown"),
                    "degraded": str(status) != "available",
                    "checked_at": built_at,
                    "error": tool.status_message or None,
                },
                "provenance": "runtime",
                "tags": list(tool.tags or []) + [str(domain)],
                "provider": None,
                "source_ref": str(tool.module_path or ""),
            }
        )
    return entries, len(entries)


def _read_store() -> tuple[list[dict[str, Any]], int]:
    """Plugin / MCP / connector health from the hub aggregator.

    Store ``module:*`` and ``skill:*`` items are deliberately dropped here —
    those kinds are read from their own richer sources below (module catalog,
    skills loader), and a duplicated id would make ``find()`` answer twice.
    """
    from navig.hub.aggregator import collect_store

    built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entries: list[dict[str, Any]] = []
    for item in collect_store(include_available=True, refresh=False):
        if item.kind in ("module", "skill"):
            continue
        entries.append(
            {
                "id": item.id,
                "kind": item.kind,
                "name": item.label,
                "summary": str(item.description or ""),
                "how_to_invoke": {"type": item.kind, "ref": item.id},
                "inputs": None,
                "outputs": None,
                "safety": "unknown",
                "requires": {
                    "config": [],
                    "plugins": [item.id] if item.kind != "plugin" else [],
                    "entitlement": None,
                },
                "declared": {"origin": "store", "status": item.state},
                "observed": {
                    "state": item.state,
                    "degraded": bool(item.degraded),
                    "checked_at": built_at,
                    "error": None,
                },
                "provenance": "runtime",
                "tags": list(item.actions or []),
                "provider": item.provider,
                "source_ref": item.source,
            }
        )
    return entries, len(entries)


def _read_modules() -> tuple[list[dict[str, Any]], int]:
    from navig.modules.registry import get_registry

    built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entries: list[dict[str, Any]] = []
    for mod in get_registry().list_modules(include_dev=False):
        mid = mod.get("id") or ""
        if not mid:
            continue
        enabled = bool(mod.get("enabled", False))
        locked = bool(mod.get("locked", False))
        entries.append(
            {
                "id": f"module:{mid}",
                "kind": "module",
                "name": mod.get("label") or mid,
                "summary": str(mod.get("description") or ""),
                "how_to_invoke": {"type": "module", "ref": f"module:{mid}"},
                "inputs": None,
                "outputs": None,
                "safety": "unknown",
                "requires": {
                    "config": [],
                    "plugins": list(mod.get("requires") or []),
                    "entitlement": mod.get("capability"),
                },
                "declared": {"origin": "module-registry", "status": mod.get("kind") or ""},
                "observed": {
                    "state": "available" if (enabled and not locked) else "unwired",
                    "degraded": locked,
                    "checked_at": built_at,
                    "error": None,
                },
                "provenance": "runtime",
                "tags": list(mod.get("surfaces") or []),
                "provider": mod.get("source"),
                "source_ref": mid,
            }
        )
    return entries, len(entries)


def _read_skills() -> tuple[list[dict[str, Any]], int]:
    """Skills from the loader (frontmatter truth), with live wire state merged
    in from the store's ``skill:*`` items when present."""
    from navig.hub.aggregator import collect_store
    from navig.skills.loader import load_all_skills

    built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store_state: dict[str, Any] = {}
    try:
        store_state = {
            item.id: item for item in collect_store(include_available=True) if item.kind == "skill"
        }
    except Exception as exc:  # noqa: BLE001 - state merge is best-effort
        logger.debug("store state merge unavailable for skills: %s", exc)

    entries: list[dict[str, Any]] = []
    for skill in load_all_skills():
        sid = getattr(skill, "id", "") or ""
        if not sid:
            continue
        item = store_state.get(f"skill:{sid}")
        if item is not None:
            observed = {
                "state": item.state,
                "degraded": bool(item.degraded),
                "checked_at": built_at,
                "error": None,
            }
            provenance = "runtime"
        else:
            observed = {
                "state": "available",
                "degraded": False,
                "checked_at": built_at,
                "error": None,
            }
            provenance = "source"
        entries.append(
            {
                "id": f"skill:{sid}",
                "kind": "skill",
                "name": getattr(skill, "name", "") or sid,
                "summary": str(getattr(skill, "description", "") or ""),
                "how_to_invoke": {"type": "skill", "ref": sid},
                "inputs": None,
                "outputs": None,
                "safety": str(getattr(skill, "safety", "unknown") or "unknown"),
                "requires": {
                    "config": [],
                    "plugins": [],
                    "entitlement": None,
                },
                "declared": {
                    "origin": "skill-frontmatter",
                    "status": str(getattr(skill, "category", "") or ""),
                },
                "observed": observed,
                "provenance": provenance,
                "tags": list(getattr(skill, "tags", []) or []),
                "provider": None,
                "source_ref": str(getattr(skill, "source_path", "") or ""),
            }
        )
    return entries, len(entries)


def _read_agent_tools() -> tuple[list[dict[str, Any]], int]:
    from navig.agent.agent_tool_registry import _is_available, get_agent_tool_registry

    built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entries: list[dict[str, Any]] = []
    registry = get_agent_tool_registry()
    for entry in registry.entries():
        name = getattr(entry, "name", "") or ""
        if not name:
            continue
        # tool_ref is a live object for some entries (e.g. DebriefBuildTool) —
        # the index stores a JSON-safe reference, never the object itself.
        tool_ref = getattr(entry, "tool_ref", "")
        if not isinstance(tool_ref, str):
            tool_ref = str(getattr(tool_ref, "name", "") or type(tool_ref).__name__)
        available = _is_available(entry)
        entries.append(
            {
                "id": f"agent_tool:{name}",
                "kind": "agent_tool",
                "name": name,
                "summary": str((entry.schema or {}).get("description") or ""),
                "how_to_invoke": {"type": "agent_tool", "ref": name},
                "inputs": entry.schema,
                "outputs": None,
                "safety": "unknown",
                "requires": {
                    "config": [],
                    "plugins": [],
                    "entitlement": None,
                },
                "declared": {"origin": "agent-tool-registry", "status": getattr(entry, "toolset", "")},
                "observed": {
                    "state": "available" if available else "disabled",
                    "degraded": not available,
                    "checked_at": built_at,
                    "error": None,
                },
                "provenance": "runtime",
                "tags": [getattr(entry, "toolset", "") or ""],
                "provider": getattr(entry, "origin", None),
                "source_ref": tool_ref,
            }
        )
    return entries, len(entries)


_READERS: tuple[tuple[str, Callable[[], tuple[list[dict[str, Any]], int]]], ...] = (
    ("commands", _read_commands),
    ("tools", _read_tools),
    ("store", _read_store),
    ("modules", _read_modules),
    ("skills", _read_skills),
    ("agent_tools", _read_agent_tools),
)


# ── snapshot lifecycle ───────────────────────────────────────────────────────


def _navig_version() -> str:
    try:
        from importlib.metadata import version

        return version("navig")
    except Exception:  # pragma: no cover - metadata missing in exotic installs
        return "unknown"


def build_index() -> dict[str, Any]:
    """Collect every reader (best-effort), write the snapshot, return it."""
    entries: list[dict[str, Any]] = []
    fingerprints: dict[str, int] = {}
    errors: dict[str, str] = {}
    for name, reader in _READERS:
        try:
            rows, count = reader()
            entries.extend(rows)
            fingerprints[name] = count
        except Exception as exc:  # noqa: BLE001 - one broken source must not kill the build
            logger.warning("capability index reader %s failed: %s", name, exc)
            errors[name] = f"{type(exc).__name__}: {exc}"
            fingerprints[name] = -1

    entries.sort(key=lambda e: e["id"])
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "navig_version": _navig_version(),
        "source_fingerprints": fingerprints,
        "errors": errors,
        "entries": entries,
    }
    path = index_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from navig.core.json_io import atomic_write_json

        atomic_write_json(snapshot, path)
    except Exception as exc:  # noqa: BLE001 - an unpersistable cache is a miss, not a crash
        # The snapshot still returns usable (in-memory) this turn; the next
        # ensure_index() sees no file and rebuilds. One offending field must
        # never take the whole index down.
        logger.warning("capability index snapshot not persisted: %s", exc)
    return snapshot


def load_index() -> dict[str, Any] | None:
    """Read the snapshot; ``None`` when absent/corrupt/foreign-schema."""
    path = index_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a bad cache is a miss, never a crash
        logger.debug("capability index unreadable, treating as absent: %s", exc)
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return None
    if not isinstance(data.get("entries"), list):
        return None
    return data


def _built_epoch(stamp: str | None) -> float:
    """Parse the gmtime ``built_at`` stamp to a UTC epoch.

    ``time.mktime`` would read the struct as LOCAL time, so any machine east of
    UTC would see its own cache as instantly stale (age = tz offset).
    """
    import calendar

    try:
        return float(calendar.timegm(time.strptime(stamp or "", "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, TypeError):
        return 0.0


def ensure_index(*, max_age_s: int = DEFAULT_TTL_S, force: bool = False) -> dict[str, Any]:
    """Return a fresh-enough index, rebuilding when absent or older than *max_age_s*."""
    if not force:
        cached = load_index()
        if cached is not None:
            built = _built_epoch(cached.get("built_at"))
            if built and (time.time() - built) <= max_age_s:
                return cached
    return build_index()


def invalidate() -> None:
    """Drop the snapshot so the next reader rebuilds (called after skill builds)."""
    try:
        index_path().unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - defensive
        logger.debug("could not invalidate capability index: %s", exc)


def status() -> dict[str, Any]:
    """Freshness + counts for the status surface. Honest about staleness."""
    cached = load_index()
    if cached is None:
        return {
            "present": False,
            "fresh": False,
            "built_at": None,
            "age_s": None,
            "entries": 0,
            "kinds": {},
            "errors": {},
        }
    try:
        built = _built_epoch(cached.get("built_at"))
        age_s: float | None = max(0.0, time.time() - built) if built else None
    except (KeyError, ValueError):
        age_s = None
    kinds: dict[str, int] = {}
    for entry in cached["entries"]:
        kinds[entry.get("kind", "?")] = kinds.get(entry.get("kind", "?"), 0) + 1
    return {
        "present": True,
        "fresh": age_s is not None and age_s <= DEFAULT_TTL_S,
        "built_at": cached.get("built_at"),
        "age_s": age_s,
        "entries": len(cached["entries"]),
        "kinds": kinds,
        "errors": cached.get("errors") or {},
    }


# ── query ────────────────────────────────────────────────────────────────────

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _words(text: str) -> list[str]:
    """Lowercase word tokens — matching is WORD-based, never raw substring.

    Substring matching let a stopword like "the" hit inside "synthesize"
    (score 6, a false reuse verdict) — words+prefix keeps "dock"→"docker"
    working while "the" only matches an actual word "the".
    """
    return [w for w in _WORD_SPLIT_RE.split((text or "").lower()) if w]


def _field(token: str, words: list[str], *, exact: int, prefix: int) -> int:
    """Exact word → *exact* points; word starting with the token → *prefix*;
    otherwise 0. Summary/tag never use prefix points — they tilt, not identify."""
    for w in words:
        if w == token:
            return exact
    for w in words:
        if w.startswith(token):
            return prefix
    return 0


def score_entry(entry: dict[str, Any], query_tokens: list[str]) -> int:
    """Rank one entry against query tokens. Single implementation — ``find()``
    and ``decide()`` must agree on what counts as a match.

    Identity lives in name (3 exact / 2 prefix) and id (2/1); summary and tags
    add 1 for an exact word only. So two incidental summary words (e.g. "the"
    + "weekly") can never reach MATCH_SCORE_MIN on their own — that bug let a
    fake goal "reuse" an unrelated command.
    """
    if not query_tokens:
        return 0
    name_w = _words(str(entry.get("name", "")))
    id_w = _words(str(entry.get("id", "")))
    summary_w = _words(str(entry.get("summary", "")))
    tags_w = _words(" ".join(str(t) for t in entry.get("tags") or []))
    score = 0
    for tok in query_tokens:
        score += _field(tok, name_w, exact=3, prefix=2)
        score += _field(tok, id_w, exact=2, prefix=1)
        score += _field(tok, summary_w, exact=1, prefix=0)
        score += _field(tok, tags_w, exact=1, prefix=0)
    return score


def tokenize_query(query: str) -> list[str]:
    """Query → word tokens (``:`` and ``/`` split so ids/paths tokenize)."""
    text = (query or "").replace(":", " ").replace("/", " ").replace("-", " ")
    return _words(text)


def find(
    query: str,
    *,
    kinds: list[str] | None = None,
    limit: int = 10,
    index: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Word-ranked lookup over name/summary/tags/id — deterministic, no LLM.

    Score: 3× word-hit in name · 2× in id · 1× in summary · 1× in a tag
    (exact word 2×, prefix 1× inside each field's weight). Entries matching no
    token are never returned (an empty query returns the first *limit* entries
    so callers can page the catalog).
    """
    snap = index if index is not None else ensure_index()
    tokens = tokenize_query(query)

    scored: list[tuple[int, str, dict[str, Any]]] = []
    for entry in snap.get("entries", []):
        if kinds and entry.get("kind") not in kinds:
            continue
        if not tokens:
            scored.append((0, entry["id"], entry))
            continue
        score = score_entry(entry, tokens)
        if score > 0:
            scored.append((score, entry["id"], entry))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in scored[: max(0, limit)]]
