"""
Packs System for NAVIG - Shareable Operations Bundles

Packs are reusable content bundles containing:
- Workflows (multi-step automation)
- Runbooks (step-by-step guides)
- Checklists (verification procedures)
- Templates (server/app configurations)
- Quick Actions (command shortcuts)

Pack sources:
- Built-in: shipped with NAVIG
- Local: ~/.navig/packs/
- Registry: online pack registry (future)
"""

import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

from navig import console_helper as ch


class PackType(str, Enum):
    """Types of packs."""
    WORKFLOW = "workflow"
    RUNBOOK = "runbook"
    CHECKLIST = "checklist"
    TEMPLATE = "template"
    QUICKACTIONS = "quickactions"
    BUNDLE = "bundle"  # Contains multiple items


class PackStatus(str, Enum):
    """Installation status of a pack."""
    AVAILABLE = "available"
    INSTALLED = "installed"
    OUTDATED = "outdated"
    LOCAL = "local"


@dataclass
class PackManifest:
    """
    Pack manifest containing metadata and contents.
    
    File: pack.yaml or <pack-name>.yaml
    """
    name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    type: PackType = PackType.RUNBOOK
    
    # Dependencies
    requires_navig: str = ">=2.0.0"
    requires_packs: List[str] = field(default_factory=list)
    
    # Content
    steps: List[Dict[str, Any]] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)
    workflows: List[str] = field(default_factory=list)  # For bundles
    quick_actions: List[Dict[str, str]] = field(default_factory=list)
    
    # Metadata
    tags: List[str] = field(default_factory=list)
    homepage: str = ""
    license: str = "MIT"
    
    # Installation info (not in file)
    source_path: Optional[Path] = None
    installed_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = asdict(self)
        d['type'] = self.type.value if isinstance(self.type, PackType) else self.type
        # Remove runtime fields
        d.pop('source_path', None)
        d.pop('installed_at', None)
        return d
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], source_path: Optional[Path] = None) -> 'PackManifest':
        """Create from dictionary."""
        # Handle type enum
        pack_type = data.get('type', 'runbook')
        if isinstance(pack_type, str):
            try:
                pack_type = PackType(pack_type)
            except ValueError:
                pack_type = PackType.RUNBOOK
        data['type'] = pack_type
        
        # Handle lists
        data.setdefault('steps', [])
        data.setdefault('variables', {})
        data.setdefault('workflows', [])
        data.setdefault('quick_actions', [])
        data.setdefault('tags', [])
        data.setdefault('requires_packs', [])
        
        # Remove unknown fields
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        
        manifest = cls(**filtered)
        manifest.source_path = source_path
        return manifest


@dataclass 
class PackStep:
    """A single step in a runbook or checklist."""
    description: str
    command: Optional[str] = None
    notes: Optional[str] = None
    prompt: Optional[str] = None  # Confirmation prompt
    continue_on_error: bool = False
    skip_if: Optional[str] = None  # Condition to skip
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PackStep':
        """Create from dictionary."""
        return cls(
            description=data.get('description', ''),
            command=data.get('command'),
            notes=data.get('notes'),
            prompt=data.get('prompt'),
            continue_on_error=data.get('continue_on_error', False),
            skip_if=data.get('skip_if'),
        )


class PackManager:
    """
    Manages pack discovery, installation, and execution.
    
    Pack locations:
    - Built-in: <navig>/packs/
    - Installed: ~/.navig/packs/installed/
    - Local: ~/.navig/packs/local/
    """
    
    def __init__(self, config_manager=None):
        """Initialize pack manager."""
        from navig.config import get_config_manager
        self.config_manager = config_manager or get_config_manager()
        
        # Pack directories
        self.builtin_dir = self._get_builtin_dir()
        self.installed_dir = self.config_manager.global_config_dir / "packs" / "installed"
        self.local_dir = self.config_manager.global_config_dir / "packs" / "local"
        
        # Ensure directories exist
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        self.local_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache
        self._packs: Dict[str, PackManifest] = {}
        self._loaded = False
    
    def _get_builtin_dir(self) -> Path:
        """Get the built-in packs directory."""
        # Try relative to module
        module_dir = Path(__file__).parent.parent.parent
        builtin = module_dir / "packs"
        if builtin.exists():
            return builtin
        
        # Try in site-packages
        import navig
        pkg_dir = Path(navig.__file__).parent.parent
        builtin = pkg_dir / "packs"
        if builtin.exists():
            return builtin
        
        return module_dir / "packs"
    
    def _load_packs(self, force: bool = False):
        """Load all available packs."""
        if self._loaded and not force:
            return
        
        self._packs = {}
        
        # Load from all sources
        for pack_dir, source in [
            (self.builtin_dir, "builtin"),
            (self.installed_dir, "installed"),
            (self.local_dir, "local"),
        ]:
            if not pack_dir.exists():
                continue
            
            # Load packs from subdirectories (recursive)
            for item in pack_dir.iterdir():
                if item.is_dir():
                    # Check if it's a pack directory (has pack.yaml or single yaml)
                    pack_yaml = item / "pack.yaml"
                    if pack_yaml.exists():
                        self._load_pack_from_file(pack_yaml, source)
                    else:
                        # It might be a category dir (like "starter")
                        # Recurse into it
                        for subitem in item.iterdir():
                            if subitem.is_dir():
                                self._load_pack_from_dir(subitem, source)
                            elif subitem.suffix in ('.yaml', '.yml'):
                                self._load_pack_from_file(subitem, source)
                elif item.suffix in ('.yaml', '.yml'):
                    self._load_pack_from_file(item, source)
        
        self._loaded = True
    
    def _load_pack_from_dir(self, pack_dir: Path, source: str):
        """Load a pack from a directory."""
        manifest_file = pack_dir / "pack.yaml"
        if not manifest_file.exists():
            manifest_file = pack_dir / "pack.yml"
        
        if not manifest_file.exists():
            # Try finding any yaml file
            yaml_files = list(pack_dir.glob("*.yaml")) + list(pack_dir.glob("*.yml"))
            if yaml_files:
                manifest_file = yaml_files[0]
            else:
                return
        
        self._load_pack_from_file(manifest_file, source)
    
    def _load_pack_from_file(self, pack_file: Path, source: str):
        """Load a pack from a YAML file."""
        try:
            with open(pack_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data or not isinstance(data, dict):
                return
            
            manifest = PackManifest.from_dict(data, source_path=pack_file)
            
            # Generate unique key
            pack_key = self._get_pack_key(manifest.name, source)
            self._packs[pack_key] = manifest
            
            # Also store by name for convenience
            if manifest.name not in self._packs:
                self._packs[manifest.name] = manifest
                
        except Exception:
            # Silently skip invalid packs
            pass
    
    def _get_pack_key(self, name: str, source: str) -> str:
        """Generate unique pack key."""
        return f"{source}/{name.lower().replace(' ', '-')}"
    
    def list_packs(
        self,
        pack_type: Optional[PackType] = None,
        tag: Optional[str] = None,
        installed_only: bool = False,
    ) -> List[PackManifest]:
        """
        List available packs.
        
        Args:
            pack_type: Filter by type
            tag: Filter by tag
            installed_only: Only show installed packs
        """
        self._load_packs()
        
        packs = []
        seen_names = set()
        
        for key, manifest in self._packs.items():
            # Skip duplicates (prefer installed over builtin)
            if manifest.name in seen_names:
                continue
            
            # Apply filters
            if pack_type and manifest.type != pack_type:
                continue
            if tag and tag not in manifest.tags:
                continue
            if installed_only and 'installed' not in str(manifest.source_path):
                continue
            
            packs.append(manifest)
            seen_names.add(manifest.name)
        
        return sorted(packs, key=lambda p: p.name.lower())
    
    def get_pack(self, name: str) -> Optional[PackManifest]:
        """Get a pack by name."""
        self._load_packs()
        
        # Try exact match
        if name in self._packs:
            return self._packs[name]
        
        # Try normalized name
        normalized = name.lower().replace(' ', '-')
        for key, manifest in self._packs.items():
            if manifest.name.lower().replace(' ', '-') == normalized:
                return manifest
        
        # Try partial match
        for key, manifest in self._packs.items():
            if normalized in manifest.name.lower():
                return manifest
        
        return None
    
    def install_pack(
        self,
        source: str,
        force: bool = False,
    ) -> Optional[PackManifest]:
        """
        Install a pack from a source.
        
        Sources:
        - Local file path
        - Built-in pack name (e.g., "starter/deployment-checklist")
        - URL (future)
        """
        source_path = Path(source)
        
        # Check if it's a file path
        if source_path.exists():
            return self._install_from_file(source_path, force)
        
        # Check if it's a built-in pack
        builtin_path = self.builtin_dir / source
        if builtin_path.exists():
            if builtin_path.is_dir():
                manifest_file = builtin_path / "pack.yaml"
                if not manifest_file.exists():
                    manifest_file = list(builtin_path.glob("*.yaml"))[0] if list(builtin_path.glob("*.yaml")) else None
            else:
                manifest_file = builtin_path
            
            if manifest_file:
                return self._install_from_file(manifest_file, force)
        
        # Try with .yaml/.yml extension
        for ext in ['.yaml', '.yml', '']:
            for dir_name in ['starter', '']:
                test_path = self.builtin_dir / dir_name / f"{source}{ext}"
                if test_path.exists():
                    return self._install_from_file(test_path, force)
        
        return None
    
    def _install_from_file(
        self,
        source_path: Path,
        force: bool = False,
    ) -> Optional[PackManifest]:
        """Install a pack from a file."""
        try:
            with open(source_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data:
                return None
            
            manifest = PackManifest.from_dict(data, source_path=source_path)
            
            # Check if already installed
            pack_name = manifest.name.lower().replace(' ', '-')
            target_dir = self.installed_dir / pack_name
            target_file = target_dir / "pack.yaml"
            
            if target_file.exists() and not force:
                # Already installed
                return manifest
            
            # Create target directory
            target_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy pack file
            shutil.copy2(source_path, target_file)
            
            # Add installation metadata
            manifest.installed_at = datetime.now().isoformat()
            manifest.source_path = target_file
            
            # Reload packs
            self._loaded = False
            
            return manifest
            
        except Exception as e:
            ch.error(f"Failed to install pack: {e}")
            return None
    
    def uninstall_pack(self, name: str) -> bool:
        """Uninstall a pack."""
        pack = self.get_pack(name)
        if not pack:
            return False
        
        # Check if it's an installed pack
        pack_name = pack.name.lower().replace(' ', '-')
        target_dir = self.installed_dir / pack_name
        
        if target_dir.exists():
            shutil.rmtree(target_dir)
            self._loaded = False
            return True
        
        # Check local packs
        local_dir = self.local_dir / pack_name
        if local_dir.exists():
            shutil.rmtree(local_dir)
            self._loaded = False
            return True
        
        return False
    
    def run_pack(
        self,
        name: str,
        variables: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        interactive: bool = True,
    ) -> bool:
        """
        Run a pack (execute its steps).
        
        Args:
            name: Pack name
            variables: Variable overrides
            dry_run: Preview without executing
            interactive: Prompt for confirmations
        """
        pack = self.get_pack(name)
        if not pack:
            ch.error(f"Pack not found: {name}")
            return False
        
        # Merge variables
        all_vars = {**pack.variables, **(variables or {})}
        
        ch.header(f"Running Pack: {pack.name}")
        ch.info(pack.description)
        print()
        
        if pack.type == PackType.CHECKLIST:
            return self._run_checklist(pack, all_vars, dry_run, interactive)
        elif pack.type == PackType.RUNBOOK:
            return self._run_runbook(pack, all_vars, dry_run, interactive)
        elif pack.type == PackType.WORKFLOW:
            return self._run_workflow(pack, all_vars, dry_run)
        elif pack.type == PackType.QUICKACTIONS:
            return self._install_quick_actions(pack)
        else:
            ch.warning(f"Pack type '{pack.type}' not supported for execution")
            return False
    
    def _run_checklist(
        self,
        pack: PackManifest,
        variables: Dict[str, Any],
        dry_run: bool,
        interactive: bool,
    ) -> bool:
        """Run a checklist pack."""
        import typer
        
        steps = [PackStep.from_dict(s) for s in pack.steps]
        completed = 0
        skipped = 0
        
        for i, step in enumerate(steps, 1):
            # Show step
            status_marker = "[ ]"
            ch.info(f"{status_marker} {i}. {step.description}")
            
            if step.notes:
                ch.dim(f"   {step.notes}")
            
            if step.command:
                cmd = self._substitute_vars(step.command, variables)
                ch.dim(f"   Command: {cmd}")
                
                if not dry_run:
                    if interactive:
                        # Ask user to confirm
                        result = typer.prompt(
                            "   Run command? [y/n/s(kip)]",
                            default="y"
                        ).lower()
                        
                        if result == 'n':
                            ch.warning(f"   Checklist stopped at step {i}")
                            return False
                        elif result == 's':
                            ch.dim("   Skipped")
                            skipped += 1
                            continue
                    
                    # Execute command
                    success = self._execute_command(cmd)
                    if success:
                        completed += 1
                        ch.success("   [x] Completed")
                    elif step.continue_on_error:
                        ch.warning("   [!] Failed (continuing)")
                        completed += 1
                    else:
                        ch.error("   [x] Failed")
                        return False
                else:
                    ch.dim("   [DRY RUN] Would execute")
                    completed += 1
            else:
                # Manual step
                if not dry_run and interactive:
                    typer.prompt(
                        "   Press Enter when complete",
                        default="",
                        show_default=False
                    )
                completed += 1
            
            print()
        
        ch.success(f"Checklist complete: {completed}/{len(steps)} steps")
        if skipped:
            ch.dim(f"({skipped} skipped)")
        
        return True
    
    def _run_runbook(
        self,
        pack: PackManifest,
        variables: Dict[str, Any],
        dry_run: bool,
        interactive: bool,
    ) -> bool:
        """Run a runbook pack (same as checklist but auto-executes)."""
        import typer
        
        steps = [PackStep.from_dict(s) for s in pack.steps]
        completed = 0
        failed = 0
        
        for i, step in enumerate(steps, 1):
            ch.info(f"Step {i}/{len(steps)}: {step.description}")
            
            if step.notes:
                ch.dim(f"  {step.notes}")
            
            if step.command:
                cmd = self._substitute_vars(step.command, variables)
                ch.dim(f"  $ {cmd}")
                
                if dry_run:
                    ch.dim("  [DRY RUN] Would execute")
                    completed += 1
                else:
                    # Check for prompt
                    if step.prompt and interactive:
                        if not typer.confirm(step.prompt, default=True):
                            ch.dim("  Skipped by user")
                            continue
                    
                    success = self._execute_command(cmd)
                    if success:
                        completed += 1
                        ch.success("  Done")
                    elif step.continue_on_error:
                        failed += 1
                        ch.warning("  Failed (continuing)")
                    else:
                        ch.error("  Failed - runbook stopped")
                        return False
            else:
                completed += 1
            
            print()
        
        if failed:
            ch.warning(f"Runbook complete: {completed}/{len(steps)} steps ({failed} failed)")
        else:
            ch.success(f"Runbook complete: {completed}/{len(steps)} steps")
        
        return failed == 0
    
    def _run_workflow(
        self,
        pack: PackManifest,
        variables: Dict[str, Any],
        dry_run: bool,
    ) -> bool:
        """Run a workflow pack using the workflow engine."""
        try:
            from navig.commands.workflow import WorkflowManager
            
            # Create a temporary workflow definition
            workflow_def = {
                'name': pack.name,
                'description': pack.description,
                'variables': variables,
                'steps': pack.steps,
            }
            
            wf_manager = WorkflowManager()
            return wf_manager.run_workflow_from_dict(workflow_def, dry_run=dry_run)
            
        except Exception as e:
            ch.error(f"Failed to run workflow: {e}")
            return False
    
    def _install_quick_actions(self, pack: PackManifest) -> bool:
        """Install quick actions from a pack."""
        if not pack.quick_actions:
            ch.warning("Pack has no quick actions to install")
            return False
        
        try:
            from navig.commands.suggest import add_quick_action
            
            installed = 0
            for action in pack.quick_actions:
                name = action.get('name', '')
                command = action.get('command', '')
                description = action.get('description', '')
                
                if name and command:
                    add_quick_action(name, command, description)
                    installed += 1
            
            ch.success(f"Installed {installed} quick actions from pack")
            return True
            
        except Exception as e:
            ch.error(f"Failed to install quick actions: {e}")
            return False
    
    def _substitute_vars(self, text: str, variables: Dict[str, Any]) -> str:
        """Substitute ${var} placeholders."""
        import re
        
        def replace(match):
            var_name = match.group(1)
            return str(variables.get(var_name, match.group(0)))
        
        return re.sub(r'\$\{(\w+)\}', replace, text)
    
    def _execute_command(self, command: str) -> bool:
        """Execute a command."""
        import subprocess
        import sys
        
        try:
            # Check if it's a navig command
            if command.startswith('navig '):
                # Run as subprocess
                result = subprocess.run(
                    [sys.executable, '-m', 'navig'] + command[6:].split(),
                    capture_output=False,
                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                )
                return result.returncode == 0
            else:
                # Run as shell command
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=False,
                )
                return result.returncode == 0
                
        except Exception as e:
            ch.error(f"Command failed: {e}")
            return False
    
    def create_pack(
        self,
        name: str,
        pack_type: PackType = PackType.RUNBOOK,
        description: str = "",
        steps: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Path]:
        """
        Create a new pack in the local directory.
        
        Returns the path to the created pack file.
        """
        pack_name = name.lower().replace(' ', '-')
        pack_dir = self.local_dir / pack_name
        pack_dir.mkdir(parents=True, exist_ok=True)
        
        pack_file = pack_dir / "pack.yaml"
        
        manifest = PackManifest(
            name=name,
            description=description or f"Custom pack: {name}",
            type=pack_type,
            author=self._get_author(),
            steps=steps or [],
        )
        
        try:
            with open(pack_file, 'w', encoding='utf-8') as f:
                yaml.dump(manifest.to_dict(), f, default_flow_style=False, sort_keys=False)
            
            self._loaded = False
            return pack_file
            
        except Exception as e:
            ch.error(f"Failed to create pack: {e}")
            return None
    
    def _get_author(self) -> str:
        """Get the current user for pack authorship."""
        import os
        return os.environ.get('USER', os.environ.get('USERNAME', 'Unknown'))
    
    def search_packs(self, query: str) -> List[PackManifest]:
        """Search packs by name, description, or tags."""
        self._load_packs()
        
        query_lower = query.lower()
        results = []
        seen_names = set()  # Deduplicate results
        
        for manifest in self._packs.values():
            # Skip duplicates
            if manifest.name in seen_names:
                continue
            
            score = 0
            
            # Name match
            if query_lower in manifest.name.lower():
                score += 10
            
            # Description match
            if query_lower in manifest.description.lower():
                score += 5
            
            # Tag match
            for tag in manifest.tags:
                if query_lower in tag.lower():
                    score += 3
            
            if score > 0:
                results.append((score, manifest))
                seen_names.add(manifest.name)
        
        # Sort by score
        results.sort(key=lambda x: -x[0])
        return [m for _, m in results]


# ============================================================================
# CLI DISPLAY FUNCTIONS
# ============================================================================

def list_packs(
    pack_type: Optional[str] = None,
    tag: Optional[str] = None,
    installed_only: bool = False,
    plain: bool = False,
    json_out: bool = False,
):
    """List available packs."""
    manager = PackManager()
    
    # Parse type filter
    type_filter = None
    if pack_type:
        try:
            type_filter = PackType(pack_type)
        except ValueError:
            ch.error(f"Invalid pack type: {pack_type}")
            ch.info(f"Valid types: {', '.join(t.value for t in PackType)}")
            return
    
    packs = manager.list_packs(pack_type=type_filter, tag=tag, installed_only=installed_only)
    
    if not packs:
        ch.info("No packs found")
        return
    
    if json_out:
        import json
        print(json.dumps([p.to_dict() for p in packs], indent=2))
        return
    
    if plain:
        for pack in packs:
            status = "installed" if 'installed' in str(pack.source_path) else "available"
            print(f"{pack.name}\t{pack.type.value}\t{status}\t{pack.description[:50]}")
        return
    
    from rich.table import Table
    from rich.console import Console
    
    console = Console()
    table = Table(title="Available Packs")
    
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Version")
    table.add_column("Status")
    table.add_column("Description")
    
    for pack in packs:
        status = "[green]installed[/green]" if 'installed' in str(pack.source_path) else "[dim]available[/dim]"
        if 'local' in str(pack.source_path):
            status = "[yellow]local[/yellow]"
        
        table.add_row(
            pack.name,
            pack.type.value,
            pack.version,
            status,
            pack.description[:40] + "..." if len(pack.description) > 40 else pack.description,
        )
    
    console.print(table)


def show_pack(name: str, plain: bool = False, json_out: bool = False):
    """Show pack details."""
    manager = PackManager()
    pack = manager.get_pack(name)
    
    if not pack:
        ch.error(f"Pack not found: {name}")
        return
    
    if json_out:
        import json
        print(json.dumps(pack.to_dict(), indent=2))
        return
    
    if plain:
        print(f"Name: {pack.name}")
        print(f"Type: {pack.type.value}")
        print(f"Version: {pack.version}")
        print(f"Description: {pack.description}")
        print(f"Steps: {len(pack.steps)}")
        return
    
    from rich.panel import Panel
    from rich.console import Console
    
    console = Console()
    
    # Header
    console.print(Panel(
        f"[bold]{pack.name}[/bold]\n{pack.description}",
        title=f"Pack: {pack.type.value}",
        subtitle=f"v{pack.version} by {pack.author}",
    ))
    
    # Steps
    if pack.steps:
        console.print("\n[bold]Steps:[/bold]")
        for i, step in enumerate(pack.steps, 1):
            desc = step.get('description', 'No description')
            cmd = step.get('command', '')
            console.print(f"  {i}. {desc}")
            if cmd:
                console.print(f"     [dim]$ {cmd}[/dim]")
    
    # Quick Actions
    if pack.quick_actions:
        console.print("\n[bold]Quick Actions:[/bold]")
        for action in pack.quick_actions:
            console.print(f"  • {action.get('name')}: {action.get('command')}")
    
    # Source
    if pack.source_path:
        console.print(f"\n[dim]Source: {pack.source_path}[/dim]")


def install_pack(source: str, force: bool = False):
    """Install a pack."""
    manager = PackManager()
    
    ch.info(f"Installing pack: {source}")
    
    manifest = manager.install_pack(source, force=force)
    
    if manifest:
        ch.success(f"Installed: {manifest.name} v{manifest.version}")
        ch.dim(f"Type: {manifest.type.value}")
        ch.dim(f"Steps: {len(manifest.steps)}")
    else:
        ch.error(f"Failed to install pack: {source}")
        ch.info("Try: navig pack list --all to see available packs")


def uninstall_pack(name: str, force: bool = False):
    """Uninstall a pack."""
    import typer
    
    manager = PackManager()
    pack = manager.get_pack(name)
    
    if not pack:
        ch.error(f"Pack not found: {name}")
        return
    
    if not force:
        if not typer.confirm(f"Uninstall pack '{pack.name}'?"):
            ch.info("Cancelled")
            return
    
    if manager.uninstall_pack(name):
        ch.success(f"Uninstalled: {pack.name}")
    else:
        ch.error("Failed to uninstall pack")


def run_pack(
    name: str,
    variables: Optional[Dict[str, str]] = None,
    dry_run: bool = False,
    yes: bool = False,
):
    """Run a pack."""
    manager = PackManager()
    
    success = manager.run_pack(
        name,
        variables=variables,
        dry_run=dry_run,
        interactive=not yes,
    )
    
    if not success:
        raise SystemExit(1)


def create_pack(
    name: str,
    pack_type: str = "runbook",
    description: str = "",
):
    """Create a new pack."""
    manager = PackManager()
    
    try:
        ptype = PackType(pack_type)
    except ValueError:
        ch.error(f"Invalid pack type: {pack_type}")
        ch.info(f"Valid types: {', '.join(t.value for t in PackType)}")
        return
    
    path = manager.create_pack(name, pack_type=ptype, description=description)
    
    if path:
        ch.success(f"Created pack: {name}")
        ch.info(f"Location: {path}")
        ch.dim("Edit the pack.yaml file to add steps")
    else:
        ch.error("Failed to create pack")


def search_packs(query: str, plain: bool = False, json_out: bool = False):
    """Search for packs."""
    manager = PackManager()
    results = manager.search_packs(query)
    
    if not results:
        ch.info(f"No packs found matching: {query}")
        return
    
    if json_out:
        import json
        print(json.dumps([p.to_dict() for p in results], indent=2))
        return
    
    if plain:
        for pack in results:
            print(f"{pack.name}\t{pack.type.value}\t{pack.description[:50]}")
        return
    
    ch.header(f"Search Results: {query}")
    for pack in results:
        ch.info(f"• {pack.name} ({pack.type.value})")
        ch.dim(f"  {pack.description}")


# Ensure import works
import os
