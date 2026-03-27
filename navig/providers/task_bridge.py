"""
NAVIG Task Bridge — provider routing for natural-language instructions.

Every provider implements BaseTaskProvider:
  - can_handle(instruction) → float  (0.0–1.0 confidence)
  - process(instruction) → ProviderResult

TaskBridge.route(instruction) calls all providers that score above threshold
and returns the list of ProviderResult objects.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

# ============================================================================
# Data types
# ============================================================================


@dataclass
class ProviderResult:
    """Result from a single provider execution."""

    provider: str
    success: bool
    output: str
    error: str | None = None


# ============================================================================
# Base class
# ============================================================================


class BaseTaskProvider(ABC):
    """Abstract base for task providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier."""

    @abstractmethod
    def can_handle(self, instruction: str) -> float:
        """Return confidence score 0.0–1.0 that this provider can handle the instruction."""

    @abstractmethod
    def process(self, instruction: str) -> ProviderResult:
        """Execute the instruction. Must not raise — return ProviderResult(success=False) on error."""


# ============================================================================
# Task Bridge
# ============================================================================


class TaskBridge:
    """Routes instructions through a list of BaseTaskProvider instances."""

    THRESHOLD = 0.1  # Minimum confidence to attempt a provider

    def __init__(self, providers: list[BaseTaskProvider]) -> None:
        self._providers = providers

    def route(self, instruction: str) -> list[ProviderResult]:
        """Route instruction to all providers that can handle it."""
        results: list[ProviderResult] = []
        for provider in self._providers:
            try:
                score = provider.can_handle(instruction)
            except Exception as exc:
                results.append(
                    ProviderResult(
                        provider=provider.name,
                        success=False,
                        output="",
                        error=f"can_handle error: {exc}",
                    )
                )
                continue

            if score < self.THRESHOLD:
                continue

            try:
                result = provider.process(instruction)
            except Exception as exc:
                result = ProviderResult(
                    provider=provider.name,
                    success=False,
                    output="",
                    error=str(exc),
                )
            results.append(result)

        return results


# ============================================================================
# Stub providers
# ============================================================================


def _keyword_score(instruction: str, keywords: list[str]) -> float:
    """Simple keyword-based confidence scorer."""
    lowered = instruction.lower()
    hits = sum(1 for kw in keywords if kw in lowered)
    return min(1.0, hits / max(len(keywords), 1) * 2)


class EmailProvider(BaseTaskProvider):
    """Handles email-related instructions."""

    _KEYWORDS = ["email", "mail", "send", "digest", "newsletter", "inbox", "message"]

    @property
    def name(self) -> str:
        return "email"

    def can_handle(self, instruction: str) -> float:
        return _keyword_score(instruction, self._KEYWORDS)

    def process(self, instruction: str) -> ProviderResult:
        try:
            from navig.commands.email import get_email_provider  # type: ignore

            provider = get_email_provider()
            result = provider.send_from_instruction(instruction)
            return ProviderResult(provider=self.name, success=True, output=str(result))
        except ImportError:
            return ProviderResult(
                provider=self.name,
                success=False,
                output="",
                error="email module not available (navig[email] not installed)",
            )
        except Exception as exc:
            return ProviderResult(provider=self.name, success=False, output="", error=str(exc))


class CalendarProvider(BaseTaskProvider):
    """Handles calendar and scheduling instructions."""

    _KEYWORDS = [
        "schedule",
        "calendar",
        "meeting",
        "remind",
        "appointment",
        "event",
        "tomorrow",
        "today",
    ]

    @property
    def name(self) -> str:
        return "calendar"

    def can_handle(self, instruction: str) -> float:
        return _keyword_score(instruction, self._KEYWORDS)

    def process(self, instruction: str) -> ProviderResult:
        try:
            from navig.commands.calendar import (
                create_event_from_instruction,  # type: ignore
            )

            result = create_event_from_instruction(instruction)
            return ProviderResult(provider=self.name, success=True, output=str(result))
        except ImportError:
            return ProviderResult(
                provider=self.name,
                success=False,
                output="",
                error="calendar module not available",
            )
        except Exception as exc:
            return ProviderResult(provider=self.name, success=False, output="", error=str(exc))


class CommsProvider(BaseTaskProvider):
    """Handles communications instructions (Slack, Telegram, Matrix)."""

    _KEYWORDS = [
        "slack",
        "telegram",
        "matrix",
        "notify",
        "alert",
        "message",
        "chat",
        "team",
        "channel",
    ]

    @property
    def name(self) -> str:
        return "comms"

    def can_handle(self, instruction: str) -> float:
        return _keyword_score(instruction, self._KEYWORDS)

    def process(self, instruction: str) -> ProviderResult:
        # Attempt to route through gateway comms channels
        try:
            from navig.gateway.comms import dispatch_message  # type: ignore

            dispatch_message(instruction)
            return ProviderResult(
                provider=self.name,
                success=True,
                output="Message dispatched via gateway",
            )
        except ImportError:
            pass  # optional dependency not installed; feature disabled
        except Exception as exc:
            return ProviderResult(provider=self.name, success=False, output="", error=str(exc))

        return ProviderResult(
            provider=self.name,
            success=False,
            output="",
            error="comms gateway not configured",
        )


class RemoteProvider(BaseTaskProvider):
    """Handles remote server/ops instructions."""

    _KEYWORDS = [
        "run",
        "deploy",
        "server",
        "ssh",
        "remote",
        "production",
        "staging",
        "restart",
        "health",
    ]

    @property
    def name(self) -> str:
        return "remote"

    def can_handle(self, instruction: str) -> float:
        return _keyword_score(instruction, self._KEYWORDS)

    def process(self, instruction: str) -> ProviderResult:
        # Suggest the navig run command rather than executing blindly
        return ProviderResult(
            provider=self.name,
            success=True,
            output=f'Suggested: navig run "{instruction}" — review and confirm before executing',
        )


# ============================================================================
# Factory
# ============================================================================


def build_default_providers() -> list[BaseTaskProvider]:
    """Return the default set of task providers."""
    return [
        EmailProvider(),
        CalendarProvider(),
        CommsProvider(),
        RemoteProvider(),
    ]
