"""
Workflow Commands - Reusable command sequences for NAVIG

Workflows are defined as YAML files and support:
- Sequential command execution
- Variable substitution (${variable})
- Conditional steps (continue_on_error, skip_on_error)
- Dry-run mode
- Interactive prompts
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from rich.table import Table

from navig import console_helper as ch

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class WorkflowStep:
    """Represents a single step in a workflow."""
    name: str
    command: str
    description: str = ""
    prompt: str = ""  # Interactive confirmation prompt
    continue_on_error: bool = False
    skip_on_error: bool = False  # Skip this step if previous failed

    def to_dict(self) -> Dict[str, Any]:
        """Convert step to dictionary for serialization."""
        data = {"name": self.name, "command": self.command}
        if self.description:
            data["description"] = self.description
        if self.prompt:
            data["prompt"] = self.prompt
        if self.continue_on_error:
            data["continue_on_error"] = True
        if self.skip_on_error:
            data["skip_on_error"] = True
        return data


@dataclass
class Workflow:
    """Represents a complete workflow definition."""
    name: str
    description: str = ""
    variables: Dict[str, Any] = field(default_factory=dict)
    steps: List[WorkflowStep] = field(default_factory=list)
    author: str = ""
    version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        """Convert workflow to dictionary for serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "variables": self.variables,
            "steps": [step.to_dict() for step in self.steps]
        }


# ============================================================================
# WORKFLOW MANAGER
# ============================================================================

class WorkflowManager:
    """Manages workflow discovery, parsing, validation, and execution."""

    def __init__(self, config_manager=None):
        """Initialize workflow manager with config paths."""
        from navig.config import get_config_manager
        self.config_manager = config_manager or get_config_manager()

        # Workflow locations (in priority order)
        self.locations = self._get_workflow_locations()

        # Cached workflows
        self._workflows: Dict[str, Workflow] = {}
        self._workflow_paths: Dict[str, Path] = {}

    def _get_workflow_locations(self) -> List[Path]:
        """Get all workflow directories in priority order."""
        locations = []

        # 1. Project-local (highest priority)
        if self.config_manager.app_config_dir:
            local_workflows = self.config_manager.app_config_dir / "workflows"
            if local_workflows.exists():
                locations.append(local_workflows)

        # 2. Global user workflows
        global_workflows = self.config_manager.global_config_dir / "workflows"
        global_workflows.mkdir(parents=True, exist_ok=True)
        locations.append(global_workflows)

        # 3. Built-in workflows (bundled with NAVIG)
        builtin_workflows = Path(__file__).parent.parent / "resources" / "workflows"
        if builtin_workflows.exists():
            locations.append(builtin_workflows)

        return locations

    def discover_workflows(self) -> Dict[str, Path]:
        """
        Discover all available workflows.
        
        Returns:
            Dict mapping workflow names to their file paths.
            Project-local workflows override global ones.
        """
        workflows = {}

        # Process in reverse order so higher-priority locations override
        for location in reversed(self.locations):
            if location.exists():
                for yaml_file in location.glob("*.yaml"):
                    name = yaml_file.stem
                    workflows[name] = yaml_file
                for yml_file in location.glob("*.yml"):
                    name = yml_file.stem
                    workflows[name] = yml_file

        self._workflow_paths = workflows
        return workflows

    def get_workflow_source(self, path: Path) -> str:
        """Get human-readable source label for workflow path."""
        path_str = str(path)

        if "resources" in path_str:
            return "builtin"
        elif self.config_manager.app_config_dir and str(self.config_manager.app_config_dir) in path_str:
            return "project"
        else:
            return "global"

    def load_workflow(self, name: str) -> Optional[Workflow]:
        """
        Load and parse a workflow by name.
        
        Args:
            name: Workflow name (filename without extension)
            
        Returns:
            Workflow object or None if not found
        """
        if not self._workflow_paths:
            self.discover_workflows()

        if name not in self._workflow_paths:
            return None

        path = self._workflow_paths[name]
        return self._parse_workflow_file(path)

    def _parse_workflow_file(self, path: Path) -> Optional[Workflow]:
        """Parse a YAML workflow file into a Workflow object."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)

            if not data:
                return None

            # Parse steps
            steps = []
            for step_data in data.get("steps", []):
                step = WorkflowStep(
                    name=step_data.get("name", "Unnamed step"),
                    command=step_data.get("command", ""),
                    description=step_data.get("description", ""),
                    prompt=step_data.get("prompt", ""),
                    continue_on_error=step_data.get("continue_on_error", False),
                    skip_on_error=step_data.get("skip_on_error", False),
                )
                steps.append(step)

            return Workflow(
                name=data.get("name", path.stem),
                description=data.get("description", ""),
                variables=data.get("variables", {}),
                steps=steps,
                author=data.get("author", ""),
                version=str(data.get("version", "1.0")),
            )
        except yaml.YAMLError as e:
            ch.error(f"YAML parsing error in {path}: {e}")
            return None
        except Exception as e:
            ch.error(f"Error loading workflow {path}: {e}")
            return None

    def validate_workflow(self, workflow: Workflow) -> List[str]:
        """
        Validate a workflow for common issues.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if not workflow.name:
            errors.append("Workflow name is required")

        if not workflow.steps:
            errors.append("Workflow must have at least one step")

        for i, step in enumerate(workflow.steps):
            if not step.command:
                errors.append(f"Step {i+1} '{step.name}' has no command")

            # Check for undefined variables
            var_pattern = r'\$\{(\w+)\}'
            variables = re.findall(var_pattern, step.command)
            for var in variables:
                if var not in workflow.variables:
                    errors.append(f"Step {i+1} uses undefined variable: ${{{var}}}")

        return errors

    def substitute_variables(self, text: str, variables: Dict[str, Any], extra_vars: Dict[str, Any] = None) -> str:
        """
        Substitute ${variable} placeholders with actual values.
        
        Args:
            text: Text containing variable placeholders
            variables: Workflow-defined variables
            extra_vars: Runtime-provided variable overrides
            
        Returns:
            Text with variables substituted
        """
        merged_vars = {**variables}
        if extra_vars:
            merged_vars.update(extra_vars)

        def replace_var(match):
            var_name = match.group(1)
            return str(merged_vars.get(var_name, match.group(0)))

        return re.sub(r'\$\{(\w+)\}', replace_var, text)

    def prompt_for_variables(self, workflow: Workflow, provided_vars: Dict[str, str] = None) -> Dict[str, str]:
        """
        Interactively prompt for missing variables.
        
        Args:
            workflow: Workflow with variable definitions
            provided_vars: Variables already provided via CLI
            
        Returns:
            Complete variable dictionary
        """
        import typer

        result = {}
        provided_vars = provided_vars or {}

        for var_name, default_value in workflow.variables.items():
            if var_name in provided_vars:
                result[var_name] = provided_vars[var_name]
            else:
                # Prompt with default value
                default_str = f" [{default_value}]" if default_value else ""
                user_input = typer.prompt(
                    f"  {var_name}{default_str}",
                    default=str(default_value) if default_value else "",
                    show_default=False
                )
                result[var_name] = user_input if user_input else str(default_value)

        return result

    def execute_workflow(
        self,
        workflow: Workflow,
        variables: Dict[str, str] = None,
        dry_run: bool = False,
        skip_prompts: bool = False,
        verbose: bool = False
    ) -> bool:
        """
        Execute a workflow.
        
        Args:
            workflow: Workflow to execute
            variables: Variable values (will prompt for missing)
            dry_run: If True, show commands without executing
            skip_prompts: If True, auto-confirm all prompts
            verbose: If True, show detailed output
            
        Returns:
            True if all steps succeeded, False otherwise
        """
        ch.header(f"Workflow: {workflow.name}")
        if workflow.description:
            ch.info(workflow.description)
        ch.console.print("")

        # Resolve variables
        final_vars = self.prompt_for_variables(workflow, variables) if not skip_prompts else (variables or {})

        # Merge with defaults
        for var_name, default_value in workflow.variables.items():
            if var_name not in final_vars:
                final_vars[var_name] = str(default_value)

        if dry_run:
            ch.warning("🔍 DRY RUN MODE - Commands will NOT be executed\n")

        success_count = 0
        fail_count = 0
        skip_count = 0
        previous_failed = False

        for i, step in enumerate(workflow.steps, 1):
            step_header = f"[{i}/{len(workflow.steps)}] {step.name}"

            # Check skip_on_error
            if step.skip_on_error and previous_failed:
                ch.warning(f"⏭️  {step_header} - Skipped (previous step failed)")
                skip_count += 1
                continue

            # Substitute variables in command
            command = self.substitute_variables(step.command, final_vars)

            ch.console.print(f"\n[bold cyan]→ {step_header}[/bold cyan]")
            if step.description:
                ch.console.print(f"   {step.description}")
            ch.console.print(f"   [dim]$ navig {command}[/dim]")

            # Handle interactive prompts
            if step.prompt and not skip_prompts and not dry_run:
                import typer
                if not typer.confirm(f"   {step.prompt}", default=True):
                    ch.warning("   Skipped by user")
                    skip_count += 1
                    continue

            if dry_run:
                ch.success(f"   ✓ Would execute: navig {command}")
                success_count += 1
                continue

            # Execute the command
            try:
                result = self._execute_navig_command(command, verbose)
                if result:
                    ch.success("   ✓ Completed")
                    success_count += 1
                    previous_failed = False
                else:
                    if step.continue_on_error:
                        ch.warning("   ⚠ Failed (continuing)")
                        fail_count += 1
                        previous_failed = True
                    else:
                        ch.error("   ✗ Failed")
                        fail_count += 1
                        previous_failed = True
                        if not step.continue_on_error:
                            ch.error("\nWorkflow aborted due to step failure.")
                            break
            except Exception as e:
                ch.error(f"   ✗ Error: {e}")
                fail_count += 1
                previous_failed = True
                if not step.continue_on_error:
                    ch.error("\nWorkflow aborted due to step failure.")
                    break

        # Summary
        ch.console.print("")
        ch.header("Workflow Summary")

        summary_parts = []
        if success_count:
            summary_parts.append(f"[green]✓ {success_count} succeeded[/green]")
        if fail_count:
            summary_parts.append(f"[red]✗ {fail_count} failed[/red]")
        if skip_count:
            summary_parts.append(f"[yellow]⏭ {skip_count} skipped[/yellow]")

        ch.console.print("  " + " | ".join(summary_parts))

        return fail_count == 0

    def _execute_navig_command(self, command: str, verbose: bool = False) -> bool:
        """
        Execute a NAVIG command.
        
        Args:
            command: Command string (without 'navig' prefix)
            verbose: Show command output
            
        Returns:
            True if command succeeded
        """
        # Build full command
        full_command = [sys.executable, "-m", "navig"] + command.split()

        try:
            result = subprocess.run(
                full_command,
                capture_output=not verbose,
                text=True,
                timeout=300  # 5 minute timeout
            )

            if verbose and result.stdout:
                ch.console.print(result.stdout)

            return result.returncode == 0
        except subprocess.TimeoutExpired:
            ch.error("Command timed out after 5 minutes")
            return False
        except Exception as e:
            ch.error(f"Command execution error: {e}")
            return False

    def create_workflow(self, name: str, global_scope: bool = False) -> Optional[Path]:
        """
        Create a new workflow from template.
        
        Args:
            name: Workflow name
            global_scope: If True, create in global directory
            
        Returns:
            Path to created workflow file
        """
        # Determine target directory
        if global_scope:
            target_dir = self.config_manager.global_config_dir / "workflows"
        elif self.config_manager.app_config_dir:
            target_dir = self.config_manager.app_config_dir / "workflows"
        else:
            target_dir = self.config_manager.global_config_dir / "workflows"

        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{name}.yaml"

        if target_file.exists():
            ch.error(f"Workflow '{name}' already exists at {target_file}")
            return None

        # Create template
        template = Workflow(
            name=name.replace("-", " ").replace("_", " ").title(),
            description="Describe what this workflow does",
            version="1.0",
            variables={"host": "production", "app": "myapp"},
            steps=[
                WorkflowStep(
                    name="Example step",
                    command="host list",
                    description="This is an example step"
                ),
                WorkflowStep(
                    name="Step with prompt",
                    command="run \"echo 'Hello from ${host}'\"",
                    prompt="Continue with this step?"
                ),
            ]
        )

        with open(target_file, 'w', encoding='utf-8') as f:
            yaml.dump(template.to_dict(), f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        return target_file

    def delete_workflow(self, name: str) -> bool:
        """
        Delete a workflow file.
        
        Args:
            name: Workflow name
            
        Returns:
            True if deleted successfully
        """
        if not self._workflow_paths:
            self.discover_workflows()

        if name not in self._workflow_paths:
            ch.error(f"Workflow '{name}' not found")
            return False

        path = self._workflow_paths[name]

        # Check if it's a builtin workflow
        if "resources" in str(path):
            ch.error(f"Cannot delete built-in workflow '{name}'")
            return False

        try:
            path.unlink()
            ch.success(f"Deleted workflow: {path}")
            return True
        except Exception as e:
            ch.error(f"Failed to delete workflow: {e}")
            return False


# ============================================================================
# CLI COMMANDS
# ============================================================================

def list_workflows():
    """List all available workflows."""
    manager = WorkflowManager()
    workflows = manager.discover_workflows()

    if not workflows:
        ch.warning("No workflows found.")
        ch.info("\nCreate one with: navig workflow create <name>")
        ch.info("Or copy built-in workflows to ~/.navig/workflows/")
        return

    table = Table(title="Available Workflows")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="dim")
    table.add_column("Description")
    table.add_column("Steps", justify="right")

    for name, path in sorted(workflows.items()):
        source = manager.get_workflow_source(path)
        workflow = manager.load_workflow(name)
        if workflow:
            desc = workflow.description[:50] + "..." if len(workflow.description) > 50 else workflow.description
            table.add_row(name, source, desc, str(len(workflow.steps)))
        else:
            table.add_row(name, source, "[red]Parse error[/red]", "-")

    ch.console.print(table)


def show_workflow(name: str):
    """Display workflow definition."""
    manager = WorkflowManager()
    manager.discover_workflows()

    if name not in manager._workflow_paths:
        ch.error(f"Workflow '{name}' not found")
        return

    path = manager._workflow_paths[name]
    workflow = manager.load_workflow(name)

    if not workflow:
        ch.error(f"Failed to parse workflow '{name}'")
        return

    # Header
    ch.header(workflow.name)
    if workflow.description:
        ch.console.print(f"[dim]{workflow.description}[/dim]\n")

    # Metadata
    ch.console.print(f"[bold]Source:[/bold] {manager.get_workflow_source(path)}")
    ch.console.print(f"[bold]Path:[/bold] {path}")
    ch.console.print(f"[bold]Version:[/bold] {workflow.version}")
    if workflow.author:
        ch.console.print(f"[bold]Author:[/bold] {workflow.author}")

    # Variables
    if workflow.variables:
        ch.console.print("\n[bold]Variables:[/bold]")
        for var_name, default_value in workflow.variables.items():
            ch.console.print(f"  ${{{var_name}}} = {default_value}")

    # Steps
    ch.console.print(f"\n[bold]Steps ({len(workflow.steps)}):[/bold]")
    for i, step in enumerate(workflow.steps, 1):
        status_icons = []
        if step.continue_on_error:
            status_icons.append("⚠️")
        if step.prompt:
            status_icons.append("❓")

        icons = " ".join(status_icons)
        ch.console.print(f"  {i}. [cyan]{step.name}[/cyan] {icons}")
        ch.console.print(f"     [dim]$ navig {step.command}[/dim]")
        if step.description:
            ch.console.print(f"     {step.description}")


def run_workflow(
    name: str,
    dry_run: bool = False,
    yes: bool = False,
    verbose: bool = False,
    var: List[str] = None
):
    """Execute a workflow."""
    manager = WorkflowManager()
    workflow = manager.load_workflow(name)

    if not workflow:
        ch.error(f"Workflow '{name}' not found")
        return

    # Parse variable overrides
    variables = {}
    if var:
        for v in var:
            if "=" in v:
                key, value = v.split("=", 1)
                variables[key] = value
            else:
                ch.warning(f"Invalid variable format: {v} (expected name=value)")

    # Validate
    errors = manager.validate_workflow(workflow)
    if errors:
        ch.error("Workflow validation failed:")
        for error in errors:
            ch.console.print(f"  • {error}")
        return

    # Execute
    success = manager.execute_workflow(
        workflow,
        variables=variables,
        dry_run=dry_run,
        skip_prompts=yes,
        verbose=verbose
    )

    if not success and not dry_run:
        raise SystemExit(1)


def validate_workflow(name: str):
    """Validate workflow syntax and structure."""
    manager = WorkflowManager()
    workflow = manager.load_workflow(name)

    if not workflow:
        ch.error(f"Workflow '{name}' not found or failed to parse")
        raise SystemExit(1)

    errors = manager.validate_workflow(workflow)

    if errors:
        ch.error(f"Workflow '{name}' has {len(errors)} error(s):")
        for error in errors:
            ch.console.print(f"  ✗ {error}")
        raise SystemExit(1)
    else:
        ch.success(f"Workflow '{name}' is valid")
        ch.console.print(f"  • {len(workflow.steps)} steps")
        ch.console.print(f"  • {len(workflow.variables)} variables")


def create_workflow(name: str, global_scope: bool = False):
    """Create a new workflow from template."""
    manager = WorkflowManager()
    path = manager.create_workflow(name, global_scope=global_scope)

    if path:
        ch.success(f"Created workflow: {path}")
        ch.info(f"\nEdit with: navig workflow edit {name}")
        ch.info(f"Run with: navig workflow run {name}")


def delete_workflow(name: str, force: bool = False):
    """Delete a workflow."""
    import typer

    manager = WorkflowManager()
    manager.discover_workflows()

    if name not in manager._workflow_paths:
        ch.error(f"Workflow '{name}' not found")
        return

    if not force:
        if not typer.confirm(f"Delete workflow '{name}'?", default=False):
            ch.info("Cancelled")
            return

    manager.delete_workflow(name)


def edit_workflow(name: str):
    """Open workflow in default editor."""
    manager = WorkflowManager()
    manager.discover_workflows()

    if name not in manager._workflow_paths:
        ch.error(f"Workflow '{name}' not found")
        return

    path = manager._workflow_paths[name]

    # Try to open with editor
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL"))

    if not editor:
        # Platform-specific defaults
        if sys.platform == "win32":
            editor = "notepad"
        elif sys.platform == "darwin":
            editor = "open"
        else:
            editor = "nano"

    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.run([editor, str(path)])
        ch.success(f"Opened {path}")
    except Exception as e:
        ch.error(f"Failed to open editor: {e}")
        ch.info(f"Manually edit: {path}")

