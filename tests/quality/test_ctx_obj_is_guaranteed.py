"""A sub-app that writes `ctx.obj[...]` must guarantee the dict exists.

The root `navig` callback calls `ctx.ensure_object(dict)`, so through the real CLI every
`ctx.obj["x"] = y` is fine. Reached any OTHER way — a test, a programmatic invoke of the
sub-app — `ctx.obj` is None and the assignment dies with:

    'NoneType' object does not support item assignment

That is not merely inconvenient. **A crash is also a non-zero exit**, so a regression test
asserting `exit_code == 2` for a usage error passes just as happily when the command never
ran at all. This bit twice while sweeping exit-honesty: `history.py` and `host.py` both
needed the guard before their failure paths could be tested at all, and `test_host_remove`
carries an explicit anti-vacuity test for exactly this reason.

Measured when this guard was added: 78 unguarded writes across 10 modules — `app` 29,
`skills` 12, `server_template` 11 (which had no group callback at all), then files,
webserver, server, local, tunnel, context, flow. Four modules (backup, db, history, host)
already followed the convention, so this makes the tree consistent rather than inventing
a rule.

The fix is a no-op in production by construction; it exists so that a test which reaches a
sub-app directly fails for the RIGHT reason.
"""
from __future__ import annotations

import re
from pathlib import Path

COMMANDS = Path(__file__).resolve().parents[2] / "navig" / "commands"

_WRITE = re.compile(r"ctx\.obj\[")
_TYPER_APP = re.compile(r"^\w+ = typer\.Typer\(", re.MULTILINE)


def _modules_writing_ctx_obj() -> list[tuple[Path, int]]:
    out: list[tuple[Path, int]] = []
    for path in sorted(COMMANDS.glob("*.py")):
        # utf-8-sig: a BOM is a SyntaxError to ast.parse and would make the file
        # invisible to every guard that reads it.
        src = path.read_text(encoding="utf-8-sig")
        writes = len(_WRITE.findall(src))
        if writes and _TYPER_APP.search(src):
            out.append((path, writes))
    return out


def test_every_ctx_obj_writer_guarantees_the_dict() -> None:
    scanned = _modules_writing_ctx_obj()

    assert len(scanned) > 5, (
        f"only {len(scanned)} modules matched — navig/commands/ has many more sub-apps "
        "that write ctx.obj. The detector is looking for the wrong thing and this guard "
        "is checking nothing."
    )

    unguarded = [
        f"{path.name} ({writes} write(s))"
        for path, writes in scanned
        if "ensure_object" not in path.read_text(encoding="utf-8-sig")
    ]

    assert not unguarded, (
        "These write ctx.obj[...] but never call ctx.ensure_object(dict), so reaching the "
        "sub-app without the root `navig` callback dies with \"'NoneType' object does not "
        "support item assignment\" — and because a crash is also non-zero, a test "
        "asserting an exit code can pass while the command never ran:\n  "
        + "\n  ".join(unguarded)
        + "\n\nAdd `ctx.ensure_object(dict)` as the first statement of the app's "
        "@<name>_app.callback(); add a callback if the app has none."
    )


def test_the_detector_sees_a_writer_without_the_guard(tmp_path) -> None:
    """Anti-vacuity: a detector that quietly matches nothing reports a clean tree for
    code it never read."""
    fake = tmp_path / "fake_cmd.py"
    fake.write_text(
        "import typer\n"
        "fake_app = typer.Typer()\n"
        "@fake_app.callback()\n"
        "def cb(ctx):\n"
        "    pass\n"
        "@fake_app.command('go')\n"
        "def go(ctx):\n"
        "    ctx.obj['x'] = 1\n",
        encoding="utf-8",
    )
    src = fake.read_text(encoding="utf-8")
    assert _WRITE.search(src) and _TYPER_APP.search(src)
    assert "ensure_object" not in src, "the fixture must model the UNGUARDED shape"


def test_a_module_without_a_typer_app_is_not_required_to_guard(tmp_path) -> None:
    """A plain helper module that happens to touch a passed-in ctx is not a sub-app and
    has no callback to put the guard in — flagging it would demand a fix that cannot be
    written."""
    fake = tmp_path / "helper.py"
    fake.write_text("def use(ctx):\n    ctx.obj['x'] = 1\n", encoding="utf-8")
    src = fake.read_text(encoding="utf-8")
    assert _WRITE.search(src)
    assert not _TYPER_APP.search(src), "no Typer app -> out of scope"
