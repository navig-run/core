"""Multi-backend screenshot capture for Windows desktop automation.

Backend priority chain (lowest number = highest priority):
  1. ``dxcam`` (DXGI, priority 10) — fastest; single-monitor region capture.
  2. ``mss``   (cross-process, priority 20) — reliable multi-monitor support.
  3. ``PIL.ImageGrab`` (priority 100) — always available; slowest.

The active backend is selected once per process via
``get_screenshot_backend()``, which reads the ``NAVIG_SCREENSHOT_BACKEND``
environment variable (default ``"auto"``).

Ported and hardened from CursorTouch/Windows-MCP (MIT licence)
desktop/screenshot.py.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image as PILImage

# ─── Constants  ───────────────────────────────────────────────────────────────

# Environment variable that selects the screenshot backend.
_BACKEND_ENV_VAR = "NAVIG_SCREENSHOT_BACKEND"

# Registry of all backend subclasses, keyed by their declared name.
_BACKEND_REGISTRY: dict[str, type[_ScreenshotBackend]] = {}


# ─── Backend base & registry ──────────────────────────────────────────────────


class _ScreenshotBackend:
    """Abstract base class for screenshot backends.

    Subclasses are auto-registered via ``__init_subclass__``.  Each subclass
    must declare a ``name`` (unique identifier) and ``priority`` (lower =
    preferred).
    """

    name: str = ""
    priority: int = 999

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls.name:
            _BACKEND_REGISTRY[cls.name] = cls

    def is_available(self) -> bool:
        """Return True if this backend's dependencies are importable."""
        raise NotImplementedError

    def capture_region(
        self,
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> "PILImage.Image":
        """Capture and return a PIL Image of the given screen region."""
        raise NotImplementedError


# ─── dxcam backend ────────────────────────────────────────────────────────────


class _DxcamBackend(_ScreenshotBackend):
    """DXGI-based capture via the ``dxcam`` library (fastest, Windows only)."""

    name = "dxcam"
    priority = 10

    @staticmethod
    @lru_cache(maxsize=1)
    def is_available() -> bool:
        if sys.platform != "win32":
            return False
        try:
            import dxcam  # type: ignore[import]  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def capture_region(self, left: int, top: int, right: int, bottom: int) -> "PILImage.Image":
        import dxcam  # type: ignore[import]
        from PIL import Image  # noqa: PLC0415

        camera = dxcam.create()
        try:
            frame = camera.grab(region=(left, top, right, bottom))
            if frame is None:
                raise RuntimeError("dxcam returned None frame")
            return Image.fromarray(frame)
        finally:
            del camera


# ─── mss backend ──────────────────────────────────────────────────────────────


class _MssBackend(_ScreenshotBackend):
    """Cross-platform capture via the ``mss`` library."""

    name = "mss"
    priority = 20

    @staticmethod
    @lru_cache(maxsize=1)
    def is_available() -> bool:
        try:
            import mss  # type: ignore[import]  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def capture_region(self, left: int, top: int, right: int, bottom: int) -> "PILImage.Image":
        import mss  # type: ignore[import]
        from PIL import Image  # noqa: PLC0415

        with mss.mss() as sct:
            monitor = {"top": top, "left": left, "width": right - left, "height": bottom - top}
            shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


# ─── Pillow / ImageGrab backend ───────────────────────────────────────────────


class _PillowBackend(_ScreenshotBackend):
    """Fallback capture via ``PIL.ImageGrab`` (always available)."""

    name = "pillow"
    priority = 100

    @staticmethod
    @lru_cache(maxsize=1)
    def is_available() -> bool:
        try:
            from PIL import ImageGrab  # type: ignore[import]  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def capture_region(self, left: int, top: int, right: int, bottom: int) -> "PILImage.Image":
        from PIL import ImageGrab  # type: ignore[import]

        return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)


# ─── Backend selection ────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_screenshot_backend(name: str = "auto") -> _ScreenshotBackend:
    """Return the best available screenshot backend.

    Args:
        name:  ``"auto"`` (default) to pick by priority, or the explicit
               backend name (``"dxcam"``, ``"mss"``, ``"pillow"``).

    Raises:
        RuntimeError: When *name* is explicit but the backend is unavailable,
                      or when *name* is ``"auto"`` and no backend is available.
    """
    if name == "auto":
        candidates = sorted(_BACKEND_REGISTRY.values(), key=lambda b: b.priority)
        for backend_cls in candidates:
            instance = backend_cls()
            if instance.is_available():
                return instance
        raise RuntimeError("No screenshot backend available; install pillow.")

    if name not in _BACKEND_REGISTRY:
        available = ", ".join(sorted(_BACKEND_REGISTRY))
        raise ValueError(f"Unknown screenshot backend {name!r}; available: {available}")

    instance = _BACKEND_REGISTRY[name]()
    if not instance.is_available():
        raise RuntimeError(
            f"Screenshot backend {name!r} is not available. "
            "Install the required package and try again."
        )
    return instance


def _get_env_backend() -> _ScreenshotBackend:
    """Read ``NAVIG_SCREENSHOT_BACKEND`` and return the selected backend."""
    name = os.environ.get(_BACKEND_ENV_VAR, "auto").strip().lower()
    return get_screenshot_backend(name)


# ─── Public API ───────────────────────────────────────────────────────────────


def capture(
    left: int,
    top: int,
    right: int,
    bottom: int,
    backend: str | _ScreenshotBackend | None = None,
) -> tuple["PILImage.Image", str]:
    """Capture a screen region and return ``(image, backend_name_used)``.

    Args:
        left, top, right, bottom:  Screen coordinates in virtual-desktop space.
        backend:  Optional backend name or instance.  *None* uses the env-
                  configured backend (auto-detection chain).

    Returns:
        A 2-tuple ``(PIL.Image, str)`` where the string is the name of the
        backend that produced the image.
    """
    if isinstance(backend, str):
        be = get_screenshot_backend(backend)
    elif backend is None:
        be = _get_env_backend()
    else:
        be = backend

    img = be.capture_region(left, top, right, bottom)
    return img, be.name


def capture_full_screen(
    backend: str | _ScreenshotBackend | None = None,
) -> tuple["PILImage.Image", str]:
    """Capture the entire virtual desktop.

    Tries ``PIL.ImageGrab.grab(all_screens=True)`` geometry to determine
    bounds, then delegates to ``capture()``.
    """
    try:
        from PIL import ImageGrab  # type: ignore[import]

        screen = ImageGrab.grab(all_screens=True)
        w, h = screen.size
        return capture(0, 0, w, h, backend=backend)
    except Exception:  # noqa: BLE001
        # Reasonable fallback for single-monitor setups.
        return capture(0, 0, 1920, 1080, backend=backend)
