"""Connector catalog — every service NAVIG knows about, whether or not it can connect yet.

The connector *registry* (``navig.connectors.registry``) holds the connectors that are
implemented and can run. This catalog is the wider map: every service worth connecting,
grouped by category and country, ranked into **batches** so they can be resolved a
batch at a time (verify the vendor's integration, then wire it).

Data lives in ``data/<category>.jsonl`` — one entry per line, fixed key order, so a
row is greppable, hand-editable, and reviews as a one-line diff. The files ARE the
source of truth: ``core/tools/connectors_catalog_import.py`` only ever *appends* ids it
has not seen, so a hand edit is never overwritten by a re-import.

An entry's lifecycle (``status``):

* ``planned``  — listed, nothing checked yet. ``country``/``category`` are best-effort.
* ``verified`` — a person checked the vendor: how it integrates (``integration``), its
  endpoint (``mcp`` for a vendor-hosted MCP server), category and country.
* ``live``     — usable from NAVIG today (``native`` names the core connector id when
  it is one of ours).

Resolving a batch = move its rows from ``planned`` to ``verified``/``live``. See
``docs/connectors-catalog.md`` for the workflow.

Nothing here stores a vendor's marketing copy: an entry is facts (name, vendor,
category, country, integration), so the catalog is ours to ship.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any


class CatalogError(ValueError):
    """A catalog row is malformed. Raised on load — shipped data must be valid."""


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    icon: str


# Display order. ``other`` is last on purpose: it is the "not sorted yet" bucket that a
# batch review empties, not a real category.
CATEGORIES: tuple[Category, ...] = (
    Category("files", "Files & storage", "🗂️"),
    Category("email", "Email", "✉️"),
    Category("meetings", "Calendar & meetings", "📅"),
    Category("communication", "Chat & messaging", "💬"),
    Category("knowledge", "Docs & knowledge", "📚"),
    Category("projects", "Projects & tasks", "✅"),
    Category("developer", "Developer tools", "🧑‍💻"),
    Category("cloud", "Cloud & hosting", "☁️"),
    Category("data", "Databases & data", "🗄️"),
    Category("observability", "Monitoring & incidents", "📟"),
    Category("analytics", "Product analytics", "📈"),
    Category("automation", "Automation", "⚙️"),
    Category("web", "Web search & scraping", "🌐"),
    Category("ai", "AI & models", "🤖"),
    Category("design", "Design & slides", "🎨"),
    Category("media", "Video, audio & music", "🎬"),
    Category("sales", "CRM & sales", "🤝"),
    Category("marketing", "Marketing & ads", "📣"),
    Category("seo", "SEO & search visibility", "🔎"),
    Category("email-marketing", "Email marketing", "📬"),
    Category("support", "Customer support", "🎧"),
    Category("forms", "Forms & surveys", "📝"),
    Category("commerce", "Commerce & shipping", "🛒"),
    Category("payments", "Payments & billing", "💳"),
    Category("finance", "Accounting & finance", "🧾"),
    Category("markets", "Markets & investing", "📊"),
    Category("crypto", "Crypto", "🪙"),
    Category("hr", "HR & recruiting", "👥"),
    Category("legal", "Legal & contracts", "⚖️"),
    Category("security", "Security & compliance", "🛡️"),
    Category("health", "Health & life sciences", "🧬"),
    Category("research", "Research & science", "🔬"),
    Category("education", "Education", "🎓"),
    Category("travel", "Travel", "✈️"),
    Category("food", "Food & delivery", "🍽️"),
    Category("lifestyle", "Maps, weather & lifestyle", "🧭"),
    Category("real-estate", "Real estate", "🏠"),
    Category("public-data", "Public & government data", "🏛️"),
    Category("other", "Not sorted yet", "📦"),
)
CATEGORY_IDS: frozenset[str] = frozenset(c.id for c in CATEGORIES)

# Where an entry came from — also its default resolution order.
#   navig     — a connector NAVIG ships that the public directories do not list
#   official  — vendor-published integration
#   desktop   — runs locally (a desktop extension / local MCP server), not vendor-hosted
#   community — third-party, unreviewed; lowest trust
TIERS: tuple[str, ...] = ("navig", "official", "desktop", "community")
STATUSES: tuple[str, ...] = ("planned", "verified", "live")
# How NAVIG reaches it, once known.
#   native     — a Python connector in core (navig/connectors/<id>)
#   remote_mcp — the vendor's hosted MCP server (``mcp`` holds the URL)
#   local_mcp  — an MCP server run on the user's machine
#   api        — a plain HTTP API source
INTEGRATIONS: tuple[str, ...] = ("native", "remote_mcp", "local_mcp", "api")
# beta · new · trending: as the vendor labels it. client-approval: the vendor's MCP server
# admits only approved or pre-registered apps (an allowlist, a waitlist, a developer app the
# user must create, or an admin switch), so it cannot be one-click connected.
FLAGS: frozenset[str] = frozenset({"beta", "new", "trending", "client-approval"})

# ISO 3166-1 alpha-2 → region. A country missing here is a validation error, so adding
# a vendor from a new country means adding it here (one line) rather than a silent "?".
REGIONS: dict[str, str] = {
    # North America
    "US": "north-america", "CA": "north-america", "MX": "latin-america",
    # Latin America
    "BR": "latin-america", "AR": "latin-america", "CL": "latin-america", "CO": "latin-america",
    "PE": "latin-america", "UY": "latin-america",
    # Europe
    "GB": "europe", "IE": "europe", "FR": "europe", "DE": "europe", "NL": "europe",
    "BE": "europe", "LU": "europe", "CH": "europe", "AT": "europe", "ES": "europe",
    "PT": "europe", "IT": "europe", "SE": "europe", "NO": "europe", "DK": "europe",
    "FI": "europe", "IS": "europe", "PL": "europe", "CZ": "europe", "SK": "europe",
    "HU": "europe", "RO": "europe", "BG": "europe", "GR": "europe", "CY": "europe",
    "EE": "europe", "LV": "europe", "LT": "europe", "SI": "europe", "HR": "europe",
    "RS": "europe", "UA": "europe", "MT": "europe",
    # Middle East & Africa
    "IL": "middle-east-africa", "TR": "middle-east-africa", "AE": "middle-east-africa",
    "SA": "middle-east-africa", "EG": "middle-east-africa", "ZA": "middle-east-africa",
    "NG": "middle-east-africa", "KE": "middle-east-africa", "MA": "middle-east-africa",
    # Asia-Pacific
    "IN": "asia-pacific", "JP": "asia-pacific", "KR": "asia-pacific", "CN": "asia-pacific",
    "TW": "asia-pacific", "HK": "asia-pacific", "SG": "asia-pacific", "AU": "asia-pacific",
    "NZ": "asia-pacific", "ID": "asia-pacific", "VN": "asia-pacific", "TH": "asia-pacific",
    "MY": "asia-pacific", "PH": "asia-pacific", "PK": "asia-pacific", "BD": "asia-pacific",
}
REGION_IDS: tuple[str, ...] = (
    "north-america", "latin-america", "europe", "middle-east-africa", "asia-pacific",
)

# Entries per batch. A batch is the unit of one review session: small enough to verify
# every row's integration by hand, large enough that the catalog resolves in finite time.
BATCH_SIZE = 25

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_KEYS = (
    "id", "name", "vendor", "category", "country", "tier", "rank", "batch",
    "status", "integration", "native", "mcp", "flags",
)


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    name: str
    vendor: str
    category: str
    country: str | None
    tier: str
    rank: int
    batch: int
    status: str = "planned"
    integration: str | None = None
    native: str | None = None
    mcp: str | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def region(self) -> str | None:
        return REGIONS.get(self.country) if self.country else None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["flags"] = list(self.flags)
        d["region"] = self.region
        return d

    def to_row(self) -> str:
        """The canonical one-line JSONL form — fixed key order, so diffs stay one line."""
        d = asdict(self)
        d["flags"] = list(self.flags)
        return json.dumps({k: d[k] for k in _KEYS}, ensure_ascii=False)


def entry_from_dict(raw: dict[str, Any], *, where: str = "") -> CatalogEntry:
    """Build and validate one entry. Every rule here is a promise the UI and CLI rely on."""
    unknown = set(raw) - set(_KEYS)
    if unknown:
        raise CatalogError(f"{where}: unknown key(s) {sorted(unknown)}")
    try:
        e = CatalogEntry(
            id=raw["id"], name=raw["name"], vendor=raw["vendor"], category=raw["category"],
            country=raw.get("country"), tier=raw["tier"], rank=int(raw["rank"]),
            batch=int(raw["batch"]), status=raw.get("status", "planned"),
            integration=raw.get("integration"), native=raw.get("native"),
            mcp=raw.get("mcp"), flags=tuple(raw.get("flags") or ()),
        )
    except KeyError as exc:
        raise CatalogError(f"{where}: missing key {exc}") from None
    where = where or e.id
    if not _ID_RE.match(e.id):
        raise CatalogError(f"{where}: id {e.id!r} must be lowercase kebab-case")
    if not e.name.strip() or not e.vendor.strip():
        raise CatalogError(f"{where}: name and vendor are required")
    if e.category not in CATEGORY_IDS:
        raise CatalogError(f"{where}: unknown category {e.category!r}")
    if e.country is not None and e.country not in REGIONS:
        raise CatalogError(f"{where}: country {e.country!r} has no region — add it to REGIONS")
    if e.tier not in TIERS:
        raise CatalogError(f"{where}: unknown tier {e.tier!r}")
    if e.status not in STATUSES:
        raise CatalogError(f"{where}: unknown status {e.status!r}")
    if e.integration is not None and e.integration not in INTEGRATIONS:
        raise CatalogError(f"{where}: unknown integration {e.integration!r}")
    if set(e.flags) - FLAGS:
        raise CatalogError(f"{where}: unknown flag(s) {sorted(set(e.flags) - FLAGS)}")
    if e.rank < 1 or e.batch != batch_of(e.rank):
        raise CatalogError(f"{where}: batch {e.batch} does not match rank {e.rank}")
    # A row may only claim what it can back up.
    if e.status == "live" and e.integration is None:
        raise CatalogError(f"{where}: a live entry must say how it integrates")
    if e.integration == "native" and not e.native:
        raise CatalogError(f"{where}: a native integration must name its core connector")
    if e.integration == "remote_mcp" and not (e.mcp or "").startswith("https://"):
        raise CatalogError(f"{where}: a remote_mcp integration needs an https:// mcp URL")
    return e


def batch_of(rank: int) -> int:
    return (rank - 1) // BATCH_SIZE + 1


def _data_files() -> list[tuple[str, str]]:
    root = resources.files(__package__).joinpath("data")
    return sorted(
        (p.name, p.read_text(encoding="utf-8"))
        for p in root.iterdir()
        if p.name.endswith(".jsonl")
    )


def parse_rows(files: Iterable[tuple[str, str]]) -> list[CatalogEntry]:
    """Parse ``(filename, text)`` pairs into entries, enforcing the cross-row rules."""
    entries: list[CatalogEntry] = []
    seen_ids: dict[str, str] = {}
    seen_ranks: dict[int, str] = {}
    for name, text in files:
        file_category = name.removesuffix(".jsonl")
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            where = f"{name}:{n}"
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CatalogError(f"{where}: not valid JSON ({exc.msg})") from None
            e = entry_from_dict(raw, where=where)
            if e.category != file_category:
                raise CatalogError(f"{where}: category {e.category!r} filed under {name}")
            if e.id in seen_ids:
                raise CatalogError(f"{where}: duplicate id {e.id!r} (also {seen_ids[e.id]})")
            if e.rank in seen_ranks:
                raise CatalogError(f"{where}: duplicate rank {e.rank} (also {seen_ranks[e.rank]})")
            seen_ids[e.id] = where
            seen_ranks[e.rank] = where
            entries.append(e)
    entries.sort(key=lambda e: e.rank)
    return entries


@lru_cache(maxsize=1)
def load_catalog() -> tuple[CatalogEntry, ...]:
    """Every catalog entry, ordered by rank (most important first)."""
    return tuple(parse_rows(_data_files()))


def query(
    *,
    category: str | None = None,
    country: str | None = None,
    region: str | None = None,
    tier: str | None = None,
    status: str | None = None,
    batch: int | None = None,
    text: str | None = None,
    integration: str | None = None,
    entries: Iterable[CatalogEntry] | None = None,
) -> list[CatalogEntry]:
    """Filter the catalog. Every filter is optional and they combine with AND."""
    needle = (text or "").strip().lower()
    country = country.upper() if country else None
    out = []
    for e in load_catalog() if entries is None else entries:
        if category and e.category != category:
            continue
        if country and e.country != country:
            continue
        if region and e.region != region:
            continue
        if tier and e.tier != tier:
            continue
        if status and e.status != status:
            continue
        if batch is not None and e.batch != batch:
            continue
        if integration and e.integration != integration:
            continue
        if needle and needle not in e.name.lower() and needle not in e.vendor.lower():
            continue
        out.append(e)
    return out


def summary(entries: Iterable[CatalogEntry] | None = None) -> dict[str, Any]:
    """Counts per status / tier / category / region, and the next batch to resolve."""
    rows = list(load_catalog() if entries is None else entries)

    def count(key) -> dict[str, int]:
        c: dict[str, int] = {}
        for e in rows:
            k = key(e) or "unknown"
            c[k] = c.get(k, 0) + 1
        return c

    pending = [e.batch for e in rows if e.status == "planned"]
    return {
        "total": len(rows),
        "batches": max((e.batch for e in rows), default=0),
        "next_batch": min(pending) if pending else None,
        "by_status": count(lambda e: e.status),
        "by_tier": count(lambda e: e.tier),
        "by_category": count(lambda e: e.category),
        "by_region": count(lambda e: e.region),
    }


__all__ = [
    "BATCH_SIZE", "CATEGORIES", "CATEGORY_IDS", "CatalogEntry", "CatalogError", "Category",
    "FLAGS", "INTEGRATIONS", "REGIONS", "REGION_IDS", "STATUSES", "TIERS", "batch_of",
    "entry_from_dict", "load_catalog", "parse_rows", "query", "summary",
]
