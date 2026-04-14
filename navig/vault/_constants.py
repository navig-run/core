"""Zero-dependency constants shared across vault modules.

Kept in a separate leaf to avoid circular imports (vault/core.py imports from
vault/session.py, so neither of those can import from the other at module level).
"""

from __future__ import annotations

# Idle-timeout for an unlocked vault session in seconds (30 minutes).
_DEFAULT_TTL: int = 1800
