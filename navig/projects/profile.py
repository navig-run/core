"""Project profile — stack, package manager and every runnable script.

Builds on :func:`navig.commands.project_inspect.inspect_path` (root manifests,
stack detection) and adds what a project hub needs on top:

* scripts from **every workspace package**, not just the root — a monorepo's
  ``apps/web`` dev server is the script you actually want to run;
* a **stable key** per script (``kind:cwd:name``) so the runner can be asked to
  start a script by key and re-derive the command itself — the client never
  sends a command line;
* a ``danger`` flag for scripts whose name or body deploys, publishes, resets or
  deletes, so the UI asks before running them;
* ``long_running`` for dev servers / watchers (they get a port probe).

Read-only and bounded: manifests are read with the inspector's byte caps and
the workspace walk is capped at :data:`MAX_PACKAGES`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["MAX_PACKAGES", "build_profile", "find_script", "is_danger", "script_key"]

MAX_PACKAGES = 60
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", "out", ".next", "target", ".dev"}

_DANGER_NAME_RE = re.compile(
    r"(^|[:_\-./])(deploy|publish|release|reset|nuke|drop|destroy|purge|wipe|prune|rollback|"
    r"prod|production|upload|push)([:_\-./]|$)",
    re.IGNORECASE,
)
#: A segment that makes a scary-sounding name read-only (`deploy:status:prod`,
#: `store:publish:dry`, `verify:webhooks:prod`) — unless its BODY is destructive.
_SAFE_SEGMENTS = frozenset({
    "dry", "status", "logs", "log", "health", "check", "verify", "rehearse", "smoke", "preview",
    "test", "lint", "list", "ls", "info", "diff", "plan", "whoami", "show", "help",
})
_DANGER_BODY_RE = re.compile(
    r"(rm\s+-rf|rimraf|git\s+push|--force\b|wrangler\s+(deploy|publish)\b|npm\s+publish\b|"
    r"pnpm\s+publish\b|twine\s+upload|gh\s+release\s+create|drop\s+(table|database)|"
    r"Remove-Item\s+.*-Recurse)",
    re.IGNORECASE,
)
_LONG_RUNNING_RE = re.compile(r"(^|[:_\-])(dev|start|serve|watch|preview|storybook)([:_\-]|$)", re.IGNORECASE)


def is_danger(name: str, command: str = "") -> bool:
    """True when running this script could deploy, publish or destroy something.

    Measured on real projects before tuning: `deploy:status:prod`, `*:dry` and
    `verify:*:prod` were flagged next to `deploy:prod`, and a confirm that fires on
    every read-only script trains you to click through the one that matters.
    """
    if _DANGER_BODY_RE.search(command or ""):
        return True
    if not _DANGER_NAME_RE.search(name):
        return False
    segments = {seg.lower() for seg in re.split(r"[:_\-./]", name) if seg}
    return not (segments & _SAFE_SEGMENTS)


def script_key(kind: str, cwd: str, name: str) -> str:
    """The stable id of a script: ``kind:cwd:name`` (cwd is ``.`` for the root)."""
    return f"{kind}:{cwd or '.'}:{name}"


def _workspace_globs(root: Path, pkg: dict[str, Any]) -> list[str]:
    globs: list[str] = []
    ws = pkg.get("workspaces")
    if isinstance(ws, list):
        globs += [g for g in ws if isinstance(g, str)]
    elif isinstance(ws, dict) and isinstance(ws.get("packages"), list):
        globs += [g for g in ws["packages"] if isinstance(g, str)]
    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.is_file():
        try:
            import yaml  # noqa: PLC0415

            data = yaml.safe_load(pnpm.read_text(encoding="utf-8", errors="replace")) or {}
            pk = data.get("packages") if isinstance(data, dict) else None
            if isinstance(pk, list):
                globs += [g for g in pk if isinstance(g, str)]
        except Exception:  # noqa: BLE001 — a malformed workspace file only costs the sub-packages
            logger.debug("pnpm-workspace.yaml unreadable at %s", root, exc_info=True)
    return globs


def _workspace_dirs(root: Path, globs: list[str]) -> list[Path]:
    include = [g.strip().rstrip("/") for g in globs if g.strip() and not g.strip().startswith("!")]
    exclude = [g.strip()[1:].rstrip("/") for g in globs if g.strip().startswith("!")]
    found: dict[str, Path] = {}
    for pattern in include:
        pattern = pattern.removeprefix("./")
        if not pattern or pattern.startswith(("/", "..")):
            continue
        try:
            candidates = root.glob(pattern)
            for cand in candidates:
                if len(found) >= MAX_PACKAGES:
                    break
                try:
                    rel = cand.relative_to(root)
                except ValueError:
                    continue
                if any(part in _SKIP_DIRS for part in rel.parts):
                    continue
                if not (cand / "package.json").is_file():
                    continue
                rel_posix = rel.as_posix()
                if any(Path(rel_posix).match(ex) for ex in exclude):
                    continue
                found.setdefault(rel_posix, cand)
        except (OSError, ValueError, NotImplementedError):
            logger.debug("workspace glob %r failed under %s", pattern, root, exc_info=True)
    return [found[k] for k in sorted(found)]


def _entries(raw: list[dict[str, str]], cwd: str, package: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in raw:
        source = c.get("source", "")
        kind = {"python": "python", "make": "make", "just": "just"}.get(source, "npm")
        name = c.get("name", "")
        command = c.get("command", "")
        out.append({
            "key": script_key(kind, cwd, name),
            "name": name,
            "kind": kind,
            "runner": source,
            "cwd": cwd,
            "package": package,
            "command": command,
            "run": c.get("run", ""),
            "danger": is_danger(name, command),
            "long_running": bool(_LONG_RUNNING_RE.search(name)),
        })
    return out


def build_profile(path: str | Path) -> dict[str, Any]:
    """The full profile of a project directory. Never raises on a bad file."""
    from navig.commands import project_inspect as pi  # noqa: PLC0415

    root = Path(path)
    info = pi.inspect_path(str(root))
    if not info.get("is_dir"):
        return {"exists": bool(info.get("exists")), "path": str(root), "stack": [], "scripts": [],
                "packages": [], "metadata": {}, "readme_excerpt": ""}

    root_name = info.get("suggested_name") or root.name
    scripts = _entries(info.get("commands", []), "", root_name)

    pkg = pi._load_json(root / "package.json")
    pm = pi._detect_package_manager(root)
    packages: list[dict[str, Any]] = []
    for sub in _workspace_dirs(root, _workspace_globs(root, pkg)):
        sub_pkg = pi._load_json(sub / "package.json")
        rel = sub.relative_to(root).as_posix()
        name = str(sub_pkg.get("name") or sub.name)
        sub_scripts = _entries(pi._npm_commands(sub, sub_pkg, pm), rel, name)
        packages.append({"name": name, "cwd": rel, "scripts": len(sub_scripts)})
        scripts += sub_scripts

    return {
        "exists": True,
        "path": info.get("path", str(root)),
        "name": root_name,
        "stack": info.get("stack", []),
        "metadata": info.get("metadata", {}),
        "package_manager": (info.get("metadata") or {}).get("package_manager"),
        "packages": packages,
        "scripts": scripts,
        "readme_excerpt": info.get("readme_excerpt", ""),
    }


def find_script(profile: dict[str, Any], key: str) -> dict[str, Any] | None:
    """The script with *key* in *profile*, or None — the runner's only lookup."""
    return next((s for s in profile.get("scripts", []) if s.get("key") == key), None)
