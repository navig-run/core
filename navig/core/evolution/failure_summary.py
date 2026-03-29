from __future__ import annotations

import re

_FAILED_TEST_RE = re.compile(r"^FAILED\s+([^\s]+)", re.MULTILINE)
_PYTEST_COUNT_RE = re.compile(r"(\d+)\s+failed", re.IGNORECASE)
_TRACEBACK_LINE_RE = re.compile(r"^E\s+(.*)$", re.MULTILINE)


def summarize_check_failure(stdout: str, stderr: str) -> str:
    """Build a compact, actionable summary from validation command output."""
    stdout_text = (stdout or "").strip()
    stderr_text = (stderr or "").strip()
    combined = f"{stdout_text}\n{stderr_text}".strip()
    if not combined:
        return ""

    failed_matches = _FAILED_TEST_RE.findall(combined)
    failed_count = 0
    count_match = _PYTEST_COUNT_RE.search(combined)
    if count_match:
        try:
            failed_count = int(count_match.group(1))
        except ValueError:
            failed_count = 0
    if failed_count <= 0:
        failed_count = len(failed_matches)

    traceback_match = _TRACEBACK_LINE_RE.search(combined)
    top_error = traceback_match.group(1).strip() if traceback_match else ""

    lines: list[str] = []
    if failed_count > 0:
        lines.append(f"- Failed tests: {failed_count}")
    if failed_matches:
        preview = ", ".join(failed_matches[:3])
        lines.append(f"- First failing targets: {preview}")
    if top_error:
        lines.append(f"- Top traceback: {top_error}")

    if not lines:
        first_line = (stderr_text or stdout_text).splitlines()[0] if (stderr_text or stdout_text) else "unknown"
        lines.append(f"- Validation output: {first_line}")

    lines.append("- Suggested next step: address the first failing target and rerun checks.")
    return "\n".join(lines)
