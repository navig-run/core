"""The Lens format — a standard ``==UserScript==`` header, plus optional ``@navig-*`` keys.

Any Tampermonkey / Violentmonkey ``.user.js`` is a valid Lens. NAVIG reads the header
once, at save time, into a :class:`LensManifest`; the original source is stored
byte-for-byte, so an unknown key is never lost (``extra`` keeps it for display, the
file keeps it for export).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from navig.lenses.patterns import JsRegex, PatternError, include_to_regex, match_pattern_to_regex

_HEADER_RE = re.compile(
    r"^[ \t]*//[ \t]*==UserScript==[ \t]*\r?\n(.*?)^[ \t]*//[ \t]*==/UserScript==",
    re.MULTILINE | re.DOTALL,
)
_LINE_RE = re.compile(r"^[ \t]*//[ \t]*@([\w:.-]+)(?:[ \t]+(.*?))?[ \t]*$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

RUN_AT = ("document-start", "document-body", "document-end", "document-idle")
DEFAULT_RUN_AT = "document-idle"

# GM APIs the page runtime implements. A granted name outside this set is reported as a
# warning and passed to the script as `undefined`, so feature-detecting scripts
# (`typeof GM_xmlhttpRequest !== "undefined"`) take their fallback path.
SUPPORTED_GRANTS = frozenset(
    {
        "none",
        "GM_info",
        "GM_addStyle",
        "GM_getValue",
        "GM_setValue",
        "GM_deleteValue",
        "GM_listValues",
        "GM_openInTab",
        "GM_setClipboard",
        "GM_notification",
        "GM_registerMenuCommand",
        "GM_log",
        "unsafeWindow",
        "window.close",
        "window.focus",
        "window.onurlchange",
        "GM.info",
        "GM.addStyle",
        "GM.getValue",
        "GM.setValue",
        "GM.deleteValue",
        "GM.listValues",
        "GM.openInTab",
        "GM.setClipboard",
        "GM.notification",
        "GM.registerMenuCommand",
        "GM_xmlhttpRequest",
        # Privileged: sends to the NAVIG inbox through an isolated, per-Lens channel. Only a
        # host that can prove which Lens is calling exposes it (see compile.CAPTURE_GRANT).
        "navig.capture",
        "GM_addValueChangeListener",
        "GM_removeValueChangeListener",
        "GM.xmlHttpRequest",
        "GM.addValueChangeListener",
        "GM.removeValueChangeListener",
    }
)
# Header keys NAVIG understands; anything else lands in `extra` (kept, shown, ignored).
_KNOWN_KEYS = frozenset(
    {
        "name",
        "namespace",
        "version",
        "description",
        "author",
        "match",
        "include",
        "exclude",
        "exclude-match",
        "run-at",
        "noframes",
        "require",
        "grant",
        "icon",
        "iconURL",
        "homepage",
        "homepageURL",
        "navig-id",
        "navig-capability",
    }
)


class LensFormatError(ValueError):
    """The source is not a valid Lens — the message says what to fix."""


@dataclass
class LensManifest:
    """Everything NAVIG needs to know about a Lens, parsed from its header."""

    id: str
    name: str
    namespace: str = ""
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    icon: str = ""
    homepage: str = ""
    matches: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    exclude_matches: list[str] = field(default_factory=list)
    run_at: str = DEFAULT_RUN_AT
    noframes: bool = False
    requires: list[str] = field(default_factory=list)
    grants: list[str] = field(default_factory=list)
    capability: str | None = None
    extra: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Compiled once here; the runtime only runs `new RegExp(source, flags)`.
    include_res: list[JsRegex] = field(default_factory=list)
    exclude_res: list[JsRegex] = field(default_factory=list)

    @property
    def runs_anywhere(self) -> bool:
        return bool(self.include_res)

    @property
    def unsupported_grants(self) -> list[str]:
        return [g for g in self.grants if g not in SUPPORTED_GRANTS]

    def runtime_dict(self) -> dict[str, Any]:
        """The manifest as the page runtime sees it (embedded in the compiled script)."""
        return {
            "id": self.id,
            "name": self.name,
            "namespace": self.namespace,
            "version": self.version,
            "description": self.description,
            "runAt": self.run_at,
            "noframes": self.noframes,
            "grants": [g for g in self.grants if g in SUPPORTED_GRANTS],
            "matches": list(self.matches),
            "includes": list(self.includes),
            "excludes": list(self.excludes) + list(self.exclude_matches),
            "include": [r.to_dict() for r in self.include_res],
            "exclude": [r.to_dict() for r in self.exclude_res],
        }

    def to_dict(self) -> dict[str, Any]:
        """The manifest for APIs and `--json` output."""
        return {
            "id": self.id,
            "name": self.name,
            "namespace": self.namespace,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "icon": self.icon,
            "homepage": self.homepage,
            "matches": list(self.matches),
            "includes": list(self.includes),
            "excludes": list(self.excludes),
            "exclude_matches": list(self.exclude_matches),
            "run_at": self.run_at,
            "noframes": self.noframes,
            "requires": list(self.requires),
            "grants": list(self.grants),
            "unsupported_grants": self.unsupported_grants,
            "capability": self.capability,
            "extra": dict(self.extra),
            "warnings": list(self.warnings),
            # The exact rule the page runtime evaluates, so a host UI ("which Lenses run on
            # this page?") answers with the same RegExps instead of re-implementing patterns.
            "include_res": [r.to_dict() for r in self.include_res],
            "exclude_res": [r.to_dict() for r in self.exclude_res],
        }


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48].strip("-") or "lens"


def derive_id(name: str, namespace: str) -> str:
    """A stable id from the identity Tampermonkey uses (namespace + name): re-installing
    the same script UPDATES it instead of adding a duplicate."""
    base = slugify(name)
    if not namespace:
        return base
    digest = hashlib.sha1(namespace.encode("utf-8")).hexdigest()[:6]
    return f"{base}-{digest}"


def split_header(source: str) -> tuple[str, int] | None:
    """Return the header block's inner text and the offset where the header ends."""
    m = _HEADER_RE.search(source)
    if not m:
        return None
    return m.group(1), m.end()


def parse_manifest(source: str) -> LensManifest:
    """Parse a Lens / userscript source into a manifest. Raises :class:`LensFormatError`."""
    if not isinstance(source, str) or not source.strip():
        raise LensFormatError("the Lens source is empty")
    found = split_header(source)
    if found is None:
        raise LensFormatError(
            "no ==UserScript== header found — a Lens starts with "
            "'// ==UserScript==' … '// ==/UserScript=='"
        )
    block, _ = found
    keys: dict[str, list[str]] = {}
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue  # free-text comment lines inside the header are legal
        key, value = m.group(1), (m.group(2) or "").strip()
        keys.setdefault(key, []).append(value)

    def first(key: str) -> str:
        values = keys.get(key) or [""]
        return values[0]

    name = first("name")
    if not name:
        raise LensFormatError("the header has no @name")
    namespace = first("namespace")
    warnings: list[str] = []

    lens_id = first("navig-id").lower()
    if lens_id:
        if not _ID_RE.match(lens_id):
            raise LensFormatError(
                f"@navig-id {lens_id!r} must be lowercase letters, digits, '.', '_' or '-' "
                "(max 64)"
            )
    else:
        lens_id = derive_id(name, namespace)

    run_at = first("run-at") or DEFAULT_RUN_AT
    if run_at not in RUN_AT:
        warnings.append(f"@run-at {run_at!r} is not supported — running at {DEFAULT_RUN_AT}")
        run_at = DEFAULT_RUN_AT

    manifest = LensManifest(
        id=lens_id,
        name=name,
        namespace=namespace,
        version=first("version") or "0.0.0",
        description=first("description"),
        author=first("author"),
        icon=first("icon") or first("iconURL"),
        homepage=first("homepage") or first("homepageURL"),
        matches=[v for v in keys.get("match", []) if v],
        includes=[v for v in keys.get("include", []) if v],
        excludes=[v for v in keys.get("exclude", []) if v],
        exclude_matches=[v for v in keys.get("exclude-match", []) if v],
        run_at=run_at,
        noframes="noframes" in keys,
        requires=[v for v in keys.get("require", []) if v],
        grants=[v for v in keys.get("grant", []) if v],
        capability=first("navig-capability") or None,
        extra={k: v for k, v in keys.items() if k not in _KNOWN_KEYS},
        warnings=warnings,
    )

    try:
        manifest.include_res = [match_pattern_to_regex(p) for p in manifest.matches] + [
            include_to_regex(p) for p in manifest.includes
        ]
        manifest.exclude_res = [include_to_regex(p) for p in manifest.excludes] + [
            match_pattern_to_regex(p) for p in manifest.exclude_matches
        ]
    except PatternError as exc:
        raise LensFormatError(str(exc)) from exc

    if not manifest.include_res:
        # Deliberate: a Lens with no @match runs NOWHERE rather than everywhere, so a
        # forgotten header line (or an agent-written Lens) can't silently touch every site.
        manifest.warnings.append(
            "no @match or @include — this Lens runs nowhere. "
            "Add '// @match *://*/*' to run it on every site."
        )
    for grant in manifest.unsupported_grants:
        manifest.warnings.append(
            f"@grant {grant} is not supported yet — the script sees it as undefined"
        )
    for url in manifest.requires:
        if not url.lower().startswith("https://"):
            raise LensFormatError(f"@require {url!r} must be an https:// URL")
    if "resource" in keys:
        manifest.warnings.append("@resource is not supported yet — GM_getResourceText is undefined")
    return manifest
