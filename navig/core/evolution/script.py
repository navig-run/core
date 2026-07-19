import os
from pathlib import Path
from typing import Any

from navig.ai import ask_ai_with_context
from navig.console_helper import error, success
from navig.core.evolution.base import BaseEvolver


class ScriptEvolver(BaseEvolver):
    """Evolves Python scripts for automation tasks."""

    def __init__(self):
        super().__init__()
        # Test/override seam; the default is resolved lazily below.
        self._scripts_dir_override: Path | None = None

        self._system_prompt = """
You are a Python Script Generator.
Your task is to generate a standalone Python script for a specific task.

Context:
- Scripts are saved to your NAVIG scripts directory (`~/.navig/scripts`).
- They can import from `navig` modules if needed.
- Use standard libraries where possible.

Output Format:
```python
#!/usr/bin/env python3
# Description: ...

import sys
import os

def main():
    # Implementation
    pass

if __name__ == "__main__":
    main()
```

Constraints:
- Output only valid Python code inside a markdown block.
- Include error handling.
- Use type hints.
"""

    @property
    def _scripts_dir(self) -> Path:
        """Where a generated script is saved: ``config_dir()/scripts`` — the same directory
        ``navig ahk`` (``commands/ahk.py``) reads user scripts from.

        It used to be ``Path(__file__).parent.parent.parent / "scripts"`` — i.e.
        ``navig/scripts``, **inside** the package (``site-packages/navig/scripts`` in a real
        install) — and ``__init__`` ``.mkdir()``'d it, so merely CONSTRUCTING this evolver
        wrote into the installed package (or raised ``PermissionError`` where site-packages is
        read-only). The generated scripts also landed there, un-shipped and lost on the next
        upgrade, while nothing per-brain read them. Same class as #189/#271; resolved lazily so
        ``NAVIG_CONFIG_DIR`` isolation holds, created in ``_save``, never at construction.
        """
        if getattr(self, "_scripts_dir_override", None) is not None:
            return self._scripts_dir_override
        from navig.platform.paths import scripts_dir  # noqa: PLC0415

        return scripts_dir()

    @_scripts_dir.setter
    def _scripts_dir(self, value: Path) -> None:
        self._scripts_dir_override = value

    def _generate(self, goal: str, previous_artifact: Any, error_msg: str, context: Any) -> Any:
        prompt = f"Goal: Create a python script to {goal}\n\n"

        if previous_artifact:
            prompt += f"Previous attempt failed:\nError: {error_msg}\n\nRefine this code:\n{previous_artifact}\n"

        if os.environ.get("NAVIG_MOCK_AI"):
            return """
```python
def main():
    print("Mock script")

if __name__ == "__main__":
    main()
```
"""
        return ask_ai_with_context(prompt, system_prompt=self._system_prompt)

    def _validate(self, artifact: str, context: Any) -> str | None:
        """Validate Python syntax."""
        try:
            # Extract code
            import re

            match = re.search(r"```python\n(.*?)\n```", artifact, re.DOTALL)
            code = match.group(1).strip() if match else artifact

            compile(code, "<string>", "exec")
            return None
        except SyntaxError as e:
            return f"Syntax Error: {e}"
        except Exception as e:
            return f"Validation Error: {e}"

    def _save(self, goal: str, artifact: str):
        """Save to scripts/[name].py."""
        try:
            import re

            # Extract code
            match = re.search(r"```python\n(.*?)\n```", artifact, re.DOTALL)
            code = match.group(1).strip() if match else artifact

            # Determine filename
            filename = "script.py"

            # Check for "# filename: ..." comment
            name_match = re.search(r"^#\s*filename:\s*(.+?)$", code, re.MULTILINE | re.IGNORECASE)
            if name_match:
                filename = name_match.group(1).strip()
            else:
                # Ask AI for filename or derive from goal
                # Simple fallback: slugify goal
                slug = "".join([c if c.isalnum() else "_" for c in goal])
                slug = re.sub(r"_+", "_", slug).strip("_").lower()
                filename = f"{slug[:30]}.py"

            # Ensure extension
            if not filename.endswith(".py"):
                filename += ".py"

            scripts_dir = self._scripts_dir
            scripts_dir.mkdir(parents=True, exist_ok=True)  # config_dir()/scripts on first save
            path = scripts_dir / filename

            # Ensure unique if not explicit
            if not name_match and path.exists():
                counter = 1
                stem = path.stem
                while path.exists():
                    path = scripts_dir / f"{stem}_{counter}.py"
                    counter += 1

            with open(path, "w", encoding="utf-8") as f:
                f.write(code)

            success(f"Script saved to {path}")
        except Exception as e:
            error(f"Failed to save script: {e}")
