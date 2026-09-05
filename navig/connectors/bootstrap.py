"""Connector bootstrap — single source of truth for built-in connector registration.

Called by:
- ``navig.commands.connector_cmd`` (CLI surface)
- ``navig.mcp.tools.connectors.register()`` (MCP server startup)

Adding a new connector here is all that is needed to expose it to both surfaces.
"""

from __future__ import annotations

import logging

_CONNECTORS_LOADED = False
_LOADING = False
_log = logging.getLogger("navig.connectors")


def ensure_connectors_loaded() -> None:
    """Lazily register all built-in connectors into the singleton registry.

    Idempotent: safe to call multiple times; registration happens only once.
    Individual connectors that fail to import (missing deps / bad config) are
    silently skipped so one broken connector cannot block the others.

    The "already done" flag is set only **after** the work succeeds. It used to be
    set on entry, which conflated two different jobs: guarding against re-entrancy
    and recording completion. Anything that escaped this function — the registry
    import below is not inside a ``try`` — then left the flag stuck at True, so
    every later call returned immediately and the process stayed permanently
    connector-less with no way to retry. Re-entrancy now has its own flag, so a
    connector module that imports something which calls back in here still cannot
    recurse, while a genuine failure remains retryable.
    """
    global _CONNECTORS_LOADED, _LOADING
    if _CONNECTORS_LOADED or _LOADING:
        return
    _LOADING = True
    try:
        _register_all()
    finally:
        _LOADING = False
    _CONNECTORS_LOADED = True


def invalidate() -> None:
    """Forget that connectors were loaded, so the next call re-registers them.

    Called by :meth:`ConnectorRegistry.reset`, because clearing the registrations is
    exactly what makes the "already loaded" claim false. Without this the two disagree:
    the registry is empty while the flag says loaded, so `ensure_connectors_loaded()`
    returns immediately and the process stays permanently connector-less — the failure
    this module's own docstring describes, reached from the other direction.

    Measured before the fix: after `tests/connectors/test_connectors_bootstrap.py` ran,
    `flag=True registry=0`, and a reload could not recover it. Every connector-derived
    MCP tool then vanished, which silently made two coverage guards vacuous
    (`test_connector_write_tools_are_dangerous` / `test_connector_reads_are_not_safe`).
    """
    global _CONNECTORS_LOADED, _LOADING
    _CONNECTORS_LOADED = False
    _LOADING = False


def _register_all() -> None:
    """Do the actual registration. Raising leaves the load retryable."""
    from navig.connectors.registry import get_connector_registry

    registry = get_connector_registry()

    _candidates = [
        # Google-family (OAuth: uses GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)
        ("navig.connectors.gmail.connector", "GmailConnector", "Gmail"),
        ("navig.connectors.google_calendar.connector", "GoogleCalendarConnector", "Google Calendar"),
        ("navig.connectors.google_drive.connector", "GoogleDriveConnector", "Google Drive"),
        ("navig.connectors.google_maps.connector", "GoogleMapsConnector", "Google Maps"),
        ("navig.connectors.youtube.connector", "YouTubeConnector", "YouTube"),
        # Developer
        ("navig.connectors.github.connector", "GitHubConnector", "GitHub"),
        ("navig.connectors.linear.connector", "LinearConnector", "Linear"),
        # Communication
        ("navig.connectors.slack.connector", "SlackConnector", "Slack"),
        # Knowledge
        ("navig.connectors.notion.connector", "NotionConnector", "Notion"),
        # AI
        ("navig.connectors.perplexity.connector", "PerplexityConnector", "Perplexity AI"),
        # Infrastructure
        ("navig.connectors.supabase.connector", "SupabaseConnector", "Supabase"),
        ("navig.connectors.gcp_translate.connector", "GcpTranslateConnector", "GCP Translate"),
        # Commerce/finance connectors (partner_center, paddle, stripe, bank) ship in the
        # private `navig-harbor` plugin and self-register via ConnectorRegistry on enable —
        # they are intentionally NOT listed here (public core stays free of business code).
    ]

    for module_path, class_name, label in _candidates:
        try:
            import importlib

            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            registry.register(cls)
        except Exception as exc:
            _log.debug("%s connector load failed: %s", label, exc)

    # Connectors shipped by installed plugins (e.g. the private navig-harbor) via the
    # ``navig.connectors`` entry-point group — registered the same way so they appear in
    # CLI + gateway contexts without being referenced in public core.
    try:
        from importlib.metadata import entry_points

        try:
            _ceps = entry_points(group="navig.connectors")
        except TypeError:  # Python <3.10
            _ceps = entry_points().get("navig.connectors", [])  # type: ignore[attr-defined]
        for _ep in _ceps:
            try:
                registry.register(_ep.load())
            except Exception as exc:
                _log.debug("entry-point connector %s load failed: %s", _ep.name, exc)
    except Exception as exc:
        _log.debug("entry-point connector discovery skipped: %s", exc)

    # Register OAuth provider configs so get_auth_url() works for each connector.
    # Each loader reads env vars (GITHUB_CLIENT_ID etc.) — silently skipped if absent.
    #
    # The whole block is guarded: ConnectorAuthManager pulls in the vault (and
    # transitively the `cryptography` package).  If that import chain is broken
    # (e.g. a missing native dep), connector *registration* must still succeed —
    # the registry is useful for listing even when OAuth isn't available yet.
    try:
        import importlib

        from navig.connectors.auth_manager import ConnectorAuthManager

        _oauth_loaders = [
            ("navig.connectors.github.oauth_config", "get_github_oauth_config", "github"),
            ("navig.connectors.slack.oauth_config", "get_slack_oauth_config", "slack"),
            ("navig.connectors.notion.oauth_config", "get_notion_oauth_config", "notion"),
            ("navig.connectors.google_drive.oauth_config", "get_google_drive_oauth_config", "google_drive"),
            ("navig.connectors.gmail.oauth_config", "get_gmail_oauth_config", "gmail"),
            ("navig.connectors.linear.oauth_config", "get_linear_oauth_config", "linear"),
        ]
        for module_path, fn_name, connector_id in _oauth_loaders:
            try:
                mod = importlib.import_module(module_path)
                config = getattr(mod, fn_name)()
                if config:
                    ConnectorAuthManager.register_provider(connector_id, config)
            except Exception as exc:
                _log.debug("%s OAuth config load failed: %s", connector_id, exc)
    except Exception as exc:
        _log.debug("OAuth provider registration skipped (auth manager unavailable): %s", exc)
