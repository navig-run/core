"""Multi-backend screenshot capture for desktop automation.

Backend priority chain (lowest number = highest priority):
  1. ``dxcam`` (DXGI, priority 10) — fastest; single-monitor region capture. Windows.
  2. ``mss``   (cross-process, priority 20) — reliable multi-monitor support.
  3. ``system`` (priority 30) — the OS command this SESSION has: ``screencapture`` on
     macOS, ``grim``/``gnome-screenshot``/``spectacle`` on Wayland, ``scrot``/``import``
     on X11 (see ``navig.platform.capture``). The only tier that works on Wayland.
  4. ``PIL.ImageGrab`` (priority 100) — needs an X11 display on Linux; slowest.

The active backend is selected once per process via
``get_screenshot_backend()``, which reads the ``NAVIG_SCREENSHOT_BACKEND``
environment variable (default ``"auto"``).
"""

from __future__ import annotations

import abc
import os
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from navig.platform import capture as platform_capture

if TYPE_CHECKING:
    from PIL import Image as PILImage

# ─── Constants  ───────────────────────────────────────────────────────────────

# Environment variable that selects the screenshot backend.
_BACKEND_ENV_VAR = "NAVIG_SCREENSHOT_BACKEND"

# Registry of all backend subclasses, keyed by their declared name.
_BACKEND_REGISTRY: dict[str, type[_ScreenshotBackend]] = {}


# ─── Backend base & registry ──────────────────────────────────────────────────


class _ScreenshotBackend(abc.ABC):
    """Abstract base class for screenshot backends.

    Subclasses are auto-registered via ``__init_subclass__``.  Each subclass
    must declare a ``name`` (unique identifier) and ``priority`` (lower =
    preferred), and implement both abstract methods below — the base is a real
    ``abc.ABC`` so a subclass that forgets one can't be instantiated (a clear
    ``TypeError`` at construction, not a late ``NotImplementedError`` on call),
    and the base itself can never be used as a (broken) backend.
    """

    name: str = ""
    priority: int = 999

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls.name:
            _BACKEND_REGISTRY[cls.name] = cls

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return True if this backend's dependencies are importable."""
        raise NotImplementedError

    @abc.abstractmethod
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


# ─── system-command backend (macOS + Linux) ──────────────────────────────────


class _SystemBackend(_ScreenshotBackend):
    """Capture through the OS tool this session has, chosen by the platform seam.

    This is the tier that works on Wayland: ``mss`` and Pillow both speak X11, and a
    Wayland compositor does not serve one (XWayland may exist but the OS capture path
    does not need it). ``navig.platform.capture`` owns the ordering and the messages.
    """

    name = "system"
    priority = 30

    @staticmethod
    @lru_cache(maxsize=1)
    def is_available() -> bool:
        if platform_capture.session() in ("windows", "headless"):
            return False
        return platform_capture.choose() is not None

    def capture_region(self, left: int, top: int, right: int, bottom: int) -> "PILImage.Image":
        from PIL import Image  # noqa: PLC0415

        backend = platform_capture.choose()
        if backend is None:  # availability is re-checked, so this is a race, not a norm
            raise RuntimeError(platform_capture.install_hint())
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            output = Path(handle.name)
        try:
            argv = backend.argv(str(output), region=(left, top, right, bottom))
            if argv is None:
                raise RuntimeError(f"{backend.name} is not a command-line capture backend")
            result = subprocess.run(argv, capture_output=True)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace")
                raise RuntimeError(f"{backend.name} failed: {detail.strip() or result.returncode}")
            return Image.open(output).convert("RGB")
        finally:
            output.unlink(missing_ok=True)


# ─── Pillow / ImageGrab backend ───────────────────────────────────────────────


class _PillowBackend(_ScreenshotBackend):
    """Fallback capture via ``PIL.ImageGrab`` (needs an X11 display on Linux)."""

    name = "pillow"
    priority = 100

    @staticmethod
    @lru_cache(maxsize=1)
    def is_available() -> bool:
        if platform_capture.session() == "wayland":
            # ImageGrab's Linux implementation is X11-only: under pure Wayland it raises
            # OSError("X connection failed") and the caller gets a crash instead of a
            # capture. The `system` tier covers Wayland.
            return False
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


def _full_screen_size() -> tuple[int, int] | None:
    """The virtual desktop's pixel size, or None when nothing on this host knows it."""
    try:
        import mss  # type: ignore[import]

        with mss.mss() as sct:
            monitor = sct.monitors[0]
            return int(monitor["width"]), int(monitor["height"])
    except Exception:  # noqa: BLE001 — absence is the answer, not a crash
        pass
    try:
        from PIL import ImageGrab  # type: ignore[import]

        return ImageGrab.grab(all_screens=True).size
    except Exception:  # noqa: BLE001
        return None


def _capture_native_full() -> tuple["PILImage.Image", str]:
    """Full-screen capture from the OS tool itself — no geometry needed.

    Used when nothing can report the desktop size (a Wayland session without mss):
    guessing 1920×1080, which this function used to do, produced a silently cropped
    image on every other resolution.
    """
    from PIL import Image  # noqa: PLC0415

    backend = platform_capture.choose()
    if backend is None:
        raise RuntimeError(platform_capture.install_hint())
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        output = Path(handle.name)
    try:
        argv = backend.argv(str(output))
        if argv is None:
            raise RuntimeError(f"{backend.name} is not a command-line capture backend")
        result = subprocess.run(argv, capture_output=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace")
            raise RuntimeError(f"{backend.name} failed: {detail.strip() or result.returncode}")
        return Image.open(output).convert("RGB"), backend.name
    finally:
        output.unlink(missing_ok=True)


def capture_full_screen(
    backend: str | _ScreenshotBackend | None = None,
) -> tuple["PILImage.Image", str]:
    """Capture the entire virtual desktop.

    The bounds come from ``mss`` when it is installed (the only library that reports the
    whole virtual desktop), then Pillow. When neither knows — which is the normal case on
    Wayland — the OS tool captures the full screen itself instead of this module guessing
    a size.
    """
    size = _full_screen_size()
    if size is not None:
        return capture(0, 0, size[0], size[1], backend=backend)
    return _capture_native_full()
