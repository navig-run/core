"""
navig.plans — Plans Inbox Reconciliation Engine.

Provides a deterministic pipeline for processing ``.navig/inbox/`` items
into the canonical ``.navig/plans/`` structure using file-suffix lifecycle
state management (``.md`` → ``.md.done`` / ``.md.archive`` / ``.md.review``).

Modules:
  scaffold               Canonical directory scaffolding
  inbox_reader            Inbox-only reader (never reads from plans/)
  current_phase_manager   Phase state, advance, block
  inbox_processor         Reconciliation pipeline
  review_queue            .review item API
  milestone_progress      Frontmatter-driven milestone tracking + visual strip
  corpus_scanner          Full-corpus duplicate + conflict detection
"""

from navig.plans.context import PlanContext
from navig.plans.corpus_scanner import CorpusScanner
from navig.plans.current_phase_manager import CurrentPhaseManager, PhaseState
from navig.plans.inbox_processor import InboxProcessor, ReconciliationResult
from navig.plans.inbox_reader import InboxItem, InboxReader
from navig.plans.milestone_progress import MilestoneProgressEngine, MilestoneState
from navig.plans.review_queue import ReviewItem, ReviewQueue
from navig.plans.scaffold import scaffold_plans_structure

__all__ = [
    "CorpusScanner",
    "CurrentPhaseManager",
    "InboxItem",
    "InboxProcessor",
    "InboxReader",
    "MilestoneProgressEngine",
    "MilestoneState",
    "PhaseState",
    "PlanContext",
    "ReconciliationResult",
    "ReviewItem",
    "ReviewQueue",
    "scaffold_plans_structure",
]
