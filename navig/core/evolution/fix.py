import os
import tempfile
from pathlib import Path
from typing import Any

from navig.ai import ask_ai_with_context
from navig.console_helper import error, info, success
from navig.core.evolution.base import BaseEvolver
from navig.core.evolution.failure_summary import summarize_check_failure


class FixEvolver(BaseEvolver):
    """Evolves existing code to fix bugs or add features."""

    def __init__(self, target_file: Path, check_command: str | None = None):
        super().__init__()
        self.target_file = target_file
        self.check_command = check_command
        self.last_failure_summary = ""
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

    def _generate(self, goal: str, previous_artifact: Any, error_msg: str, context: Any) -> Any:
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
                cmd_str = self.check_command.replace("{file}", tmp_path)

                info(f"Running validation: {cmd_str}")
                result = subprocess.run(cmd_str, shell=True, capture_output=True, text=True)

                if result.returncode != 0:
                    self.last_failure_summary = summarize_check_failure(
                        result.stdout,
                        result.stderr,
                    )
                    detail = f"Check Failed:\nStdout: {result.stdout}\nStderr: {result.stderr}"
                    if self.last_failure_summary:
                        detail += f"\n\nFailure Summary:\n{self.last_failure_summary}"
                    return detail
                self.last_failure_summary = ""

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

            # Write new code atomically, then swap original to backup
            _tmp_path: Path | None = None
            try:
                _fd, _tmp = tempfile.mkstemp(dir=self.target_file.parent, suffix=".tmp")
                _tmp_path = Path(_tmp)
                with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                    _fh.write(code)
                # Only rename original after the new content is safely on-disk
                if self.target_file.exists():
                    self.target_file.rename(backup_path)
                os.replace(_tmp_path, self.target_file)
                _tmp_path = None
            finally:
                if _tmp_path is not None:
                    _tmp_path.unlink(missing_ok=True)

            success(f"Fixed code saved to {self.target_file}")
            info(f"Backup at {backup_path}")
        except Exception as e:
            error(f"Failed to save fix: {e}")
