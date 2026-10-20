"""Canonical web-search provider registry — single source of truth.

Zero-dependency by design: the CLI imports this while building Typer help
(``navig --help`` must stay on the fast path), whereas ``navig.tools.web``
pulls in ``requests``/``trafilatura`` at module import. Dispatch, the deck
search routes, CLI help, onboarding and status UIs all read their provider
vocabulary from here — never from a second copy.
"""

# Providers web_search() can dispatch to (explicit ``--provider`` / config value).
SUPPORTED_SEARCH_PROVIDERS = (
    "deepseek_search",
    "firecrawl",
    "tavily",
    "brave",
    "serpapi",
    "mojeek",
    "duckduckgo",
)

# Keyed engines in auto-cascade order: the best grounding first, each attempted
# only when its key resolves, cascading further on *failure* (bad key, quota,
# network) rather than dead-ending.
SEARCH_KEYED_CASCADE = (
    "deepseek_search",
    "tavily",
    "brave",
    "serpapi",
)

# No-key engines used when no keyed engine succeeds.
SEARCH_KEYLESS_FALLBACK = (
    "mojeek",
    "duckduckgo",
)

# Full auto-cascade order (keyed first, then keyless).
SEARCH_CASCADE = SEARCH_KEYED_CASCADE + SEARCH_KEYLESS_FALLBACK

# Providers that work without any API key (never prompt for credentials).
KEYLESS_PROVIDERS = ("duckduckgo", "mojeek")

# Display labels (UI/CLI help). "auto" lives here so surfaces do not hardcode it.
SEARCH_PROVIDER_LABELS = {
    "auto": "Auto (cascade)",
    "deepseek_search": "DeepSeek",
    "firecrawl": "Firecrawl",
    "tavily": "Tavily",
    "brave": "Brave",
    "serpapi": "SerpApi",
    "mojeek": "Mojeek",
    "duckduckgo": "DuckDuckGo",
}
