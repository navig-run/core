import os
from pathlib import Path
from typing import Any

import yaml

from navig.ai import ask_ai_with_context
from navig.console_helper import error, success
from navig.core.evolution.base import BaseEvolver


class PackEvolver(BaseEvolver):
    """Evolves Packs (collections of skills/workflows)."""

    def __init__(self, packs_dir: Path | str | None = None):
        super().__init__()
        # Default to packages_dir() — config_dir()/packs, the SAME directory `navig install`
        # writes packs to and the pack loader reads from — so an evolved pack is actually
        # installable. This resolves the long-standing TODO: the default WAS `Path("packs")`,
        # CWD-relative, so a generated pack landed in whatever dir the process happened to run
        # in (`core/` under pytest, dirtying the tracked tree) and nothing ever loaded it. The
        # eager `mkdir` was already removed (constructing an evolver must not touch the disk);
        # `_save` mkdirs the target when it actually writes. An explicit `packs_dir` still wins.
        from navig.platform.paths import packages_dir

        self._packs_dir = Path(packs_dir) if packs_dir is not None else packages_dir()
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

    def _validate(self, artifact: str, context: Any) -> str | None:
        """Validate Pack YAML."""
        try:
            # Extract YAML
            import re

            match = re.search(r"```yaml\n(.*?)\n```", artifact, re.DOTALL)
            code = match.group(1).strip() if match else artifact

            data = yaml.safe_load(code)
            if not isinstance(data, dict):
                return "Root must be dictionary"
            if "name" not in data:
                return "Missing 'name'"
            if "skills" not in data and "workflows" not in data:
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

            name = data.get("name", "unnamed_pack")
            pack_dir = self._packs_dir / name
            pack_dir.mkdir(parents=True, exist_ok=True)

            path = pack_dir / "pack.yaml"
            with open(path, "w", encoding="utf-8") as f:
                f.write(code)

            success(f"Pack saved to {path}")
        except Exception as e:
            error(f"Failed to save pack: {e}")
