"""Browser automation module using Playwright."""

from .controller import BrowserController, BrowserConfig
from .router import get_browser, fast_browser, stealth_browser, cdp_browser
from .orchestrator import CortexOrchestrator
from .template_runner import TemplateRunner

__all__ = [
    "BrowserController",
    "BrowserConfig",
    "get_browser",
    "fast_browser",
    "stealth_browser",
    "cdp_browser",
    "CortexOrchestrator",
    "TemplateRunner",
]
