"""Browser automation module using Playwright."""

from .controller import BrowserConfig, BrowserController
from .orchestrator import CortexOrchestrator
from .router import cdp_browser, fast_browser, get_browser, stealth_browser
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
