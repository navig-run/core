"""
navig.providers._local_defaults — zero-dependency leaf for local provider URLs.

The canonical defaults live here so every module can import a named constant
instead of repeating the same URL literal.

Two variants per provider:
  *_BASE_URL   — explicit loopback (127.0.0.1), used for internal probes and
                  routing dictionaries.
  *_USER_BASE_URL — localhost, used for user-facing defaults (config, TUI,
                    provider constructors) where the user may also substitute
                    a remote host.
"""

from __future__ import annotations

# ── Ollama ────────────────────────────────────────────────────────────────────

# Internal probe / routing dict default  →  explicit loopback
_OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"

# User-facing config default  →  symbolic localhost
_OLLAMA_USER_BASE_URL: str = "http://localhost:11434"

# ── llama.cpp ─────────────────────────────────────────────────────────────────

# Internal probe / routing dict default  →  explicit loopback
_LLAMACPP_BASE_URL: str = "http://127.0.0.1:8080"

# User-facing config default  →  symbolic localhost
_LLAMACPP_USER_BASE_URL: str = "http://localhost:8080"
