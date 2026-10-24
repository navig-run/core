"""NAVIG Reflex — deterministic plan/commit routing in core.

The package generalises the contract already shipped in ``navig-voice``
(``SkillManifest`` + pure ``propose`` + idempotent ``commit``) to every registered
action: one tool manifest, one deterministic preclassifier, one commit-token table
in the existing ``RuntimeStore``, and one ``Answer`` envelope.

No LLM is imported by this package. An utterance the router does not recognise
returns ``matched=False`` so the caller can fall through to the existing ``ask_ai``
path — that is the *only* fallback in the whole layer.
"""

from __future__ import annotations

from navig.reflex.answer_bridge import (
    ANSWER_FIELDS,
    answer_as_dict,
    answer_from_dict,
    make_answer,
)
from navig.reflex.errors import (
    CommandFailed,
    ManifestError,
    PlanError,
    PlanExpired,
    PlanUnknown,
    ReflexError,
    TargetUnavailable,
)
from navig.reflex.evidence import write_evidence
from navig.reflex.executor import CommandOutcome, SubprocessRunner
from navig.reflex.manifest import (
    RISK_LEVELS,
    SlotSpec,
    ToolManifest,
    ToolRegistry,
    build_registry,
    default_registry,
    validate_against_command_manifest,
)
from navig.reflex.plans import PLAN_TTL_SECONDS, CommitPlan, new_token
from navig.reflex.preclassify import Preclassification, classify, normalize
from navig.reflex.profiles import (
    DEFAULT_PROFILE,
    POLICY_TABLE,
    PROFILE_NAMES,
    describe,
    needs_explicit_commit,
    resolve_profile,
)
from navig.reflex.resolve import EndpointResolver, EndpointView, StaticResolver, StoreResolver
from navig.reflex.service import ReflexRouter, RouteResult
from navig.reflex.store import CommitPlanStore

__all__ = [
    "ANSWER_FIELDS",
    "ReflexError",
    "ReflexRouter",
    "CommitPlan",
    "CommitPlanStore",
    "CommandFailed",
    "CommandOutcome",
    "DEFAULT_PROFILE",
    "EndpointResolver",
    "EndpointView",
    "ManifestError",
    "PLAN_TTL_SECONDS",
    "POLICY_TABLE",
    "PROFILE_NAMES",
    "PlanError",
    "PlanExpired",
    "PlanUnknown",
    "Preclassification",
    "RISK_LEVELS",
    "RouteResult",
    "SlotSpec",
    "StaticResolver",
    "StoreResolver",
    "SubprocessRunner",
    "TargetUnavailable",
    "ToolManifest",
    "ToolRegistry",
    "answer_as_dict",
    "answer_from_dict",
    "build_registry",
    "classify",
    "default_registry",
    "describe",
    "make_answer",
    "needs_explicit_commit",
    "new_token",
    "normalize",
    "resolve_profile",
    "validate_against_command_manifest",
    "write_evidence",
]
