import os
from pathlib import Path
from typing import Any

from navig.ai import ask_ai_with_context
from navig.console_helper import error, info, success
from navig.core.evolution.base import BaseEvolver


class FixEvolver(BaseEvolver):
    """Evolves existing code to fix bugs or add features."""

    def __init__(self, target_file: Path, check_command: str | None = None):
        super().__init__()
        self.target_file = target_file
        self.check_command = check_command
        self._system_prompt = """
You are a Code Repair Expert.
Your task is to FIX or IMPROVE the provided code based on the user's request.

Input:
- Current Code
- User Request / Error Description

Output:
- The COMPLETE corrected code.
- Do not output diffs. Output the full file content.

Constraints:
- Preserve existing style.
- Output valid code in markdown block.
"""

    def _generate(
        self, goal: str, previous_artifact: Any, error_msg: str, context: Any
    ) -> Any:
        # specific context for fix
        try:
            current_code = self.target_file.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

        prompt = f"Goal/Error: {goal}\n\n"
        prompt += f"File: {self.target_file.name}\n"
        prompt += f"Current Code:\n```\n{current_code}\n```\n\n"

        if previous_artifact:
            prompt += f"Previous fix failed:\nError: {error_msg}\n\nRefine this code:\n{previous_artifact}\n"

        if os.environ.get("NAVIG_MOCK_AI"):
            return f"```python\n# Fixed version of {self.target_file.name}\n{current_code}\n# Fix applied\n```"

        return ask_ai_with_context(prompt, system_prompt=self._system_prompt)

    def _validate(self, artifact: str, context: Any) -> str | None:
        # 1. Basic syntax check if python
        import re

        # Extract code to temp file for checking
        match = re.search(r"```\w*\n(.*?)\n```", artifact, re.DOTALL)
        code = match.group(1).strip() if match else artifact

        if self.target_file.suffix == ".py":
            try:
                compile(code, "<string>", "exec")
            except SyntaxError as e:
                return f"Syntax Error: {e}"

        # 2. Run external check command if provided
        if self.check_command:
            import subprocess
            import tempfile

            # Write to temp file
            suffix = self.target_file.suffix
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=suffix, delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(code)
                tmp_path = tmp.name

            try:
                # Replace {file} placeholder
                cmd = self.check_command.replace("{file}", tmp_path)

                # If command doesn't have {file}, append it?
                # No, assume user knows what they are doing or runs a project-wide check that might fail
                # if this file is isolated. Best practice: check ONLY this file.

                info(f"Running validation: {cmd}")
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

                if result.returncode != 0:
                    return f"Check Failed:\nStdout: {result.stdout}\nStderr: {result.stderr}"

            except Exception as e:
                return f"Validation execution failed: {e}"
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        return None

    def _save(self, goal: str, artifact: str):
        try:
            import re

            # Try to find code block matching extension
            ext = self.target_file.suffix.strip(".")
            pattern = re.compile(rf"```{ext}\n(.*?)\n```", re.DOTALL)
            match = pattern.search(artifact)
            if not match:
                # Fallback to generic block
                match = re.search(r"```\n(.*?)\n```", artifact, re.DOTALL)

            code = match.group(1).strip() if match else artifact

            # Backup original?
            backup_path = self.target_file.with_suffix(f"{self.target_file.suffix}.bak")
            self.target_file.rename(backup_path)

            with open(self.target_file, "w", encoding="utf-8") as f:
                f.write(code)

            success(f"Fixed code saved to {self.target_file}")
            info(f"Backup at {backup_path}")
        except Exception as e:
            error(f"Failed to save fix: {e}")
