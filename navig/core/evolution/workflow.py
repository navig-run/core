import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from navig.ai import ask_ai_with_context
from navig.console_helper import error, success
from navig.core.evolution.base import BaseEvolver


class WorkflowEvolver(BaseEvolver):
    """Evolves cross-platform YAML workflows."""

    def __init__(self):
        super().__init__()
        # AUDIT self-check: Correct implementation? yes - restores valid prompt string syntax.
        # AUDIT self-check: Break callers? no - keeps the same prompt contract and class API.
        # AUDIT self-check: Simpler alternative? yes - single triple-quoted literal is simplest.
        self._system_prompt = """You are a workflow automation expert.
Your task is to generate a VALID YAML workflow file for the Navig Automation Engine.

Output Format:
```yaml
name: workflow_name
description: "Brief description"
variables:
  var_name: "default_value"
steps:
  - action: action_name
    args:
      arg_key: "arg_value"
    capture: "variable_to_save_result_to"
    if: "condition_to_check (e.g., title == 'Calculator')"
    platform:
      windows:
        action: windows_specific_action
        args:
          key: "value"
```

Available Actions and Args:
- open_app: {target: "path/url"}
- click: {x: int, y: int, button: "left"}
- type: {text: "string", delay: int}
- send: {keys: "string"}
- move_window: {selector: "title", x: int, y: int, width: int, height: int}
- resize_window: {selector: "title", width: int, height: int}
- maximize_window: {selector: "title"}
- minimize_window: {selector: "title"}
- activate_window: {selector: "title"}
- close_window: {selector: "title"}
- snap_window: {selector: "title", position: "left|right|top|bottom"}
- wait: {seconds: float}
- wait_for: {type: "window", target: "title", timeout: 30.0}
- get_focused_window: {}
- run_command: {command: "shell command"}
- read_text: {selector: "title", control: "ControlID"}

Constraints:
- Output only valid YAML inside a markdown block.
- Use variables {{var_name}} in ARGS only.
- In 'if' conditions, use variable names DIRECTLY (e.g. `success == True`, not `{{success}}`).
- Ensure unique workflow name based on goal.
- Use 'capture' to store step output into variables.
"""
        self._workflows_dir = Path(__file__).parent.parent.parent.parent / "workflows"

    def _generate(self, goal: str, previous_artifact: Any, error_msg: str, context: Any) -> Any:

        prompt = f"Goal: {goal}\n\n"

        if previous_artifact:
            prompt += f"Previous attempt failed:\nError: {error_msg}\n\n"
            prompt += f"Refine this YAML:\n{previous_artifact}\n"
        else:
            prompt += "Generate a new workflow YAML."

        if os.environ.get("NAVIG_MOCK_AI"):
            return """
name: mock_workflow
steps:
  - action: wait
    args:
      seconds: 1.0
"""

        response = ask_ai_with_context(prompt, system_prompt=self._system_prompt)

        # Extract YAML
        match = re.search(r"```yaml\n(.*?)\n```", response, re.DOTALL)
        if match:
            return match.group(1).strip()

        match = re.search(r"```\n(.*?)\n```", response, re.DOTALL)
        if match:
            return match.group(1).strip()

        return response # Fallback if no block

    def _validate(self, artifact: str, context: Any) -> Optional[str]:
        """Validate YAML structure."""
        try:
            data = yaml.safe_load(artifact)
            if not isinstance(data, dict):
                return "Root must be a dictionary"
            if 'steps' not in data:
                return "Missing 'steps' list"
            if not isinstance(data['steps'], list):
                return "'steps' must be a list"

            # Check for known actions (optional, but good for validation)
            # This list should stay in sync with ActionRegistry or similar
            known_actions = {
                'open_app', 'click', 'type', 'send', 'move_window',
                'resize_window', 'maximize_window', 'minimize_window',
                'activate_window', 'close_window', 'snap_window',
                'wait', 'wait_for', 'get_focused_window',
                'run_command', 'read_text', 'scroll', 'double_click'
            }

            for i, step in enumerate(data['steps']):
                if not isinstance(step, dict):
                    return f"Step {i+1} must be a dictionary"

                if 'action' not in step:
                    return f"Step {i+1} missing 'action'"

                action = step['action']
                if action not in known_actions and not action.startswith('custom_'):
                    # Warning only? Or strict?
                    # Let's be strict for core actions to prevent hallucinations
                    return f"Step {i+1}: Unknown action '{action}'"

                if 'args' in step and not isinstance(step['args'], dict):
                     return f"Step {i+1}: 'args' must be a dictionary"

                # Validate platform constraints
                if 'platform' in step:
                    if not isinstance(step['platform'], dict):
                        return f"Step {i+1}: 'platform' must be a dictionary"

                    for plat, override in step['platform'].items():
                        if plat not in ['windows', 'linux', 'macos']:
                            return f"Step {i+1}: Unknown platform '{plat}'"

                        if not isinstance(override, dict):
                            return f"Step {i+1}: Platform override for '{plat}' must be a dictionary"

                        if 'action' in override and override['action'] not in known_actions:
                             return f"Step {i+1} ({plat}): Unknown action '{override['action']}'"

            return None # Valid
        except yaml.YAMLError as e:
            return f"YAML Syntax Error: {e}"
        except Exception as e:
            return f"Validation Error: {e}"

    def _save(self, goal: str, artifact: str):
        """Save to workflows dir."""
        try:
            data = yaml.safe_load(artifact)
            name = data.get('name', 'unnamed_workflow')
            # Sanitize name
            name = "".join([c if c.isalnum() else "_" for c in name])

            path = self._workflows_dir / f"{name}.yaml"
            with open(path, 'w', encoding='utf-8') as f:
                f.write(artifact)

            success(f"Workflow saved to {path}")
        except Exception as e:
            error(f"Failed to save workflow: {e}")
