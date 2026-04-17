"""NAVIG Blackbox Seal — mark a bundle as immutable.

A sealed bundle cannot be appended to.  The seal is represented by a
``SEALED`` marker file in the blackbox directory.  Primarily used for
incident investigation: seal the state at the time of the incident,
then export for analysis.
"""

from __future__ import annotations

from pathlib import Path

from navig.core.yaml_io import atomic_write_text

from .types import Bundle

__all__ = ["seal_bundle", "is_sealed", "unseal"]

_SEAL_MARKER = "SEALED"


def seal_bundle(
    bundle: Bundle,
    blackbox_dir: Path | None = None,
) -> Bundle:
    """Mark a bundle (and the blackbox dir) as sealed.

    Writes a ``SEALED`` marker file to *blackbox_dir* so subsequent
    recording calls know to reject appends (recorders check :func:`is_sealed`).

    Returns
    -------
    Bundle
        The same bundle with ``sealed=True``.
    """
    if blackbox_dir is None:
        from navig.platform.paths import blackbox_dir as _bbdir

        blackbox_dir = _bbdir()

    blackbox_dir.mkdir(parents=True, exist_ok=True)
    marker = blackbox_dir / _SEAL_MARKER
    atomic_write_text(marker, bundle.created_at.isoformat())

    bundle.sealed = True
    return bundle


def is_sealed(blackbox_dir: Path | None = None) -> bool:
    """Return True if the blackbox directory is currently sealed."""
    if blackbox_dir is None:
        from navig.platform.paths import blackbox_dir as _bbdir

        blackbox_dir = _bbdir()
    return (blackbox_dir / _SEAL_MARKER).exists()


def unseal(blackbox_dir: Path | None = None) -> bool:
    """Remove the ``SEALED`` marker, allowing recording to resume.

    Returns True if the marker was present and removed.
    """
    if blackbox_dir is None:
        from navig.platform.paths import blackbox_dir as _bbdir

        blackbox_dir = _bbdir()
    marker = blackbox_dir / _SEAL_MARKER
    if marker.exists():
        marker.unlink()
        return True
    return False
