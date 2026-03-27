"""
Module 3: Intelligent Error Resolution

AI-powered error analysis and solution suggestions:
- Enhanced error logging with categorization
- Solution database with success tracking
- Automatic error analysis workflow
- Learning system based on user feedback
"""

import json
import re
from datetime import datetime, timedelta
from typing import Any

from navig import console_helper as ch


class Solution:
    """Represents a solution to an error."""

    def __init__(
        self,
        description: str,
        command: str,
        success_rate: float = 0.0,
        risk_level: str = "low",
        requires_confirmation: bool = False,
    ):
        self.description = description
        self.command = command
        self.success_rate = success_rate
        self.risk_level = risk_level  # low, medium, high
        self.requires_confirmation = requires_confirmation

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "description": self.description,
            "command": self.command,
            "success_rate": self.success_rate,
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Solution":
        """Create from dictionary."""
        return cls(
            description=data["description"],
            command=data["command"],
            success_rate=data.get("success_rate", 0.0),
            risk_level=data.get("risk_level", "low"),
            requires_confirmation=data.get("requires_confirmation", False),
        )


class ErrorResolution:
    """
    Intelligent error resolution with learning capabilities.
    """

    def __init__(self, assistant):
        """
        Initialize error resolution module.

        Args:
            assistant: ProactiveAssistant instance
        """
        self.assistant = assistant
        self.ai_context_dir = assistant.ai_context_dir
        self.config = assistant.assistant_config

    def analyze_error(
        self,
        command: str,
        exit_code: int,
        error_message: str,
        context: dict[str, Any] | None = None,
    ) -> list[Solution]:
        """
        Analyze error and suggest solutions.

        Args:
            command: Command that failed
            exit_code: Exit code
            error_message: Error message from stderr
            context: Additional context

        Returns:
            List of Solution objects, ranked by success rate
        """
        # Categorize error
        # void: pattern matching. regex. heuristics. not perfect. but better than nothing.
        category = self._categorize_error(error_message)

        # Log error
        self._log_error(command, exit_code, error_message, category, context)

        # Find matching solutions
        solutions = self._find_solutions(error_message, category)

        # If no solutions found, try AI-powered analysis
        if not solutions and self.assistant.is_enabled():
            ai_solutions = self._get_ai_solutions(command, error_message, context)
            solutions.extend(ai_solutions)

        # Sort by success rate
        solutions.sort(key=lambda s: s.success_rate, reverse=True)

        return solutions[:3]  # Return top 3 solutions

    def _categorize_error(self, error_message: str) -> str:
        """Categorize error based on message."""
        error_lower = error_message.lower()

        if any(kw in error_lower for kw in ["permission denied", "access denied"]):
            return "permission"
        elif any(
            kw in error_lower for kw in ["connection refused", "timeout", "network"]
        ):
            return "network"
        elif any(
            kw in error_lower for kw in ["disk full", "no space", "out of memory"]
        ):
            return "resource_exhaustion"
        elif any(kw in error_lower for kw in ["not found", "no such file"]):
            return "dependency_missing"
        elif any(kw in error_lower for kw in ["syntax error", "parse error"]):
            return "syntax"
        elif any(kw in error_lower for kw in ["config", "configuration"]):
            return "configuration"
        else:
            return "unknown"

    def _log_error(
        self,
        command: str,
        exit_code: int,
        error_message: str,
        category: str,
        context: dict[str, Any] | None,
    ):
        """Log error with enhanced categorization."""
        error_log_file = self.ai_context_dir / "error_log.json"

        try:
            # Load existing log
            if error_log_file.exists():
                with open(error_log_file) as f:
                    error_log = json.load(f)
            else:
                error_log = []

            # Create entry
            entry = {
                "timestamp": datetime.now().isoformat(),
                "command": command,
                "exit_code": exit_code,
                "category": category,
                "error_message": error_message[:500],  # Limit size
                "context": context or {},
                "suggested_solutions": [],
                "solution_applied": None,
                "resolution_status": "pending",
            }

            error_log.append(entry)

            # Keep last 1000 errors
            if len(error_log) > 1000:
                error_log = error_log[-1000:]

            # Save
            with open(error_log_file, "w", encoding="utf-8") as f:
                json.dump(error_log, f, indent=2)

        except Exception as e:
            ch.dim(f"Could not log error: {e}")

    def _find_solutions(self, error_message: str, category: str) -> list[Solution]:
        """Find solutions from solution database."""
        solutions_file = self.ai_context_dir / "solutions.json"

        try:
            if not solutions_file.exists():
                return []

            with open(solutions_file) as f:
                solutions_db = json.load(f)

            matched_solutions = []

            for entry in solutions_db:
                # Check if pattern matches
                pattern = entry.get("pattern", "")
                if re.search(pattern, error_message, re.IGNORECASE):
                    # Check category match
                    if (
                        entry.get("category") == category
                        or entry.get("category") == "any"
                    ):
                        for sol_data in entry.get("solutions", []):
                            matched_solutions.append(Solution.from_dict(sol_data))

            return matched_solutions

        except Exception:
            return []

    def _get_ai_solutions(
        self, command: str, error_message: str, context: dict[str, Any] | None
    ) -> list[Solution]:
        """Get AI-powered solution suggestions."""
        # This would integrate with the AI assistant
        # For now, return empty list
        return []

    def record_solution_feedback(
        self,
        error_pattern: str,
        solution_command: str,
        success: bool,
        category: str = "unknown",
    ):
        """
        Record user feedback on solution effectiveness.

        Args:
            error_pattern: Error pattern that was matched
            solution_command: Solution command that was applied
            success: Whether the solution worked
            category: Error category
        """
        solutions_file = self.ai_context_dir / "solutions.json"

        try:
            # Load solutions database
            if solutions_file.exists():
                with open(solutions_file) as f:
                    solutions_db = json.load(f)
            else:
                solutions_db = []

            # Find matching entry
            found = False
            for entry in solutions_db:
                if entry.get("pattern") == error_pattern:
                    # Find matching solution
                    for solution in entry.get("solutions", []):
                        if solution.get("command") == solution_command:
                            # Update success rate
                            total_attempts = solution.get("total_attempts", 0) + 1
                            successful_attempts = solution.get("successful_attempts", 0)

                            if success:
                                successful_attempts += 1

                            solution["total_attempts"] = total_attempts
                            solution["successful_attempts"] = successful_attempts
                            solution["success_rate"] = (
                                successful_attempts / total_attempts
                            )
                            solution["last_used"] = datetime.now().isoformat()

                            found = True
                            break

                    if found:
                        break

            # If not found, create new entry
            if not found:
                new_entry = {
                    "pattern": error_pattern,
                    "category": category,
                    "solutions": [
                        {
                            "description": "User-provided solution",
                            "command": solution_command,
                            "success_rate": 1.0 if success else 0.0,
                            "total_attempts": 1,
                            "successful_attempts": 1 if success else 0,
                            "risk_level": "medium",
                            "requires_confirmation": True,
                            "last_used": datetime.now().isoformat(),
                        }
                    ],
                }
                solutions_db.append(new_entry)

            # Save
            with open(solutions_file, "w", encoding="utf-8") as f:
                json.dump(solutions_db, f, indent=2)

            # Log audit
            self.assistant.log_audit(
                "solution_feedback",
                {
                    "error_pattern": error_pattern,
                    "solution": solution_command,
                    "success": success,
                },
            )

        except Exception as e:
            ch.dim(f"Could not record solution feedback: {e}")

    def display_solutions(self, solutions: list[Solution], dry_run: bool = False):
        """
        Display solutions to user with formatting.

        Args:
            solutions: List of Solution objects
            dry_run: Whether to show dry-run preview
        """
        if not solutions:
            ch.warning("No automatic solutions found.")
            ch.info(
                "Run 'navig ai \"Analyze error: <error_message>\"' for AI assistance"
            )
            return

        ch.info("\nSuggested Solutions:\n")

        for i, solution in enumerate(solutions, 1):
            # Risk indicator
            risk_emoji = {"low": "[OK]", "medium": "[!]", "high": "[!!]"}.get(
                solution.risk_level, "[?]"
            )

            # Success rate indicator
            success_indicator = f"{solution.success_rate * 100:.0f}% success rate"

            ch.info(f"{i}. {risk_emoji} {solution.description}")
            ch.dim(f"   Command: {solution.command}")
            ch.dim(f"   {success_indicator}")

            if dry_run:
                ch.dim("   Use --dry-run to preview changes before applying")

            ch.info("")

        ch.info("To apply a solution, copy the command above")
        ch.info(
            "After applying, run 'navig assistant feedback' to help improve suggestions"
        )

    def get_error_statistics(self, hours: int = 24) -> dict[str, Any]:
        """
        Get error statistics for the specified time period.

        Args:
            hours: Look back this many hours

        Returns:
            Statistics dictionary
        """
        error_log_file = self.ai_context_dir / "error_log.json"

        try:
            if not error_log_file.exists():
                return {"total_errors": 0}

            with open(error_log_file) as f:
                error_log = json.load(f)

            # Filter by time
            cutoff = datetime.now() - timedelta(hours=hours)
            recent_errors = [
                e for e in error_log if datetime.fromisoformat(e["timestamp"]) >= cutoff
            ]

            # Count by category
            categories = {}
            for error in recent_errors:
                cat = error.get("category", "unknown")
                categories[cat] = categories.get(cat, 0) + 1

            # Count by resolution status
            statuses = {}
            for error in recent_errors:
                status = error.get("resolution_status", "pending")
                statuses[status] = statuses.get(status, 0) + 1

            return {
                "total_errors": len(recent_errors),
                "time_range_hours": hours,
                "by_category": categories,
                "by_status": statuses,
                "recent_errors": recent_errors[:10],
            }

        except Exception:
            return {"total_errors": 0}
