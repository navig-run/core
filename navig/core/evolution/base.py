import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

_FENCE_RE = re.compile(r"```[^\n`]*\n(.*?)\n```", re.DOTALL)


def extract_code_block(artifact: str) -> str:
    """Pull the code out of the model's fenced markdown block.

    ONE extractor for every evolver, because two parsers for one format drift.
    `fix.py` had two: `_validate` accepted any language tag (``` plus \\w*)
    while `_save` demanded the *target file's extension* (```py for a .py
    file). Models write ```python — so validation compiled the clean code and
    the save wrote the ENTIRE fenced block, backticks and all, into the user's
    source file, then reported success. Every extension mismatched.

    An artifact with no fence is returned unchanged: the model answering with
    bare code is a supported shape, not a parse failure.
    """
    match = _FENCE_RE.search(artifact)
    return match.group(1).strip() if match else artifact


def safe_artifact_name(raw: str, fallback: str) -> str:
    """Reduce a model-supplied name to one safe path segment.

    The evolvers take the artifact's name from the **model's own output** — a
    ``# filename:`` comment in generated code, a pack's ``name:`` field — and
    ``Path(dir) / name`` offers no protection there. A relative name climbs out
    with ``..``, and an ABSOLUTE name discards the base directory entirely:
    ``Path("/a/b") / "/etc/passwd"`` is ``/etc/passwd``, not ``/a/b/etc/passwd``.
    So keep only the final segment, and only characters that cannot rebuild a
    path from it.
    """
    segment = str(raw).replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in segment)
    cleaned = cleaned.lstrip(".")  # no "." / ".." / hidden dotfiles
    return cleaned or fallback


@dataclass
class EvolutionResult:
    success: bool
    artifact: Any = None
    error: str = ""
    history: list[str] | None = None
    attempts: int = 0


class BaseEvolver(ABC):
    """Abstract base class for evolving artifacts."""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.history = []
        # Set by `_save` when persistence fails, so the reason reaches
        # `EvolutionResult.error` instead of only the console.
        self._save_error: str = ""

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
                # Generated and validated — but it is only a success once it is
                # actually persisted. `_save` reports that; it must never be
                # assumed (see the docstring below).
                if not self._save(goal, new_artifact):
                    return EvolutionResult(
                        False,
                        artifact=new_artifact,
                        error=self._save_error or "Validated, but saving the result failed",
                        attempts=attempt,
                        history=self.history,
                    )
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

    def _save(self, goal: str, artifact: Any) -> bool:
        """Persist the validated artifact. Return True only if it is on disk.

        The base implementation persists nothing, so it succeeds trivially.

        An override MUST return False when the write failed, and MUST NOT
        raise — `evolve()` turns a False into an honest failed
        `EvolutionResult`, whereas an exception here would escape as a crash.
        Returning None (the old signature) now reads as failure, which is the
        safe direction: an evolver that forgets to report gets a loud "not
        saved" rather than a green "created" over a file that is not there.
        Set `self._save_error` to the reason so it reaches the caller.
        """
        return True
