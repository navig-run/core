"""navig.update — NAVIG self-update engine.

Distinct from ``navig.deploy`` (which pushes project files to hosts):
this package upgrades the installed NAVIG version on local and remote nodes.
"""
from navig.update.models import UpdatePlan, UpdateResult, NodeResult, VersionInfo
from navig.update.targets import UpdateTarget, TargetResolver
from navig.update.lifecycle import UpdateEngine

__all__ = [
    "UpdateTarget",
    "TargetResolver",
    "UpdatePlan",
    "UpdateResult",
    "NodeResult",
    "VersionInfo",
    "UpdateEngine",
]
