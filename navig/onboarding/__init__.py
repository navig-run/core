"""
navig.onboarding — single canonical onboarding package.

Replaces (deleted, no shims):
  - navig.cli.wizard.SetupWizard
  - navig.cli.wizard.BootstrapWizard
  - navig.commands.onboard  (run_onboard, TUI wizard)
  - navig.commands.quickstart

Entry point:  navig init
Artifact:     ~/.navig/onboarding.json   (resumable, version-control safe)
Genesis:      ~/.navig/genesis.json      (immutable after first write)
Avatar:       ~/.navig/avatar.png        (512×512 QR, shareable)
"""

from .engine import EngineConfig, OnboardingEngine, StepResult  # noqa: F401
from .genesis import GenesisData, load_or_create  # noqa: F401

__all__ = [
    "EngineConfig",
    "OnboardingEngine",
    "StepResult",
    "GenesisData",
    "load_or_create",
]
