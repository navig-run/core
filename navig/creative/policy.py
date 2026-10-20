"""Authorized publishing — default local-only, allow-listed private repos only.

Config (``creative.publish``, written via ``cm.set_global`` — never a shallow
subtree update):

.. code-block:: yaml
    creative:
      publish:
        mode: local        # local | repo  ← DEFAULT: nothing leaves this machine
        repo: ""           # active destination (must also be in repos[])
        repos: []          # allow-list of local checkouts or github:owner/name
        branch: main
        auto_push: false   # non-interactive (automatic=True) publishes need this
        allow_public: false

The checklist (``verify_publish_plan``) is DETERMINISTIC — blocking is a list of
failed checks, never a prompt: allow-list membership · visibility (public is
always refused, even with allow_public) · branch exists · no force in argv ·
credentials for remote destinations · HEAD unchanged since the plan · lock-free
destination (``navig repo``'s own ``lock_state``) · secret scan over the staged
diff of the INTENDED paths. Path-scoped add/commit means unrelated dirty work in
the destination is preserved byte-for-byte (scenario 7).

Enablement is an owner-run interactive command (``navig creative publish
enable``); the default-off switch is what keeps §3's pause list intact. Tests
use a local bare repo + injected visibility probe — no real GitHub.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_POLICY: dict[str, Any] = {
    "mode": "local",
    "repo": "",
    "repos": [],
    "branch": "main",
    "auto_push": False,
    "allow_public": False,
}

#: Patterns that must never leave this machine inside a capability.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\s*[:=]\s*\S"),
    # The staged .env itself: diff headers name it as `+++ b/<path>/.env`.
    re.compile(r"(?m)^\+\+\+ b/\S*\.env\b"),
)

__all__ = [
    "DEFAULT_POLICY",
    "CheckResult",
    "PublishPlan",
    "load_policy",
    "verify_publish_plan",
    "make_plan",
    "publish_proposal",
]


@dataclass
class CheckResult:
    key: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "ok": self.ok, "detail": self.detail}


@dataclass
class PublishPlan:
    """A snapshot of intent — re-verified immediately before it executes."""

    dest: str
    branch: str
    head: str
    #: Repo-relative paths this publish may touch (path-scoped add/commit).
    paths: list[str]
    #: Absolute source (the built skill dir).
    src: str
    #: Remote visibility answer; None = probed at verify time.
    visibility: str | None = None
    checks: list[CheckResult] = field(default_factory=list)


def load_policy() -> dict[str, Any]:
    """Effective policy (defaults merged); never raises."""
    try:
        from navig.config import get_config_manager

        raw = (get_config_manager().global_config or {}).get("creative", {}) or {}
        pub = raw.get("publish", {}) or {}
    except Exception as exc:  # noqa: BLE001 - unreadable config = safe defaults
        logger.debug("creative.publish config unavailable: %s", exc)
        pub = {}
    policy = dict(DEFAULT_POLICY)
    for key in DEFAULT_POLICY:
        if key in pub:
            policy[key] = pub[key]
    # Shape sanity — a malformed stored value must not open a hole.
    if policy["mode"] not in ("local", "repo"):
        policy["mode"] = "local"
    if not isinstance(policy["repos"], list):
        policy["repos"] = []
    policy["branch"] = str(policy["branch"] or "main")
    policy["auto_push"] = bool(policy["auto_push"])
    policy["allow_public"] = bool(policy["allow_public"])
    return policy


def _git(dest: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(dest), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
        return 127, str(exc)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def _probe_visibility(dest: str) -> str:
    """'private' | 'public'. Local paths are private by definition; github: uses gh."""
    if not dest.startswith("github:"):
        return "private"
    repo = dest.split(":", 1)[1]
    code, out = _git_run_gh("repo", "view", repo, "--json", "visibility")
    if code != 0:
        return "unknown"
    try:
        return str(json.loads(out).get("visibility") or "unknown")
    except json.JSONDecodeError:
        return "unknown"


def _git_run_gh(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
        return 127, str(exc)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def make_plan(proposal: dict[str, Any], *, policy: dict[str, Any] | None = None) -> PublishPlan | None:
    """Snapshot the intent for a BUILT proposal; None when it cannot even form."""
    policy = policy if policy is not None else load_policy()
    if policy["mode"] != "repo":
        return None
    dest = str(policy.get("repo") or "")
    if not dest:
        return None
    from navig.creative.build import skill_target_dir

    artifact = proposal.get("artifact") or {}
    slug = str(artifact.get("slug") or "")
    src = skill_target_dir(slug)
    if not slug or not (src / "SKILL.md").exists():
        return None
    # Destination = the checkout; the capability lands under skills/<slug>/.
    dest_path = Path(dest).expanduser()
    head_code, head = _git(dest_path, "rev-parse", "HEAD")
    if head_code != 0:
        return None
    return PublishPlan(
        dest=dest,
        branch=str(policy.get("branch") or "main"),
        head=head.strip(),
        paths=[f"skills/{slug}"],
        src=str(src),
    )


def verify_publish_plan(
    plan: PublishPlan,
    *,
    policy: dict[str, Any] | None = None,
    probe: Callable[[str], str] | None = None,
    automatic: bool = False,
) -> list[CheckResult]:
    """The deterministic checklist. Any ``ok=False`` ⇒ the caller must not push."""
    policy = policy if policy is not None else load_policy()
    probe = probe or _probe_visibility
    results: list[CheckResult] = []

    # 1. Mode + allow-list — an unlisted destination is scenario 8's blocked case.
    allow_listed = plan.dest in [str(r) for r in policy.get("repos", [])]
    results.append(
        CheckResult(
            "allow_list",
            policy.get("mode") == "repo" and allow_listed,
            f"mode={policy.get('mode')!r} dest={plan.dest!r} "
            f"in allow-list={allow_listed}",
        )
    )

    # 2. Visibility — 'public' is ALWAYS refused, whatever allow_public says.
    visibility = plan.visibility or probe(plan.dest)
    results.append(
        CheckResult(
            "visibility",
            visibility in ("private", "local"),
            f"visibility={visibility!r} (public destinations are never publishable)",
        )
    )

    # 3. Automatic publishes need the persistent policy's explicit auto_push.
    results.append(
        CheckResult(
            "authorization",
            (not automatic) or bool(policy.get("auto_push")),
            "explicit operator action" if not automatic else
            f"automatic publish requires creative.publish.auto_push (auto_push="
            f"{policy.get('auto_push')})",
        )
    )

    dest_path = Path(plan.dest).expanduser()

    # 4. Branch exists in the destination (or its remote).
    bc, _ = _git(dest_path, "rev-parse", "--verify", plan.branch)
    results.append(CheckResult("branch", bc == 0, f"branch {plan.branch!r} resolvable"))

    # 5. HEAD unchanged since the plan was made (a stale plan must re-verify).
    hc, head_now = _git(dest_path, "rev-parse", "HEAD")
    results.append(
        CheckResult(
            "head",
            hc == 0 and head_now.strip() == plan.head,
            f"planned {plan.head[:10]!r} vs now {head_now.strip()[:10]!r}",
        )
    )

    # 6. No force in the argv we will build (constructed once, asserted here).
    push_argv = ["git", "-C", str(dest_path), "push", "origin", f"HEAD:{plan.branch}"]
    push_tail = push_argv[push_argv.index("push") + 1 :]
    no_force = not any(
        a in ("--force", "-f") or a.startswith("+") for a in push_tail
    )
    results.append(CheckResult("no_force", no_force, "push argv is a plain fast-forward push"))

    # 7. Foreign lock / live session on the destination (reuses navig repo's classifier).
    results.append(CheckResult("lock_free", *_lock_check(dest_path)))

    # 8. Credentials for remote destinations (local checkouts need only git).
    if plan.dest.startswith("github:"):
        code, out = _git_run_gh("auth", "status")
        results.append(
            CheckResult("credentials", code == 0, (out.splitlines() or ["gh auth unavailable"])[0][:120])
        )
    else:
        results.append(CheckResult("credentials", True, "local destination — plain git"))

    plan.checks = results
    return results


def _lock_check(dest: Path) -> tuple[bool, str]:
    lock_file = dest / ".dev" / "agent.lock"
    if not lock_file.exists():
        return True, "no agent lock on the destination"
    try:
        from navig.commands.repo import LOCK_TTL_MINUTES, lock_state

        lock = json.loads(lock_file.read_text(encoding="utf-8"))
        state = lock_state(lock).get("state")
    except Exception as exc:  # noqa: BLE001 - unreadable lock ⇒ refuse (fail closed)
        return False, f"destination lock unreadable ({exc}) — refusing"
    if state == "held":
        return False, f"another session holds a fresh lock on {dest} (ttl {LOCK_TTL_MINUTES}m)"
    return True, f"destination lock state={state}"


def _scan_staged_diff(dest: Path, paths: list[str]) -> list[str]:
    """Secret patterns over the staged diff of the INTENDED paths only."""
    code, out = _git(dest, "diff", "--cached", "--", *paths)
    if code != 0:
        return [f"could not read staged diff: {out[:120]}"]
    hits: list[str] = []
    for pattern in _SECRET_PATTERNS:
        m = pattern.search(out)
        if m:
            hits.append(f"staged diff matches {pattern.pattern!r}: …{m.group(0)[:40]}…")
    return hits


def publish_proposal(
    proposal_id: str,
    *,
    automatic: bool = False,
    probe: Callable[[str], str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Publish a BUILT proposal under the policy. Returns an honest verdict.

    On success the proposal gains ``version_ref {commit, remote, branch}``; on a
    blocked checklist the proposal is untouched and every check is returned.
    """
    from navig.creative import proposals as prop
    from navig.creative.mode import get_agent_mode

    if get_agent_mode() != "creative":
        return {
            "ok": False,
            "blocked": True,
            "reason": "Agent Mode is Standard — publishing needs Creative mode.",
            "checks": [],
        }

    proposal = prop.get_proposal(proposal_id)
    if proposal is None:
        return {"ok": False, "blocked": True, "reason": "proposal not found", "checks": []}
    if proposal.get("status") != prop.STATUS_BUILT:
        return {
            "ok": False,
            "blocked": True,
            "reason": f"proposal is {proposal.get('status')!r} — only a built "
            "capability can be published",
            "checks": [],
        }

    policy = load_policy()
    plan = make_plan(proposal, policy=policy)
    if plan is None:
        return {
            "ok": False,
            "blocked": True,
            "reason": "publishing is local-only (creative.publish.mode=local) or the "
            "destination/source could not be resolved",
            "checks": [],
        }

    checks = verify_publish_plan(plan, policy=policy, probe=probe, automatic=automatic)
    failed = [c for c in checks if not c.ok]
    if failed:
        return {
            "ok": False,
            "blocked": True,
            "reason": "; ".join(f"{c.key}: {c.detail}" for c in failed),
            "checks": [c.as_dict() for c in checks],
        }

    dest = Path(plan.dest).expanduser()
    rel_target = plan.paths[0]
    abs_target = dest / rel_target

    # Copy the capability into the destination checkout (intended path only).
    import shutil

    try:
        abs_target.parent.mkdir(parents=True, exist_ok=True)
        if abs_target.exists():
            shutil.rmtree(abs_target)
        shutil.copytree(plan.src, abs_target)
    except OSError as exc:
        return {"ok": False, "blocked": False, "reason": f"copy failed: {exc}", "checks": []}

    # Stage SCOPED to the intended paths — anything else dirty stays untouched.
    code, out = _git(dest, "add", "--", *plan.paths)
    if code != 0:
        return {"ok": False, "blocked": False, "reason": f"git add failed: {out[:200]}", "checks": []}

    # Secret scan over the staged diff — the last gate before a commit exists.
    hits = _scan_staged_diff(dest, plan.paths)
    if hits:
        _git(dest, "reset", "--", *plan.paths)  # unstage; nothing committed
        return {
            "ok": False,
            "blocked": True,
            "reason": "secret scan refused the staged diff: " + "; ".join(hits),
            "checks": [c.as_dict() for c in checks],
        }

    slug = str((proposal.get("artifact") or {}).get("slug") or "capability")
    message = f"creative: publish skill {slug} (proposal {proposal_id})"
    code, out = _git(dest, "commit", "-m", message, "--", *plan.paths)
    committed_here = code == 0
    if not committed_here:
        # Nothing-to-commit (identical content re-published) is fine ONLY if the
        # pending commit can then be pushed — a retry after a failed push lands
        # here, and skipping the push would silently never deliver it.
        if "nothing to commit" not in out.lower() and "no changes added" not in out.lower():
            return {"ok": False, "blocked": False,
                    "reason": f"git commit failed: {out[:200]}", "checks": []}

    code, out = _git(dest, "push", "origin", f"HEAD:{plan.branch}")
    if code != 0:
        # The local commit (if any) is a valid version record; a push failure is
        # retryable (scenario 10) and reported — nothing is force-rewritten.
        head_code, head_now = _git(dest, "rev-parse", "HEAD")
        return {
            "ok": False,
            "blocked": False,
            "reason": f"push failed (retryable): {out[:200]}",
            "head": head_now.strip() if head_code == 0 else None,
            "checks": [c.as_dict() for c in checks],
        }

    head_code, head_now = _git(dest, "rev-parse", "HEAD")
    ref = {
        "commit": head_now.strip() if head_code == 0 else None,
        "remote": plan.dest,
        "branch": plan.branch,
    }
    prop.set_status(proposal_id, prop.STATUS_BUILT, version_ref=ref)
    return {"ok": True, "blocked": False,
            "reason": "published" if committed_here else "pushed existing commit",
            "version_ref": ref, "checks": [c.as_dict() for c in checks]}
