"""
ExternalDriver — official coding-agent runtimes (Claude Code, Codex, Copilot).

These are subscriptions whose auth + inference are owned by the vendor's own
tool. NAVIG **delegates**: it detects whether the official runtime is installed
(and, as a non-secret heuristic, whether it has been configured) and stores only
a *reference* — it NEVER reads or copies credential files
(`~/.claude/.credentials.json`, `~/.codex/auth.json`, …).

Detection mirrors craft's runtime-resolver approach: `shutil.which` on PATH plus
a few known install locations (the equivalent of `existsSync(candidate)`). A
detected runtime advertises only the AUTH capability — never INFERENCE — so the
UI shows "Detected externally / Connected externally — not yet routable" and the
router never tries to run inference through it until a real adapter lands.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navig.providers.connection_types import Capability
from navig.providers.drivers.base import AuthStart, ProviderDriver, ValidationResult


@dataclass(frozen=True)
class ExternalRuntimeSpec:
    template_id: str
    label: str
    binaries: tuple[str, ...]          # candidate executables to probe on PATH
    extra_dirs: tuple[str, ...] = ()    # known install dirs (under $HOME)
    config_paths: tuple[str, ...] = ()  # non-secret "has been configured" signal (existence only)


# The catalog of delegated runtimes. config_paths are checked for *existence only*
# (never read) as a soft "configured" hint — auth_state stays detected_external.
EXTERNAL_RUNTIMES: dict[str, ExternalRuntimeSpec] = {
    "claude-code": ExternalRuntimeSpec(
        template_id="claude-code",
        label="Claude Code",
        binaries=("claude",),
        extra_dirs=(".local/bin", ".npm-global/bin", "AppData/Roaming/npm"),
        config_paths=(".claude",),
    ),
    "codex": ExternalRuntimeSpec(
        template_id="codex",
        label="Codex (ChatGPT)",
        binaries=("codex",),
        extra_dirs=(".local/bin", ".npm-global/bin", "AppData/Roaming/npm"),
        config_paths=(".codex",),
    ),
    "copilot": ExternalRuntimeSpec(
        template_id="copilot",
        label="GitHub Copilot",
        binaries=("copilot", "gh"),
        extra_dirs=(".local/bin",),
        config_paths=(".config/github-copilot", "AppData/Local/github-copilot"),
    ),
}


def _which(binary: str, extra_dirs: tuple[str, ...]) -> str | None:
    found = shutil.which(binary)
    if found:
        return found
    home = Path(os.path.expanduser("~"))
    for d in extra_dirs:
        for name in (binary, f"{binary}.exe", f"{binary}.cmd"):
            cand = home / d / name
            if cand.exists():
                return str(cand)
    return None


def _configured(config_paths: tuple[str, ...]) -> bool:
    """Existence-only check (never reads contents) — a soft 'has been used' hint."""
    home = Path(os.path.expanduser("~"))
    return any((home / p).exists() for p in config_paths)


class ExternalDriver(ProviderDriver):
    driver = "external"
    # AUTH only — explicitly NOT inference. Promotion to a routable state requires
    # a real adapter (later phase), so the UI never over-promises.
    advertised_capabilities = {Capability.AUTH}

    def detect(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for spec in EXTERNAL_RUNTIMES.values():
            path = None
            for b in spec.binaries:
                path = _which(b, spec.extra_dirs)
                if path:
                    break
            if not path:
                continue
            out.append({
                "template_id": spec.template_id,
                "label": spec.label,
                "runtime_path": path,
                "installed": True,
                "configured": _configured(spec.config_paths),
            })
        return out

    async def start_auth(self, template_id, *, api_key=None, endpoint=None, **kwargs) -> AuthStart:
        # Delegated: the user authenticates with the official tool. We register a
        # reference rather than minting/storing any token here.
        return AuthStart(flow="external", handle=None)

    async def validate(self, *, secret_ref, endpoint=None, model=None) -> ValidationResult:
        # We don't run inference through delegated runtimes (yet); validation only
        # confirms the runtime is still installed.
        spec = None
        for s in EXTERNAL_RUNTIMES.values():
            spec = s
            break
        installed = bool(spec and any(_which(b, spec.extra_dirs) for b in spec.binaries))
        return ValidationResult(
            ok=installed,
            health="healthy" if installed else "unreachable",
            error_message=None if installed else "Official runtime not found on PATH.",
        )


def detect_external_runtimes() -> list[dict[str, Any]]:
    """Convenience: detect all delegated coding-agent runtimes (non-secret)."""
    return ExternalDriver().detect()
