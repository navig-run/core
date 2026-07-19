"""Signer-shape cache — remember the signed endpoint a browser tier learned, per domain.

TikTok's comment endpoint is signed (``msToken``/``X-Bogus``) and the signing rotates, so a
pure-HTTP reversed signer rots fast. The durable design (Stage 3) rides the browser's own
signing; this cache makes that *self-healing*: when the browser tier intercepts the signed
request, we record its **shape** — endpoint path, query-param names, header names, method —
keyed by domain with a TTL. The orchestrator uses a live template as a signal that the fast
path recently worked and to know exactly which endpoint to target; when it expires or blocks
again, the browser re-derives it.

SECURITY: only the *shape* is stored — NEVER the signed values, cookies, ``msToken`` or
``X-Bogus`` (those are secrets). So the cache file holds nothing sensitive.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["SignerTemplate", "SignerCache", "shape_from_request", "get_cache"]

_DEFAULT_TTL = 6 * 3600  # 6h — a learned shape is a hint, re-derived on expiry/block


@dataclass
class SignerTemplate:
    domain: str
    path: str
    method: str = "GET"
    param_names: list[str] = field(default_factory=list)
    header_names: list[str] = field(default_factory=list)
    learned_at: float = 0.0
    ttl: float = _DEFAULT_TTL
    success_count: int = 0

    def is_fresh(self, now: float) -> bool:
        return (self.learned_at + self.ttl) > now


# Header/param names that are pure secrets/noise — never worth recording even as names is
# fine, but we keep them so the shape is faithful; values are already excluded upstream.


def shape_from_request(domain: str, request: dict | None, *,
                       ttl: float = _DEFAULT_TTL,
                       clock=time.time) -> SignerTemplate | None:
    """Build a SignerTemplate from an intercepted request shape (names only, no values)."""
    if not request:
        return None
    url = request.get("url") or ""
    parts = urlsplit(url)
    if not parts.path:
        return None
    # Extract param NAMES only (values may carry msToken/X-Bogus — excluded).
    param_names = sorted({
        kv.split("=", 1)[0] for kv in parts.query.split("&") if kv
    })
    return SignerTemplate(
        domain=domain,
        path=parts.path,
        method=(request.get("method") or "GET").upper(),
        param_names=param_names,
        header_names=list(request.get("header_names") or []),
        learned_at=clock(),
        ttl=ttl,
        success_count=1,
    )


class SignerCache:
    """Per-domain learned-shape store, persisted as JSON (no secrets)."""

    def __init__(self, path: Path | None = None, *, clock=time.time):
        self._path = path
        self._clock = clock
        self._data: dict[str, dict] = {}
        self._loaded = False

    # ── persistence ──────────────────────────────────────────────────────────
    def _default_path(self) -> Path:
        from navig.platform.paths import config_dir  # noqa: PLC0415

        return config_dir() / "signer-cache.json"

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        path = self._path or self._default_path()
        self._path = path
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
        except Exception:  # noqa: BLE001 — corrupt cache is non-fatal, just re-learn
            self._data = {}
        self._loaded = True

    def _save(self) -> None:
        path = self._path or self._default_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001 — best-effort; a lost cache just means re-learning
            pass

    # ── API ──────────────────────────────────────────────────────────────────
    def get(self, domain: str) -> SignerTemplate | None:
        """Return a FRESH template for *domain*, or None (expired templates are dropped)."""
        self._ensure_loaded()
        rec = self._data.get(domain)
        if not rec:
            return None
        try:
            tpl = SignerTemplate(**rec)
        except TypeError:
            return None
        if not tpl.is_fresh(self._clock()):
            return None
        return tpl

    def put(self, template: SignerTemplate) -> None:
        """Record/refresh a learned template (bumps success_count if same endpoint)."""
        self._ensure_loaded()
        prev = self._data.get(template.domain)
        if prev and prev.get("path") == template.path:
            template.success_count = int(prev.get("success_count", 0)) + 1
        self._data[template.domain] = asdict(template)
        self._save()

    def invalidate(self, domain: str) -> None:
        """Drop a domain's template (it blocked → force a re-heal from the browser)."""
        self._ensure_loaded()
        if self._data.pop(domain, None) is not None:
            self._save()


_cache: SignerCache | None = None


def get_cache() -> SignerCache:
    """Process-wide signer cache (lazily loaded from ``config_dir()/signer-cache.json``)."""
    global _cache
    if _cache is None:
        _cache = SignerCache()
    return _cache
