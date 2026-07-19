"""
Plugin marketplaces — Claude Code compatible.

A **marketplace** is a source (git repo or local dir) that ships a
`.claude-plugin/marketplace.json` (or `marketplace.json`) listing installable
plugins:

    {"name": "navig-community",
     "plugins": [{"name": "telegram", "source": "./plugins/telegram",
                  "description": "Telegram manager", "version": "1.2.0"}]}

The set of registered marketplaces is kept in a small local registry file
(`<plugins_dir>/marketplaces.json`). Nothing here reaches the network unless you
call :func:`fetch_marketplace` on a git URL, and every install is validated
through the CC-compatible package host (`package.load_package`) before it lands
in the plugins dir — a broken bundle is rejected, never copied.

See `docs/plugin-spec.md`. Distribution is polyrepo: the canonical marketplace is
`navig-community`; `api.navig.run` indexes it (control plane only, no user data).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = "marketplaces.json"
_MANIFEST_NAMES = (".claude-plugin/marketplace.json", "marketplace.json")


def _is_git_url(src: str) -> bool:
    return src.startswith(("http://", "https://", "git@", "ssh://"))


@dataclass
class MarketplaceEntry:
    """One installable plugin advertised by a marketplace."""

    name: str
    source: str                       # subpath (relative to the marketplace) or git URL
    description: str = ""
    version: str = ""
    marketplace: str = ""             # owning marketplace name (filled on resolve)
    commands: list[str] = field(default_factory=list)  # top-level CLI verbs it provides

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, marketplace: str = "") -> "MarketplaceEntry":
        raw_commands = data.get("commands", [])
        commands = [str(c) for c in raw_commands] if isinstance(raw_commands, list) else []
        return cls(
            name=str(data.get("name", "")),
            source=str(data.get("source", data.get("name", ""))),
            description=str(data.get("description", "")),
            version=str(data.get("version", "")),
            marketplace=marketplace,
            commands=commands,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name, "source": self.source,
            "description": self.description, "version": self.version,
            "marketplace": self.marketplace,
        }
        if self.commands:
            out["commands"] = self.commands
        return out


@dataclass
class Marketplace:
    """A registered marketplace: a name + the URL/path it was added from."""

    name: str
    url: str                          # git URL or local dir path
    entries: list[MarketplaceEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # Entries ARE persisted: they're the offline catalog cache that lets
        # resolve_provider() and the Store's AVAILABLE rows work without a
        # network/git fetch (refreshed whenever the marketplace is re-added).
        return {
            "name": self.name,
            "url": self.url,
            "entries": [e.to_dict() for e in self.entries],
        }


def parse_marketplace_manifest(data: dict[str, Any], *, url: str = "") -> Marketplace:
    """Parse a `marketplace.json` payload into a :class:`Marketplace`."""
    name = str(data.get("name") or "marketplace")
    entries = [
        MarketplaceEntry.from_dict(p, marketplace=name)
        for p in (data.get("plugins") or [])
        if isinstance(p, dict) and p.get("name")
    ]
    return Marketplace(name=name, url=url, entries=entries)


def _read_manifest_dir(root: Path) -> dict[str, Any] | None:
    for rel in _MANIFEST_NAMES:
        cand = root / rel
        if cand.exists():
            return json.loads(cand.read_text(encoding="utf-8"))
    return None


def fetch_marketplace(url: str, *, workdir: Path | None = None) -> Marketplace:
    """Load a marketplace from a local dir or a git URL.

    Git URLs are shallow-cloned into a temp dir (or ``workdir``). Raises on a
    missing/unparseable manifest — callers surface it, never crash boot.
    """
    if _is_git_url(url):
        owns_tmp = workdir is None  # clean only a temp WE created; caller owns `workdir`
        clone_root = Path(workdir or tempfile.mkdtemp(prefix="navig-market-"))
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(clone_root)],
                check=True, capture_output=True, text=True,
            )
            data = _read_manifest_dir(clone_root)
            if data is None:
                raise FileNotFoundError(f"no marketplace.json in {url}")
            mkt = parse_marketplace_manifest(data, url=url)
            # mkt.url is the git URL (not the clone path), so the clone is only
            # needed to read the manifest here — free it when we own it.
            mkt.url = url
            return mkt
        finally:
            if owns_tmp:
                shutil.rmtree(clone_root, ignore_errors=True)

    root = Path(url).expanduser()
    data = _read_manifest_dir(root)
    if data is None:
        raise FileNotFoundError(f"no marketplace.json in {root}")
    return parse_marketplace_manifest(data, url=str(root))


class MarketplaceStore:
    """Local registry of added marketplaces (`<plugins_dir>/marketplaces.json`).

    Pass ``plugins_dir`` explicitly in tests to avoid touching real state.
    """

    def __init__(self, plugins_dir: Path | None = None) -> None:
        if plugins_dir is None:
            from navig.config import get_config_manager

            plugins_dir = get_config_manager().plugins_dir
        self.plugins_dir = Path(plugins_dir)
        self.registry_path = self.plugins_dir / REGISTRY_FILENAME

    # ── registry io ────────────────────────────────────────────────────────
    def _load(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {"marketplaces": []}
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — a corrupt registry must not crash
            logger.warning("marketplace registry unreadable: %s", exc)
            return {"marketplaces": []}

    def _save(self, data: dict[str, Any]) -> None:
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── crud ───────────────────────────────────────────────────────────────
    def list_marketplaces(self) -> list[Marketplace]:
        out: list[Marketplace] = []
        for m in self._load().get("marketplaces", []):
            if not (m.get("name") and m.get("url")):
                continue
            entries = [
                MarketplaceEntry.from_dict(e, marketplace=m["name"])
                for e in (m.get("entries") or [])
                if isinstance(e, dict) and e.get("name")
            ]
            out.append(Marketplace(name=m["name"], url=m["url"], entries=entries))
        return out

    def add(self, url: str) -> Marketplace:
        """Register a marketplace (validates its manifest first)."""
        mkt = fetch_marketplace(url)          # raises if the manifest is bad
        data = self._load()
        rows = [m for m in data.get("marketplaces", []) if m.get("name") != mkt.name]
        rows.append(mkt.to_dict())
        self._save({"marketplaces": rows})
        return mkt

    def remove(self, name: str) -> bool:
        data = self._load()
        rows = data.get("marketplaces", [])
        kept = [m for m in rows if m.get("name") != name]
        if len(kept) == len(rows):
            return False
        self._save({"marketplaces": kept})
        return True

    def refresh(self, name: str | None = None) -> list[tuple[str, str]]:
        """Re-fetch the live manifest for one/all marketplaces and rewrite the
        cached entries. Returns [(name, status)] per marketplace.

        The cached entries (offline catalog for resolve_provider / the Store's
        AVAILABLE rows) otherwise only update on `add`, so a removed/renamed
        upstream plugin would be advertised forever. Never raises.
        """
        data = self._load()
        rows = data.get("marketplaces", [])
        results: list[tuple[str, str]] = []
        changed = False
        for row in rows:
            if name is not None and row.get("name") != name:
                continue
            try:
                mkt = fetch_marketplace(row["url"])
                row["entries"] = [e.to_dict() for e in mkt.entries]
                changed = True
                results.append((row["name"], f"{len(mkt.entries)} plugins"))
            except Exception as exc:  # noqa: BLE001
                results.append((row.get("name", "?"), f"unreachable: {str(exc)[:80]}"))
        if changed:
            self._save({"marketplaces": rows})
        return results

    # ── resolution ─────────────────────────────────────────────────────────
    def resolve(self, plugin_name: str) -> tuple[Marketplace, MarketplaceEntry] | None:
        """Find the first marketplace advertising ``plugin_name``."""
        for row in self.list_marketplaces():
            try:
                mkt = fetch_marketplace(row.url)
            except Exception as exc:  # noqa: BLE001 — skip an unreachable marketplace
                logger.warning("marketplace %s unreachable: %s", row.name, exc)
                continue
            for entry in mkt.entries:
                if entry.name == plugin_name:
                    return mkt, entry
        return None
