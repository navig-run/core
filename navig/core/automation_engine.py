
"""
Cross-Platform Automation Workflow Engine
"""
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

from navig.console_helper import error, info, warning
from navig.core.safe_eval import safe_eval


@dataclass
class WorkflowStep:
    action: str
    args: Dict[str, Any]
    platform_override: Optional[Dict[str, Any]] = None
    capture: Optional[str] = None
    if_condition: Optional[str] = None

@dataclass
class Workflow:
    name: str
    steps: List[WorkflowStep]
    description: str = ""
    variables: Dict[str, str] = None

class WorkflowEngine:
    def __init__(self):
        self._navig_root = Path(__file__).parent.parent.parent
        self._workflows_dir = self._navig_root / "workflows"
        self._workflows_dir.mkdir(exist_ok=True)
        self._workflow_cache: Dict[str, Tuple[float, Workflow]] = {}

        # Lazy load adapters
        self._ahk = None
        self._linux = None
        self._macos = None

    @property
    def ahk(self):
        if not self._ahk and sys.platform == 'win32':
            from navig.adapters.automation.ahk import AHKAdapter
            self._ahk = AHKAdapter()
        return self._ahk

    @property
    def linux(self):
        if not self._linux and sys.platform == 'linux':
            from navig.adapters.automation.linux import LinuxAdapter
            self._linux = LinuxAdapter()
        return self._linux

    @property
    def macos(self):
        if not self._macos and sys.platform == 'darwin':
            from navig.adapters.automation.macos import MacOSAdapter
            self._macos = MacOSAdapter()
        return self._macos

    @property
    def adapter(self):
        """Get the appropriate adapter for current platform."""
        if sys.platform == 'win32':
            return self.ahk
        elif sys.platform == 'linux':
            return self.linux
        elif sys.platform == 'darwin':
            return self.macos
        return None

    def load_workflow(self, name: str) -> Optional[Workflow]:
        """Load workflow from YAML file."""
        # Check standard locations
        possible_paths = [
            self._workflows_dir / f"{name}.yaml",
            self._workflows_dir / f"{name}.yml",
            Path.home() / ".navig" / "workflows" / f"{name}.yaml"
        ]

        target_path = None
        for p in possible_paths:
            if p.exists():
                target_path = p
                break

        if not target_path:
            return None

        try:
            mtime = os.stat(target_path).st_mtime
            if name in self._workflow_cache:
                cached_mtime, cached_wf = self._workflow_cache[name]
                if mtime == cached_mtime:
                    return cached_wf

            with open(target_path, 'r', encoding='utf-8') as f:
                data = yaml.load(f, Loader=SafeLoader)

            steps = []
            for s in data.get('steps', []):
                steps.append(WorkflowStep(
                    action=s.get('action'),
                    args=s.get('args', {}),
                    platform_override=s.get('platform', None),
                    capture=s.get('capture', None),
                    if_condition=s.get('if', None)
                ))

            wf = Workflow(
                name=data.get('name',name),
                description=data.get('description', ''),
                variables=data.get('variables', {}),
                steps=steps
            )
            self._workflow_cache[name] = (mtime, wf)
            return wf
        except Exception as e:
            error(f"Failed to load workflow {name}: {e}")
            return None

    def execute_workflow(self, workflow: Workflow, variables: Dict[str, str] = None):
        """Execute a cross-platform workflow."""
        info(f"Executing workflow: {workflow.name}")

        # Merge variables
        current_vars = workflow.variables.copy() if workflow.variables else {}
        if variables:
            current_vars.update(variables)

        for i, step in enumerate(workflow.steps):
            action = step.action
            args = step.args.copy()

            # Resolve variables in args
            for k, v in args.items():
                if isinstance(v, str) and "{{" in v:
                    for var_k, var_v in current_vars.items():
                        v = v.replace(f"{{{{{var_k}}}}}", str(var_v))
                    args[k] = v

            # Check Condition
            if step.if_condition:
                cond = step.if_condition

                # Use safe evaluation with variables map

                # Prepare variables for eval (convert all to appropriate types if possible, otherwise strings)
                eval_vars = current_vars.copy()

                # Helper: smart cast? For now just pass strings/dicts as is
                # 'true', 'false' strings -> booleans?
                # Let's rely on standard python types in variables

                try:
                    result = safe_eval(cond, eval_vars)
                    if not result:
                        info(f"Skipping step {i+1} (condition false): {cond}")
                        continue
                except Exception as e:
                    warning(f"Condition evaluation failed '{cond}': {e}. Skipping step.")
                    continue

            # Platform overrides
            if step.platform_override:
                platform_key = "windows" if sys.platform == 'win32' else sys.platform
                if platform_key in step.platform_override:
                    override = step.platform_override[platform_key]
                    if 'action' in override:
                        action = override['action']
                    if 'args' in override:
                        override_args = override['args'].copy()
                        for k, v in override_args.items():
                             if isinstance(v, str) and "{{" in v:
                                for var_k, var_v in current_vars.items():
                                    v = v.replace(f"{{{{{var_k}}}}}", str(var_v))
                                override_args[k] = v
                        args.update(override_args)

            # Execute Action
            result = self._execute_action(action, args)

            # Capture Output
            if step.capture and result is not None:
                current_vars[step.capture] = str(result)
                info(f"Captured variable '{step.capture}': {result}")

        return current_vars

    def _execute_action(self, action: str, args: Dict[str, Any]) -> Any:
        """Dispatch action to appropriate adapter."""
        # Platform-independent actions
        if action == "wait":
            time.sleep(float(args.get('seconds', 1.0)))
            return True

        if action == "run_command":
            cmd = args.get('command')
            if cmd:
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if res.returncode == 0:
                    return res.stdout.strip()
            return ""

        # Get platform-specific adapter
        adapter = self.adapter
        if not adapter or not adapter.is_available():
            warning(f"Platform {sys.platform} automation not available.")
            return None

        # Dispatch to adapter methods
        if action == "open_app":
            return adapter.open_app(args.get('target', ''))
        elif action == "click":
            return adapter.click(args.get('x'), args.get('y'), args.get('button', 'left'))
        elif action == "type":
            return adapter.type_text(args.get('text'), args.get('delay', 50))
        elif action == "send":
            return adapter.send_keys(args.get('keys'))
        elif action == "mouse_move":
            return adapter.mouse_move(args.get('x'), args.get('y'), args.get('speed', 2))
        elif action == "get_focused_window":
            info_obj = adapter.get_focused_window()
            return info_obj.to_dict() if info_obj else None
        elif action == "activate_window":
            return adapter.activate_window(args.get('selector'))
        elif action == "close_window":
            return adapter.close_window(args.get('selector'))
        elif action == "move_window":
            return adapter.move_window(
                args.get('selector'),
                args.get('x'),
                args.get('y'),
                args.get('width'),
                args.get('height')
            )
        elif action == "resize_window":
            # Some adapters may not have resize_window, use move_window with current pos
            if hasattr(adapter, 'resize_window'):
                return adapter.resize_window(args.get('selector'), args.get('width'), args.get('height'))
            else:
                # Get current window location and resize
                return adapter.move_window(args.get('selector'), 0, 0, args.get('width'), args.get('height'))
        elif action == "maximize_window":
            return adapter.maximize_window(args.get('selector'))
        elif action == "minimize_window":
            return adapter.minimize_window(args.get('selector'))
        elif action == "snap_window":
            return adapter.snap_window(args.get('selector'), args.get('position'))
        elif action == "get_clipboard":
            return adapter.get_clipboard()
        elif action == "set_clipboard":
            return adapter.set_clipboard(args.get('text', ''))
        elif action == "wait_for":
            # Wait for condition
            check_type = args.get('type', 'window')
            target = args.get('target', '')
            timeout = float(args.get('timeout', 30.0))
            start = time.time()

            if check_type == 'window':
                # Poll for window existence
                while time.time() - start < timeout:
                    # Try to activate to check existence
                    result = adapter.activate_window(target)
                    if result and result.success:
                        return True
                    time.sleep(0.5)
                return False
            return False
        elif action == "read_text":
            # This is AHK-specific, not all platforms support it
            if hasattr(adapter, 'read_text'):
                text = adapter.read_text(args.get('selector'), control_id=args.get('control', ''))
                if text:
                    info(f"Read text: {text}")
                return text
            else:
                warning("read_text not supported on this platform")
                return None
        else:
            warning(f"Unknown action: {action}")
            return None
