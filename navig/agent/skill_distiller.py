"""Skill-distillation prose drafting — the agent-layer seam for `--ai`.

``navig skill distill --ai`` asks an LLM to rewrite the deterministic draft's
PROSE (title line, step commentary, pitfall wording) into a better recipe.
This is the ONLY place the distill flow touches an LLM (all inference lives
in ``navig/agent/`` — architectural law), mirroring
:mod:`navig.agent.plan_drafter` over :func:`navig.llm.generate.llm_generate`.

Input contract (enforced upstream, ``navig.skill_distill``): everything passed
here is ALREADY sanitized — secrets redacted, instance values placeholdered.
Nothing unsanitized may leave the machine (plan-evidence-ledger.md §3).

Output contract: a full SKILL.md whose frontmatter block is the caller's
deterministic one, verbatim — the model only writes the body. That keeps
``name``/``description``/``safety`` (the routing + risk contract) exactly as
the honest deterministic pipeline computed them.

Raises :class:`SkillDraftUnavailableError` when no LLM backend is configured
or reachable, so the CLI can print a clean hint instead of a stack trace.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_DRAFT_TEMPERATURE = 0.2
_DRAFT_MAX_TOKENS = 1600
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", re.DOTALL)


class SkillDraftUnavailableError(RuntimeError):
    """No LLM backend is configured/reachable — AI drafting cannot run."""


def _is_unavailable(exc: Exception) -> bool:
    """True when *exc* means "no usable LLM", not "this call failed"."""
    try:
        from navig.llm.liveness import classify_probe_error

        status, _ = classify_probe_error(exc)
        return status in ("nokey", "unreachable")
    except Exception:  # pragma: no cover — classifier is best-effort
        return False


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def _split_frontmatter(markdown: str) -> tuple[str, str]:
    """(frontmatter block incl. both ``---`` fences, body) of *markdown*."""
    parts = markdown.split("---", 2)
    if markdown.startswith("---") and len(parts) >= 3:
        return f"---{parts[1]}---", parts[2].lstrip("\n")
    return "", markdown


def draft_distilled_skill(deterministic_markdown: str) -> str:
    """Rewrite the deterministic draft's body with an LLM; keep the frontmatter.

    Parameters
    ----------
    deterministic_markdown:
        The full SKILL.md produced by :func:`navig.skill_distill.render_skill_md`
        — already sanitized; the only material shown to the model.

    Returns
    -------
    str
        A full SKILL.md: the caller's frontmatter verbatim + the AI body.

    Raises
    ------
    SkillDraftUnavailableError
        When no LLM backend is configured or reachable.
    RuntimeError
        When generation fails or returns an unusable draft.
    """
    from navig.llm.generate import llm_generate  # lazy — keep import cost off boot

    frontmatter, body = _split_frontmatter(deterministic_markdown)
    if not frontmatter:
        raise ValueError("deterministic draft has no frontmatter — refusing to draft over it")

    system = (
        "You are NAVIG's skill-distillation assistant. You receive a DRAFT "
        "skill recipe distilled from a real command session. Rewrite its "
        "markdown BODY to read like a clear, reusable runbook. Rules:\n"
        "- Keep every command string EXACTLY as given — never invent, merge, "
        "reorder, or drop a command, a placeholder (<host>, <secret>, …), or "
        "a warning annotation.\n"
        "- Keep the section structure (Steps, Pitfalls observed, Placeholders, "
        "Provenance) and all provenance lines verbatim.\n"
        "- Improve only prose: the intro, per-step explanations of WHY each "
        "step exists, and what each pitfall teaches.\n"
        "- Respond with ONLY the raw markdown body — no YAML frontmatter, no "
        "code fences around the whole answer, no commentary."
    )
    try:
        raw = llm_generate(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": body},
            ],
            mode="planning",
            temperature=_DRAFT_TEMPERATURE,
            max_tokens=_DRAFT_MAX_TOKENS,
        )
    except Exception as exc:
        if _is_unavailable(exc):
            raise SkillDraftUnavailableError(
                "No AI backend is configured or reachable — connect a provider "
                "(`navig connect`) or rerun without --ai for the deterministic draft."
            ) from exc
        logger.warning("skill distill AI draft failed: %s", exc)
        raise RuntimeError(f"AI draft failed: {exc}") from exc

    new_body = _strip_fences(raw or "")
    if new_body.startswith("---"):
        # The model returned frontmatter despite instructions — drop it; the
        # deterministic frontmatter is the contract.
        _, new_body = _split_frontmatter(new_body)
    if not new_body.strip():
        raise RuntimeError("the model returned an empty draft")

    # Command fidelity gate: every step command from the deterministic draft
    # must survive verbatim, or the "recipe from what actually worked" claim
    # is void — fall back is the caller's decision, we just refuse.
    for line in body.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+\.\s+`", stripped):
            cmd = stripped.split("`")[1] if "`" in stripped else ""
            if cmd and f"`{cmd}`" not in new_body:
                raise RuntimeError(
                    f"AI draft dropped or altered a step command ({cmd!r}) — refusing it"
                )

    return f"{frontmatter}\n\n{new_body.strip()}\n"
