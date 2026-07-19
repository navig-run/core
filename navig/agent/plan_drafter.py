"""Plan-doc drafting — the agent-layer seam for AI plan-suite generation.

Given a space's VISION.md (the seed), the scaffold template for one plan doc,
and the names of docs that already exist, draft real starter content for that
doc. This is the ONLY place the plans-generate flow touches an LLM (all
inference lives in ``navig/agent/`` — architectural law), mirroring how the
deck board's task generation funnels through :func:`navig.llm.generate.llm_generate`.

Callers (``navig/gateway/deck/routes/plans.py`` mode="ai") invoke
:func:`draft_plan_doc` once per missing doc, sequentially, off the event loop.

Raises :class:`PlanDraftUnavailableError` when no LLM backend is configured or
reachable, so routes can map it to a clean 503 instead of a stack trace.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Deterministic-ish drafts: plan docs should come out the same for the same
# vision, so re-runs don't churn the suite.
_DRAFT_TEMPERATURE = 0.2
_DRAFT_MAX_TOKENS = 1600
_VISION_CAP = 6000  # chars of VISION.md fed to the model (plans, not books)

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", re.DOTALL)


class PlanDraftUnavailableError(RuntimeError):
    """No LLM backend is configured/reachable — drafting cannot run.

    Routes map this to a 503 with the message verbatim; surfaces should point
    the user at provider setup (the Connections app / ``navig connect``).
    """


def _is_unavailable(exc: Exception) -> bool:
    """True when *exc* means "no usable LLM", not "this call failed"."""
    try:
        from navig.llm.liveness import classify_probe_error

        status, _ = classify_probe_error(exc)
        return status in ("nokey", "unreachable")
    except Exception:  # pragma: no cover — classifier is best-effort
        return False


def _strip_fences(text: str) -> str:
    """Unwrap a whole-response markdown code fence, if the model added one."""
    m = _FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def draft_plan_doc(
    *,
    doc_name: str,
    template: str,
    vision: str,
    existing_docs: Sequence[str] = (),
    project_name: str = "",
) -> str:
    """Draft one plan doc's markdown from the project vision. Sync + blocking.

    Parameters
    ----------
    doc_name:
        The doc being drafted, e.g. ``ROADMAP.md`` or ``phases/CURRENT_PHASE.md``.
    template:
        The scaffold's skeleton for this doc — its frontmatter keys and section
        headings are the structural contract the draft must keep.
    vision:
        VISION.md content (or a caller-supplied vision seed). Required.
    existing_docs:
        Names of plan docs that already exist in the suite, so the draft can
        reference them instead of duplicating their content.
    project_name:
        Optional human name of the space/project for the prompt.

    Returns
    -------
    str
        Markdown for the doc (fences stripped, non-empty).

    Raises
    ------
    PlanDraftUnavailableError
        When no LLM backend is configured or reachable.
    RuntimeError
        When generation fails or returns an empty draft.
    """
    from navig.llm.generate import llm_generate  # lazy — keep import cost off boot

    vision = (vision or "").strip()
    if not vision:
        raise ValueError("vision text is required to draft plan docs")

    system = (
        "You are NAVIG's planning assistant. Draft the initial content of ONE "
        "project plan document based on the project's vision. Rules:\n"
        "- Keep the template's structure EXACTLY: same frontmatter keys (update "
        "only their values where the vision implies better ones) and the same "
        "markdown section headings, in order.\n"
        "- Replace placeholder prose with concrete, project-specific content "
        "grounded ONLY in the vision. Do not invent facts, dates, or metrics "
        "the vision does not imply.\n"
        "- Prefer short actionable checklists (`- [ ] …`) over paragraphs.\n"
        "- Keep it concise: a starting plan a human will edit, not a novel.\n"
        "- Respond with ONLY the raw markdown for the document — no code "
        "fences, no commentary before or after."
    )
    user_parts = [f"Document to draft: {doc_name}"]
    if project_name:
        user_parts.append(f"Project: {project_name}")
    user_parts.append(f"\nProject vision (the seed — ground everything in this):\n{vision[:_VISION_CAP]}")
    user_parts.append(f"\nTemplate skeleton (keep this structure):\n{template.strip()}")
    if existing_docs:
        user_parts.append(
            "\nOther plan docs that already exist (reference them, don't restate them): "
            + ", ".join(sorted(existing_docs))
        )
    user = "\n".join(user_parts)

    try:
        raw = llm_generate(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            mode="planning",
            temperature=_DRAFT_TEMPERATURE,
            max_tokens=_DRAFT_MAX_TOKENS,
        )
    except PlanDraftUnavailableError:
        raise
    except Exception as exc:
        if _is_unavailable(exc):
            raise PlanDraftUnavailableError(
                "No AI backend is configured or reachable — connect a provider "
                "(Connections app, or `navig connect`) and retry."
            ) from exc
        logger.warning("plan draft failed for %s: %s", doc_name, exc)
        raise RuntimeError(f"draft failed: {exc}") from exc

    text = _strip_fences(raw or "")
    if not text:
        raise RuntimeError("the model returned an empty draft")
    return text
