from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class EvolutionResult:
    success: bool
    artifact: Any = None
    error: str = ""
    history: list[str] = None
    attempts: int = 0


class BaseEvolver(ABC):
    """Abstract base class for evolving artifacts."""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.history = []

    def evolve(self, goal: str, context: Any = None) -> EvolutionResult:
        """
        Generate, refine, and validate an artifact.
        """
        self.history = []

        # Check cache/library first (optional override)
        cached = self._check_cache(goal)
        if cached:
            return EvolutionResult(True, artifact=cached, attempts=0)

        current_artifact = None
        current_error = ""

        for attempt in range(1, self.max_retries + 1):
            # Generate / Refine
            try:
                new_artifact = self._generate(goal, current_artifact, current_error, context)
            except Exception as e:
                return EvolutionResult(False, error=f"Generation failed: {e}", attempts=attempt)

            if not new_artifact:
                return EvolutionResult(
                    False, error="Generator returned empty artifact", attempts=attempt
                )

            self.history.append(str(new_artifact)[:500])  # Store snippet

            # Validate / Test
            validation_error = self._validate(new_artifact, context)

            if not validation_error:
                # Success!
                self._save(goal, new_artifact)
                return EvolutionResult(
                    True, artifact=new_artifact, attempts=attempt, history=self.history
                )

            current_artifact = new_artifact
            current_error = validation_error

        return EvolutionResult(
            False, error=current_error, attempts=self.max_retries, history=self.history
        )

    def _check_cache(self, goal: str) -> Any | None:
        """Override to check existing libraries."""
        return None

    @abstractmethod
    def _generate(self, goal: str, previous_artifact: Any, error: str, context: Any) -> Any:
        """Generate or refine the artifact."""
        pass

    @abstractmethod
    def _validate(self, artifact: Any, context: Any) -> str | None:
        """Return error string if invalid, None if valid."""
        pass

    def _save(self, goal: str, artifact: Any):
        """Save successful artifact."""
        pass
