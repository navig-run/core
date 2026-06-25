"""Remote NAVIG deploy — install + start the daemon on a configured host over SSH.

Reuses :class:`navig.remote.RemoteOperations` for SSH exec. The install one-liner
(`curl … install.sh | bash`) can take minutes, so it runs **detached** and we poll
for ``navig --version``. **Idempotent**: a re-run upgrades + restarts. Linux/macOS
hosts only (the install.sh path). Used by ``navig host deploy``, the onboarding
first-host step, and the deck Remote "Deploy NAVIG" button.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

_INSTALL_URL = "https://navig.run/install.sh"
_NAVIG = "$HOME/.local/bin/navig"  # explicit path — non-interactive SSH may not source ~/.bashrc
_POLL_TRIES = 40
_POLL_INTERVAL_S = 15.0  # 40 × 15s ≈ 10 min ceiling for a cold install


@dataclass
class DeployStep:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class DeployResult:
    host: str
    ok: bool
    version: str = ""
    steps: list[DeployStep] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "host": self.host,
            "ok": self.ok,
            "version": self.version,
            "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in self.steps],
        }


def _run(ops, cfg, command: str, *, trust_new_host: bool = False) -> tuple[bool, str]:
    try:
        res = ops.execute_command(command, cfg, capture_output=True, trust_new_host=trust_new_host)
        out = ((res.stdout or "") + (res.stderr or "")).strip()
        return res.returncode == 0, out
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def deploy_to_host(
    host_name: str,
    *,
    public_url: str = "",
    dry_run: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> DeployResult:
    """SSH into *host_name*, install + start NAVIG, verify, and (optionally) set
    ``cloud direct <public_url>``. Returns a :class:`DeployResult`."""
    from navig.config import get_config_manager  # noqa: PLC0415
    from navig.remote import RemoteOperations  # noqa: PLC0415

    def emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    cm = get_config_manager()
    try:
        cfg = cm.load_host_config(host_name)
    except Exception:  # noqa: BLE001
        cfg = None
    result = DeployResult(host=host_name, ok=True)
    if not cfg:
        result.ok = False
        result.steps.append(DeployStep("resolve-host", False, f"unknown host '{host_name}' — add it with `navig host add`"))
        return result

    ops = RemoteOperations(cm)

    install_cmd = (
        f"nohup bash -c 'curl -fsSL {_INSTALL_URL} | bash' "
        "> /tmp/navig-install.log 2>&1 </dev/null & echo started"
    )
    service_cmd = f"{_NAVIG} service install --method systemd 2>&1 | tail -3; {_NAVIG} service start 2>&1 | tail -3"
    verify_cmd = f"{_NAVIG} --version 2>&1 | tail -1"

    if dry_run:
        planned = [
            ("ssh", "echo navig-ssh-ok"),
            ("detect-os", "uname -s"),
            ("install", install_cmd),
            ("service", service_cmd),
            ("verify", verify_cmd),
        ]
        if public_url:
            planned.append(("reachability", f"{_NAVIG} cloud direct {public_url}"))
        for name, cmd in planned:
            result.steps.append(DeployStep(name, True, f"[dry-run] {cmd}"))
        return result

    # 1. SSH reachable (accept the host key on first connect).
    emit("Connecting…")
    ok, out = _run(ops, cfg, "echo navig-ssh-ok", trust_new_host=True)
    result.steps.append(DeployStep("ssh", ok, out))
    if not ok:
        result.ok = False
        return result

    # 2. OS gate (install.sh = Linux/macOS).
    ok, os_out = _run(ops, cfg, "uname -s")
    is_unix = ok and any(s in os_out.lower() for s in ("linux", "darwin"))
    result.steps.append(DeployStep("detect-os", is_unix, os_out or "could not detect OS"))
    if not is_unix:
        result.ok = False
        result.steps.append(DeployStep("detect-os", False, "remote deploy supports Linux/macOS hosts only (Windows: install manually)"))
        return result

    # 3. Install (detached) + poll for navig --version.
    emit("Installing NAVIG (this can take a few minutes)…")
    ok, out = _run(ops, cfg, install_cmd)
    result.steps.append(DeployStep("install-start", ok, out))
    if not ok:
        result.ok = False
        return result
    installed = False
    for i in range(_POLL_TRIES):
        time.sleep(_POLL_INTERVAL_S)
        ok, ver = _run(ops, cfg, f"{_NAVIG} --version 2>/dev/null || true")
        ver = ver.strip()
        if ver and any(ch.isdigit() for ch in ver):
            installed = True
            result.version = ver
            break
        emit(f"…still installing (~{int((i + 1) * _POLL_INTERVAL_S)}s)")
    result.steps.append(DeployStep("install", installed, result.version or "timed out waiting for `navig --version`"))
    if not installed:
        result.ok = False
        return result

    # 4. Service install + start (install.sh usually does this; explicit = idempotent).
    emit("Registering + starting the service…")
    ok, out = _run(ops, cfg, service_cmd)
    result.steps.append(DeployStep("service", ok, out))

    # 5. Optional reachability: this host is a VPS with a public URL.
    if public_url:
        emit("Setting reachability (cloud direct)…")
        ok, out = _run(ops, cfg, f"{_NAVIG} cloud direct {public_url} 2>&1 | tail -3")
        result.steps.append(DeployStep("reachability", ok, out))

    # 6. Verify.
    ok, out = _run(ops, cfg, verify_cmd)
    result.steps.append(DeployStep("verify", ok, out))
    result.ok = installed and ok
    return result
