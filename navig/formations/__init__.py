"""
NAVIG Formations — Profile-Based Agent System

Formations transform NAVIG into domain-specific operational personas.
Each formation contains a set of AI agents with specialized roles.

Usage:
    from navig.formations.loader import get_active_formation, list_available_formations
    from navig.formations.types import Formation, AgentSpec
"""

from navig.formations.types import AgentSpec, ApiConnector, Formation, ProfileConfig
from navig.formations.schema import FormationValidationError

__all__ = [
    "AgentSpec",
    "ApiConnector",
    "Formation",
    "FormationValidationError",
    "ProfileConfig",
]
