"""Block trust, execution policy, and provenance — the security floor.

A block is untrusted content that can execute commands and consume secrets, so
these checks run **before** the runner touches a subprocess:

  * **Trust** — Stage 1 accepts blocks only from the first-party community
    source (``navig-run/community/blocks/*``) or local paths flagged
    ``--dev-untrusted``. Community publishing stays disabled until publisher
    signing ships (Stage 3).
  * **Effective risk** — computed from a step's declared *capabilities*, never
    from its self-asserted ``safety`` label. A manifest cannot call ``rm -rf``
    "safe". Destructive steps need a *named* approval (``--approve <step-id>``);
    a blanket ``--yes`` is not enough.
  * **Secret redaction** — secret values are scrubbed from every string that
    could persist (receipts, ops ledger, logs, ``--json``), automatically.
  * **Path safety** — templated destinations are confined to allowed roots.
  * **Lockfile** — every installed/applied block is pinned by digest in
    ``.navig/blocks.lock.json``; a digest mismatch on re-apply is a hard fail.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# First-party trusted source (owner/repo). Everything else needs --dev-untrusted
# until publisher signing lands (Stage 3).
TRUSTED_OWNER = "navig-run"
TRUSTED_REPO = "community"

RISK_SAFE = "safe"
RISK_MODERATE = "moderate"
RISK_DESTRUCTIVE = "destructive"
_RISK_RANK = {RISK_SAFE: 0, RISK_MODERATE: 1, RISK_DESTRUCTIVE: 2}

# Capability prefixes that are inherently destructive.
_DESTRUCTIVE_PREFIXES = (
    "ssh",
    "sudo",
    "service",
    "firewall",
    "package",
    "publish",
    "delete",
    "remote",
)


class TrustError(RuntimeError):
    """Raised when a block's source is not trusted for execution."""


class PolicyError(RuntimeError):
    """Raised when execution policy blocks a step (approval, path, risk)."""


# ---------------------------------------------------------------------------
# Trust
# ---------------------------------------------------------------------------


def is_trusted_source(owner: str, repo: str) -> bool:
    return owner == TRUSTED_OWNER and repo == TRUSTED_REPO


def check_install_trust(owner: str | None, repo: str | None, *, dev_untrusted: bool) -> str:
    """Return the trust label for an install source, or raise TrustError.

    Returns ``"first-party"`` for the community source, ``"dev"`` for a local /
    flagged install. Anything else raises.
    """
    if owner and repo and is_trusted_source(owner, repo):
        return "first-party"
    if dev_untrusted:
        return "dev"
    raise TrustError(
        "block source is not first-party. Stage-1 blocks install only from "
        f"github:{TRUSTED_OWNER}/{TRUSTED_REPO}/blocks/*. "
        "Re-run with --dev-untrusted to install a local/unverified block "
        "(it will be marked trust=dev in the lockfile and every receipt)."
    )


# ---------------------------------------------------------------------------
# Effective risk (computed, not self-asserted)
# ---------------------------------------------------------------------------


def _within(child: str, root: Path) -> bool:
    try:
        return Path(child).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def capability_risk(capabilities: list[str], *, workdir: Path | None = None) -> str:
    """Compute the effective risk of a step from its declared capabilities."""
    has_exec = any(c.startswith("exec:") for c in capabilities)
    has_network = any(c.startswith("network:") for c in capabilities)
    risk = RISK_SAFE

    # Writes to the workdir or NAVIG's own config dir are expected/safe targets
    # (install-type blocks configure the space); anywhere else is destructive.
    safe_write_targets = {"workdir", "{{workdir}}", "config_dir", "{{config_dir}}"}

    for cap in capabilities:
        head = cap.split(":", 1)[0]
        if head in _DESTRUCTIVE_PREFIXES:
            return RISK_DESTRUCTIVE
        if cap.startswith("filesystem:write:"):
            target = cap.split(":", 2)[-1]
            if target not in safe_write_targets:
                if workdir is None or not _within(target, workdir):
                    return RISK_DESTRUCTIVE

    if has_exec:
        risk = _max_risk(risk, RISK_MODERATE)
    return risk


def _max_risk(a: str, b: str) -> str:
    return a if _RISK_RANK.get(a, 0) >= _RISK_RANK.get(b, 0) else b


def requires_named_approval(risk: str) -> bool:
    return risk == RISK_DESTRUCTIVE


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


class Redactor:
    """Scrubs known secret values from any string before it persists."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def add(self, value: str | None) -> None:
        if value and isinstance(value, str) and len(value) >= 3:
            self._values.add(value)

    def scrub(self, text: str) -> str:
        if not text:
            return text
        out = text
        for v in self._values:
            if v in out:
                out = out.replace(v, "‹redacted›")
        return out

    def scrub_obj(self, obj):
        """Deep-scrub strings inside dicts/lists (for receipt artifacts)."""
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, dict):
            return {k: self.scrub_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub_obj(v) for v in obj]
        return obj


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def safe_dest(path_str: str, allowed_roots: list[Path]) -> Path:
    """Normalize a destination path and confirm it lives under an allowed root.

    Raises PolicyError on traversal outside every allowed root.
    """
    p = Path(os.path.expanduser(path_str)).resolve()
    for root in allowed_roots:
        try:
            if p.is_relative_to(root.resolve()):
                return p
        except (OSError, ValueError):
            continue
    raise PolicyError(
        f"destination '{path_str}' resolves outside allowed roots "
        f"({', '.join(str(r) for r in allowed_roots)})"
    )


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------


def lockfile_path(project_root: Path) -> Path:
    return project_root / ".navig" / "blocks.lock.json"


def read_lockfile(project_root: Path) -> dict:
    p = lockfile_path(project_root)
    if not p.exists():
        return {"version": 1, "blocks": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "blocks": {}}


def write_lock_entry(
    project_root: Path,
    block_id: str,
    *,
    version: str,
    digest: str,
    source: str,
    trust: str,
    installed_at: str,
) -> None:
    lock = read_lockfile(project_root)
    lock.setdefault("blocks", {})[block_id] = {
        "version": version,
        "digest": digest,
        "source": source,
        "trust": trust,
        "installed_at": installed_at,
    }
    p = lockfile_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(lock, indent=2), encoding="utf-8")


def verify_locked_digest(project_root: Path, block_id: str, digest: str) -> None:
    """Raise TrustError if a locked block's digest no longer matches.

    A mismatch means the on-disk block changed since it was pinned — treat as
    tampering and refuse to run until re-installed.
    """
    entry = read_lockfile(project_root).get("blocks", {}).get(block_id)
    if entry and entry.get("digest") and entry["digest"] != digest:
        raise TrustError(
            f"block '{block_id}' digest changed since it was locked "
            f"(locked {entry['digest'][:19]}…, on-disk {digest[:19]}…). "
            "Re-install it to accept the change."
        )


_RE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def valid_block_id(block_id: str) -> bool:
    return bool(_RE_ID.match(block_id))


# ---------------------------------------------------------------------------
# Requirements (declared `requires:` preconditions — tools + plugins present)
# ---------------------------------------------------------------------------


def _plugin_installed(name: str) -> bool:
    """Is a plugin named ``name`` available? Checks the pip entry-point dist first
    (first-party plugins register that way), then the package-format install dir."""
    try:
        from importlib import metadata

        metadata.version(name)
        return True
    except Exception:  # noqa: BLE001 — PackageNotFoundError and friends
        pass
    try:
        from navig.plugins.package import installed_plugin_roots

        for root in installed_plugin_roots(include_disabled=True):
            if root.name == name:
                return True
    except Exception:  # noqa: BLE001 — plugin host optional
        pass
    return False


@dataclass
class RequirementCheck:
    """One declared precondition, and whether *this machine* satisfies it."""

    kind: str            # "tool" | "plugin" | "detect"
    name: str
    ok: bool
    detail: str = ""     # why it failed
    fix: str = ""        # the exact command that satisfies it

    def message(self) -> str:
        """One line: what's missing, and how to get it."""
        if self.kind == "detect":
            base = f"{self.name} ({self.detail})" if self.detail else self.name
        else:
            base = f"required {self.kind} '{self.name}' is {self.detail or 'unavailable'}"
        return f"{base} — {self.fix}" if self.fix else base


def requirement_entries(items) -> list[tuple[str, str]]:
    """Normalize a ``tools:``/``plugins:`` list into ``(name, install-command)`` pairs.

    Two forms, both accepted — a bare name, or a mapping carrying the *exact* command
    that satisfies it. The generic fallback (``pip install <name>``) is often simply
    wrong (navig-mobile really needs ``pip install "navig-mobile[all]"``; agent-device
    is an npm package), and a diagnostic that prints a command which doesn't work is
    worse than one that prints none::

        tools:
          - adb                                    # bare name → generic hint
          - name: agent-device                     # …or the real fix command
            install: npm install -g agent-device@latest

    Malformed entries are dropped (author-time lint catches them — see validate_block).
    """
    out: list[tuple[str, str]] = []
    for item in items or []:
        if isinstance(item, str) and item.strip():
            out.append((item.strip(), ""))
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                out.append((name, str(item.get("install") or "").strip()))
    return out


def check_requirements(
    requires: dict | None, *, probe: bool = False, timeout: float = 10.0
) -> list[RequirementCheck]:
    """The single source of truth for *"can this block run here?"*.

    Checks the declared preconditions:

      * ``tools``   — external binaries that must be on PATH (e.g. ``agent-device``);
      * ``plugins`` — NAVIG plugins that must be installed (e.g. ``navig-mobile``);
      * ``detect``  — runtime probes, **only when** ``probe=True``.

    Pure by default: tools/plugins are read-only lookups, safe to call from any display
    path. ``detect`` probes *execute a command*, so callers opt in deliberately — a real
    apply and ``navig block doctor`` do; dry-run and ``block show`` must not.
    """
    import shutil

    req = requires or {}
    checks: list[RequirementCheck] = []
    for name, fix in requirement_entries(req.get("tools")):
        ok = shutil.which(name) is not None
        checks.append(RequirementCheck(
            "tool", name, ok,
            "" if ok else "not on PATH",
            "" if ok else (fix or "install it and put it on PATH")))
    for name, fix in requirement_entries(req.get("plugins")):
        ok = _plugin_installed(name)
        checks.append(RequirementCheck(
            "plugin", name, ok,
            "" if ok else "not installed",
            "" if ok else (fix or f"pip install {name}")))
    if probe:
        for pr in run_detect_probes(req, timeout=timeout):
            checks.append(RequirementCheck(
                "detect", pr.label, pr.ok,
                "" if pr.ok else pr.detail, "" if pr.ok else pr.hint))
    return checks


def unmet_requirements(requires: dict | None) -> list[str]:
    """Messages for each unmet **static** requirement (tools + plugins); ``[]`` = all
    satisfied. Pure — runs no subprocess. ``detect`` probes have side effects and are
    executed separately (:func:`check_requirements` with ``probe=True``)."""
    return [c.message() for c in check_requirements(requires) if not c.ok]


@dataclass
class ProbeResult:
    """Outcome of one ``requires.detect`` availability probe."""

    label: str
    ok: bool
    detail: str = ""
    hint: str = ""


def run_detect_probes(requires: dict | None, *, timeout: float = 10.0) -> list[ProbeResult]:
    """Execute each ``requires.detect`` probe and report whether it passed.

    A probe is a **read-only availability check** for runtime state that ``tools``/
    ``plugins`` can't express (e.g. "is an emulator booted?"), declared as::

        requires:
          detect:
            - label: A booted Android device/emulator
              run: [adb, get-state]      # argv — shell=false, never a shell string
              expect_exit: 0              # default 0
              expect_output: "device"     # optional stdout substring
              hint: start an emulator or plug in a device

    SECURITY: this runs the declared ``run`` argv as a subprocess — argv-only (no
    shell), with a hard ``timeout``. Unlike the pure :func:`unmet_requirements`, it
    has side effects, so callers run it deliberately: the runner only on a **real**
    apply (never dry-run — that would break the no-side-effects contract), and
    ``navig block doctor``. A malformed probe is reported unmet rather than executed.
    Probes must be side-effect-free checks; a block's source trust (first-party /
    ``--dev-untrusted`` at install) is what authorizes running them.
    """
    import subprocess

    out: list[ProbeResult] = []
    for probe in (requires or {}).get("detect") or []:
        if not isinstance(probe, dict):
            out.append(ProbeResult("detect", False, "invalid probe (must be a mapping)"))
            continue
        run = probe.get("run")
        label = str(probe.get("label") or (run[0] if isinstance(run, list) and run else "detect"))
        hint = str(probe.get("hint") or "")
        if not (isinstance(run, list) and run and all(isinstance(a, str) for a in run)):
            out.append(ProbeResult(label, False, "probe has no valid 'run' argv list", hint))
            continue
        try:
            proc = subprocess.run(  # noqa: S603 — argv list, shell=False
                run, capture_output=True, text=True, timeout=timeout, shell=False)
        except subprocess.TimeoutExpired:
            out.append(ProbeResult(label, False, f"probe timed out after {timeout:g}s", hint))
            continue
        except FileNotFoundError:
            out.append(ProbeResult(label, False, f"'{run[0]}' not found", hint))
            continue
        except OSError as exc:
            out.append(ProbeResult(label, False, f"probe error: {exc}", hint))
            continue
        try:
            expect_exit = int(probe.get("expect_exit", 0))
        except (TypeError, ValueError):
            expect_exit = 0
        ok = proc.returncode == expect_exit
        expect_output = probe.get("expect_output")
        if ok and expect_output is not None:
            ok = str(expect_output) in (proc.stdout or "")
        detail = f"exit={proc.returncode}" if ok else f"exit={proc.returncode} (expected {expect_exit})"
        out.append(ProbeResult(label, ok, detail, hint))
    return out
