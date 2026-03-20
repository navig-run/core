
import os
from pathlib import Path
from typing import Any, Optional

import yaml

from navig.ai import ask_ai_with_context
from navig.console_helper import error, success
from navig.core.evolution.base import BaseEvolver


class SkillEvolver(BaseEvolver):
    """Evolves SKILL.md definitions."""

    def __init__(self, skills_root: Path):
        super().__init__()
        self._skills_root = skills_root
        self._system_prompt = """
You are a Navig Skill Designer.
Your task is to generate a VALID SKILL.md file for a new skill.

Format:
---
name: skill_name
description: "Brief description of what the skill does"
dependencies: ["dependency1"]
---

# Instructions
Detailed markdown instructions on how to perform the skill.
Identify any prerequisite tools or setups needed.
"""

    def _generate(self, goal: str, previous_artifact: Any, error_msg: str, context: Any) -> Any:

        prompt = f"Goal: Create a skill for {goal}\n\n"

        if previous_artifact:
            prompt += f"Previous attempt failed:\nError: {error_msg}\n\nRefine this SKILL.md content."

        if os.environ.get("NAVIG_MOCK_AI"):
            return """---
name: mock_skill
description: mocked skill
---
# Instructions
Do nothing
"""

        return ask_ai_with_context(prompt, system_prompt=self._system_prompt)

    def _validate(self, artifact: str, context: Any) -> Optional[str]:
        """Validate frontmatter and markdown structure."""
        if not artifact.startswith("---"):
            return "Missing YAML frontmatter start (---)"

        try:
            # Extract frontmatter
            parts = artifact.split("---", 2)
            if len(parts) < 3:
                return "Invalid frontmatter format (missing closing ---)"

            frontmatter = yaml.safe_load(parts[1])
            if 'name' not in frontmatter:
                return "Frontmatter missing 'name'"
            if 'description' not in frontmatter:
                return "Frontmatter missing 'description'"

            if len(parts[2].strip()) < 10:
                return "Instructions seem too short or missing"

            return None # Valid
        except yaml.YAMLError as e:
            return f"YAML Frontmatter Error: {e}"
        except Exception as e:
            return f"Validation Error: {e}"

    def _save(self, goal: str, artifact: str):
        """Save to skills/[name]/SKILL.md."""
        try:
            parts = artifact.split("---", 2)
            frontmatter = yaml.safe_load(parts[1])
            name = frontmatter.get('name', 'unnamed_skill')
            # Sanitize name
            name = "".join([c if c.isalnum() or c in '-_' else '_' for c in name]).lower()

            skill_dir = self._skills_root / name
            skill_dir.mkdir(parents=True, exist_ok=True)

            path = skill_dir / "SKILL.md"
            with open(path, 'w', encoding='utf-8') as f:
                f.write(artifact)

            success(f"Skill saved to {path}")
        except Exception as e:
            error(f"Failed to save skill: {e}")
