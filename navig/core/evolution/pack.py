
from typing import Any, Optional
import yaml
from pathlib import Path
import os

from navig.core.evolution.base import BaseEvolver
from navig.ai import ask_ai_with_context
from navig.console_helper import success, error

class PackEvolver(BaseEvolver):
    """Evolves Packs (collections of skills/workflows)."""
    
    def __init__(self):
        super().__init__()
        self._packs_dir = Path("packs") # Assume relative to CWD for now
        self._packs_dir.mkdir(exist_ok=True)
        self._system_prompt = """
You are a Navig Pack Designer.
A 'Pack' is a collection of related skills and workflows.

Output Format (YAML):
```yaml
name: pack_name
description: "Brief description"
version: "0.1.0"
skills:
  - list_of_skill_names
workflows:
  - list_of_workflow_names
install_script: |
  # shell script to run on install (optional)
```

Constraints:
- Output only valid YAML.
- Include dependencies relevant to the goal.
"""

    def _generate(self, goal: str, previous_artifact: Any, error_msg: str, context: Any) -> Any:
        
        prompt = f"Goal: Create a pack for {goal}\n\n"
        
        if previous_artifact:
            prompt += f"Previous attempt failed:\nError: {error_msg}\n\nRefine this YAML."
            
        if os.environ.get("NAVIG_MOCK_AI"):
            return """
name: mock_pack
description: A mock pack
skills:
  - mock_skill
workflows:
  - mock_workflow
"""
            
        return ask_ai_with_context(prompt, system_prompt=self._system_prompt)

    def _validate(self, artifact: str, context: Any) -> Optional[str]:
        """Validate Pack YAML."""
        try:
            # Extract YAML
            import re
            match = re.search(r"```yaml\n(.*?)\n```", artifact, re.DOTALL)
            code = match.group(1).strip() if match else artifact
            
            data = yaml.safe_load(code)
            if not isinstance(data, dict):
                return "Root must be dictionary"
            if 'name' not in data:
                return "Missing 'name'"
            if 'skills' not in data and 'workflows' not in data:
                return "Must contain skills or workflows"
                
            return None
        except Exception as e:
            return f"Validation Error: {e}"

    def _save(self, goal: str, artifact: str):
        """Save to packs/[name]/pack.yaml."""
        try:
            import re
            match = re.search(r"```yaml\n(.*?)\n```", artifact, re.DOTALL)
            code = match.group(1).strip() if match else artifact
            data = yaml.safe_load(code)
            
            name = data.get('name', 'unnamed_pack')
            pack_dir = self._packs_dir / name
            pack_dir.mkdir(parents=True, exist_ok=True)
            
            path = pack_dir / "pack.yaml"
            with open(path, 'w', encoding='utf-8') as f:
                f.write(code)
                
            success(f"Pack saved to {path}")
        except Exception as e:
            error(f"Failed to save pack: {e}")
