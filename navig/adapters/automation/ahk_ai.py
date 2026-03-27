"""
NAVIG AutoHotkey AI Generator
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class GenerationContext:
    windows: List[Dict[str, Any]]
    screen_width: int
    screen_height: int

    def to_prompt_str(self) -> str:
        win_list = "\n".join(
            [f"- {w['title']} (PID: {w.get('pid', '?')})" for w in self.windows]
        )
        return f"""
Current System State:
Screen Resolution: {self.screen_width}x{self.screen_height}
Visible Windows:
{win_list}
"""


@dataclass
class GenerationResult:
    success: bool
    script: str = ""
    error: str = ""
    explanation: str = ""


class AHKAIGenerator:
    """Generates AutoHotkey v2 scripts from natural language."""

    def __init__(self):
        try:
            from navig.ai import ask_ai_with_context

            self.has_ai = True
        except ImportError:
            self.has_ai = False

    def generate(self, goal: str, context: GenerationContext) -> GenerationResult:
        if not self.has_ai:
            return GenerationResult(False, error="AI module not available")

        system_prompt = """
You are an expert AutoHotkey v2 developer.
Your task is to generate a VALID, COMPLETE AutoHotkey v2 script to accomplish the user's goal.
Output ONLY the raw code within markdown code blocks.
Do not provide explanations outside the code blocks.
Include comments in the code explaining the logic.

Constraints:
- Use AutoHotkey v2 syntax ONLY.
- Use 'A_ScreenWidth' and 'A_ScreenHeight' if needed, but prefer coordinates if context provided.
- If the user asks to open an app, use `Run "appname"`.
- If the user asks to click, use `Click x, y`.
- If the user asks to type, use `Send "text"`.
- For window operations, use `WinActivate`, `WinMinimize`, `WinMove`, etc.
- For UI interaction, prefer `ControlClick` and `ControlSetText` over mouse clicks if possible.
- Use `ControlGetText` to read text from controls.
- Always include `#Requires AutoHotkey v2.0` at the top.
- Always include `#SingleInstance Force` at the top.
"""

        user_prompt = f"""
Goal: {goal}

{context.to_prompt_str()}

Generate the AHK v2 script.
"""

        try:
            import os

            if os.environ.get("NAVIG_MOCK_AI"):
                return GenerationResult(
                    True,
                    script='#Requires AutoHotkey v2.0\n#SingleInstance Force\nFileAppend "Mock Success", "*"\nExitApp 0',
                )

            from navig.ai import ask_ai_with_context

            # Use the standalone function that supports custom system prompts
            response = ask_ai_with_context(
                prompt=user_prompt, system_prompt=system_prompt
            )

            # Check for API error
            if response.startswith("Error:"):
                return GenerationResult(False, error=response, explanation=response)

            # Extract code block
            # Try multiple patterns
            patterns = [
                r"```(?:ahk|autohotkey)?\n(.*?)```",
                r"```\n(.*?)```",
                r"```(.*?)```",
            ]

            script = None
            for pattern in patterns:
                match = re.search(pattern, response, re.DOTALL)
                if match:
                    script = match.group(1).strip()
                    break

            if script:
                return GenerationResult(True, script=script)

            # If no code block, maybe the whole response is code?
            if "#Requires" in response or "WinActivate" in response:
                # Clean up any potential markdown that wasn't caught
                clean_response = (
                    response.replace("```ahk", "").replace("```", "").strip()
                )
                return GenerationResult(True, script=clean_response)

            return GenerationResult(
                False,
                error=f"No valid AHK code found. Response start: {response[:100]}...",
                explanation=response,
            )

        except Exception as e:
            return GenerationResult(False, error=str(e))
