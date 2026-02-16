
import typer
from typing import Optional
from pathlib import Path

from navig.lazy_loader import lazy_import
ch = lazy_import("navig.console_helper")

evolution_app = typer.Typer(
    name="evolve",
    help="Auto-evolve skills, workflows, and more",
    no_args_is_help=True,
)

@evolution_app.command("skill")
def evolve_skill(
    goal: str = typer.Argument(..., help="Description of the skill to create"),
    skills_root: Optional[Path] = typer.Option(None, "--root", "-r", help="Root directory for skills"),
    retries: int = typer.Option(3, "--retries", "-n", help="Max evolution attempts"),
):
    """Generate and refine a new skill definition (SKILL.md)."""
    from navig.core.evolution.skill import SkillEvolver
    
    if not skills_root:
        # Default to navig/skills if exists, else ~/.navig/skills
        # For now assume local project skills
        skills_root = Path("skills") # Relative to CWD
        
    evolver = SkillEvolver(skills_root)
    evolver.max_retries = retries
    
    ch.info(f"Evolving skill for: {goal}")
    result = evolver.evolve(goal)
    
    if result.success:
        ch.success("Skill evolution successful!")
        # Could print path
    else:
        ch.error(f"Skill evolution failed: {result.error}")
        
@evolution_app.command("workflow")
def evolve_workflow(
    goal: str = typer.Argument(..., help="Description of the workflow to create"),
    retries: int = typer.Option(3, "--retries", "-n", help="Max evolution attempts"),
):
    """Generate and refine a new automation workflow (YAML)."""
    from navig.core.evolution.workflow import WorkflowEvolver
    
    evolver = WorkflowEvolver()
    evolver.max_retries = retries
    
    ch.info(f"Evolving workflow for: {goal}")
    result = evolver.evolve(goal)
    
    if result.success:
        ch.success("Workflow evolution successful!")
    else:
        ch.error(f"Workflow evolution failed: {result.error}")


@evolution_app.command("pack")
def evolve_pack(
    goal: str = typer.Argument(..., help="Description of the pack to create"),
    retries: int = typer.Option(3, "--retries", "-n", help="Max evolution attempts"),
):
    """Generate a new Pack (collection of skills)."""
    from navig.core.evolution.pack import PackEvolver
    
    evolver = PackEvolver()
    evolver.max_retries = retries
    
    ch.info(f"Evolving pack for: {goal}")
    result = evolver.evolve(goal)
    
    if result.success:
        ch.success("Pack evolution successful!")
    else:
        ch.error(f"Pack evolution failed: {result.error}")


@evolution_app.command("script")
def evolve_script(
    goal: str = typer.Argument(..., help="Description of the script to create"),
    retries: int = typer.Option(3, "--retries", "-n", help="Max evolution attempts"),
):
    """Generate a Python automation script."""
    from navig.core.evolution.script import ScriptEvolver
    
    evolver = ScriptEvolver()
    evolver.max_retries = retries
    
    ch.info(f"Evolving script for: {goal}")
    result = evolver.evolve(goal)
    
    if result.success:
        ch.success("Script evolution successful!")
    else:
        ch.error(f"Script evolution failed: {result.error}")


@evolution_app.command("fix")
def evolve_fix(
    file_path: Path = typer.Argument(..., help="Path to the file to fix"),
    instruction: str = typer.Argument(..., help="Description of the bug or improvement"),
    check: Optional[str] = typer.Option(None, "--check", "-c", help="Command to run for validation (use {file} as placeholder)"),
):
    """Attempt to fix or improve an existing file."""
    if not file_path.exists():
        ch.error(f"File not found: {file_path}")
        raise typer.Exit(1)
        
    from navig.core.evolution.fix import FixEvolver
    
    evolver = FixEvolver(file_path, check_command=check)
    
    ch.info(f"Analyzing {file_path.name} for fix: {instruction}")
    result = evolver.evolve(instruction)
    
    if result.success:
        ch.success("File update successful!")
    else:
        ch.error(f"Fix failed: {result.error}")
