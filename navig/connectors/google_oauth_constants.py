"""
Shared Google OAuth 2.0 endpoint constants.

Imported by all Google connector oauth_config modules to ensure a single
source of truth for Google's endpoint URLs.
"""

from __future__ import annotations

# Standard Google OAuth 2.0 endpoints — shared across all Google API connectors.
# Do NOT duplicate these in individual oauth_config.py files.
GOOGLE_AUTH_URL: str = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL: str = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL: str = "https://www.googleapis.com/oauth2/v3/userinfo"
