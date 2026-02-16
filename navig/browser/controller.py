"""Browser automation controller using Playwright."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# Lazy imports to avoid issues when playwright not installed
_playwright = None
_Browser = None
_Page = None


def _get_playwright():
    """Lazy import of playwright."""
    global _playwright
    if _playwright is None:
        try:
            from playwright.async_api import async_playwright
            _playwright = async_playwright
        except ImportError:
            raise ImportError(
                "Playwright not installed. Install with: pip install playwright && playwright install chromium"
            )
    return _playwright


@dataclass
class BrowserConfig:
    """Browser configuration."""
    enabled: bool = True
    headless: bool = True
    timeout_ms: int = 30000
    viewport_width: int = 1280
    viewport_height: int = 720
    user_data_dir: Optional[str] = None
    screenshot_dir: str = "~/.navig/screenshots"
    proxy: Optional[str] = None
    ignore_https_errors: bool = False
    
    # Security
    allowed_domains: List[str] = field(default_factory=list)  # Empty = allow all
    blocked_domains: List[str] = field(default_factory=list)
    
    @classmethod
    def from_config(cls, config: dict) -> 'BrowserConfig':
        """Load from navig config dict."""
        browser_cfg = config.get('browser', {})
        viewport = browser_cfg.get('viewport', {})
        
        return cls(
            enabled=browser_cfg.get('enabled', True),
            headless=browser_cfg.get('headless', True),
            timeout_ms=browser_cfg.get('timeout_seconds', 30) * 1000,
            viewport_width=viewport.get('width', 1280),
            viewport_height=viewport.get('height', 720),
            user_data_dir=browser_cfg.get('user_data_dir'),
            screenshot_dir=browser_cfg.get('screenshot_dir', '~/.navig/screenshots'),
            proxy=browser_cfg.get('proxy'),
            ignore_https_errors=browser_cfg.get('ignore_https_errors', False),
            allowed_domains=browser_cfg.get('allowed_domains', []),
            blocked_domains=browser_cfg.get('blocked_domains', []),
        )


class BrowserController:
    """
    Playwright-based browser automation controller.
    
    Provides high-level browser operations for autonomous agent use.
    
    Example:
        controller = BrowserController()
        await controller.start()
        
        result = await controller.navigate("https://example.com")
        print(result['title'])
        
        await controller.fill("#search", "query")
        await controller.click("#submit")
        
        path = await controller.screenshot()
        
        await controller.stop()
    """
    
    def __init__(self, config: Optional[BrowserConfig] = None):
        self.config = config or BrowserConfig()
        
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        
        # Ensure directories exist
        self._screenshot_dir = Path(self.config.screenshot_dir).expanduser()
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
    
    @property
    def is_running(self) -> bool:
        """Check if browser is running."""
        return self._page is not None
    
    async def start(self):
        """Start browser instance."""
        if self._browser:
            logger.warning("Browser already started")
            return
        
        if not self.config.enabled:
            raise RuntimeError("Browser automation is disabled in config")
        
        logger.info("Starting browser...")
        
        async_playwright = _get_playwright()
        self._playwright = await async_playwright().start()
        
        # Launch options
        launch_opts = {
            "headless": self.config.headless,
        }
        
        if self.config.proxy:
            launch_opts["proxy"] = {"server": self.config.proxy}
        
        # Context options
        context_opts = {
            "viewport": {
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            },
            "ignore_https_errors": self.config.ignore_https_errors,
        }
        
        # Use persistent context if user_data_dir specified
        if self.config.user_data_dir:
            user_data = Path(self.config.user_data_dir).expanduser()
            user_data.mkdir(parents=True, exist_ok=True)
            
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_path=str(user_data),
                **{**launch_opts, **context_opts}
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        else:
            self._browser = await self._playwright.chromium.launch(**launch_opts)
            self._context = await self._browser.new_context(**context_opts)
            self._page = await self._context.new_page()
        
        self._page.set_default_timeout(self.config.timeout_ms)
        
        logger.info("Browser started successfully")
    
    async def stop(self):
        """Stop browser instance."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        
        logger.info("Browser stopped")
    
    async def _ensure_started(self):
        """Ensure browser is started."""
        if not self._page:
            await self.start()
    
    def _check_domain_allowed(self, url: str) -> bool:
        """Check if URL domain is allowed."""
        from urllib.parse import urlparse
        
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        # Check blocked domains
        for blocked in self.config.blocked_domains:
            pattern = blocked.lower().replace('*', '')
            if pattern in domain:
                return False
        
        # Check allowed domains (if specified)
        if self.config.allowed_domains:
            for allowed in self.config.allowed_domains:
                pattern = allowed.lower().replace('*', '')
                if pattern in domain:
                    return True
            return False
        
        return True
    
    async def navigate(
        self,
        url: str,
        wait_until: str = "domcontentloaded"
    ) -> Dict[str, Any]:
        """
        Navigate to URL.
        
        Args:
            url: URL to navigate to
            wait_until: Wait condition (domcontentloaded, load, networkidle)
        
        Returns:
            Dict with page info (title, url, status)
        """
        await self._ensure_started()
        
        if not self._check_domain_allowed(url):
            raise ValueError(f"Domain not allowed: {url}")
        
        response = await self._page.goto(url, wait_until=wait_until)
        
        return {
            "url": self._page.url,
            "title": await self._page.title(),
            "status": response.status if response else None,
        }
    
    async def fill(self, selector: str, value: str) -> bool:
        """Fill a form field."""
        await self._ensure_started()
        await self._page.fill(selector, value)
        return True
    
    async def click(self, selector: str) -> bool:
        """Click an element."""
        await self._ensure_started()
        await self._page.click(selector)
        return True
    
    async def double_click(self, selector: str) -> bool:
        """Double-click an element."""
        await self._ensure_started()
        await self._page.dblclick(selector)
        return True
    
    async def type_text(self, selector: str, text: str, delay: int = 50) -> bool:
        """Type text into an element with delay between keystrokes."""
        await self._ensure_started()
        await self._page.type(selector, text, delay=delay)
        return True
    
    async def press(self, key: str) -> bool:
        """Press a keyboard key."""
        await self._ensure_started()
        await self._page.keyboard.press(key)
        return True
    
    async def screenshot(
        self,
        name: Optional[str] = None,
        full_page: bool = False,
        selector: Optional[str] = None
    ) -> str:
        """
        Take screenshot of current page.
        
        Args:
            name: Filename (auto-generated if not provided)
            full_page: Capture full scrollable page
            selector: Capture specific element only
        
        Returns:
            Path to saved screenshot
        """
        await self._ensure_started()
        
        if not name:
            name = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        
        if not name.endswith('.png'):
            name += '.png'
        
        path = self._screenshot_dir / name
        
        if selector:
            element = await self._page.query_selector(selector)
            if element:
                await element.screenshot(path=str(path))
            else:
                raise ValueError(f"Element not found: {selector}")
        else:
            await self._page.screenshot(path=str(path), full_page=full_page)
        
        logger.info(f"Screenshot saved: {path}")
        return str(path)
    
    async def get_content(self) -> str:
        """Get page HTML content."""
        await self._ensure_started()
        return await self._page.content()
    
    async def get_text(self, selector: Optional[str] = None) -> str:
        """Get text content of page or element."""
        await self._ensure_started()
        
        if selector:
            element = await self._page.query_selector(selector)
            if element:
                return await element.text_content() or ""
            return ""
        
        return await self._page.text_content("body") or ""
    
    async def get_attribute(self, selector: str, attribute: str) -> Optional[str]:
        """Get element attribute value."""
        await self._ensure_started()
        
        element = await self._page.query_selector(selector)
        if element:
            return await element.get_attribute(attribute)
        return None
    
    async def get_value(self, selector: str) -> str:
        """Get input element value."""
        await self._ensure_started()
        return await self._page.input_value(selector)
    
    async def evaluate(self, script: str) -> Any:
        """Execute JavaScript in page context."""
        await self._ensure_started()
        return await self._page.evaluate(script)
    
    async def wait_for_selector(
        self,
        selector: str,
        timeout: Optional[int] = None,
        state: str = "visible"
    ) -> bool:
        """
        Wait for element to appear.
        
        Args:
            selector: CSS selector
            timeout: Timeout in ms (uses default if not specified)
            state: visible, hidden, attached, detached
        
        Returns:
            True if element found, False if timeout
        """
        await self._ensure_started()
        
        try:
            await self._page.wait_for_selector(
                selector,
                timeout=timeout,
                state=state
            )
            return True
        except Exception:
            return False
    
    async def wait_for_navigation(self, timeout: Optional[int] = None) -> bool:
        """Wait for navigation to complete."""
        await self._ensure_started()
        
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=timeout)
            return True
        except Exception:
            return False
    
    async def select_option(self, selector: str, value: str) -> bool:
        """Select option from dropdown."""
        await self._ensure_started()
        await self._page.select_option(selector, value)
        return True
    
    async def check(self, selector: str) -> bool:
        """Check a checkbox."""
        await self._ensure_started()
        await self._page.check(selector)
        return True
    
    async def uncheck(self, selector: str) -> bool:
        """Uncheck a checkbox."""
        await self._ensure_started()
        await self._page.uncheck(selector)
        return True
    
    async def hover(self, selector: str) -> bool:
        """Hover over an element."""
        await self._ensure_started()
        await self._page.hover(selector)
        return True
    
    async def scroll(self, selector: Optional[str] = None, x: int = 0, y: int = 0) -> bool:
        """Scroll page or element."""
        await self._ensure_started()
        
        if selector:
            await self._page.evaluate(
                f"document.querySelector('{selector}').scrollBy({x}, {y})"
            )
        else:
            await self._page.evaluate(f"window.scrollBy({x}, {y})")
        
        return True
    
    async def get_cookies(self) -> List[Dict[str, Any]]:
        """Get all cookies."""
        await self._ensure_started()
        return await self._context.cookies()
    
    async def set_cookies(self, cookies: List[Dict[str, Any]]):
        """Set cookies."""
        await self._ensure_started()
        await self._context.add_cookies(cookies)
    
    async def clear_cookies(self):
        """Clear all cookies."""
        await self._ensure_started()
        await self._context.clear_cookies()
    
    async def go_back(self) -> bool:
        """Navigate back."""
        await self._ensure_started()
        await self._page.go_back()
        return True
    
    async def go_forward(self) -> bool:
        """Navigate forward."""
        await self._ensure_started()
        await self._page.go_forward()
        return True
    
    async def reload(self) -> bool:
        """Reload page."""
        await self._ensure_started()
        await self._page.reload()
        return True
    
    async def get_url(self) -> str:
        """Get current URL."""
        await self._ensure_started()
        return self._page.url
    
    async def get_title(self) -> str:
        """Get page title."""
        await self._ensure_started()
        return await self._page.title()
    
    async def pdf(self, path: Optional[str] = None) -> str:
        """
        Save page as PDF (only works in headless mode).
        
        Returns path to saved PDF.
        """
        await self._ensure_started()
        
        if not self.config.headless:
            raise RuntimeError("PDF generation only works in headless mode")
        
        if not path:
            path = str(self._screenshot_dir / f"page_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
        
        await self._page.pdf(path=path)
        return path
    
    async def query_selector_all(self, selector: str) -> List[Dict[str, Any]]:
        """
        Query all matching elements and return their basic info.
        
        Returns list of dicts with tag, text, and common attributes.
        """
        await self._ensure_started()
        
        elements = await self._page.query_selector_all(selector)
        results = []
        
        for el in elements:
            tag = await el.evaluate("el => el.tagName.toLowerCase()")
            text = (await el.text_content() or "").strip()[:100]
            href = await el.get_attribute("href")
            src = await el.get_attribute("src")
            id_attr = await el.get_attribute("id")
            class_attr = await el.get_attribute("class")
            
            results.append({
                "tag": tag,
                "text": text,
                "href": href,
                "src": src,
                "id": id_attr,
                "class": class_attr,
            })
        
        return results
