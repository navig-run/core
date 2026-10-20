"""Build a proposed capability through the EXISTING authoring contracts.

No parallel scaffolding: the artifact is a SKILL.md written to the user store
(``store_dir()/skills/<slug>/`` — the path ``navig skill new`` already uses),
validated by the SAME linter the CLI reports (``lint_skill_path`` extracted from
``skills_lint``) plus the runtime parser's security checks, made visible to the
agent by the loader, and activated only through the normal activation machinery
(never force-activated as a side effect of building).

Injection hygiene: the body carries only the artifact's deterministic fields and
command literals fenced via ``defuse_fences`` — raw evidence text (error strings
from history) is never rendered into a file that becomes agent context.

Crash safety: writes are atomic, and a validation failure ROLLS THE FILE BACK
so an interrupted/failed build leaves no half-skill for the loader to find
(scenario 10: failed → retryable).

The mode gate (Creative only) and the overlap preflight live here, so the CLI,
the deck route and the agent tool share one choke point.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from navig.creative.decide import ArtifactSpec

logger = logging.getLogger(__name__)

__all__ = [
    "render_skill_md",
    "build_artifact",
    "build_proposal",
    "skill_target_dir",
]


def _skills_root() -> Path:
    from navig.platform.paths import store_dir

    return store_dir() / "skills"


def skill_target_dir(slug: str) -> Path:
    return _skills_root() / slug


def render_skill_md(spec: ArtifactSpec) -> str:
    """Render the SKILL.md text — deterministic fields only, no raw evidence."""
    import yaml

    from navig.tools.untrusted_text import defuse_fences

    description = (
        f'Use when the user wants to {spec.description.rstrip(".")} '
        f'— created by NAVIG Creative from repeated operation history.'
    )
    frontmatter = yaml.safe_dump(
        {
            "name": spec.slug,
            "description": description,
            "safety": "safe",
            "version": "0.0.1",
        },
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    ).strip()

    lines = [
        "---",
        frontmatter,
        "---",
        "",
        f"# {spec.title}",
        "",
        description,
        "",
        "## Commands",
        "",
    ]
    if spec.commands:
        fenced = "\n".join(defuse_fences(cmd) for cmd in spec.commands)
        lines += ["```bash", fenced, "```", ""]
    else:
        lines += ["_No command sequence was recorded for this skill._", ""]
    lines += [
        "## Verification",
        "",
        "```bash",
        "navig skill lint .",
        "```",
        "",
    ]
    return "\n".join(lines)


def build_artifact(
    spec: ArtifactSpec,
    *,
    root: Path | None = None,
) -> tuple[Path | None, list[str]]:
    """Write + validate one artifact. ``(target_dir, problems)``.

    Refuses to touch an existing directory (overlap — another session or an
    earlier build already owns that slug) and rolls the write back when
    validation fails, so the caller sees either a healthy skill or nothing.
    """
    if spec.kind != "skill":  # pragma: no cover - contract guard
        return None, [f"unsupported artifact kind: {spec.kind}"]

    base = root if root is not None else _skills_root()
    target = base / spec.slug
    if target.exists():
        return None, [f"overlap: {target} already exists — refusing to overwrite"]

    text = render_skill_md(spec)
    skill_file = target / "SKILL.md"
    try:
        target.mkdir(parents=True, exist_ok=True)
        from navig.core.yaml_io import atomic_write_text

        atomic_write_text(skill_file, text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("creative build write failed: %s", exc)
        _rollback(target, skill_file)
        return None, [f"write failed: {exc}"]

    problems = _validate(skill_file)
    if problems:
        _rollback(target, skill_file)
        return None, problems

    try:
        from navig.creative.index import invalidate

        invalidate()
    except Exception as exc:  # noqa: BLE001 - a stale cache beats a failed build
        logger.debug("index invalidate after build failed: %s", exc)
    return target, []


def _validate(skill_file: Path) -> list[str]:
    """Zero ``fail`` rows from the CLI linter + a clean runtime parse."""
    from navig.commands.skills import lint_skill_path

    checks = lint_skill_path(skill_file)
    fails = [f"{check}: {detail}" for level, check, detail in checks if level == "fail"]
    if fails:
        return fails
    try:
        from navig.skills.loader import parse_skill_file

        parsed = parse_skill_file(skill_file)
    except Exception as exc:  # noqa: BLE001 - SkillSecurityError lands here too
        return [f"runtime parse refused: {exc}"]
    if parsed is None:
        return ["runtime parse returned nothing for the written SKILL.md"]
    return []


def _rollback(target: Path, skill_file: Path) -> None:
    """Remove what this build wrote — a failed build must leave nothing behind."""
    try:
        skill_file.unlink(missing_ok=True)
        target.rmdir()  # only succeeds when empty (our dir, our file)
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("creative build rollback incomplete for %s: %s", target, exc)


def build_proposal(proposal_id: str, *, now: float | None = None) -> dict[str, Any]:
    """Build the artifact a proposal carries. One choke point for every surface.

    Returns ``{"ok": bool, "reason": str, "path": str | None}`` and drives the
    proposal status (building → built / failed). Standard mode, unknown ids,
    artifact-less proposals and slug collisions are all refused without writes.
    """
    from navig.creative import proposals as prop
    from navig.creative.mode import get_agent_mode

    if get_agent_mode() != "creative":
        return {
            "ok": False,
            "reason": "Agent Mode is Standard — building needs Creative mode "
            "(Settings → Brain → Agent Mode).",
            "path": None,
        }

    proposal = prop.get_proposal(proposal_id)
    if proposal is None:
        return {"ok": False, "reason": f"proposal {proposal_id} not found", "path": None}
    if proposal.get("status") in (prop.STATUS_BUILT, prop.STATUS_DISMISSED, prop.STATUS_BUILDING):
        return {
            "ok": False,
            "reason": f"proposal is {proposal.get('status')} — not buildable now",
            "path": None,
        }
    artifact = proposal.get("artifact")
    if not artifact:
        return {
            "ok": False,
            "reason": "this proposal has no artifact (reuse/configure/compose "
            "proposals are run, not built)",
            "path": None,
        }

    # Overlap preflight: another proposal is ALREADY implementing this slug
    # (accepted/building/built). A merely-open duplicate does not block — two
    # open proposals with one slug would deadlock each other; the loser hits the
    # target-directory check below once the winner has written the skill.
    slug = str(artifact.get("slug") or "")
    for other in prop.list_proposals():
        if other.get("id") == proposal_id:
            continue
        other_art = other.get("artifact") or {}
        if other_art.get("slug") == slug and other.get("status") in (
            prop.STATUS_ACCEPTED,
            prop.STATUS_BUILDING,
            prop.STATUS_BUILT,
        ):
            return {
                "ok": False,
                "reason": f"overlapping work: proposal {other['id']} already owns "
                f"slug {slug!r} (status {other.get('status')})",
                "path": None,
            }

    prop.set_status(proposal_id, prop.STATUS_BUILDING)
    spec = ArtifactSpec(
        kind="skill",
        slug=slug,
        title=str(artifact.get("title") or slug),
        description=str(artifact.get("description") or slug),
        commands=[str(c) for c in (artifact.get("commands") or [])],
    )
    target, problems = build_artifact(spec)
    if target is None:
        reason = "; ".join(problems) or "build failed"
        prop.set_status(proposal_id, prop.STATUS_FAILED, error=reason)
        return {"ok": False, "reason": reason, "path": None}

    prop.set_status(proposal_id, prop.STATUS_BUILT, version_ref=None)
    return {"ok": True, "reason": "built", "path": str(target)}
