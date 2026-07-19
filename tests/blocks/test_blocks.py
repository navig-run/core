"""Blocks Stage-1: happy path + the security floor.

Run: cd core && python -m pytest tests/blocks -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def space(tmp_path, monkeypatch):
    """A tmp NAVIG config dir + a project root with a .navig/ (isolated)."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    proj = tmp_path / "proj"
    (proj / ".navig").mkdir(parents=True)
    monkeypatch.chdir(proj)
    return proj


def _write_block(proj: Path, block_id: str, body: str) -> Path:
    d = proj / ".navig" / "blocks" / block_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "BLOCK.md").write_text(body, encoding="utf-8")
    return d / "BLOCK.md"


_MATERIALIZE = """\
---
id: {id}
spec_version: 1
name: {id}
version: 0.1.0
category: general
license: MIT
target: local
inputs:
  - key: message
    type: string
    required: true
    default: hi
steps:
  - id: write
    kind: materialize
    capabilities: [filesystem:write:workdir]
    dest: "{{{{workdir}}}}/.navig/out/{id}.txt"
    content: "{{{{inputs.message}}}}\\n"
verify:
  kind: file_exists
  level: self-check
  path: "{{{{workdir}}}}/.navig/out/{id}.txt"
---
body
"""


# ── Parsing + validation ──────────────────────────────────────────────


def test_parse_and_validate_ok(space):
    from navig.blocks.loader import parse_block_file, validate_block

    bf = _write_block(space, "ok", _MATERIALIZE.format(id="ok"))
    block = parse_block_file(bf)
    assert block.id == "ok"
    assert block.digest.startswith("sha256:")
    assert validate_block(block) == []


def test_reject_secret_in_argv(space):
    from navig.blocks.loader import parse_block_file, validate_block

    body = """\
---
id: leak
spec_version: 1
name: leak
version: 0.1.0
category: x
license: MIT
target: local
inputs:
  - key: token
    type: secret
steps:
  - id: s
    kind: command
    capabilities: [exec:echo]
    argv: ["echo", "{{inputs.token}}"]
verify: {kind: none}
---
b
"""
    problems = validate_block(parse_block_file(_write_block(space, "leak", body)))
    assert any("secret" in p and "argv" in p for p in problems)


def test_reject_undeclared_token(space):
    from navig.blocks.loader import parse_block_file, validate_block

    body = """\
---
id: undec
spec_version: 1
name: undec
version: 0.1.0
category: x
license: MIT
target: local
inputs: []
steps:
  - id: s
    kind: command
    capabilities: [exec:echo]
    argv: ["echo", "{{inputs.nope}}"]
verify: {kind: none}
---
b
"""
    problems = validate_block(parse_block_file(_write_block(space, "undec", body)))
    assert any("undeclared" in p for p in problems)


def test_reject_vault_token_in_argv(space):
    from navig.blocks.loader import parse_block_file, validate_block

    body = """\
---
id: vaulty
spec_version: 1
name: vaulty
version: 0.1.0
category: x
license: MIT
target: local
inputs: []
steps:
  - id: s
    kind: command
    capabilities: [exec:echo]
    argv: ["echo", "{{vault.secret}}"]
verify: {kind: none}
---
b
"""
    problems = validate_block(parse_block_file(_write_block(space, "vaulty", body)))
    assert any("secret" in p.lower() for p in problems)


def test_reject_non_local_target(space):
    from navig.blocks.loader import parse_block_file, validate_block

    body = _MATERIALIZE.format(id="remote").replace("target: local", "target: remote")
    problems = validate_block(parse_block_file(_write_block(space, "remote", body)))
    assert any("target" in p for p in problems)


# ── Risk computation ──────────────────────────────────────────────────


def test_capability_risk():
    from navig.blocks.policy import capability_risk

    assert capability_risk(["filesystem:write:workdir"]) == "safe"
    assert capability_risk(["exec:steamcmd", "network:x"]) == "moderate"
    assert capability_risk(["sudo", "exec:apt"]) == "destructive"
    assert capability_risk(["publish"]) == "destructive"
    assert capability_risk(["filesystem:write:/etc"]) == "destructive"


# ── Execution: happy path + receipt ───────────────────────────────────


def test_apply_materialize_succeeds(space):
    from navig.blocks.loader import parse_block_file
    from navig.blocks.receipts import build_receipt, persist_receipt
    from navig.blocks.runner import apply_block

    block = parse_block_file(_write_block(space, "hello", _MATERIALIZE.format(id="hello")))
    run = apply_block(block, {"message": "world"}, yes=True, workdir=space)
    assert run.outcome == "succeeded"
    assert run.verification_level == "self-check"
    assert (space / ".navig" / "out" / "hello.txt").read_text().strip() == "world"

    receipt = build_receipt(block, run, {"message": "world"}, trust="first-party")
    p = persist_receipt(receipt)
    assert json.loads(p.read_text())["outcome"] == "succeeded"
    # run journal exists with a terminal state
    assert Path(run.journal_path).exists()


def test_no_verify_reports_none_not_selfcheck(space):
    """A block that ran steps but declares no verify must not claim 'self-check'."""
    from navig.blocks.loader import parse_block_file
    from navig.blocks.runner import apply_block

    body = """\
---
id: noverify
spec_version: 1
name: noverify
version: 0.1.0
category: x
license: MIT
target: local
inputs: []
steps:
  - id: w
    kind: materialize
    capabilities: [filesystem:write:workdir]
    dest: "{{workdir}}/.navig/out/nv.txt"
    content: "x\\n"
verify: {kind: none}
---
b
"""
    block = parse_block_file(_write_block(space, "noverify", body))
    run = apply_block(block, {}, yes=True, workdir=space)
    assert run.outcome == "succeeded"
    assert run.verification_level == "none"


def test_dry_run_never_writes_or_resolves_secret(space, monkeypatch):
    from navig.blocks import runner
    from navig.blocks.loader import parse_block_file
    from navig.blocks.runner import apply_block

    called = {"n": 0}

    def _boom(_inp):
        called["n"] += 1
        raise AssertionError("secret resolved during dry-run")

    monkeypatch.setattr(runner, "_default_secret_resolver", _boom)

    body = """\
---
id: dry
spec_version: 1
name: dry
version: 0.1.0
category: x
license: MIT
target: local
inputs:
  - key: token
    type: secret
steps:
  - id: w
    kind: materialize
    capabilities: [filesystem:write:workdir]
    dest: "{{workdir}}/.navig/out/dry.txt"
    content: "static\\n"
verify: {kind: none}
---
b
"""
    block = parse_block_file(_write_block(space, "dry", body))
    run = apply_block(block, {}, dry_run=True, workdir=space)
    assert run.outcome == "planned"
    assert called["n"] == 0
    assert not (space / ".navig" / "out" / "dry.txt").exists()  # no mutation


def test_destructive_step_requires_named_approval(space):
    from navig.blocks.loader import parse_block_file
    from navig.blocks.runner import apply_block

    py = sys.executable.replace("\\", "/")
    body = f"""\
---
id: destro
spec_version: 1
name: destro
version: 0.1.0
category: x
license: MIT
target: local
inputs: []
steps:
  - id: nuke
    kind: command
    safety: destructive
    capabilities: [sudo, "exec:python"]
    argv: ["{py}", "-c", "print('ran')"]
verify: {{kind: none}}
---
b
"""
    block = parse_block_file(_write_block(space, "destro", body))

    blocked = apply_block(block, {}, yes=True, workdir=space)  # --yes only
    assert blocked.outcome == "failed"
    assert blocked.steps[0].status == "blocked"

    ok = apply_block(block, {}, yes=True, approvals={"nuke"}, workdir=space)
    assert ok.steps[0].status == "ok"


def test_secret_absent_from_receipt(space, monkeypatch):
    from navig.blocks import runner
    from navig.blocks.loader import parse_block_file
    from navig.blocks.receipts import build_receipt
    from navig.blocks.runner import apply_block

    monkeypatch.setattr(runner, "_default_secret_resolver", lambda inp: "SUPERSECRET123")

    py = sys.executable.replace("\\", "/")
    body = f"""\
---
id: sec
spec_version: 1
name: sec
version: 0.1.0
category: x
license: MIT
target: local
inputs:
  - key: token
    type: secret
steps:
  - id: use
    kind: command
    capabilities: ["exec:python"]
    secret_env: {{TOK: inputs.token}}
    argv: ["{py}", "-c", "import os; print('len', len(os.environ.get('TOK','')))"]
verify: {{kind: none}}
---
b
"""
    block = parse_block_file(_write_block(space, "sec", body))
    run = apply_block(block, {}, yes=True, workdir=space)
    receipt = build_receipt(block, run, {}, trust="first-party")
    blob = receipt.to_json()
    assert "SUPERSECRET123" not in blob
    assert "‹secret›" in blob or "secret" in blob.lower()


# ── Provenance / lockfile ─────────────────────────────────────────────


def test_lockfile_digest_mismatch_rejected(space):
    from datetime import datetime, timezone

    from navig.blocks.policy import TrustError, verify_locked_digest, write_lock_entry

    write_lock_entry(space, "x", version="1.0.0", digest="sha256:aaa", source="s",
                     trust="first-party", installed_at=datetime.now(timezone.utc).isoformat())
    verify_locked_digest(space, "x", "sha256:aaa")  # match: no raise
    with pytest.raises(TrustError):
        verify_locked_digest(space, "x", "sha256:bbb")


def test_path_traversal_rejected(space):
    from navig.blocks.policy import PolicyError, safe_dest

    with pytest.raises(PolicyError):
        safe_dest("../../etc/evil", [space])
    # in-root is fine
    ok = safe_dest(str(space / "sub" / "f.txt"), [space])
    assert str(space) in str(ok)


# ── Composition guards ────────────────────────────────────────────────


def test_block_cycle_detected(space):
    from navig.blocks.loader import parse_block_file
    from navig.blocks.policy import PolicyError
    from navig.blocks.runner import apply_block

    block = parse_block_file(_write_block(space, "self", _MATERIALIZE.format(id="self")))
    with pytest.raises(PolicyError):
        apply_block(block, {"message": "x"}, yes=True, workdir=space,
                    _ancestry=("self",))


def test_depth_limit(space):
    from navig.blocks.loader import parse_block_file
    from navig.blocks.policy import PolicyError
    from navig.blocks.runner import apply_block

    block = parse_block_file(_write_block(space, "deep", _MATERIALIZE.format(id="deep")))
    with pytest.raises(PolicyError):
        apply_block(block, {"message": "x"}, yes=True, workdir=space, _depth=99)


# ── Skill shim + legacy normalization ─────────────────────────────────


def test_skill_shim_generated(space):
    from navig.blocks.loader import write_skill_shim

    bf = _write_block(space, "shimmed", _MATERIALIZE.format(id="shimmed"))
    out = write_skill_shim(bf.parent)
    text = out.read_text(encoding="utf-8")
    assert out.name == "SKILL.md"
    assert "generated-by: navig-block" in text
    assert "navig apply shimmed" in text


def test_normalize_legacy_asset(space):
    from navig.blocks.loader import normalize_legacy_asset, parse_block_file, validate_block

    d = space / ".navig" / "blocks" / "oldpb"
    d.mkdir(parents=True)
    (d / "README.md").write_text("---\nname: Old PB\n---\nStep one. Step two.\n", encoding="utf-8")
    out = normalize_legacy_asset(d, "oldpb")
    block = parse_block_file(out)
    assert block.id == "oldpb"
    assert validate_block(block) == []
    assert block.steps[0].kind == "instruction"


# ── Plugin-shipped block discovery ────────────────────────────────────


def test_plugin_shipped_block_discovered(space, monkeypatch):
    """A block a plugin ships in its own `blocks/` dir is discoverable — the seam
    that lets navig-mobile own `app-ui-verify` without re-vendoring it into core."""
    import navig.plugins.package as pkg
    from navig.blocks.loader import find_block, get_block_dirs

    pdir = space / "fake_plugin" / "blocks"
    (pdir / "plugblock").mkdir(parents=True)
    (pdir / "plugblock" / "BLOCK.md").write_text(
        _MATERIALIZE.format(id="plugblock"), encoding="utf-8")

    # get_block_dirs imports plugin_capability_dirs lazily from this module.
    monkeypatch.setattr(pkg, "plugin_capability_dirs",
                        lambda kind: [pdir] if kind == "blocks" else [])

    assert pdir.resolve() in {p.resolve() for p in get_block_dirs()}
    block = find_block("plugblock")
    assert block is not None and block.id == "plugblock"


# ── Requirements gate (declared `requires:` tools/plugins) ────────────


_REQ_TEMPLATE = """\
---
id: reqd
spec_version: 1
name: reqd
version: 0.1.0
category: x
license: MIT
target: local
requires:
  tools: [{tool}]
inputs: []
steps:
  - id: write
    kind: materialize
    capabilities: [filesystem:write:workdir]
    dest: "{{{{workdir}}}}/.navig/out/reqd.txt"
    content: "ran\\n"
verify: {{kind: none}}
---
b
"""


def test_unmet_requirements_tool(monkeypatch):
    from navig.blocks.policy import unmet_requirements

    assert unmet_requirements({}) == []
    assert unmet_requirements(None) == []
    monkeypatch.setattr("shutil.which", lambda n: None)  # nothing on PATH
    out = unmet_requirements({"tools": ["agent-device"]})
    assert len(out) == 1 and "agent-device" in out[0]
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/" + n)  # present
    assert unmet_requirements({"tools": ["agent-device"]}) == []


def test_unmet_requirements_plugin(monkeypatch):
    from navig.blocks import policy

    monkeypatch.setattr(policy, "_plugin_installed", lambda name: name == "installed-plugin")
    assert policy.unmet_requirements({"plugins": ["installed-plugin"]}) == []
    out = policy.unmet_requirements({"plugins": ["missing-plugin"]})
    assert len(out) == 1 and "missing-plugin" in out[0]


def test_apply_fails_fast_on_unmet_requirement(space, monkeypatch):
    from navig.blocks.loader import parse_block_file
    from navig.blocks.runner import apply_block

    monkeypatch.setattr("shutil.which", lambda n: None)  # required tool absent
    bf = _write_block(space, "reqd", _REQ_TEMPLATE.format(tool="agent-device"))
    run = apply_block(parse_block_file(bf), {}, yes=True, workdir=space)
    assert run.outcome == "failed"
    assert "agent-device" in (run.error or "")
    assert run.steps == []  # the gate ran BEFORE any step
    assert not (space / ".navig" / "out" / "reqd.txt").exists()  # nothing mutated


def test_dry_run_surfaces_unmet_requirement(space, monkeypatch):
    from navig.blocks.loader import parse_block_file
    from navig.blocks.runner import apply_block

    monkeypatch.setattr("shutil.which", lambda n: None)
    bf = _write_block(space, "reqd", _REQ_TEMPLATE.format(tool="agent-device"))
    run = apply_block(parse_block_file(bf), {}, dry_run=True, workdir=space)
    assert run.outcome == "planned"  # dry-run still plans (mutates nothing)
    assert "agent-device" in str(run.evidence.get("unmet_requirements"))


def test_apply_runs_when_requirement_met(space, monkeypatch):
    from navig.blocks.loader import parse_block_file
    from navig.blocks.runner import apply_block

    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/" + n)  # required tool present
    bf = _write_block(space, "reqd", _REQ_TEMPLATE.format(tool="agent-device"))
    run = apply_block(parse_block_file(bf), {}, yes=True, workdir=space)
    assert run.outcome == "succeeded"
    assert (space / ".navig" / "out" / "reqd.txt").exists()


# ── detect probes (requires.detect runtime availability checks) ───────


def test_run_detect_probes_pass_fail_output():
    from navig.blocks.policy import run_detect_probes

    py = sys.executable
    assert run_detect_probes({}) == []
    assert run_detect_probes(None) == []
    # exit 0 → ok
    r = run_detect_probes({"detect": [{"label": "ok", "run": [py, "-c", "raise SystemExit(0)"]}]})
    assert len(r) == 1 and r[0].ok and "exit=0" in r[0].detail
    # exit 3, expect 0 → fail
    r = run_detect_probes({"detect": [{"label": "bad", "run": [py, "-c", "raise SystemExit(3)"]}]})
    assert not r[0].ok and "exit=3" in r[0].detail
    # expect_output present → ok; absent → fail
    ok = run_detect_probes({"detect": [{"run": [py, "-c", "print('device')"], "expect_output": "device"}]})
    assert ok[0].ok
    bad = run_detect_probes({"detect": [{"run": [py, "-c", "print('device')"], "expect_output": "offline"}]})
    assert not bad[0].ok


def test_run_detect_probes_missing_and_invalid():
    from navig.blocks.policy import run_detect_probes

    r = run_detect_probes({"detect": [{"label": "nope", "run": ["definitely-not-real-xyz-123"]}]})
    assert not r[0].ok and "not found" in r[0].detail
    r = run_detect_probes({"detect": [{"label": "invalid"}]})  # no run argv
    assert not r[0].ok and "run" in r[0].detail
    r = run_detect_probes({"detect": ["not-a-dict"]})
    assert not r[0].ok


def test_run_detect_probes_timeout():
    from navig.blocks.policy import run_detect_probes

    r = run_detect_probes(
        {"detect": [{"label": "slow", "run": [sys.executable, "-c", "import time; time.sleep(5)"]}]},
        timeout=0.3)
    assert not r[0].ok and "timed out" in r[0].detail


def test_apply_blocks_on_failed_detect_probe(space, monkeypatch):
    from navig.blocks import policy
    from navig.blocks.loader import parse_block_file
    from navig.blocks.runner import apply_block

    # tools/plugins fine, but a detect probe fails. Patch the real seam (policy) —
    # the runner reaches probes through check_requirements(probe=True).
    monkeypatch.setattr(policy, "run_detect_probes",
                        lambda req, **k: [policy.ProbeResult("emulator", False, "exit=1", "boot an emulator")])
    bf = _write_block(space, "hello", _MATERIALIZE.format(id="hello"))
    run = apply_block(parse_block_file(bf), {"message": "x"}, yes=True, workdir=space)
    assert run.outcome == "failed"
    assert "emulator" in (run.error or "") and "boot an emulator" in (run.error or "")
    assert run.steps == []  # gate before steps
    assert not (space / ".navig" / "out" / "hello.txt").exists()


def test_dry_run_does_not_execute_detect_probes(space, monkeypatch):
    from navig.blocks import policy
    from navig.blocks.loader import parse_block_file
    from navig.blocks.runner import apply_block

    def _boom(req, **k):
        raise AssertionError("detect probes must NOT run in dry-run")

    monkeypatch.setattr(policy, "run_detect_probes", _boom)
    bf = _write_block(space, "hello", _MATERIALIZE.format(id="hello"))
    run = apply_block(parse_block_file(bf), {"message": "x"}, dry_run=True, workdir=space)
    assert run.outcome == "planned"  # planned without ever probing


def test_block_doctor_exit_codes(space, monkeypatch):
    from typer.testing import CliRunner

    from navig.commands.block import block_app

    cli = CliRunner()
    _write_block(space, "reqd", _REQ_TEMPLATE.format(tool="agent-device"))

    monkeypatch.setattr("shutil.which", lambda n: None)  # required tool absent
    res = cli.invoke(block_app, ["doctor", "reqd"])
    assert res.exit_code == 1 and "agent-device" in res.stdout

    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/" + n)  # present
    res = cli.invoke(block_app, ["doctor", "reqd"])
    assert res.exit_code == 0


def test_block_doctor_no_requirements(space):
    from typer.testing import CliRunner

    from navig.commands.block import block_app

    cli = CliRunner()
    _write_block(space, "hello", _MATERIALIZE.format(id="hello"))
    res = cli.invoke(block_app, ["doctor", "hello"])
    assert res.exit_code == 0 and "no requirements" in res.stdout.lower()


def test_block_doctor_json(space, monkeypatch):
    import json as _json

    from typer.testing import CliRunner

    from navig.commands.block import block_app

    cli = CliRunner()
    _write_block(space, "reqd", _REQ_TEMPLATE.format(tool="agent-device"))
    monkeypatch.setattr("shutil.which", lambda n: None)
    res = cli.invoke(block_app, ["doctor", "reqd", "--json"])
    assert res.exit_code == 1
    data = _json.loads(res.stdout)
    assert data["ok"] is False and data["checks"][0]["kind"] == "tool"


# ── per-requirement install commands + one source of truth ───────────────────

_REQ_HINTS = """\
---
id: hinted
spec_version: 1
name: hinted
version: 0.1.0
category: x
license: MIT
target: local
requires:
  tools:
    - name: agent-device
      install: npm install -g agent-device@latest
  plugins:
    - name: navig-mobile
      install: pip install "navig-mobile[all]"
inputs: []
steps:
  - id: write
    kind: materialize
    capabilities: [filesystem:write:workdir]
    dest: "{{workdir}}/.navig/out/hinted.txt"
    content: "ran\\n"
verify: {kind: none}
---
b
"""


def test_requirement_entries_accepts_both_forms():
    from navig.blocks.policy import requirement_entries

    assert requirement_entries(None) == []
    assert requirement_entries(["adb"]) == [("adb", "")]
    assert requirement_entries([{"name": "agent-device", "install": "npm i -g agent-device"}]) == [
        ("agent-device", "npm i -g agent-device")]
    # mixed, plus junk that must not crash the gate
    assert requirement_entries(["adb", {"name": "x"}, {}, "", 7, None]) == [("adb", ""), ("x", "")]


def test_check_requirements_uses_the_blocks_own_install_command(monkeypatch):
    """REGRESSION: doctor printed a generic `pip install <name>` that is simply wrong for
    a plugin with extras — and dropped any non-string requirement entirely."""
    from navig.blocks import policy

    monkeypatch.setattr("shutil.which", lambda n: None)
    monkeypatch.setattr(policy, "_plugin_installed", lambda name: False)
    req = {
        "tools": [{"name": "agent-device", "install": "npm install -g agent-device@latest"}],
        "plugins": [{"name": "navig-mobile", "install": 'pip install "navig-mobile[all]"'}],
    }
    checks = policy.check_requirements(req)
    assert [c.name for c in checks] == ["agent-device", "navig-mobile"]  # NOT dropped
    assert checks[0].fix == "npm install -g agent-device@latest"
    assert checks[1].fix == 'pip install "navig-mobile[all]"'
    # …and the fix reaches the runner's failure message
    msgs = policy.unmet_requirements(req)
    assert "npm install -g agent-device@latest" in msgs[0]
    assert 'pip install "navig-mobile[all]"' in msgs[1]


def test_check_requirements_falls_back_to_generic_hint(monkeypatch):
    from navig.blocks import policy

    monkeypatch.setattr("shutil.which", lambda n: None)
    monkeypatch.setattr(policy, "_plugin_installed", lambda name: False)
    checks = policy.check_requirements({"tools": ["adb"], "plugins": ["navig-x"]})
    assert checks[0].fix == "install it and put it on PATH"
    assert checks[1].fix == "pip install navig-x"


def test_check_requirements_is_pure_unless_probing(monkeypatch):
    """The display paths (`block show`, dry-run) must never execute a detect probe."""
    from navig.blocks import policy

    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/x")
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr(policy, "run_detect_probes", _boom)
    req = {"tools": ["x"], "detect": [{"run": ["echo", "hi"]}]}
    policy.check_requirements(req)                 # default probe=False
    assert called["n"] == 0
    policy.check_requirements(req, probe=True)     # opt in
    assert called["n"] == 1


def test_apply_gate_reports_the_real_install_command(space, monkeypatch):
    from navig.blocks import policy
    from navig.blocks.loader import parse_block_file
    from navig.blocks.runner import apply_block

    monkeypatch.setattr("shutil.which", lambda n: None)
    monkeypatch.setattr(policy, "_plugin_installed", lambda name: False)
    bf = _write_block(space, "hinted", _REQ_HINTS)
    run = apply_block(parse_block_file(bf), inputs={}, project_root=space)
    assert run.outcome == "failed"
    assert "npm install -g agent-device@latest" in run.error
    assert 'pip install "navig-mobile[all]"' in run.error


def test_block_doctor_prints_the_real_install_command(space, monkeypatch):
    import json as _json

    from typer.testing import CliRunner

    from navig.blocks import policy
    from navig.commands.block import block_app

    monkeypatch.setattr("shutil.which", lambda n: None)
    monkeypatch.setattr(policy, "_plugin_installed", lambda name: False)
    _write_block(space, "hinted", _REQ_HINTS)
    cli = CliRunner()

    res = cli.invoke(block_app, ["doctor", "hinted"])
    assert res.exit_code == 1
    assert "npm install -g agent-device@latest" in res.stdout

    res = cli.invoke(block_app, ["doctor", "hinted", "--json"])
    data = _json.loads(res.stdout)
    fixes = {c["name"]: c["fix"] for c in data["checks"]}
    assert fixes["agent-device"] == "npm install -g agent-device@latest"
    assert fixes["navig-mobile"] == 'pip install "navig-mobile[all]"'


# ── author-time `requires:` lint (navig block verify) ────────────────────────

def _requires_problems(requires) -> list[str]:
    from navig.blocks.loader import _validate_requires

    return _validate_requires(requires)


def test_validate_requires_rejects_the_ignored_permissions_key():
    """`permissions` was 'reserved, not enforced' — a requirement the gate silently
    ignores is worse than none, because the author believes it is enforced."""
    problems = _requires_problems({"permissions": ["screen-recording"]})
    assert len(problems) == 1
    assert "not enforced" in problems[0] and "detect" in problems[0]


def test_validate_requires_catches_malformed_shapes():
    assert _requires_problems(None) == []
    assert _requires_problems({"tools": ["adb"], "plugins": ["navig-mobile"]}) == []
    assert "must be a mapping" in _requires_problems(["adb"])[0]
    assert "must be a list" in _requires_problems({"tools": "adb"})[0]          # common typo
    assert "unknown key" in _requires_problems({"tool": ["adb"]})[0]            # singular typo
    assert "requires a 'name'" in _requires_problems({"tools": [{"install": "x"}]})[0]
    assert "unknown key 'instal'" in _requires_problems(
        {"tools": [{"name": "adb", "instal": "x"}]})[0]                          # misspelt install
    assert "empty name" in _requires_problems({"plugins": [""]})[0]


def test_validate_requires_catches_malformed_detect_probes():
    assert _requires_problems({"detect": [{"run": ["adb", "get-state"]}]}) == []
    assert "non-empty 'run'" in _requires_problems({"detect": [{"label": "x"}]})[0]
    assert "no shell string" in _requires_problems({"detect": [{"run": ["adb", 7]}]})[0]
    assert "must be an integer" in _requires_problems(
        {"detect": [{"run": ["adb"], "expect_exit": "0"}]})[0]
    assert "must be a list" in _requires_problems({"detect": {"run": ["adb"]}})[0]


def test_validate_block_surfaces_requires_problems(space):
    """The lint reaches `navig block verify` — an author sees it before a live apply."""
    from navig.blocks.loader import parse_block_file, validate_block

    bad = _REQ_HINTS.replace(
        "  tools:\n    - name: agent-device\n      install: npm install -g agent-device@latest",
        "  permissions: [screen-recording]")
    bf = _write_block(space, "hinted", bad)
    problems = validate_block(parse_block_file(bf))
    assert any("permissions" in p and "detect" in p for p in problems)


# ── advisory lint: a step that runs an undeclared binary ─────────────────────

_UNDECLARED_TOOL = """\
---
id: undeclared
spec_version: 1
name: undeclared
version: 0.1.0
category: x
license: MIT
target: local
{requires}inputs: []
steps:
  - id: nav
    kind: command
    capabilities: [exec:navig]
    argv: [navig, host, list]
  - id: publish
    kind: command
    capabilities: [exec:msstore, publish]
    argv: [msstore, publish, "app.msix"]
verify: {{kind: none}}
---
b
"""


def _undeclared_block(space, *, declare: bool):
    from navig.blocks.loader import parse_block_file

    req = ("requires:\n  tools:\n    - name: msstore\n"
           "      install: dotnet tool install --global MSStore.CLI\n") if declare else ""
    bf = _write_block(space, "undeclared", _UNDECLARED_TOOL.format(requires=req))
    return parse_block_file(bf)


def test_lint_flags_a_step_that_runs_an_undeclared_binary(space):
    """The exact trap this catches: `doctor` says "no requirements — ready to apply",
    then the apply dies at step 1 with 'msstore: command not found'."""
    from navig.blocks.loader import lint_block, validate_block

    b = _undeclared_block(space, declare=False)
    assert validate_block(b) == []          # NOT a hard error — it must still apply
    findings = lint_block(b)
    # exactly one: `msstore` is flagged, the `navig` step is not (it is the host CLI)
    assert len(findings) == 1
    assert "msstore" in findings[0] and "requires.tools" in findings[0]


def test_lint_is_clean_once_the_tool_is_declared(space):
    from navig.blocks.loader import lint_block

    assert lint_block(_undeclared_block(space, declare=True)) == []


_TEMPLATED_ARGV = """\
---
id: templated
spec_version: 1
name: templated
version: 0.1.0
category: x
license: MIT
target: local
inputs:
  - key: tool
    type: string
    required: true
steps:
  - id: run
    kind: command
    capabilities: [exec:tool]
    argv: ["{{inputs.tool}}", "--go"]
verify: {kind: none}
---
b
"""


def test_lint_ignores_templated_argv(space):
    """A templated argv[0] resolves at apply time — nothing to check statically."""
    from navig.blocks.loader import lint_block, parse_block_file

    bf = _write_block(space, "templated", _TEMPLATED_ARGV)
    assert lint_block(parse_block_file(bf)) == []


def test_block_verify_warns_but_does_not_refuse(space):
    """Advisories never block: validation is enforced at apply, so promoting these to
    problems would refuse third-party blocks that already run."""
    from typer.testing import CliRunner

    from navig.commands.block import block_app

    _undeclared_block(space, declare=False)
    res = CliRunner().invoke(block_app, ["verify", "undeclared"])
    assert res.exit_code == 0                       # still valid
    assert "advisory" in res.stdout.lower()
    assert "msstore" in res.stdout


# ── the guard that was missing: every SHIPPED block is sound ─────────────────

def _repo_root() -> Path:
    # core/tests/blocks/test_blocks.py → blocks → tests → core → repo root
    return Path(__file__).resolve().parents[3]


def _builtin_blocks_dir() -> Path:
    """The blocks bundled INSIDE the package — the ones `navig apply` actually reads."""
    from navig.platform.paths import builtin_store_dir

    return builtin_store_dir() / "blocks"


def _shipped_block_files() -> list[Path]:
    """Every block that is tracked and shipped, from all three homes:

    1. ``navig/builtin/blocks/`` — bundled in the wheel; what `navig apply` resolves.
    2. ``registry/blocks/`` — the marketplace catalog (browse / install).
    3. ``plugins/*/*/blocks/`` — plugin-owned (applyable when the plugin is installed).

    This list used to omit (1) entirely, with the note *"`core/store/` is gitignored
    runtime content, not source"*. Both halves of that were wrong: `core/store` is not
    gitignored (it was simply never `git add`-ed), and it was never a *runtime* dir — it
    was the OLD builtin store, left behind when the store moved into the package. So the
    twelve builtin blocks sat there untracked, absent from every wheel and every fresh
    clone, and this guard — the one meant to keep the catalog honest — was told to look
    away. Meanwhile test_workflow.py kept failing because they were missing. Two tests
    disagreed about reality and the failing one was ignored.
    """
    root = _repo_root()
    files = sorted(_builtin_blocks_dir().glob("*/BLOCK.md"))
    files += sorted((root / "registry" / "blocks").glob("*/BLOCK.md"))
    files += sorted((root / "plugins").glob("*/*/blocks/*/BLOCK.md"))
    return files


def test_the_builtin_block_catalog_is_not_empty():
    """`navig apply <id>` resolves against the package's builtin store. If that directory
    is empty, every builtin Block is a 404 — which is exactly what shipped."""
    files = sorted(_builtin_blocks_dir().glob("*/BLOCK.md"))
    assert files, (
        f"no BLOCK.md under {_builtin_blocks_dir()} — the builtin catalog is empty, so "
        "`navig apply safe-deployment` finds nothing. Blocks must live INSIDE the package "
        "(navig/builtin/blocks/) or they never reach a wheel."
    )


@pytest.mark.skipif(not (Path(__file__).resolve().parents[3] / "registry").is_dir(),
                    reason="registry/ is not present (standalone core checkout)")
def test_builtin_and_registry_copies_of_the_same_block_do_not_drift():
    """A block listed in the marketplace AND bundled in the wheel exists twice on disk.

    Nothing kept the two in step, and they drifted: `msstore-publish` gained a
    `requires.tools` declaration in registry/ (so `navig block doctor` could check for the
    MSStore CLI) while the store's copy kept none. Users browsing the Bay and users running
    `navig apply` were reading different blocks.
    """
    root = _repo_root()
    drifted = []
    for builtin in sorted(_builtin_blocks_dir().glob("*/BLOCK.md")):
        twin = root / "registry" / "blocks" / builtin.parent.name / "BLOCK.md"
        if not twin.is_file():
            continue  # builtin-only block — nothing to keep in step
        # splitlines() so a CRLF/LF difference is not reported as a content change
        if builtin.read_text(encoding="utf-8").splitlines() != twin.read_text(
            encoding="utf-8"
        ).splitlines():
            drifted.append(builtin.parent.name)
    assert not drifted, (
        "these blocks ship in the wheel AND in the marketplace catalog, and the two copies "
        f"have diverged: {drifted}. Update both, or the Bay advertises one block while "
        "`navig apply` runs another."
    )


@pytest.mark.skipif(not (Path(__file__).resolve().parents[3] / "registry").is_dir(),
                    reason="registry/ is not present (standalone core checkout)")
def test_every_shipped_block_parses_and_validates():
    """Nothing used to lint the shipped catalog — a malformed block could ship."""
    from navig.blocks.loader import parse_block_file, validate_block

    files = _shipped_block_files()
    assert files, "no shipped blocks found — the glob is wrong"
    for bf in files:
        block = parse_block_file(bf)
        assert block is not None, f"{bf} failed to parse"
        assert validate_block(block) == [], f"{bf.parent.name}: {validate_block(block)}"


@pytest.mark.skipif(not (Path(__file__).resolve().parents[3] / "registry").is_dir(),
                    reason="registry/ is not present (standalone core checkout)")
def test_every_shipped_block_declares_the_binaries_it_runs():
    """msstore-publish ran `msstore` and steam-build-upload ran `steamcmd` while both
    declared no requirements at all — so `navig block doctor` reported them ready and
    the apply died at the first step. This keeps the whole catalog honest."""
    from navig.blocks.loader import lint_block, parse_block_file

    offenders = {}
    for bf in _shipped_block_files():
        findings = lint_block(parse_block_file(bf))
        if findings:
            offenders[bf.parent.name] = findings
    assert offenders == {}, f"shipped blocks with undeclared binaries: {offenders}"


# ── vaulted block inputs actually resolve ────────────────────────────────────
# `{vault.NAME}` inputs resolved through `from navig.vault import get_secret` — a name
# that never existed, so the import always failed and every vaulted secret silently
# resolved to nothing (Blocks are the paid tier; this quietly broke them). The resolver
# now uses reveal_secret(get_vault(), label), the same reader `navig vault set`'s path
# resolver uses.

def test_vaulted_block_input_resolves_the_real_secret(tmp_path, monkeypatch):
    import navig.vault as _nv
    from navig.blocks.loader import BlockInput
    from navig.blocks.runner import _default_secret_resolver
    from navig.vault.core import Vault

    vault = Vault(vault_dir=tmp_path)
    # Stored exactly how `navig vault set` stores a secret: JSON with a "value" field.
    vault.put("MY_API_KEY", json.dumps({"value": "s3cr3t-value"}).encode())
    monkeypatch.setattr(_nv, "get_vault", lambda *a, **k: vault)

    inp = BlockInput(key="api_key", type="secret", vault="{vault.MY_API_KEY}")
    # A present secret must be returned WITHOUT falling through to the hidden prompt.
    assert _default_secret_resolver(inp) == "s3cr3t-value"


def test_vaulted_block_input_missing_secret_falls_through(tmp_path, monkeypatch):
    import navig.vault as _nv
    from navig.blocks.loader import BlockInput
    from navig.blocks.runner import _default_secret_resolver
    from navig.vault.core import Vault

    vault = Vault(vault_dir=tmp_path)  # empty
    monkeypatch.setattr(_nv, "get_vault", lambda *a, **k: vault)
    # No secret + no env override → resolver falls through to the interactive prompt
    # (typer.prompt, imported lazily). Patch it to a sentinel so the test never blocks.
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "PROMPTED")

    inp = BlockInput(key="api_key", type="secret", vault="{vault.NOPE}")
    # The vault miss must fall through to the prompt — never resolve to a wrong secret.
    assert _default_secret_resolver(inp) == "PROMPTED"
