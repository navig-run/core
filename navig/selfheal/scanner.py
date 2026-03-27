"""navig.selfheal.scanner — LLM-powered code-quality scanner.

Reads local ``.py`` source files, sends batches to the configured LLM via
``navig.llm_generate.llm_generate``, and returns structured
:class:`ScanFinding` objects filtered by confidence threshold.

Security constraints
--------------------
* Files whose path contains ``vault/``, ``secret``, ``token``, or ``key``
  are **never** sent to the LLM.
* Only ``.py`` source files are scanned — no config, env, or binary files.
* Raw file paths are relativised before sending so installation-specific
  absolute paths are not leaked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Typing helpers
# ---------------------------------------------------------------------------

SeverityT = Literal["critical", "high", "medium", "low"]
CategoryT = Literal["bug", "performance", "readability", "security", "architecture"]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_FINDING_SCHEMA = """\
{
  "file": "<relative path>",
  "line": <int>,
  "severity": "critical | high | medium | low",
  "category": "bug | performance | readability | security | architecture",
  "description": "<concise explanation>",
  "suggested_fix": "<concrete code change>",
  "confidence": <float 0.0–1.0>
}"""

_SYSTEM_PROMPT = (
    "You are a precise Python code reviewer. Analyse the provided source file "
    "and return a JSON array of findings. Each finding must match this schema "
    "exactly:\n\n" + _FINDING_SCHEMA + "\n\n"
    "Focus on: code smells, bare except clauses, missing error handling, "
    "redundant logic, missing docstrings and type hints, modularisation "
    "opportunities, and security anti-patterns.\n"
    "Return ONLY a JSON array with no prose, no markdown fences, no "
    "explanatory text. If there are no findings, return an empty array []."
)

# Paths / name components that indicate sensitive content — never send to LLM.
# Note: the "vault" directory is checked via Path.parts in _is_sensitive_path
# so it does not need a backslash/forward-slash variant here.
_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"\btoken\b", re.IGNORECASE),
    re.compile(r"\bkey\b", re.IGNORECASE),
    re.compile(r"\.env", re.IGNORECASE),
    re.compile(r"credentials?", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
]

# Max characters sent per file — keeps token counts reasonable.
_MAX_FILE_CHARS = 8_000


class ScanFinding(BaseModel):
    """A single code-quality issue found during a self-heal scan.

    Attributes:
        file: Repository-relative path to the affected file.
        line: 1-based line number of the issue.
        severity: Criticality level — one of critical / high / medium / low.
        category: Class of issue.
        description: Human-readable explanation of the problem.
        suggested_fix: Concrete code change that resolves the issue.
        confidence: LLM confidence score in the range [0.0, 1.0].
    """

    file: str
    line: int = Field(ge=1)
    severity: SeverityT
    category: CategoryT
    description: str
    suggested_fix: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v: Any) -> float:
        """Clamp LLM-produced confidence to [0.0, 1.0]."""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    @field_validator("line", mode="before")
    @classmethod
    def _coerce_line(cls, v: Any) -> int:
        """Coerce string line numbers returned by some LLMs."""
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_sensitive_path(path: Path) -> bool:
    """Return True if *path* looks like it contains sensitive material.

    Args:
        path: Absolute or relative file path to check.

    Returns:
        True when any sensitive pattern matches the path string.
    """
    # Check path components directly — robust against OS separator differences.
    # If "vault" appears as any directory/file component, always exclude it.
    parts_lower = {p.lower() for p in path.parts}
    if "vault" in parts_lower:
        return True
    # Normalise to forward slashes so patterns don't need backslash variants.
    path_str = str(path).replace("\\", "/")
    return any(pattern.search(path_str) for pattern in _SENSITIVE_PATTERNS)


def _collect_py_files(base: Path, max_files: int = 200) -> list[Path]:
    """Collect Python source files under *base*, excluding sensitive paths.

    Args:
        base: Root directory to search.
        max_files: Hard upper limit on files returned (avoids runaway scans
            on large installs).

    Returns:
        Sorted list of absolute paths to ``.py`` files.
    """
    results: list[Path] = []
    for py_file in sorted(base.rglob("*.py")):
        if _is_sensitive_path(py_file):
            logger.debug("Skipping sensitive path: {}", py_file.name)
            continue
        # Skip compiled / cache artefacts
        if "__pycache__" in py_file.parts:
            continue
        results.append(py_file)
        if len(results) >= max_files:
            break
    return results


def _parse_llm_output(raw: str, source_file: str) -> list[dict[str, Any]]:
    """Parse and repair the raw LLM JSON output.

    Uses ``json_repair`` (existing project dependency) for robustness against
    incomplete or malformed LLM responses.

    Args:
        raw: Raw string from the LLM.
        source_file: Used only in error-log context.

    Returns:
        List of raw dicts; may be empty if parsing fails entirely.
    """
    # json_repair is an existing hard dependency — no need to gate import.
    from json_repair import repair_json  # noqa: PLC0415

    try:
        repaired = repair_json(raw.strip())
        parsed = json.loads(repaired)
        if isinstance(parsed, dict):
            # Some LLMs wrap the array in {"findings": [...]}
            for key in ("findings", "issues", "results"):
                if isinstance(parsed.get(key), list):
                    return list(parsed[key])  # type: ignore[return-value]
            return []
        if isinstance(parsed, list):
            return parsed  # type: ignore[return-value]
    except Exception as exc:  # noqa: BLE001
        logger.warning("JSON parse failed for {}: {}", source_file, exc)
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_files(
    install_path: Path,
    config: dict[str, Any] | None = None,
) -> list[ScanFinding]:
    """Scan Python source files under *install_path* using the LLM.

    Args:
        install_path: Root directory of the NAVIG installation to scan
            (typically the ``navig/`` package directory inside the clone).
        config: ``contribute`` config dict from ``navig.yaml``.  Defaults to
            an empty dict when not provided.

    Returns:
        List of :class:`ScanFinding` objects where
        ``confidence >= min_confidence``.  Findings are ordered by severity
        (critical first) then by file name.

    Example::

        from navig.platform.paths import config_dir

        repo_root = config_dir() / "core-repo" / "navig"
        findings = scan_files(repo_root)
    """
    # Defer heavy LLM import to keep startup fast (<50 ms rule).
    from navig.llm_generate import llm_generate  # noqa: PLC0415

    cfg: dict = config or {}
    min_confidence: float = float(cfg.get("min_confidence", 0.80))

    py_files = _collect_py_files(install_path)
    logger.info(
        "Scanning {} Python files (min_confidence={})", len(py_files), min_confidence
    )

    all_findings: list[ScanFinding] = []

    for py_file in py_files:
        rel_path = str(py_file.relative_to(install_path.parent))
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read {}: {}", py_file, exc)
            continue

        # Truncate to keep token budget predictable.
        if len(content) > _MAX_FILE_CHARS:
            content = content[:_MAX_FILE_CHARS] + "\n# ... (truncated)"

        user_msg = f"File: {rel_path}\n\n```python\n{content}\n```"
        try:
            raw_response = llm_generate(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                mode="coding",
                max_tokens=2048,
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM call failed for {}: {}", rel_path, exc)
            continue

        raw_dicts = _parse_llm_output(raw_response, rel_path)
        for raw_dict in raw_dicts:
            # Normalise the file path to the relative form.
            raw_dict["file"] = rel_path
            try:
                finding = ScanFinding.model_validate(raw_dict)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Invalid finding for {}: {}", rel_path, exc)
                continue
            if finding.confidence < min_confidence:
                logger.debug(
                    "Dropping low-confidence finding ({:.2f} < {:.2f}) for {}",
                    finding.confidence,
                    min_confidence,
                    rel_path,
                )
                continue
            all_findings.append(finding)

    # Sort: critical → high → medium → low, then by file
    _order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_findings.sort(key=lambda f: (_order.get(f.severity, 4), f.file, f.line))
    logger.info(
        "Scan complete: {} findings (confidence >= {})",
        len(all_findings),
        min_confidence,
    )
    return all_findings
