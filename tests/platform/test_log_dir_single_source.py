"""There is exactly ONE log directory for NAVIG's own logs: ``paths.log_dir()``.

Six modules kept building ``config_dir() / "logs"`` by hand after ``log_dir()`` became
canonical (``%LOCALAPPDATA%\\navig\\logs`` on Windows). The cost was measured on a live
machine on 2026-10-22: two log roots, ten crash dumps written into the legacy one the
same morning the doctor reported the canonical one, and ``navig agent learn`` aborting
with "No logs found to analyze" while ``remediation.log`` sat in the other root.

This test fails on any new module that builds a config-dir logs path by hand instead of
calling ``navig.platform.paths.log_dir()``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import navig

PKG = Path(navig.__file__).resolve().parent

# Where a config-dir log path may legitimately appear.
ALLOWED: dict[str, str] = {
    "platform/paths.py": "log_dir()'s own last-resort fallback when LOCALAPPDATA is unset",
    "core/crash_handler.py": (
        "the crash handler's last-resort fallback when paths.log_dir() itself raises — "
        "a crash handler must never be the thing that crashes"
    ),
    "commands/init.py": (
        "_local_log_dir() falls back to config_dir()/logs only when platformdirs is missing"
    ),
    "commands/debug_cmd.py": (
        "lists the legacy dir as well, so dumps written before this fix stay visible"
    ),
}


def _is_config_dir_call(node: ast.AST) -> bool:
    """True for ``config_dir()`` / ``get_config_dir()`` / ``paths.config_dir()``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in {"config_dir", "get_config_dir"}
    if isinstance(func, ast.Attribute):
        return func.attr in {"config_dir", "get_config_dir"}
    return False


def _is_config_root_attr(node: ast.AST) -> bool:
    """True for the ``... .base_dir`` spelling that bypasses paths.config_dir()."""
    return isinstance(node, ast.Attribute) and node.attr == "base_dir"


def _config_log_lines(tree: ast.AST) -> list[int]:
    """Line numbers of ``<config dir> / "logs"`` expressions."""
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        right = node.right
        if not (isinstance(right, ast.Constant) and right.value == "logs"):
            continue
        left = node.left
        if _is_config_dir_call(left) or _is_config_root_attr(left):
            hits.append(node.lineno)
    return sorted(set(hits))


def _offenders() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for py in sorted(PKG.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):  # pragma: no cover
            continue
        hits = _config_log_lines(tree)
        if hits:
            found[py.relative_to(PKG).as_posix()] = hits
    return found


def test_no_module_builds_a_config_dir_log_path() -> None:
    found = _offenders()
    unexpected = {m: h for m, h in found.items() if m not in ALLOWED}
    assert not unexpected, (
        "These modules build a log path under config_dir()/ instead of calling\n"
        "navig.platform.paths.log_dir(). On Windows the real log dir is\n"
        "%LOCALAPPDATA%/navig/logs — never ~/.navig — so whatever they write is invisible to\n"
        "the doctor, `navig crash` and every other reader.\n\n"
        + "\n".join(f"  {m}: line(s) {h}" for m, h in sorted(unexpected.items()))
    )


def test_allowlist_has_no_stale_entries() -> None:
    stale = sorted(set(ALLOWED) - set(_offenders()))
    assert not stale, (
        "These modules no longer build a config-dir log path — delete their ALLOWED entries:\n"
        + "\n".join(f"  {m}" for m in stale)
    )


def test_crash_dumps_land_in_the_canonical_log_dir(tmp_path, monkeypatch) -> None:
    """The property that matters: the crash writer and `navig crash` agree."""
    monkeypatch.setenv("NAVIG_LOG_DIR", str(tmp_path / "logs"))

    from navig.core.crash_handler import CrashHandler

    handler = CrashHandler()
    path = handler._log_crash_to_file(ValueError("boom"))

    assert path is not None, "the crash handler must still return the dump path"
    assert path.parent == tmp_path / "logs", (
        f"crash dump went to {path.parent}, not the canonical log_dir()"
    )
    assert path.is_file()
    assert handler._get_log_dir() == tmp_path / "logs"


def test_trace_and_remediation_writers_follow_log_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NAVIG_LOG_DIR", str(tmp_path / "logs"))

    from navig.llm.routing.trace import _trace_log_path

    assert _trace_log_path().parent == tmp_path / "logs", (
        "router traces are still written outside log_dir()"
    )

    from navig.agent.remediation import RemediationEngine

    engine = RemediationEngine(config_dir=tmp_path / "cfg", log_dir=None)
    assert engine.remediation_log.parent == tmp_path / "logs", (
        "remediation.log is still written outside log_dir()"
    )
