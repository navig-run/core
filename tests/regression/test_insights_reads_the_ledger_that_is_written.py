"""`navig insights` analysed a ledger the recorder had never written to.

The last surface in the "who else touches this?" audit (a6d05a987 SSRF,
8a5b191c2 allowed_updates, 34002599a vault). The ledger's WRITE side is clean —
`OperationRecorder.record()` is the only appender, which is what keeps the hash chain
(`navig.ledger_chain`) intact. The READ side was not.

    OperationRecorder   config.base_dir / "history"          <- writer
    InsightsEngine      global_config_dir / "history"        <- reader

`base_dir` becomes the PROJECT `.navig/` whenever the cwd is inside a navig project
(`ConfigManager` -> `paths.find_app_root`), while `global_config_dir` is always
`~/.navig`. So every operation run inside a project was appended to
`<project>/.navig/history/operations.jsonl`, and `navig insights` read the global file —
reporting "no operations" over a ledger that was sitting right next to it. Not an error:
an empty analysis, which reads as "you have done nothing" rather than "I looked in the
wrong place".

`navig ledger` already had this right — `get_operation_recorder().history_file` — so the
fix is to ask the recorder rather than rebuild the path, and the test below pins the
INVARIANT (writer and reader agree) rather than either spelling of the path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def inside_a_project(tmp_path, monkeypatch):
    """cwd inside a navig project, with an isolated global config dir.

    This is the ONLY configuration where the bug appears: `base_dir` diverges from
    `global_config_dir` exactly when `find_app_root()` finds a project.
    """
    proj = tmp_path / "myproject"
    (proj / ".navig").mkdir(parents=True)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.chdir(proj)

    # Both are process-wide singletons; a cached instance from another test would
    # describe the wrong install.
    import navig.config as config_mod
    import navig.operation_recorder as rec_mod

    monkeypatch.setattr(rec_mod, "_recorder", None, raising=False)
    monkeypatch.setattr(config_mod, "_config_manager", None, raising=False)
    return proj, cfg


def test_insights_reads_the_file_the_recorder_writes(inside_a_project):
    """The invariant, stated as such: one ledger, not two."""
    from navig.commands.insights import InsightsEngine
    from navig.operation_recorder import get_operation_recorder

    recorder = get_operation_recorder()
    engine = InsightsEngine()

    assert Path(engine.history_file).resolve() == Path(recorder.history_file).resolve(), (
        "insights analyses a different ledger than the recorder writes — inside a "
        f"project the recorder appends to {recorder.history_file} while insights reads "
        f"{engine.history_file}"
    )


def test_a_recorded_operation_is_visible_to_insights(inside_a_project):
    """End-to-end, because agreeing on a path is only the mechanism.

    Drives the real recorder, then asks insights to load — the operation must come back.
    """
    from navig.commands.insights import InsightsEngine, TimeRange
    from navig.operation_recorder import (
        OperationRecord,
        OperationStatus,
        OperationType,
        get_operation_recorder,
    )

    recorder = get_operation_recorder()
    recorder.record(
        OperationRecord(
            operation_type=OperationType.LOCAL_COMMAND,
            status=OperationStatus.SUCCESS,
            command="echo hello",
        )
    )

    loaded = InsightsEngine()._load_history(TimeRange.ALL)
    assert [op.get("command") for op in loaded] == ["echo hello"], (
        "an operation the recorder just wrote was invisible to insights — the analytics "
        f"read an empty ledger and would report 'no operations'; loaded={loaded}"
    )


def test_the_ledger_still_has_exactly_one_appender() -> None:
    """The WRITE side is what keeps the hash chain intact — it must stay single.

    Every entry carries `prev`/`sig` computed in `OperationRecorder.record()`. A second
    appender would produce lines that `navig ledger verify` reports as a broken chain,
    and a broken chain is indistinguishable from tampering.
    """
    import ast

    navig_root = Path(__file__).resolve().parents[2] / "navig"

    offenders: list[str] = []
    for py in navig_root.rglob("*.py"):
        if {"__pycache__"} & set(py.parts):
            continue
        if py.name == "operation_recorder.py":
            continue  # the canonical appender
        try:
            tree = ast.parse(py.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            # open(<...operations.jsonl...>, "a") — an append that skips the chain
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name not in {"open", "write_text", "append_text"}:
                continue
            literals = [
                a.value for a in list(node.args) + [kw.value for kw in node.keywords]
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
            if any("operations.jsonl" in s for s in literals) and any(
                s in {"a", "at", "a+"} for s in literals
            ):
                offenders.append(f"{py.name}:{node.lineno}")

    assert not offenders, (
        "something appends to operations.jsonl outside OperationRecorder.record(), so "
        f"its lines carry no hash-chain fields and `navig ledger verify` will report a "
        f"broken chain: {offenders}"
    )
