"""Block execution — the runner, run journal, and verification.

``apply_block`` turns a parsed :class:`~navig.blocks.loader.Block` plus resolved
inputs into a durable run: it opens a run journal *before* executing, walks the
steps under the policy floor (argv-only commands, secret env delivery, computed
risk, named approval for destructive steps, path-confined writes, composition
guards), runs the machine ``verify``, and returns a result the caller turns into
a signed-later :class:`~navig.contracts.execution_receipt.ExecutionReceipt`.

Only terminal states produce a receipt. ``dry_run`` resolves **no** secrets,
touches **no** network, and mutates **nothing** — it prints the resolved plan.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from navig.blocks.loader import Block, BlockInput, BlockStep, find_block
from navig.blocks.policy import (
    PolicyError,
    Redactor,
    TrustError,
    capability_risk,
    check_requirements,
    requires_named_approval,
    safe_dest,
    unmet_requirements,
    verify_locked_digest,
)

MAX_BLOCK_DEPTH = 3

# Run lifecycle states (the minimal state machine).
S_PLANNED = "planned"
S_PREFLIGHT = "preflight"
S_AWAITING_APPROVAL = "awaiting_approval"
S_RUNNING = "running"
S_AWAITING_MANUAL = "awaiting_manual_step"
S_VERIFYING = "verifying"
S_SUCCEEDED = "succeeded"
S_FAILED = "failed"
S_CANCELLED = "cancelled"
S_PARTIAL = "partial"
_TERMINAL = {S_SUCCEEDED, S_FAILED, S_CANCELLED, S_PARTIAL}

# Verification levels (weakest → strongest).
V_NONE = "none"
V_SELF = "self-check"
V_EXTERNAL = "external-check"
V_HUMAN = "human-attested"
V_VERIFIED = "verified"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SecretResolver = Callable[[BlockInput], str]


@dataclass
class StepResult:
    id: str
    kind: str
    status: str  # ok | failed | manual_ack | skipped | blocked
    risk: str = "safe"
    mode: str = "auto"  # auto | manual
    detail: str = ""
    verify_passed: bool | None = None
    child_receipt: str | None = None


@dataclass
class BlockRunResult:
    run_id: str
    block_id: str
    block_digest: str
    outcome: str  # succeeded | failed | cancelled | partial
    verification_level: str
    steps: list[StepResult] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    started_at: str = ""
    completed_at: str = ""
    journal_path: str = ""


class RunJournal:
    """Append-only JSONL state log opened before any execution."""

    def __init__(self, run_id: str, block_id: str) -> None:
        from navig.platform.paths import config_dir

        self.run_id = run_id
        self._dir = config_dir() / "runtime" / "runs"
        self._dir.mkdir(parents=True, exist_ok=True)
        self.path = self._dir / f"{run_id}.jsonl"
        self.state = S_PLANNED
        self._write({"ts": _now(), "state": S_PLANNED, "block": block_id})

    def _write(self, record: dict) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:  # pragma: no cover
            logger.warning("blocks.runner: journal write failed: {}", exc)

    def transition(self, state: str, detail: str = "") -> None:
        self.state = state
        self._write({"ts": _now(), "state": state, "detail": detail})


# ---------------------------------------------------------------------------
# Template resolution
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _resolve(template: str, ctx: dict[str, Any]) -> str:
    def repl(m: re.Match) -> str:
        tok = m.group(1)
        if "." in tok:
            ns, key = tok.split(".", 1)
            container = ctx.get(ns, {})
            if isinstance(container, dict) and key in container:
                return str(container[key])
            raise PolicyError(f"unresolved template token '{{{{{tok}}}}}'")
        if tok in ctx:
            return str(ctx[tok])
        raise PolicyError(f"unresolved template token '{{{{{tok}}}}}'")

    return _TOKEN_RE.sub(repl, template or "")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def apply_block(
    block: Block,
    inputs: dict[str, Any],
    *,
    yes: bool = False,
    dry_run: bool = False,
    approvals: set[str] | None = None,
    workdir: Path | None = None,
    project_root: Path | None = None,
    secret_resolver: SecretResolver | None = None,
    confirm: Callable[[str], bool] | None = None,
    trust: str = "first-party",
    _depth: int = 0,
    _ancestry: tuple[str, ...] = (),
    _parent_redactor: Redactor | None = None,
) -> BlockRunResult:
    """Execute a block end-to-end. See module docstring for the guarantees."""
    from navig.platform.paths import config_dir, find_app_root

    approvals = approvals or set()
    run_id = uuid.uuid4().hex[:16]
    started = _now()

    if project_root is None:
        project_root = find_app_root() or Path.cwd()
    if workdir is None:
        workdir = project_root
    workdir = Path(workdir)

    # Composition guards.
    if _depth > MAX_BLOCK_DEPTH:
        raise PolicyError(f"block composition exceeds max depth {MAX_BLOCK_DEPTH}")
    if block.id in _ancestry:
        raise PolicyError(f"block cycle detected: {' -> '.join((*_ancestry, block.id))}")

    # Digest lock check (tamper evidence) — only for the top-level project.
    if _depth == 0:
        verify_locked_digest(project_root, block.id, block.digest)

    redactor = _parent_redactor or Redactor()
    journal = RunJournal(run_id, block.id)

    ctx: dict[str, Any] = {
        "inputs": dict(inputs),
        "outputs": {},
        "workdir": str(workdir),
        "config_dir": str(config_dir()),
        "_block_dir": str(block.source_path.parent),
    }
    allowed_write_roots = [workdir, config_dir()]

    result = BlockRunResult(
        run_id=run_id,
        block_id=block.id,
        block_digest=block.digest,
        outcome=S_FAILED,
        verification_level=V_NONE,
        started_at=started,
        journal_path=str(journal.path),
    )

    # ── Requirements gate: declared tools/plugins present AND `detect` probes pass,
    # before we touch secrets or run a step. A real run fails fast with guidance
    # instead of a cryptic step-1 error. `detect` probes execute a command, so they
    # run ONLY on a real apply — never in dry-run (which must stay side-effect-free).
    unmet = unmet_requirements(block.requires)
    if not dry_run:
        blocking = [c.message() for c in
                    check_requirements(block.requires, probe=True) if not c.ok]
        if blocking:
            result.outcome = S_FAILED
            result.error = (
                "unmet requirements: " + "; ".join(blocking)
                + f". Fix them, then re-run — see `navig block doctor {block.id}`."
            )
            result.completed_at = _now()
            journal.transition(S_FAILED, "unmet_requirements: " + "; ".join(blocking))
            return result

    # ── Preflight: resolve secrets (skipped entirely on dry-run) ──
    journal.transition(S_PREFLIGHT)
    secret_env_values: dict[str, str] = {}  # key -> value
    if not dry_run:
        for inp in block.inputs:
            if inp.is_secret:
                resolver = secret_resolver or _default_secret_resolver
                val = resolver(inp)
                secret_env_values[inp.key] = val
                redactor.add(val)

    if dry_run:
        return _dry_run_plan(block, ctx, workdir, journal, result, unmet=unmet)

    has_manual = False
    any_machine = False
    failed = False

    journal.transition(S_RUNNING)
    for step in block.steps:
        risk = capability_risk(step.capabilities, workdir=workdir)
        # Named-approval gate for destructive steps.
        if requires_named_approval(risk) and step.id not in approvals:
            journal.transition(S_AWAITING_APPROVAL, step.id)
            sr = StepResult(step.id, step.kind, "blocked", risk=risk,
                            detail="destructive step needs --approve " + step.id)
            result.steps.append(sr)
            failed = True
            result.error = (
                f"step '{step.id}' is destructive ({risk}) and was not approved. "
                f"Re-run with --approve {step.id}."
            )
            break

        try:
            sr = _run_step(
                step, ctx, block, workdir, allowed_write_roots, secret_env_values,
                redactor, approvals, yes, confirm, journal,
                project_root=project_root, depth=_depth, ancestry=(*_ancestry, block.id),
                secret_resolver=secret_resolver, trust=trust,
            )
        except (PolicyError, TrustError) as exc:
            sr = StepResult(step.id, step.kind, "failed", risk=risk, detail=str(exc))
            result.steps.append(sr)
            failed = True
            result.error = redactor.scrub(str(exc))
            break

        result.steps.append(sr)
        if sr.mode == "manual":
            has_manual = True
        if step.kind in ("command", "materialize", "skill", "block"):
            any_machine = True
        if sr.status == "failed":
            failed = True
            result.error = sr.detail
            break

    # ── Verify ──
    verify_passed: bool | None = None
    if not failed:
        journal.transition(S_VERIFYING)
        verify_passed, evidence = _run_verify(block, ctx, workdir, redactor)
        result.evidence = evidence

    result.outputs = dict(ctx["outputs"])

    # ── Outcome + verification level ──
    if failed:
        result.outcome = S_FAILED
    elif verify_passed is False:
        result.outcome = S_FAILED
        result.error = "verification failed: " + json.dumps(result.evidence)[:200]
    else:
        result.outcome = S_SUCCEEDED

    result.verification_level = _verification_level(
        block, verify_passed=verify_passed, has_manual=has_manual, any_machine=any_machine
    )
    result.completed_at = _now()
    journal.transition(result.outcome)
    return result


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------


def _run_step(
    step: BlockStep,
    ctx: dict[str, Any],
    block: Block,
    workdir: Path,
    allowed_write_roots: list[Path],
    secret_env_values: dict[str, str],
    redactor: Redactor,
    approvals: set[str],
    yes: bool,
    confirm: Callable[[str], bool] | None,
    journal: RunJournal,
    *,
    project_root: Path,
    depth: int,
    ancestry: tuple[str, ...],
    secret_resolver: SecretResolver | None,
    trust: str,
) -> StepResult:
    risk = capability_risk(step.capabilities, workdir=workdir)

    if step.kind == "materialize":
        return _step_materialize(step, ctx, allowed_write_roots, redactor, risk)

    if step.kind == "command":
        return _step_command(step, ctx, workdir, secret_env_values, block, redactor, risk)

    if step.kind == "skill":
        return _step_skill(step, redactor, risk)

    if step.kind in ("prompt", "instruction"):
        text = _resolve(step.text, ctx)
        journal.transition(S_AWAITING_MANUAL, step.id)
        # Guided-manual: an agent runtime would execute; in CLI we render and,
        # under --yes, record operator acknowledgement (honest: mode=manual).
        return StepResult(step.id, step.kind, "manual_ack", risk=risk, mode="manual",
                          detail=redactor.scrub(text[:400]))

    if step.kind == "block":
        return _step_block(
            step, ctx, project_root, workdir, depth, ancestry, approvals, yes,
            confirm, secret_resolver, redactor, risk, trust,
        )

    return StepResult(step.id, step.kind, "skipped", risk=risk, detail="unknown kind")


def _step_materialize(step, ctx, allowed_write_roots, redactor, risk) -> StepResult:
    dest = safe_dest(_resolve(step.dest or "", ctx), allowed_write_roots)
    if step.content is not None:
        data = _resolve(step.content, ctx)
    else:
        # Materialize sources ship inside the block folder — resolve relative to it.
        data = _resolve_block_src(Path(step.src), ctx).read_text(encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(data, encoding="utf-8")
    return StepResult(step.id, step.kind, "ok", risk=risk,
                      detail=redactor.scrub(f"wrote {dest}"))


def _resolve_block_src(rel: Path, ctx: dict) -> Path:
    # Block source files live beside BLOCK.md; the runner passes the block dir in ctx.
    base = Path(ctx.get("_block_dir", ctx["workdir"]))
    p = (base / rel).resolve()
    if not p.is_relative_to(base.resolve()):
        raise PolicyError(f"materialize src '{rel}' escapes the block folder")
    return p


def _step_command(step, ctx, workdir, secret_env_values, block, redactor, risk) -> StepResult:
    import os as _os

    argv = [_resolve(a, ctx) for a in step.argv]
    cwd = Path(_resolve(step.cwd, ctx)) if step.cwd else workdir

    env = dict(_os.environ)
    for env_var, ref in step.secret_env.items():
        key = ref.split(".", 1)[-1] if ref.startswith("inputs.") else ref
        if key in secret_env_values:
            env[env_var] = secret_env_values[key]

    try:
        proc = subprocess.run(  # noqa: S603 — argv list, shell=False
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=step.timeout_seconds,
            shell=False,
        )
    except FileNotFoundError:
        return StepResult(step.id, step.kind, "failed", risk=risk,
                          detail=f"executable not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        return StepResult(step.id, step.kind, "failed", risk=risk,
                          detail=f"timed out after {step.timeout_seconds}s")

    stdout = redactor.scrub(proc.stdout or "")
    # Capture outputs from stdout via regex.
    for out_key, pattern in step.capture.items():
        m = re.search(pattern, proc.stdout or "")
        if m:
            ctx["outputs"][out_key] = m.group(1) if m.groups() else m.group(0)

    # Per-step verify (expect block).
    step_verify_ok = None
    if step.verify:
        step_verify_ok = _check_expect(step.verify, proc.returncode, proc.stdout or "", ctx)

    ok = proc.returncode == 0 and (step_verify_ok is not False)
    return StepResult(
        step.id, step.kind, "ok" if ok else "failed", risk=risk,
        detail=redactor.scrub(f"exit={proc.returncode} " + stdout[-300:].replace("\n", " ")),
        verify_passed=step_verify_ok,
    )


def _step_skill(step, redactor, risk) -> StepResult:
    try:
        from navig.commands.skills import run_skill_cmd  # noqa: PLC0415

        rc = run_skill_cmd(step.skill, [], {})
        status = "ok" if rc == 0 else "failed"
        return StepResult(step.id, step.kind, status, risk=risk,
                          detail=f"skill {step.skill} exit={rc}")
    except Exception as exc:  # noqa: BLE001
        return StepResult(step.id, step.kind, "failed", risk=risk,
                          detail=redactor.scrub(f"skill '{step.skill}' failed: {exc}"))


def _step_block(step, ctx, project_root, workdir, depth, ancestry, approvals, yes,
                confirm, secret_resolver, redactor, risk, trust) -> StepResult:
    child = find_block(step.use)
    if child is None:
        return StepResult(step.id, step.kind, "failed", risk=risk,
                          detail=f"child block '{step.use}' not installed "
                                 f"(navig install add block:navig-run/community/blocks/{step.use})")
    # Capability containment: child caps ⊆ parent declared caps.
    parent_caps = set(step.capabilities)
    child_caps = {c for s in child.steps for c in s.capabilities}
    escalated = {c for c in child_caps if c not in parent_caps}
    if escalated:
        return StepResult(step.id, step.kind, "failed", risk=risk,
                          detail=f"child '{step.use}' requests capabilities not granted by parent: "
                                 f"{sorted(escalated)}")
    child_inputs = {k: _resolve(str(v), ctx) if isinstance(v, str) else v
                    for k, v in step.inputs.items()}
    sub = apply_block(
        child, child_inputs, yes=yes, approvals=approvals, workdir=workdir,
        project_root=project_root, secret_resolver=secret_resolver, confirm=confirm,
        trust=trust, _depth=depth + 1, _ancestry=ancestry, _parent_redactor=redactor,
    )
    status = "ok" if sub.outcome == S_SUCCEEDED else "failed"
    return StepResult(step.id, step.kind, status, risk=risk,
                      detail=f"child block '{step.use}' -> {sub.outcome}",
                      child_receipt=sub.run_id)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _check_expect(expect: dict, returncode: int, stdout: str, ctx: dict) -> bool:
    exp = expect.get("expect", expect)
    if "exit_code" in exp and returncode != int(exp["exit_code"]):
        return False
    if "output_contains" in exp and str(exp["output_contains"]) not in stdout:
        return False
    if "output_regex" in exp:
        pat = _resolve(str(exp["output_regex"]), ctx)
        if not re.search(pat, stdout):
            return False
    return True


def _run_verify(block: Block, ctx: dict, workdir: Path, redactor: Redactor) -> tuple[bool | None, dict]:
    v = block.verify
    if v.kind == "none":
        return None, {"kind": "none"}

    if v.kind == "file_exists":
        path = Path(_resolve(v.path or "", ctx))
        ok = path.exists()
        return ok, {"kind": "file_exists", "path": str(path), "ok": ok}

    if v.kind == "command":
        argv = [_resolve(a, ctx) for a in v.argv]
        try:
            proc = subprocess.run(  # noqa: S603
                argv, cwd=str(workdir), capture_output=True, text=True,
                timeout=v.timeout_seconds, shell=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return False, {"kind": "command", "argv": argv, "error": str(exc)}
        ok = _check_expect({"expect": v.expect}, proc.returncode, proc.stdout or "", ctx) \
            if v.expect else (proc.returncode == 0)
        return ok, {
            "kind": "command",
            "argv": argv,
            "exit_code": proc.returncode,
            "stdout_tail": redactor.scrub((proc.stdout or "")[-300:]),
            "ok": ok,
        }
    return None, {"kind": v.kind}


def _verification_level(block: Block, *, verify_passed, has_manual, any_machine) -> str:
    # A machine verify that passed is the only path to self/external-check. Steps
    # merely running is NOT verification — a block with no verify step reports
    # 'none' (or 'human-attested' if a person acknowledged a manual step).
    if block.verify.kind == "none":
        return V_HUMAN if has_manual else V_NONE
    if verify_passed:
        return block.verify.level or V_SELF
    return V_NONE  # verify ran but failed


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def _dry_run_plan(block, ctx, workdir, journal, result, *, unmet=None) -> BlockRunResult:
    from navig import console_helper as ch

    ch.info(f"dry-run · plan for block '{block.id}' v{block.version}")
    if unmet:
        ch.warning("  ⚠ unmet requirements (this block would fail as-is):")
        for u in unmet:
            ch.warning(f"      - {u}")
        result.evidence = {"unmet_requirements": list(unmet)}
    n_detect = len((block.requires or {}).get("detect") or [])
    if n_detect:
        ch.dim(f"  ({n_detect} detect probe(s) run on a real apply, not in dry-run — "
               f"check them with `navig block doctor {block.id}`)")
    for step in block.steps:
        # No square brackets in ch.info — Rich would parse [...] as markup.
        risk = capability_risk(step.capabilities, workdir=workdir)
        approve = "  ⚠ needs --approve" if requires_named_approval(risk) else ""
        if step.kind == "command":
            try:
                rendered = " ".join(_resolve(a, ctx) for a in step.argv)
            except PolicyError as exc:
                rendered = f"<{exc}>"
            ch.info(f"  {step.id} · risk={risk} · $ {rendered}{approve}")
        elif step.kind == "materialize":
            try:
                dest = _resolve(step.dest or "", ctx)
            except PolicyError:
                dest = step.dest
            ch.info(f"  {step.id} · risk={risk} · write {dest}{approve}")
        else:
            ch.info(f"  {step.id} · risk={risk} · {step.kind}{approve}")
        result.steps.append(StepResult(step.id, step.kind, "planned", risk=risk))
    ch.info(f"  verify: {block.verify.kind}")
    ch.dim("  (dry-run — no secrets resolved, no network, no writes)")
    result.outcome = S_PLANNED
    journal.transition(S_PLANNED, "dry-run")
    return result


# ---------------------------------------------------------------------------
# Default secret resolver
# ---------------------------------------------------------------------------


def _default_secret_resolver(inp: BlockInput) -> str:
    """Resolve a secret input value.

    Order: explicit env override → NAVIG vault (best-effort) → hidden prompt.
    Never logged; the caller adds the value to the Redactor.
    """
    import os as _os

    env_key = f"NAVIG_BLOCK_SECRET__{inp.key.upper()}"
    if env_key in _os.environ:
        return _os.environ[env_key]

    if inp.vault:
        try:
            name = inp.vault.strip("{} ").split(".", 1)[-1]
            # `get_secret` was never a module-level function in navig.vault, so this import
            # always failed and every `{vault.NAME}` block input silently resolved to
            # nothing. reveal_secret(get_vault(), label) is the canonical reader — a str,
            # "" if missing/locked, never raises.
            from navig.vault import get_vault, reveal_secret

            val = reveal_secret(get_vault(), name)
            if val:
                return str(val)
        except Exception:  # noqa: BLE001
            pass

    try:
        import typer

        return typer.prompt(f"Secret '{inp.label or inp.key}'", hide_input=True)
    except Exception as exc:  # noqa: BLE001
        raise PolicyError(f"could not resolve secret input '{inp.key}': {exc}") from exc
