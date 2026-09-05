"""Shared attachment-bytes resolution for messaging adapters.

An attachment descriptor is ``{path|url|data, kind, filename, mime, caption?}``:
- ``data``  — raw ``bytes`` or a base64-encoded ``str``
- ``path``  — a local filesystem path (e.g. an uploaded Studio media file)
- ``url``   — an HTTP(S) URL fetched through the SSRF-guarded ``safe_fetch``

Returns the bytes, or ``None`` when nothing is resolvable.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def attachment_bytes(att: dict[str, Any], session: Any = None) -> bytes | None:
    # ``session`` is accepted for backward compatibility but intentionally NOT used
    # to fetch URLs: a caller's aiohttp session would follow redirects without the
    # SSRF policy (a legit URL 302-ing to 169.254.169.254 or the local daemon), so
    # URL media is always fetched through the guarded ``safe_fetch`` below.
    _ = session
    data = att.get("data")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        import base64

        try:
            return base64.b64decode(data)
        except Exception:  # noqa: BLE001
            pass

    path = att.get("path")
    if path:
        try:
            from pathlib import Path

            return Path(path).read_bytes()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not read attachment path %s: %s", path, exc)
            return None

    url = att.get("url")
    if url:
        # An attachment URL is untrusted (agent- / Studio- / user-supplied), so it
        # goes through the SSRF guard: safe_fetch validates the initial URL AND
        # re-checks every redirect hop, and it needs no caller session — the old
        # ``session is None`` path silently dropped URL media entirely.
        from navig.net.ssrf import SsrfBlockedError, policy_from_config, safe_fetch

        try:
            resp = await safe_fetch(url, policy_from_config())
        except SsrfBlockedError as exc:
            logger.warning("attachment url blocked by SSRF policy: %s (%s)", url, exc)
            return None
        except ValueError as exc:
            # malformed URL, non-http scheme, or redirect chain too long
            logger.warning("attachment url rejected: %s (%s)", url, exc)
            return None
        except Exception as exc:  # noqa: BLE001 — network error, missing httpx, etc.
            logger.warning("could not fetch attachment url %s: %s", url, exc)
            return None
        if resp.status_code == 200:
            return resp.content
    return None
