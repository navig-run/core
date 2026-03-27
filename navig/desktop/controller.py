"""Desktop automation controller using pyautogui."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# Lazy import to avoid issues on headless systems
_pyautogui = None
_PIL = None


def _init_pyautogui():
    """Lazy import of pyautogui."""
    global _pyautogui, _PIL
    if _pyautogui is None:
        try:
            import pyautogui

            _pyautogui = pyautogui

            # Configure failsafe
            _pyautogui.FAILSAFE = True
            _pyautogui.PAUSE = 0.1
        except ImportError as _exc:
            raise ImportError(
                "pyautogui not installed. Install with: pip install pyautogui"
            ) from _exc

    if _PIL is None:
        try:
            from PIL import Image

            _PIL = Image
        except ImportError:
            pass  # PIL is optional for some features

    return _pyautogui


@dataclass
class DesktopConfig:
    """Desktop automation configuration."""

    enabled: bool = False  # Disabled by default for security
    screenshot_dir: str = "~/.navig/screenshots"
    failsafe: bool = True
    default_pause: float = 0.1

    @classmethod
    def from_config(cls, config: dict) -> "DesktopConfig":
        """Load from navig config dict."""
        desktop_cfg = config.get("desktop", {})

        return cls(
            enabled=desktop_cfg.get("enabled", False),
            screenshot_dir=desktop_cfg.get("screenshot_dir", "~/.navig/screenshots"),
            failsafe=desktop_cfg.get("failsafe", True),
            default_pause=desktop_cfg.get("default_pause", 0.1),
        )


class DesktopController:
    """
    Desktop automation using pyautogui.

    WARNING: Desktop automation can perform any action a user can.
    Enable only when needed and with appropriate approval flows.

    Example:
        controller = DesktopController(DesktopConfig(enabled=True))

        # Take screenshot
        path = controller.screenshot()

        # Move and click
        controller.move_to(100, 200)
        controller.click(100, 200)

        # Type text
        controller.type_text("Hello World")

        # Keyboard shortcuts
        controller.hotkey('ctrl', 'c')
    """

    def __init__(self, config: DesktopConfig | None = None):
        self.config = config or DesktopConfig()
        self._initialized = False

        # Ensure directories
        self._screenshot_dir = Path(self.config.screenshot_dir).expanduser()
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_initialized(self):
        """Ensure pyautogui is initialized."""
        if not self.config.enabled:
            raise RuntimeError(
                "Desktop automation is disabled. Enable in config with 'desktop.enabled: true'"
            )

        if not self._initialized:
            pyautogui = _init_pyautogui()
            pyautogui.FAILSAFE = self.config.failsafe
            pyautogui.PAUSE = self.config.default_pause
            self._initialized = True

    def screenshot(
        self,
        region: tuple[int, int, int, int] | None = None,
        name: str | None = None,
    ) -> str:
        """
        Take screenshot of screen or region.

        Args:
            region: Optional (x, y, width, height) tuple
            name: Optional filename

        Returns:
            Path to saved screenshot
        """
        self._ensure_initialized()

        if not name:
            name = f"desktop_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

        if not name.endswith(".png"):
            name += ".png"

        path = self._screenshot_dir / name

        img = _pyautogui.screenshot(region=region)
        img.save(str(path))

        logger.info(f"Desktop screenshot saved: {path}")
        return str(path)

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
    ) -> bool:
        """
        Click at screen coordinates.

        Args:
            x, y: Screen coordinates
            button: 'left', 'right', or 'middle'
            clicks: Number of clicks

        Returns:
            True on success
        """
        self._ensure_initialized()
        _pyautogui.click(x, y, button=button, clicks=clicks)
        return True

    def double_click(self, x: int, y: int) -> bool:
        """Double-click at coordinates."""
        self._ensure_initialized()
        _pyautogui.doubleClick(x, y)
        return True

    def right_click(self, x: int, y: int) -> bool:
        """Right-click at coordinates."""
        self._ensure_initialized()
        _pyautogui.rightClick(x, y)
        return True

    def move_to(self, x: int, y: int, duration: float = 0.25) -> bool:
        """
        Move mouse to coordinates.

        Args:
            x, y: Target coordinates
            duration: Movement duration in seconds
        """
        self._ensure_initialized()
        _pyautogui.moveTo(x, y, duration=duration)
        return True

    def move_relative(self, dx: int, dy: int, duration: float = 0.25) -> bool:
        """Move mouse relative to current position."""
        self._ensure_initialized()
        _pyautogui.move(dx, dy, duration=duration)
        return True

    def drag_to(
        self,
        x: int,
        y: int,
        duration: float = 0.5,
        button: str = "left",
    ) -> bool:
        """Drag mouse to coordinates."""
        self._ensure_initialized()
        _pyautogui.drag(x, y, duration=duration, button=button)
        return True

    def type_text(self, text: str, interval: float = 0.05) -> bool:
        """
        Type text character by character.

        Args:
            text: Text to type
            interval: Delay between keystrokes
        """
        self._ensure_initialized()
        _pyautogui.typewrite(text, interval=interval)
        return True

    def write(self, text: str) -> bool:
        """
        Type text using clipboard (faster, supports unicode).

        Uses pyperclip if available, falls back to typewrite.
        """
        self._ensure_initialized()

        try:
            import pyperclip

            pyperclip.copy(text)
            _pyautogui.hotkey("ctrl", "v")
            return True
        except ImportError:
            return self.type_text(text)

    def hotkey(self, *keys: str) -> bool:
        """
        Press key combination.

        Args:
            keys: Key names (e.g., 'ctrl', 'c')

        Example:
            controller.hotkey('ctrl', 'c')  # Copy
            controller.hotkey('alt', 'tab')  # Switch window
        """
        self._ensure_initialized()
        _pyautogui.hotkey(*keys)
        return True

    def press(self, key: str, presses: int = 1, interval: float = 0.1) -> bool:
        """
        Press a single key.

        Args:
            key: Key name (e.g., 'enter', 'escape', 'tab')
            presses: Number of presses
            interval: Delay between presses
        """
        self._ensure_initialized()
        _pyautogui.press(key, presses=presses, interval=interval)
        return True

    def key_down(self, key: str) -> bool:
        """Press and hold a key."""
        self._ensure_initialized()
        _pyautogui.keyDown(key)
        return True

    def key_up(self, key: str) -> bool:
        """Release a key."""
        self._ensure_initialized()
        _pyautogui.keyUp(key)
        return True

    def scroll(
        self,
        clicks: int,
        x: int | None = None,
        y: int | None = None,
    ) -> bool:
        """
        Scroll mouse wheel.

        Args:
            clicks: Number of scroll clicks (positive = up, negative = down)
            x, y: Optional position to scroll at
        """
        self._ensure_initialized()
        _pyautogui.scroll(clicks, x=x, y=y)
        return True

    def get_screen_size(self) -> tuple[int, int]:
        """Get screen dimensions (width, height)."""
        self._ensure_initialized()
        return _pyautogui.size()

    def get_mouse_position(self) -> tuple[int, int]:
        """Get current mouse position (x, y)."""
        self._ensure_initialized()
        return _pyautogui.position()

    def locate_on_screen(
        self,
        image_path: str,
        confidence: float = 0.9,
        grayscale: bool = False,
    ) -> tuple[int, int, int, int] | None:
        """
        Find image on screen.

        Args:
            image_path: Path to image file
            confidence: Match confidence (0-1)
            grayscale: Search in grayscale (faster)

        Returns:
            (x, y, width, height) or None if not found

        Note: Requires opencv-python for confidence matching.
        """
        self._ensure_initialized()

        try:
            location = _pyautogui.locateOnScreen(
                image_path,
                confidence=confidence,
                grayscale=grayscale,
            )
            if location:
                return (location.left, location.top, location.width, location.height)
        except Exception as e:
            logger.warning(f"Image locate failed: {e}")

        return None

    def locate_all_on_screen(
        self,
        image_path: str,
        confidence: float = 0.9,
    ) -> list[tuple[int, int, int, int]]:
        """Find all occurrences of image on screen."""
        self._ensure_initialized()

        try:
            locations = list(
                _pyautogui.locateAllOnScreen(
                    image_path,
                    confidence=confidence,
                )
            )
            return [(loc.left, loc.top, loc.width, loc.height) for loc in locations]
        except Exception as e:
            logger.warning(f"Image locate failed: {e}")

        return []

    def click_image(
        self,
        image_path: str,
        confidence: float = 0.9,
        button: str = "left",
    ) -> bool:
        """
        Find image on screen and click its center.

        Returns True if image found and clicked.
        """
        location = self.locate_on_screen(image_path, confidence)
        if location:
            x, y, w, h = location
            self.click(x + w // 2, y + h // 2, button=button)
            return True
        return False

    def wait_for_image(
        self,
        image_path: str,
        timeout: float = 10.0,
        confidence: float = 0.9,
        interval: float = 0.5,
    ) -> tuple[int, int, int, int] | None:
        """
        Wait for image to appear on screen.

        Args:
            image_path: Path to image file
            timeout: Maximum wait time in seconds
            confidence: Match confidence
            interval: Check interval in seconds

        Returns:
            (x, y, width, height) or None if timeout
        """
        import time

        start = time.time()
        while time.time() - start < timeout:
            location = self.locate_on_screen(image_path, confidence)
            if location:
                return location
            time.sleep(interval)

        return None

    def get_pixel_color(self, x: int, y: int) -> tuple[int, int, int]:
        """Get RGB color of pixel at coordinates."""
        self._ensure_initialized()
        return _pyautogui.pixel(x, y)

    def pixel_matches_color(
        self,
        x: int,
        y: int,
        expected_rgb: tuple[int, int, int],
        tolerance: int = 0,
    ) -> bool:
        """Check if pixel matches expected color."""
        self._ensure_initialized()
        return _pyautogui.pixelMatchesColor(x, y, expected_rgb, tolerance=tolerance)

    def alert(self, text: str, title: str = "NAVIG") -> None:
        """Show alert dialog."""
        self._ensure_initialized()
        _pyautogui.alert(text=text, title=title)

    def confirm(self, text: str, title: str = "NAVIG") -> bool:
        """Show confirmation dialog. Returns True if OK clicked."""
        self._ensure_initialized()
        result = _pyautogui.confirm(text=text, title=title)
        return result == "OK"

    def prompt(self, text: str, title: str = "NAVIG", default: str = "") -> str | None:
        """Show text input dialog. Returns None if cancelled."""
        self._ensure_initialized()
        return _pyautogui.prompt(text=text, title=title, default=default)
