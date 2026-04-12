"""
Tests for BRIDGE_DEFAULT_PORT centralization (Phase 3).

Verifies that:
- BRIDGE_DEFAULT_PORT is importable and equals 42070
- Key consumer modules import from bridge_grid_reader (not bare literals)
- mcp_bridge base URL in llm_router uses the constant
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).parents[2]


# ---------------------------------------------------------------------------
# Constant availability
# ---------------------------------------------------------------------------


def test_bridge_default_port_importable():
    from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT

    assert BRIDGE_DEFAULT_PORT == 42070


def test_bridge_default_port_is_int():
    from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT

    assert isinstance(BRIDGE_DEFAULT_PORT, int)


def test_llm_router_mcp_bridge_url_uses_constant():
    """PROVIDER_BASE_URLS['mcp_bridge'] must contain port 42070 (via constant)."""
    from navig.llm_router import PROVIDER_BASE_URLS

    url = PROVIDER_BASE_URLS.get("mcp_bridge", "")
    assert "42070" in url, f"mcp_bridge base URL {url!r} does not contain port 42070"


# ---------------------------------------------------------------------------
# Consumer modules import from bridge_grid_reader
# ---------------------------------------------------------------------------

_IMPORT_SOURCE = "navig.providers.bridge_grid_reader"
_IMPORT_NAME_VARIANTS = {"BRIDGE_DEFAULT_PORT", "get_llm_port"}

_CONSUMER_FILES = [
    REPO_ROOT / "navig/gateway/channels/telegram.py",
    REPO_ROOT / "navig/gateway/channels/telegram_commands.py",
    REPO_ROOT / "navig/agent/ai_client.py",
    REPO_ROOT / "navig/agent/llm_providers.py",
    REPO_ROOT / "navig/daemon/telegram_worker.py",
    REPO_ROOT / "navig/commands/copilot.py",
    REPO_ROOT / "navig/providers/registry.py",
    REPO_ROOT / "navig/llm_router.py",
]


def _source_imports_bridge_reader(path: Path) -> bool:
    """Return True if the file has any import from navig.providers.bridge_grid_reader."""
    source = path.read_text(encoding="utf-8-sig")  # strips BOM if present
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == _IMPORT_SOURCE:
                for alias in node.names:
                    if alias.name in _IMPORT_NAME_VARIANTS:
                        return True
    return False


@pytest.mark.parametrize("consumer", _CONSUMER_FILES, ids=[p.name for p in _CONSUMER_FILES])
def test_consumer_imports_from_bridge_grid_reader(consumer):
    """Each consumer file must import BRIDGE_DEFAULT_PORT (or get_llm_port) from bridge_grid_reader."""
    if not consumer.exists():
        pytest.skip(f"{consumer} not found")
    assert _source_imports_bridge_reader(consumer), (
        f"{consumer.name} does not import from {_IMPORT_SOURCE}. "
        "Use BRIDGE_DEFAULT_PORT instead of bare literal 42070."
    )


# ---------------------------------------------------------------------------
# McpBridgeProvider.DEFAULT_URL uses the constant port
# ---------------------------------------------------------------------------


def test_llm_providers_mcp_bridge_default_url_has_constant_port():
    """McpBridgeProvider.DEFAULT_URL must resolve to the canonical port."""
    mod = pytest.importorskip("navig.agent.llm_providers")
    provider_cls = getattr(mod, "McpBridgeProvider", None)
    if provider_cls is None:
        pytest.skip("McpBridgeProvider not found")
    from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT

    assert str(BRIDGE_DEFAULT_PORT) in provider_cls.DEFAULT_URL, (
        f"McpBridgeProvider.DEFAULT_URL {provider_cls.DEFAULT_URL!r} "
        f"does not contain port {BRIDGE_DEFAULT_PORT}"
    )
