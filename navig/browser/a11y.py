"""Shared helpers for browser accessibility snapshots."""

from __future__ import annotations

import re

_A11Y_ROLE_RE = re.compile(r'(\w[\w\s]*)\s*(?:"([^"]*)"|\[([^\]]*)\])?')


def annotate_a11y_snapshot(raw: str) -> tuple[str, dict[int, dict[str, str]]]:
    """Annotate an ARIA snapshot with stable numeric refs."""
    if not raw:
        return "", {}

    ref_map: dict[int, dict[str, str]] = {}
    annotated_lines: list[str] = []
    ref_id = 0

    for line in raw.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("- /"):
            annotated_lines.append(line)
            continue
        if stripped.startswith("- "):
            rest = stripped[2:]
            match = _A11Y_ROLE_RE.match(rest)
            role = match.group(1).strip() if match else rest.split()[0] if rest.split() else ""
            name = (match.group(2) or match.group(3) or "").strip() if match else ""
            ref_map[ref_id] = {"role": role, "name": name, "raw_line": line}
            indent = line[: len(line) - len(stripped)]
            annotated_lines.append(f"{indent}- [{ref_id}] {rest}")
            ref_id += 1
            continue
        annotated_lines.append(line)

    return "\n".join(annotated_lines), ref_map
