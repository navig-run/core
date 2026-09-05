"""Local device-sensor monitors that feed the Privacy notification category.

Each monitor is an opt-in background producer: it watches a local signal (webcam,
later mic/screen/USB) and calls ``notify.dispatch`` on a transition, so the event
lands in the deck + every channel enabled for its type in Settings → Notifications.
"""

from __future__ import annotations

# Backward-compatible re-export. ``all_channels_failed`` now lives in
# ``navig.notify.delivery``: producers need it too, and keeping it here meant a
# producer had to import the *monitors* package to ask "did this get delivered?".
# Import it from navig.notify.delivery in new code.
from navig.notify.delivery import all_channels_failed

__all__ = ["all_channels_failed"]
