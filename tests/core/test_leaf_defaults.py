"""Unit tests for zero-dependency leaf constant modules.

Covers:
- navig._daemon_defaults  (IPC/OAuth port constants)
- navig._llm_defaults     (LLM generation defaults)
"""

from __future__ import annotations

from navig._daemon_defaults import _DAEMON_PORT, _OAUTH_REDIRECT_PORT
from navig._llm_defaults import _DEFAULT_MAX_TOKENS, _DEFAULT_TEMPERATURE

# ---------------------------------------------------------------------------
# navig._daemon_defaults
# ---------------------------------------------------------------------------


class TestDaemonDefaults:
    def test_daemon_port_is_int(self):
        assert isinstance(_DAEMON_PORT, int)

    def test_daemon_port_positive(self):
        assert _DAEMON_PORT > 0

    def test_daemon_port_value(self):
        assert _DAEMON_PORT == 8765

    def test_daemon_port_in_valid_range(self):
        # Must be an unprivileged port
        assert 1024 <= _DAEMON_PORT <= 65535

    def test_oauth_redirect_port_is_int(self):
        assert isinstance(_OAUTH_REDIRECT_PORT, int)

    def test_oauth_redirect_port_positive(self):
        assert _OAUTH_REDIRECT_PORT > 0

    def test_oauth_redirect_port_value(self):
        assert _OAUTH_REDIRECT_PORT == 1455

    def test_oauth_redirect_port_in_valid_range(self):
        assert 1024 <= _OAUTH_REDIRECT_PORT <= 65535

    def test_ports_differ(self):
        assert _DAEMON_PORT != _OAUTH_REDIRECT_PORT


# ---------------------------------------------------------------------------
# navig._llm_defaults
# ---------------------------------------------------------------------------


class TestLlmDefaults:
    def test_temperature_is_float(self):
        assert isinstance(_DEFAULT_TEMPERATURE, float)

    def test_temperature_value(self):
        assert _DEFAULT_TEMPERATURE == 0.7

    def test_temperature_in_valid_range(self):
        # 0.0 = deterministic, 2.0 = very random
        assert 0.0 <= _DEFAULT_TEMPERATURE <= 2.0

    def test_max_tokens_is_int(self):
        assert isinstance(_DEFAULT_MAX_TOKENS, int)

    def test_max_tokens_positive(self):
        assert _DEFAULT_MAX_TOKENS > 0

    def test_max_tokens_value(self):
        assert _DEFAULT_MAX_TOKENS == 4096

    def test_max_tokens_reasonable(self):
        # At least 1K, at most 1M — confirms no accidental off-by-orders
        assert 1_000 <= _DEFAULT_MAX_TOKENS <= 1_000_000
