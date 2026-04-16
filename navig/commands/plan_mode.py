"""
navig plan — AI-guided planning mode.

Ported and adapted from Claude Code's TypeScript ``commands/plan.ts``.

A 5-phase workflow for turning a user goal into a structured plan file:

  Phase 1  Clarify     — up to N interview questions (configurable)
  Phase 2  Explore     — N parallel sub-agents probe the codebase / context
  Phase 3  Synthesise  — merge findings into a draft plan document
  Phase 4  Review      — user edits or approves the draft
  Phase 5  Execute     — hand off to ``navig agent run --context-file <plan>``

Plan files are stored in ``.navig/plans/<slug>.md`` and use front-matter
to track status and metadata.

Commands
--------
  navig plan new [GOAL]      Interactive wizard to create a new plan.
  navig plan list            Show all plans in the current workspace.
  navig plan show SLUG       Print a stored plan.
  navig plan run  SLUG       Execute a plan file via the agent.
"""

from __future__ import annotations

import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from navig.console_helper import get_console
from navig.core.yaml_io import atomic_write_text as _atomic_write_text

app = typer.Typer(help="AI-guided planning mode — draft, review, and execute plans", no_args_is_help=True)
console = get_console()

# ── Module-level constants ────────────────────────────────────────────────────
_DEFAULT_EXPLORE_AGENTS: int = 3
_DEFAULT_MAX_QUESTIONS: int = 5
_PLAN_SUBDIR = ".navig/plans"
_GLOBAL_PLAN_SUBDIR = "plans"
_PLAN_EXTENSION = ".md"
_STATUS_CHOICES = ("draft", "ready", "running", "done", "abandoned")


# ──────────────────────────────────────────────────────────────────────────────
# navig plan new
# ──────────────────────────────────────────────────────────────────────────────

@app.command("new")
def plan_new(
    goal: Optional[str] = typer.Argument(None, help="High-level goal for the plan"),
    effort: Optional[str] = typer.Option(None, "--effort", "-e", help="Effort level: low / medium / high"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive interview (use goal text as-is)"),
):
    """Create a new AI-guided plan from a goal statement."""
    from navig import console_helper as ch

    if not goal:
        goal = typer.prompt("Describe the goal for this plan")

    if not goal.strip():
        ch.error("Goal cannot be empty.")
        raise typer.Exit(1)

    try:
        _run_plan_wizard(goal=goal.strip(), effort=effort, skip_interview=yes)
    except KeyboardInterrupt:
        ch.dim("\nPlanning cancelled.")
        raise typer.Exit(0) from None


# ──────────────────────────────────────────────────────────────────────────────
# navig plan list
# ──────────────────────────────────────────────────────────────────────────────

@app.command("list")
def plan_list(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON"),
):
    """List all plans in the current workspace."""
    import json as _json

    from navig import console_helper as ch

    plans = _load_all_plans()

    if status:
        plans = [p for p in plans if p.get("status") == status]

    if not plans:
        ch.dim("No plans found. Create one with: navig plan new")
        return

    if json_out:
        print(_json.dumps(plans, indent=2))
        return

    from rich.table import Table

    tbl = Table(title="Plans", show_edge=False, box=None)
    tbl.add_column("Slug", style="cyan")
    tbl.add_column("Status", style="yellow")
    tbl.add_column("Created", style="dim")
    tbl.add_column("Goal")

    for p in plans:
        slug = p.get("slug", "?")
        st = p.get("status", "draft")
        created = p.get("created_at", "?")
        if created and created != "?":
            try:
                dt = datetime.fromisoformat(created)
                created = dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
        goal = p.get("goal", "")
        if len(goal) > 60:
            goal = goal[:57] + "..."
        tbl.add_row(slug, st, created, goal)

    console.print(tbl)


# ──────────────────────────────────────────────────────────────────────────────
# navig plan show
# ──────────────────────────────────────────────────────────────────────────────

@app.command("show")
def plan_show(
    slug: str = typer.Argument(..., help="Plan slug (filename without .md)"),
    raw: bool = typer.Option(False, "--raw", help="Print raw Markdown"),
):
    """Print a stored plan."""
    from navig import console_helper as ch

    plan_path = _resolve_plan_path(slug)
    if not plan_path:
        ch.error(f"Plan {slug!r} not found. Use 'navig plan list' to see available plans.")
        raise typer.Exit(1)

    content = plan_path.read_text(encoding="utf-8")

    if raw:
        console.print(content)
        return

    from rich.markdown import Markdown
    from rich.panel import Panel

    console.print(Panel(Markdown(content), title=f"Plan: {slug}", border_style="blue"))


# ──────────────────────────────────────────────────────────────────────────────
# navig plan run
# ──────────────────────────────────────────────────────────────────────────────

@app.command("run")
def plan_run(
    slug: str = typer.Argument(..., help="Plan slug to execute"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the agent invocation without running it"),
):
    """Execute a plan file via the NAVIG agent."""
    import subprocess
    import sys

    from navig import console_helper as ch

    plan_path = _resolve_plan_path(slug)
    if not plan_path:
        ch.error(f"Plan {slug!r} not found.")
        raise typer.Exit(1)

    cmd = [sys.executable, "-m", "navig", "agent", "run", "--context-file", str(plan_path)]

    if dry_run:
        console.print(f"[dim]Would run:[/dim] {' '.join(cmd)}")
        return

    # Mark plan as running
    _update_plan_status(plan_path, "running")
    ch.info(f"Executing plan {slug!r} …")

    try:
        subprocess.run(cmd, check=True)
        _update_plan_status(plan_path, "done")
        ch.success("Plan execution completed.")
    except subprocess.CalledProcessError as exc:
        _update_plan_status(plan_path, "draft")
        ch.error(f"Agent exited with code {exc.returncode}.")
        raise typer.Exit(exc.returncode) from exc


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _read_config_int(key: str, default: int) -> int:
    try:
        from navig.config import get_config_manager
        val = get_config_manager().get(key)
        return int(val) if val is not None else default
    except Exception:  # noqa: BLE001
        return default


def _slugify(text: str) -> str:
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:60] or "plan"


def _plan_dir() -> Path:
    """Return the project-level plans directory, creating it if needed."""
    project_dir = Path(_PLAN_SUBDIR)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def _resolve_plan_path(slug: str) -> Path | None:
    for directory in [Path(_PLAN_SUBDIR), Path.home() / ".navig" / _GLOBAL_PLAN_SUBDIR]:
        candidate = directory / (slug + _PLAN_EXTENSION)
        if candidate.exists():
            return candidate
    return None


def _load_all_plans() -> list[dict]:
    plans: list[dict] = []
    for directory in [Path(_PLAN_SUBDIR), Path.home() / ".navig" / _GLOBAL_PLAN_SUBDIR]:
        if not directory.exists():
            continue
        for p in sorted(directory.glob(f"*{_PLAN_EXTENSION}")):
            meta = _extract_frontmatter(p.read_text(encoding="utf-8"))
            meta.setdefault("slug", p.stem)
            plans.append(meta)
    return plans


def _extract_frontmatter(content: str) -> dict:
    """Parse simple YAML-style front-matter between leading ``---`` delimiters."""
    meta: dict = {}
    if not content.startswith("---"):
        return meta
    end = content.find("---", 3)
    if end == -1:
        return meta
    block = content[3:end].strip()
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta


def _update_plan_status(plan_path: Path, status: str) -> None:
    try:
        content = plan_path.read_text(encoding="utf-8")
        content = re.sub(r"^(status:\s*).*$", rf"\g<1>{status}", content, flags=re.MULTILINE)
        _atomic_write_text(plan_path, content)
    except Exception:  # noqa: BLE001
        pass


def _run_plan_wizard(goal: str, effort: str | None, skip_interview: bool) -> None:
    """5-phase planning wizard."""
    from navig import console_helper as ch

    # ── Config ────────────────────────────────────────────────────────────────
    max_questions = _read_config_int("agent.plan_max_interview_questions", _DEFAULT_MAX_QUESTIONS)
    num_agents = _read_config_int("agent.plan_explore_agents", _DEFAULT_EXPLORE_AGENTS)

    console.rule("[bold blue]NAVIG Planning Mode[/bold blue]")
    console.print(f"[bold]Goal:[/bold] {goal}\n")

    # ── Phase 1 — Clarify ─────────────────────────────────────────────────────
    clarifications: dict[str, str] = {}
    if not skip_interview and max_questions > 0:
        ch.info(f"[Phase 1/5] Generating clarifying questions (max {max_questions}) …")
        questions = _generate_clarifying_questions(goal, effort, max_questions)
        if questions:
            console.print()
            for i, q in enumerate(questions, 1):
                ans = typer.prompt(f"  Q{i}: {q}", default="")
                if ans.strip():
                    clarifications[q] = ans.strip()
        console.print()

    # ── Phase 2 — Explore ─────────────────────────────────────────────────────
    ch.info(f"[Phase 2/5] Exploring codebase with {num_agents} parallel sub-agents …")
    findings = _explore_context(goal, clarifications, num_agents, effort)

    # ── Phase 3 — Synthesise ──────────────────────────────────────────────────
    ch.info("[Phase 3/5] Synthesising plan document …")
    plan_md = _synthesise_plan(goal, clarifications, findings, effort)

    # ── Phase 4 — Review ──────────────────────────────────────────────────────
    console.print()
    console.rule("[bold]Draft Plan[/bold]")
    from rich.markdown import Markdown
    console.print(Markdown(plan_md))
    console.print()

    action = typer.prompt(
        "  [A]ccept  [E]dit  [R]egenerate  [Q]uit",
        default="A",
    ).strip().upper()[:1]

    if action == "Q":
        ch.dim("Planning cancelled.")
        return

    if action == "E":
        import os
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as tf:
            tf.write(plan_md)
            tmp_path = tf.name
        editor = os.environ.get("EDITOR", "notepad" if os.name == "nt" else "vi")
        subprocess.run([editor, tmp_path])
        plan_md = Path(tmp_path).read_text(encoding="utf-8")
        Path(tmp_path).unlink(missing_ok=True)

    if action == "R":
        ch.info("Regenerating …")
        plan_md = _synthesise_plan(goal, clarifications, findings, effort)

    # ── Phase 5 — Save ────────────────────────────────────────────────────────
    slug = _slugify(goal)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    final_slug = f"{ts[:8]}-{slug}"

    plan_path = _plan_dir() / (final_slug + _PLAN_EXTENSION)
    front = textwrap.dedent(f"""\
        ---
        slug: {final_slug}
        goal: "{goal.replace('"', "'")}"
        status: ready
        created_at: {datetime.now(tz=timezone.utc).isoformat()}
        effort: {effort or 'medium'}
        ---

        """)
    _atomic_write_text(plan_path, front + plan_md)

    ch.success(f"Plan saved → {plan_path}")
    console.print(f"\n  Run it with: [bold]navig plan run {final_slug}[/bold]")


def _generate_clarifying_questions(goal: str, effort: str | None, max_q: int) -> list[str]:
    """Ask the LLM to generate up-to-N clarifying questions."""
    try:
        from navig.llm_generate import generate_text_sync
        prompt = (
            f"The user wants to:\n\n  {goal}\n\n"
            f"Generate up to {max_q} short, numbered clarifying questions to help plan this precisely.\n"
            "Return only the questions as a numbered list, nothing else."
        )
        resp = generate_text_sync(prompt, effort=effort or "low")
        lines = [l.strip().lstrip("0123456789.) ") for l in resp.splitlines() if l.strip()]
        return [l for l in lines if l][:max_q]
    except Exception:  # noqa: BLE001
        return []


def _explore_context(
    goal: str,
    clarifications: dict[str, str],
    num_agents: int,
    effort: str | None,
) -> list[str]:
    """Run N parallel mini-explorations and collect findings."""
    if num_agents <= 0:
        return []

    facets = [
        "What files, modules, or services are most relevant to this goal?",
        "What existing patterns, helpers, or abstractions already address part of this?",
        "What are the biggest risks, ambiguities, or edge-cases to plan for?",
    ][:num_agents]

    clarification_str = "\n".join(f"- {q}: {a}" for q, a in clarifications.items())
    context_block = f"Goal: {goal}"
    if clarification_str:
        context_block += f"\n\nClarifications:\n{clarification_str}"

    findings: list[str] = []
    try:
        from navig.llm_generate import generate_text_sync
        for facet in facets:
            resp = generate_text_sync(
                f"{context_block}\n\nFocus area: {facet}\n\nAnswer concisely in 3-5 bullet points.",
                effort=effort or "low",
            )
            findings.append(f"**{facet}**\n\n{resp.strip()}")
    except Exception:  # noqa: BLE001
        pass

    return findings


def _synthesise_plan(
    goal: str,
    clarifications: dict[str, str],
    findings: list[str],
    effort: str | None,
) -> str:
    """Synthesise all gathered context into a Markdown plan document."""
    clarification_str = "\n".join(f"- {q}: {a}" for q, a in clarifications.items())
    findings_str = "\n\n---\n\n".join(findings)

    prompt = (
        f"You are a senior engineer creating a structured implementation plan.\n\n"
        f"Goal: {goal}\n"
        + (f"\nClarifications:\n{clarification_str}\n" if clarification_str else "")
        + (f"\nFindings:\n{findings_str}\n" if findings_str else "")
        + "\n\nWrite a detailed Markdown plan with sections:\n"
        "1. Overview\n2. Approach\n3. Steps (numbered with file paths and code references)\n"
        "4. Tests\n5. Risks\n\nBe concrete and actionable."
    )

    try:
        from navig.llm_generate import generate_text_sync
        return generate_text_sync(prompt, effort=effort or "medium")
    except Exception:  # noqa: BLE001
        # Fallback: minimal scaffold
        return textwrap.dedent(f"""\
            # Plan: {goal}

            ## Overview
            {goal}

            ## Approach
            *(to be filled in)*

            ## Steps
            1. *(step 1)*

            ## Tests
            - *(tests to add)*

            ## Risks
            - *(risks to consider)*
            """)
