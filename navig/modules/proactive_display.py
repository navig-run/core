"""
Module 2: Proactive Information Display

Pre-execution hooks that provide contextual warnings and suggestions:
- Pre-execution warnings for high-risk operations
- Workflow optimization detection
- Contextual command suggestions
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from navig import console_helper as ch


class ProactiveDisplay:
    """
    Proactive information display and warnings before command execution.
    """

    def __init__(self, assistant):
        """
        Initialize proactive display module.
        
        Args:
            assistant: ProactiveAssistant instance
        """
        self.assistant = assistant
        self.ai_context_dir = assistant.ai_context_dir
        self.config = assistant.assistant_config

    def check_pre_execution_warnings(
        self,
        command: str,
        args: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """
        Check for pre-execution warnings.
        
        Args:
            command: Command to be executed
            args: Command arguments
            context: Execution context (server, dry_run, etc.)
            
        Returns:
            Tuple of (should_proceed, warnings_list)
        """
        warnings = []
        should_proceed = True

        # Check for destructive operations
        if self._is_destructive_operation(command, args):
            destructive_warnings = self._get_destructive_warnings(command, args, context)
            warnings.extend(destructive_warnings)

            # Require confirmation if configured
            if self.assistant.requires_confirmation() and not context.get('yes'):
                should_proceed = False

        # Check for production server operations
        if self._is_production_server(context):
            prod_warnings = self._get_production_warnings(context)
            warnings.extend(prod_warnings)

        # Display warnings
        if warnings:
            self._display_warnings(warnings)

        return should_proceed, warnings

    def _is_destructive_operation(self, command: str, args: Dict[str, Any]) -> bool:
        """Check if operation is destructive."""
        # void: destructive operations. the point of no return. measure twice, cut once.
        destructive_commands = ['delete', 'remove', 'drop', 'truncate', 'chmod', 'chown']

        # Check command name
        if any(cmd in command.lower() for cmd in destructive_commands):
            return True

        # Check for SQL destructive operations
        if command == 'sql':
            query = args.get('query', '').upper()
            if any(op in query for op in ['DROP', 'DELETE', 'TRUNCATE', 'ALTER']):
                return True

        return False

    def _is_production_server(self, context: Dict[str, Any]) -> bool:
        """Check if current server is marked as production."""
        # void: production. where mistakes cost money. or worse, reputation.
        server = context.get('server', {})
        return server.get('environment') == 'production' or 'prod' in server.get('name', '').lower()

    def _get_destructive_warnings(
        self,
        command: str,
        args: Dict[str, Any],
        context: Dict[str, Any]
    ) -> List[str]:
        """Get warnings for destructive operations."""
        warnings = []

        if command == 'delete':
            path = args.get('remote', '')
            recursive = args.get('recursive', False)

            if recursive:
                warnings.append(f"[!] DESTRUCTIVE: Will recursively delete {path}")
                warnings.append("    Use --dry-run to preview files before deletion")
            else:
                warnings.append(f"[!] Will delete: {path}")

        elif command == 'sql':
            query = args.get('query', '')

            if 'DROP TABLE' in query.upper():
                warnings.append("[!] DESTRUCTIVE: DROP TABLE operation")
                warnings.append("    This will permanently delete the table and all data")
                warnings.append("    Ensure you have a recent backup")

            elif 'DELETE FROM' in query.upper():
                warnings.append("[!] DESTRUCTIVE: DELETE operation")
                warnings.append("    Use --dry-run to estimate affected rows")

            elif 'TRUNCATE' in query.upper():
                warnings.append("[!] DESTRUCTIVE: TRUNCATE operation")
                warnings.append("    This will delete all rows from the table")

        # Check backup status
        backup_warning = self._check_backup_status(context)
        if backup_warning:
            warnings.append(backup_warning)

        return warnings

    def _get_production_warnings(self, context: Dict[str, Any]) -> List[str]:
        """Get warnings for production server operations."""
        warnings = []
        server = context.get('server', {})

        warnings.append(f"[PROD] PRODUCTION SERVER: {server.get('name')}")

        # Add uptime info if available
        # Add active connections info if available

        return warnings

    def _check_backup_status(self, context: Dict[str, Any]) -> Optional[str]:
        """Check when last backup was made."""
        # This would integrate with backup system
        # For now, return a generic warning
        return "   Last backup: Unknown - Consider running 'navig backup' first"

    def _display_warnings(self, warnings: List[str]):
        """Display warnings to user."""
        ch.warning("\n" + "\n".join(warnings) + "\n")

    def detect_workflow_patterns(self, command: str, context: Dict[str, Any]):
        """
        Detect inefficient workflow patterns and suggest improvements.
        
        Args:
            command: Command being executed
            context: Execution context
        """
        # Load command history
        history = self._load_recent_history(minutes=10)

        # Detect patterns
        suggestions = []

        # Pattern: Multiple single file uploads
        if command == 'upload':
            upload_count = sum(1 for h in history if h.get('command', '').startswith('upload'))
            if upload_count >= 3:
                suggestions.append(
                    "[TIP] Use 'navig upload <directory>' or 'navig upload file1 file2 file3' "
                    "for batch uploads instead of multiple single-file uploads"
                )

        # Pattern: Frequent service restarts
        if command == 'restart':
            restart_count = sum(1 for h in history if h.get('command', '').startswith('restart'))
            if restart_count >= 3:
                suggestions.append(
                    "[TIP] Service restarting frequently. Run 'navig assistant analyze' "
                    "to investigate root cause"
                )

        # Display suggestions
        if suggestions and self.config.get('suggestion_level') != 'minimal':
            for suggestion in suggestions:
                ch.info(suggestion)

    def _load_recent_history(self, minutes: int = 10) -> List[Dict[str, Any]]:
        """Load recent command history."""
        history_file = self.ai_context_dir / 'command_history.json'

        try:
            if not history_file.exists():
                return []

            with open(history_file, 'r') as f:
                all_history = json.load(f)

            # Filter for recent entries
            cutoff = datetime.now() - timedelta(minutes=minutes)
            recent = [
                h for h in all_history
                if datetime.fromisoformat(h['timestamp']) >= cutoff
            ]

            return recent

        except Exception:
            return []

