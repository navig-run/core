"""Browser automation — the engine lives in navig-browser; these names re-export it.

Every ``navig.browser.<module>`` beside this file is an identity alias of
``navig_browser.<module>``. This package stays a real package (not an alias of
``navig_browser``) so each submodule is loaded exactly once, under its new name.
"""
from navig.browser.controller import BrowserConfig, BrowserController
from navig.browser.orchestrator import CortexOrchestrator
from navig.browser.router import cdp_browser, fast_browser, get_browser, stealth_browser
from navig.browser.template_runner import TemplateRunner

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
