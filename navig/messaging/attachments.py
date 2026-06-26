"""Shared attachment-bytes resolution for messaging adapters.

An attachment descriptor is ``{path|url|data, kind, filename, mime, caption?}``:
- ``data``  — raw ``bytes`` or a base64-encoded ``str``
- ``path``  — a local filesystem path (e.g. an uploaded Studio media file)
- ``url``   — an HTTP(S) URL fetched via the supplied aiohttp session

Returns the bytes, or ``None`` when nothing is resolvable.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def attachment_bytes(att: dict[str, Any], session: Any = None) -> bytes | None:
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
    if url and session is not None:
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not fetch attachment url %s: %s", url, exc)
    return None
