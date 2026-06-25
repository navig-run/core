"""
navig.inbox.extract_hook — bridge the universal extractor into the inbox pipeline.

The classifier is markdown-only; this module turns any file into classifiable text
at the pipeline's call-sites. Two integration surfaces:

* :func:`content_for_classify` — the direct helper the System-A call-sites
  (deck routes ``_scan``/``_route_file``/``process_all``, the watcher) use in place
  of a raw ``read_text``. ``full=False`` is a cheap pass (filename + kind only, no
  OCR/STT) for responsive list scans; ``full=True`` runs the real (cached)
  extraction at route time.
* :func:`register_default_extract_hook` — registers a ``before_classify`` hook on
  the global :class:`~navig.inbox.hooks.HookSystem` so any hook-firing pipeline
  (System B, skills) also becomes binary-aware.

All extraction is content-addressed-cached so repeat scans/routes are instant.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.inbox.extract_hook")

_TEXT_FAST_PATH = {".md", ".markdown", ".txt", ".rst"}

_cache: Any | None = None
_cache_failed = False


def _shared_cache() -> Any | None:
    """Lazily build a content-addressed cache for extraction results."""
    global _cache, _cache_failed
    if _cache is not None or _cache_failed:
        return _cache
    try:
        from navig.gateway.channels.media_engine.media_cache import MediaCache

        _cache = MediaCache(namespace="inbox_extract")
    except Exception as exc:  # noqa: BLE001 — cache is an optimization, never required
        logger.debug("inbox extract cache unavailable: %s", exc)
        _cache_failed = True
    return _cache


def content_for_classify(path: Path, *, full: bool = True) -> str:
    """Return classifiable text for *path*.

    Plain-text/markdown is read directly. For binaries, ``full=True`` runs the
    cached universal extractor and returns the normalized markdown (frontmatter +
    extracted body); ``full=False`` returns a cheap filename/kind stub so live list
    scans stay instant.
    """
    try:
        suffix = path.suffix.lower()
        if suffix in _TEXT_FAST_PATH:
            return path.read_text(encoding="utf-8", errors="replace")

        from navig.inbox.extract import _kind_for, extract, to_markdown

        kind = _kind_for(path)
        if kind == "text":
            return path.read_text(encoding="utf-8", errors="replace")

        if not full:
            stem = path.stem.replace("-", " ").replace("_", " ").strip()
            return f"# {stem}\n\n({kind} file: {path.name})\n"

        res = extract(path, cache=_shared_cache())
        return to_markdown(res, source_label=path.name)
    except Exception as exc:  # noqa: BLE001 — must never break classification
        logger.debug("content_for_classify(%s) failed: %s", path, exc)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return f"# {path.name}\n"


def extract_to_markdown(path: Path) -> tuple[str, Any]:
    """Full cached extraction → (normalized markdown, ExtractResult). For routing."""
    from navig.inbox.extract import extract, to_markdown

    res = extract(path, cache=_shared_cache())
    return to_markdown(res, source_label=path.name), res


# ── before_classify hook (for hook-firing pipelines) ──────────────────────────


def _extract_hook(event: Any) -> Any:
    """A ``before_classify`` hook: replace binary/empty content with extracted text."""
    try:
        src = getattr(event, "source_path", "") or ""
        if getattr(event, "source_type", "file") != "file" or not src:
            return event
        p = Path(src)
        if p.suffix.lower() in _TEXT_FAST_PATH:
            return event
        from navig.inbox.extract import _kind_for

        if _kind_for(p) == "text":
            return event
        md, res = extract_to_markdown(p)
        event.content = md
        meta = getattr(event, "metadata", None)
        if isinstance(meta, dict):
            meta["_extract"] = {
                "kind": res.kind,
                "extracted_by": res.extracted_by,
                "content_hash": res.content_hash,
                "errors": res.errors,
            }
    except Exception as exc:  # noqa: BLE001
        logger.debug("extract hook skipped for %s: %s", getattr(event, "source_path", "?"), exc)
    return event


_registered = False


def register_default_extract_hook(hooks: Any | None = None) -> None:
    """Register the extraction ``before_classify`` hook on the global HookSystem (idempotent)."""
    global _registered
    if _registered:
        return
    try:
        from navig.inbox.hooks import get_hooks

        (hooks or get_hooks()).register("before_classify", _extract_hook)
        _registered = True
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not register extract hook: %s", exc)
