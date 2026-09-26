"""A loguru message written with `%s` / `%d` prints the placeholder, not the value.

loguru formats with ``str.format`` — ``logger.info("Freed port %d", port)`` prints
``Freed port %d`` and silently discards ``port``. Seven such calls sat in
``commands/gateway.py`` (the ones that name WHICH stale gateway was superseded, WHICH
PID holds the port and WHY it was left alone — the exact lines you read during an
incident); a fresh gateway boot printed
``Superseded %d stale gateway process(es): %s`` verbatim. 25 modules use loguru, so
the shape is worth a guard; measured 7 hits, all in that one file, all fixed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOTS = [REPO / "core" / "navig", *sorted((REPO / "plugins").glob("navig-*/navig_*"))]
_PERCENT = re.compile(r"%[sdrif]")


def _loguru_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "loguru":
            names |= {a.asname or a.name for a in node.names if a.name == "logger"}
    return names


def _offenders(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except SyntaxError:  # pragma: no cover - another guard's job
        return []
    names = _loguru_names(tree)
    if not names:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        target = node.func.value
        if not (isinstance(target, ast.Name) and target.id in names):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        msg = node.args[0].value
        if isinstance(msg, str) and _PERCENT.search(msg) and len(node.args) > 1:
            out.append(f"{path.relative_to(REPO)}:{node.lineno}")
    return out


def test_no_loguru_call_uses_percent_placeholders() -> None:
    hits: list[str] = []
    files = 0
    for root in ROOTS:
        for py in sorted(root.rglob("*.py")):
            files += 1
            hits += _offenders(py)
    assert files > 500, f"scan floor: only {files} files"
    assert not hits, (
        "loguru formats with str.format — these print `%s`/`%d` literally and drop the "
        "value. Use `{}`:\n  " + "\n  ".join(hits)
    )


def test_the_detector_sees_the_shape(tmp_path: Path) -> None:
    p = tmp_path / "probe.py"
    p.write_text(
        "from loguru import logger as _logger\n"
        '_logger.info("Freed port %d (PID %d)", 1, 2)\n'       # bad
        '_logger.info("Freed port {} (PID {})", 1, 2)\n'       # good
        '_logger.debug("100% done")\n'                          # a literal percent, no args
        "import logging\n"
        'logging.getLogger("x").info("stdlib %s is fine", 1)\n',  # not loguru
        encoding="utf-8",
    )
    assert [h.rsplit(":", 1)[1] for h in _offenders(p)] == ["2"]
