"""No shipped code turns off yt-dlp's TLS certificate check.

The plugin-lint "no disabled TLS" step (ruff S501-S504) matches HTTPS *calls* such as
``requests.get(..., verify=False)``. yt-dlp's switch is a dict KEY —
``{"nocheckcertificate": True}`` — or a keyword, which no ruff rule sees, so
navig-download's batch, profile and per-post downloads accepted any certificate
while that step stayed green. This scans the trees by path (core/navig plus every
plugin) for the switch set truthy, as a dict entry or a keyword argument.
"""

from __future__ import annotations

import ast
from pathlib import Path

# core/tests/quality/<this> -> parents[2] == core, parents[3] == repo root.
_CORE_ROOT = Path(__file__).resolve().parents[2] / "navig"
_PLUGINS_ROOT = Path(__file__).resolve().parents[3] / "plugins"

_EXCLUDE_DIRS = {"tests", "test", ".venv", "node_modules", ".dev", "build", "dist",
                 "__pycache__"}
_KEYS = {"nocheckcertificate", "no_check_certificate", "no_check_certificates"}


def _sources() -> list[Path]:
    roots = [_CORE_ROOT] + ([_PLUGINS_ROOT] if _PLUGINS_ROOT.is_dir() else [])
    out: list[Path] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if not _EXCLUDE_DIRS.intersection(path.relative_to(root).parts):
                out.append(path)
    return out


def _truthy(node: ast.AST) -> bool:
    """Anything but a literal falsy constant counts — a variable may well be True."""
    return not (isinstance(node, ast.Constant) and not node.value)


def _offences(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value in _KEYS
                        and _truthy(value)):
                    lines.append(key.lineno)
        elif isinstance(node, ast.keyword) and node.arg in _KEYS and _truthy(node.value):
            lines.append(node.value.lineno)
        elif (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript)
              and isinstance(node.targets[0].slice, ast.Constant)
              and node.targets[0].slice.value in _KEYS and _truthy(node.value)):
            lines.append(node.lineno)
    return lines


def test_detector_catches_every_spelling():
    src = (
        "a = {'nocheckcertificate': True}\n"
        "b = dict(nocheckcertificate=flag)\n"
        "c = {}\nc['nocheckcertificate'] = 1\n"
        "ok = {'nocheckcertificate': False}\n"
    )
    assert sorted(_offences(ast.parse(src))) == [1, 2, 4]


def test_no_shipped_code_disables_the_ytdlp_certificate_check():
    files = _sources()
    # Scan floor: an empty or truncated checkout must not pass by scanning nothing.
    assert len(files) > 500, f"only {len(files)} source files found — tree incomplete?"
    found = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        found += [f"{path.as_posix()}:{line}" for line in _offences(tree)]
    assert not found, ("yt-dlp TLS certificate check disabled — remove the switch:\n  "
                       + "\n  ".join(found))
