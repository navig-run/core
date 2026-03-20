"""navig.update — NAVIG self-update engine.

Distinct from ``navig.deploy`` (which pushes project files to hosts):
this package upgrades the installed NAVIG version on local and remote nodes.
"""
from navig.update.lifecycle import UpdateEngine
from navig.update.models import NodeResult, UpdatePlan, UpdateResult, VersionInfo
from navig.update.targets import TargetResolver, UpdateTarget

__all__ = [
    "UpdateTarget",
    "TargetResolver",
    "UpdatePlan",
    "UpdateResult",
    "NodeResult",
    "VersionInfo",
    "UpdateEngine",
]
