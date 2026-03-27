"""navig.selfheal — Self-Heal & Hive Mind Protocol.

Opt-in pipeline that scans the local NAVIG installation for code quality
issues, generates a unified diff, collects user approval via Telegram bot
or CLI, and auto-submits a GitHub Pull Request to navig-run/core.

Public surface::

    from navig.selfheal.scanner import scan_files, ScanFinding
    from navig.selfheal.patcher import build_patch
    from navig.selfheal.pr_builder import submit_pr
    from navig.selfheal.git_manager import sync_fork, create_branch
    from navig.selfheal import ContributeConfig
"""

from __future__ import annotations

from dataclasses import dataclass, field  # noqa: F401
from typing import Any

__all__ = [
    "ContributeConfig",
]


@dataclass
class ContributeConfig:
    """Typed representation of the ``contribute:`` block in ``navig.yaml``.

    Acts as a single source of truth for config key names, default values, and
    types.  Both :mod:`navig.commands.contribute` and
    :mod:`navig.commands.init` should construct instances via
    :meth:`from_dict` rather than accessing raw dict keys directly.

    Attributes:
        enabled: Whether the contribution pipeline is active.
        alias: Display name credited in PR bodies (may be empty).
        min_confidence: Minimum LLM confidence score [0.0, 1.0] to include a
            finding in the patch.
        github_token_env: Name of the environment variable used as a PAT
            fallback when the vault provider is unavailable.
        upstream_repo: Target upstream repository (should not normally be
            overridden; canonical value is ``"navig-run/core"``).
        clone_path: Override for the local clone directory.
    """

    enabled: bool = False
    alias: str = ""
    min_confidence: float = 0.80
    github_token_env: str = "NAVIG_GITHUB_TOKEN"
    upstream_repo: str = "navig-run/core"
    clone_path: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ContributeConfig:
        """Construct a :class:`ContributeConfig` from a raw config dict.

        Unknown keys are silently ignored; missing keys fall back to defaults.

        Args:
            d: Raw ``contribute`` sub-dict from ``navig.yaml``.

        Returns:
            Populated :class:`ContributeConfig` instance.
        """
        return cls(
            enabled=bool(d.get("enabled", False)),
            alias=str(d.get("alias", d.get("contributor_alias", ""))),
            min_confidence=float(d.get("min_confidence", 0.80)),
            github_token_env=str(d.get("github_token_env", "NAVIG_GITHUB_TOKEN")),
            upstream_repo=str(d.get("upstream_repo", "navig-run/core")),
            clone_path=str(d.get("clone_path", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for YAML writing.

        Returns:
            Dict with all config keys and their current values.
        """
        return {
            "enabled": self.enabled,
            "alias": self.alias,
            "min_confidence": self.min_confidence,
            "github_token_env": self.github_token_env,
            "upstream_repo": self.upstream_repo,
            "clone_path": self.clone_path,
        }
