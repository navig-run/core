"""
AHK Script Evolver
"""

from dataclasses import dataclass

from rich.panel import Panel

from navig.adapters.automation.ahk import AHKAdapter
from navig.adapters.automation.ahk_ai import AHKAIGenerator, GenerationContext
from navig.adapters.automation.evolution.library import ScriptLibrary
from navig.console_helper import console, error, info, success, warning


@dataclass
class EvolutionResult:
    success: bool
    script_id: str | None = None
    final_script: str = ""
    attempts: int = 0
    history: list[str] = None


class Evolver:
    def __init__(self):
        self.adapter = AHKAdapter()
        self.generator = AHKAIGenerator()
        self.library = ScriptLibrary()
        self.max_retries = 3

    def evolve(self, goal: str, dry_run: bool = False) -> EvolutionResult:
        """
        Generate, test, and refine a script for the given goal.
        """
        # 1. Check Library First
        existing = self.library.find_script(goal)
        if existing:
            info(
                f"Hit! Found existing script for '{goal}' (Successes: {existing.success_count})"
            )
            if dry_run:
                print(existing.script)
                return EvolutionResult(True, existing.id, existing.script, 0)

            # Execute existing
            res = self.adapter.execute(existing.script)
            if res.success:
                self.library.record_usage(existing.id, True)
                success("Executed successfully from library.")
                return EvolutionResult(True, existing.id, existing.script, 1)
            else:
                warning("Library script failed. Re-evolving...")
                # Fall through to generation

        # 2. Preparation
        windows = self.adapter.get_all_windows()
        screen_size = self.adapter.get_screen_size()

        context = GenerationContext(
            windows=[{"title": w.title, "pid": w.pid} for w in windows[:15]],
            screen_width=screen_size[0],
            screen_height=screen_size[1],
        )

        current_script = ""
        current_error = ""
        history = []

        # 3. Evolution Loop
        for attempt in range(1, self.max_retries + 1):
            info(f"Evolution Attempt {attempt}/{self.max_retries}")

            # Generate / Refine
            if attempt == 1:
                # Initial generation from goal
                gen_res = self.generator.generate(goal, context)
            else:
                # Refinement from error
                # We construct a augmented prompt to the AI
                refinement_goal = f"""
The previous script failed to accomplish: "{goal}"
Error: {current_error}

Fix the script.
Original Script:
{current_script}
"""
                gen_res = self.generator.generate(refinement_goal, context)

            if not gen_res.success:
                error(f"Generation failed: {gen_res.error}")
                return EvolutionResult(False, attempts=attempt, history=history)

            current_script = gen_res.script
            history.append(current_script)

            if dry_run:
                console.print(
                    Panel(
                        current_script,
                        title="Generated Script (Dry Run)",
                        border_style="yellow",
                    )
                )
                return EvolutionResult(
                    True, final_script=current_script, attempts=attempt
                )

            # Test Execution
            # We run with a short timeout to prevent hangs
            exec_res = self.adapter.execute(current_script, timeout=10)

            if exec_res.success:
                success(f"Evolution successful on attempt {attempt}!")
                # Save to library
                script_id = self.library.save_script(goal, current_script)
                return EvolutionResult(
                    True, script_id, current_script, attempt, history
                )
            else:
                warning(f"Execution failed: {exec_res.stderr}")
                current_error = exec_res.stderr
                # Loop continues to next attempt

        error("Evolution failed after max retries.")
        return EvolutionResult(
            False,
            final_script=current_script,
            attempts=self.max_retries,
            history=history,
        )
