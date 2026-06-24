"""Unit tests for modules/error_resolution.py — Solution dataclass and _categorize_error."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navig.modules.error_resolution import ErrorResolution, Solution


# ---------------------------------------------------------------------------
# Solution
# ---------------------------------------------------------------------------

class TestSolution:
    def test_defaults(self):
        s = Solution(description="Try this", command="systemctl restart")
        assert s.success_rate == 0.0
        assert s.risk_level == "low"
        assert s.requires_confirmation is False

    def test_custom_fields(self):
        s = Solution(
            description="Restart service",
            command="service nginx restart",
            success_rate=0.85,
            risk_level="medium",
            requires_confirmation=True,
        )
        assert s.success_rate == 0.85
        assert s.risk_level == "medium"
        assert s.requires_confirmation is True

    def test_to_dict_keys(self):
        s = Solution(description="Fix it", command="apt-get install -f")
        d = s.to_dict()
        assert "description" in d
        assert "command" in d
        assert "success_rate" in d
        assert "risk_level" in d
        assert "requires_confirmation" in d

    def test_to_dict_values(self):
        s = Solution(description="Fix it", command="apt-get install -f", success_rate=0.5)
        d = s.to_dict()
        assert d["description"] == "Fix it"
        assert d["command"] == "apt-get install -f"
        assert d["success_rate"] == 0.5

    def test_from_dict_roundtrip(self):
        original = Solution(
            description="Reload config",
            command="nginx -s reload",
            success_rate=0.9,
            risk_level="high",
            requires_confirmation=True,
        )
        d = original.to_dict()
        restored = Solution.from_dict(d)
        assert restored.description == original.description
        assert restored.command == original.command
        assert restored.success_rate == original.success_rate
        assert restored.risk_level == original.risk_level
        assert restored.requires_confirmation == original.requires_confirmation

    def test_from_dict_minimal(self):
        """from_dict works with only required keys."""
        s = Solution.from_dict({"description": "d", "command": "c"})
        assert s.success_rate == 0.0
        assert s.risk_level == "low"
        assert s.requires_confirmation is False

    def test_to_dict_is_serializable(self):
        import json
        s = Solution(description="x", command="y", success_rate=0.7)
        json.dumps(s.to_dict())  # Should not raise


# ---------------------------------------------------------------------------
# ErrorResolution._categorize_error
# ---------------------------------------------------------------------------

def _make_er(tmp_path: Path) -> ErrorResolution:
    """Build an ErrorResolution with a minimal mock assistant."""
    assistant = MagicMock()
    assistant.ai_context_dir = tmp_path
    assistant.assistant_config = {}
    return ErrorResolution(assistant)


class TestCategorizeError:
    def test_permission_denied(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("Permission denied: /etc/passwd") == "permission"

    def test_access_denied(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("Access denied (publickey)") == "permission"

    def test_connection_refused(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("Connection refused to 127.0.0.1:5432") == "network"

    def test_timeout(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("Read timeout after 30s") == "network"

    def test_network(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("Network unreachable") == "network"

    def test_disk_full(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("No space left on device: disk full") == "resource_exhaustion"

    def test_out_of_memory(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("Out of memory: kill process") == "resource_exhaustion"

    def test_not_found(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("No such file or directory") == "dependency_missing"

    def test_not_found_variant(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("docker: not found") == "dependency_missing"

    def test_syntax_error(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("syntax error near unexpected token") == "syntax"

    def test_parse_error(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("parse error: invalid JSON") == "syntax"

    def test_config_error(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("invalid configuration value") == "configuration"

    def test_unknown_fallback(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("something completely unrelated") == "unknown"

    def test_case_insensitive(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("PERMISSION DENIED") == "permission"

    def test_empty_message_is_unknown(self, tmp_path):
        er = _make_er(tmp_path)
        assert er._categorize_error("") == "unknown"
