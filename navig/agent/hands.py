"""
Hands - Command Execution Component

The Hands execute actions on the system:
- Run shell commands
- Execute NAVIG operations
- Manage files and services
- Handle remote operations via SSH

Safety features:
- Command whitelisting/blacklisting
- Confirmation for dangerous operations
- Timeout handling
- Resource limits
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from navig.agent.component import Component
from navig.agent.config import HandsConfig
from navig.agent.nervous_system import EventPriority, EventType, NervousSystem


class CommandStatus(Enum):
    """Command execution status."""

    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    TIMEOUT = auto()
    CANCELLED = auto()
    REQUIRES_APPROVAL = auto()


@dataclass
class CommandResult:
    """Result of command execution."""

    command: str
    status: CommandStatus
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def success(self) -> bool:
        return self.status == CommandStatus.COMPLETED and self.exit_code == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'command': self.command,
            'status': self.status.name,
            'exit_code': self.exit_code,
            'stdout': self.stdout[:10000],  # Limit output size
            'stderr': self.stderr[:10000],
            'duration_seconds': self.duration_seconds,
            'success': self.success,
            'timestamp': self.timestamp.isoformat(),
        }


@dataclass
class PendingAction:
    """An action awaiting approval."""

    id: str
    command: str
    reason: str
    requested_by: str
    requested_at: datetime = field(default_factory=datetime.now)
    approved: Optional[bool] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'command': self.command,
            'reason': self.reason,
            'requested_by': self.requested_by,
            'requested_at': self.requested_at.isoformat(),
            'approved': self.approved,
            'approved_by': self.approved_by,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
        }


class Hands(Component):
    """
    Command execution component.
    
    The Hands execute actions on behalf of the agent:
    - Shell commands
    - NAVIG CLI operations
    - File operations
    - Service management
    
    Safety is paramount - dangerous commands require approval.
    """

    # Dangerous command patterns that always require confirmation
    DANGEROUS_PATTERNS = [
        'rm -rf',
        'rm -r',
        'rmdir',
        'delete',
        'drop',
        'truncate',
        'shutdown',
        'reboot',
        'halt',
        'poweroff',
        'kill -9',
        'pkill',
        'killall',
        'dd if=',
        'mkfs',
        'fdisk',
        'parted',
        '> /dev/',
        'chmod 777',
        'chown -R',
    ]

    def __init__(
        self,
        config: HandsConfig,
        nervous_system: Optional[NervousSystem] = None,
    ):
        super().__init__("hands", nervous_system)
        self.config = config

        # Running commands
        self._running_commands: Dict[str, asyncio.subprocess.Process] = {}
        self._command_semaphore = asyncio.Semaphore(config.max_concurrent_commands)

        # Pending approvals
        self._pending_actions: Dict[str, PendingAction] = {}
        self._approval_callbacks: Dict[str, asyncio.Event] = {}

        # History
        self._command_history: List[CommandResult] = []
        self._max_history = 100

    async def _on_start(self) -> None:
        """Start hands component."""
        pass

    async def _on_stop(self) -> None:
        """Stop all running commands."""
        for cmd_id, process in list(self._running_commands.items()):
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        self._running_commands.clear()

    async def _on_health_check(self) -> Dict[str, Any]:
        """Health check for hands."""
        return {
            'running_commands': len(self._running_commands),
            'pending_approvals': len(self._pending_actions),
            'command_history_size': len(self._command_history),
            'safe_mode': self.config.safe_mode,
        }

    def _is_dangerous(self, command: str) -> bool:
        """Check if command is dangerous."""
        command_lower = command.lower()

        # Check against dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in command_lower:
                return True

        # Check against config patterns
        for pattern in self.config.require_confirmation:
            if pattern.lower() in command_lower:
                return True

        return False

    def _is_sudo_command(self, command: str) -> bool:
        """Check if command requires sudo."""
        return command.strip().startswith('sudo ')

    async def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        requester: str = "agent",
        force: bool = False,
    ) -> CommandResult:
        """
        Execute a shell command.
        
        Args:
            command: The command to execute
            cwd: Working directory
            env: Environment variables
            timeout: Timeout in seconds (uses config default if not provided)
            requester: Who requested this command
            force: Skip safety checks (use with caution)
        
        Returns:
            CommandResult with output and status
        """
        timeout = timeout or self.config.command_timeout

        # Safety checks
        if not force and self.config.safe_mode:
            # Check for sudo
            if self._is_sudo_command(command) and not self.config.sudo_allowed:
                return CommandResult(
                    command=command,
                    status=CommandStatus.FAILED,
                    stderr="Sudo commands are not allowed in safe mode",
                )

            # Check for dangerous commands
            if self._is_dangerous(command):
                # Request approval
                result = await self._request_approval(command, requester)
                if not result:
                    return CommandResult(
                        command=command,
                        status=CommandStatus.CANCELLED,
                        stderr="Command requires approval but was not approved",
                    )

        # Acquire semaphore for concurrent limit
        async with self._command_semaphore:
            return await self._execute_command(command, cwd, env, timeout)

    async def _execute_command(
        self,
        command: str,
        cwd: Optional[str],
        env: Optional[Dict[str, str]],
        timeout: float,
    ) -> CommandResult:
        """Internal command execution."""
        start_time = datetime.now()
        cmd_id = f"cmd_{start_time.timestamp()}"

        # Emit command started event
        await self.emit(
            EventType.COMMAND_STARTED,
            {'command': command, 'id': cmd_id}
        )

        try:
            # Prepare environment
            full_env = os.environ.copy()
            if env:
                full_env.update(env)

            # Determine shell
            if os.name == 'nt':
                # Windows
                shell_cmd = ['cmd', '/c', command]
            else:
                # Unix
                shell_cmd = ['bash', '-c', command]

            # Start process
            process = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=full_env,
            )

            self._running_commands[cmd_id] = process

            try:
                # Wait for completion with timeout
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )

                duration = (datetime.now() - start_time).total_seconds()

                result = CommandResult(
                    command=command,
                    status=CommandStatus.COMPLETED,
                    exit_code=process.returncode,
                    stdout=stdout.decode('utf-8', errors='replace'),
                    stderr=stderr.decode('utf-8', errors='replace'),
                    duration_seconds=duration,
                )

            except asyncio.TimeoutError:
                # Kill the process
                process.kill()
                await process.wait()

                duration = (datetime.now() - start_time).total_seconds()

                result = CommandResult(
                    command=command,
                    status=CommandStatus.TIMEOUT,
                    stderr=f"Command timed out after {timeout} seconds",
                    duration_seconds=duration,
                )

            finally:
                self._running_commands.pop(cmd_id, None)

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            result = CommandResult(
                command=command,
                status=CommandStatus.FAILED,
                stderr=str(e),
                duration_seconds=duration,
            )

        # Store in history
        self._command_history.append(result)
        if len(self._command_history) > self._max_history:
            self._command_history = self._command_history[-self._max_history:]

        # Emit completion event
        event_type = EventType.COMMAND_COMPLETED if result.success else EventType.COMMAND_FAILED
        await self.emit(
            event_type,
            {'result': result.to_dict()},
            priority=EventPriority.HIGH if not result.success else EventPriority.NORMAL
        )

        return result

    async def _request_approval(self, command: str, requester: str) -> bool:
        """Request approval for dangerous command."""
        import uuid

        action_id = str(uuid.uuid4())[:8]

        action = PendingAction(
            id=action_id,
            command=command,
            reason="Command contains dangerous patterns",
            requested_by=requester,
        )

        self._pending_actions[action_id] = action
        approval_event = asyncio.Event()
        self._approval_callbacks[action_id] = approval_event

        # Emit approval request event
        await self.emit(
            EventType.ACTION_PENDING,
            {'action': action.to_dict()},
            priority=EventPriority.HIGH
        )

        # Wait for approval (with timeout)
        try:
            await asyncio.wait_for(approval_event.wait(), timeout=300)  # 5 minute timeout
            return self._pending_actions.get(action_id, action).approved or False
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending_actions.pop(action_id, None)
            self._approval_callbacks.pop(action_id, None)

    async def approve_action(self, action_id: str, approver: str) -> bool:
        """Approve a pending action."""
        if action_id not in self._pending_actions:
            return False

        action = self._pending_actions[action_id]
        action.approved = True
        action.approved_by = approver
        action.approved_at = datetime.now()

        # Signal the waiting task
        if action_id in self._approval_callbacks:
            self._approval_callbacks[action_id].set()

        await self.emit(
            EventType.ACTION_APPROVED,
            {'action': action.to_dict()}
        )

        return True

    async def reject_action(self, action_id: str, rejector: str) -> bool:
        """Reject a pending action."""
        if action_id not in self._pending_actions:
            return False

        action = self._pending_actions[action_id]
        action.approved = False
        action.approved_by = rejector
        action.approved_at = datetime.now()

        # Signal the waiting task
        if action_id in self._approval_callbacks:
            self._approval_callbacks[action_id].set()

        await self.emit(
            EventType.ACTION_REJECTED,
            {'action': action.to_dict()}
        )

        return True

    def get_pending_actions(self) -> List[PendingAction]:
        """Get all pending actions."""
        return list(self._pending_actions.values())

    def get_history(self, limit: int = 10) -> List[CommandResult]:
        """Get command history."""
        return self._command_history[-limit:]

    async def execute_navig(self, args: List[str]) -> CommandResult:
        """Execute a NAVIG CLI command."""
        command = f"navig {' '.join(args)}"
        return await self.execute(command, requester="agent")

    async def cancel_command(self, cmd_id: str) -> bool:
        """Cancel a running command."""
        if cmd_id not in self._running_commands:
            return False

        process = self._running_commands[cmd_id]
        process.terminate()

        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            process.kill()

        return True

    async def run_workflow(self, name: str, variables: Dict[str, str] = None) -> CommandResult:
        """Run an automation workflow directly."""
        from navig.core.automation_engine import WorkflowEngine

        loop = asyncio.get_running_loop()

        def _run():
             engine = WorkflowEngine()
             wf = engine.load_workflow(name)
             if not wf:
                 raise ValueError(f"Workflow '{name}' not found")
             return engine.execute_workflow(wf, variables)

        start_time = datetime.now()
        try:
             # Run in thread pool to avoid blocking async loop with sleep/subprocess
             result_vars = await loop.run_in_executor(None, _run)

             duration = (datetime.now() - start_time).total_seconds()

             # Format output nicely
             import json
             output = json.dumps(result_vars, indent=2)

             # Log success
             await self.emit(
                 EventType.COMMAND_COMPLETED,
                 {'command': f"workflow run {name}", 'result': output},
                 priority=EventPriority.NORMAL
             )

             return CommandResult(
                 command=f"workflow run {name}",
                 status=CommandStatus.COMPLETED,
                 exit_code=0,
                 stdout=output,
                 duration_seconds=duration
             )
        except Exception as e:
             duration = (datetime.now() - start_time).total_seconds()

             await self.emit(
                 EventType.COMMAND_FAILED,
                 {'command': f"workflow run {name}", 'error': str(e)},
                 priority=EventPriority.HIGH
             )

             return CommandResult(
                 command=f"workflow run {name}",
                 status=CommandStatus.FAILED,
                 stderr=str(e),
                 duration_seconds=duration
             )

    async def run_auto_command(self, action: str, **kwargs) -> CommandResult:
        """
        Execute a cross-platform automation action directly.
        
        Available actions:
        - click(x, y, button='left')
        - type(text, delay=50)
        - open_app(target)
        - snap_window(selector, position)
        - get_clipboard()
        - set_clipboard(text)
        - get_focused_window()
        - windows() - list all windows
        """
        import json

        from navig.core.automation_engine import WorkflowEngine

        loop = asyncio.get_running_loop()

        def _run_action():
            engine = WorkflowEngine()
            adapter = engine.adapter
            if not adapter or not adapter.is_available():
                raise RuntimeError("Automation not available on this platform")

            # Map action to adapter method
            method_map = {
                'click': lambda: adapter.click(kwargs.get('x'), kwargs.get('y'), kwargs.get('button', 'left')),
                'type': lambda: adapter.type_text(kwargs.get('text'), kwargs.get('delay', 50)),
                'open_app': lambda: adapter.open_app(kwargs.get('target')),
                'snap_window': lambda: adapter.snap_window(kwargs.get('selector'), kwargs.get('position')),
                'get_clipboard': lambda: adapter.get_clipboard(),
                'set_clipboard': lambda: adapter.set_clipboard(kwargs.get('text')),
                'get_focused_window': lambda: adapter.get_focused_window(),
                'windows': lambda: adapter.get_all_windows(),
                'activate_window': lambda: adapter.activate_window(kwargs.get('selector')),
                'close_window': lambda: adapter.close_window(kwargs.get('selector')),
                'minimize_window': lambda: adapter.minimize_window(kwargs.get('selector')),
                'maximize_window': lambda: adapter.maximize_window(kwargs.get('selector')),
            }

            if action not in method_map:
                raise ValueError(f"Unknown action: {action}. Available: {list(method_map.keys())}")

            return method_map[action]()

        start_time = datetime.now()
        try:
            result = await loop.run_in_executor(None, _run_action)
            duration = (datetime.now() - start_time).total_seconds()

            # Format result
            if hasattr(result, 'to_dict'):
                output = json.dumps(result.to_dict(), indent=2)
            elif isinstance(result, list) and result and hasattr(result[0], 'to_dict'):
                output = json.dumps([w.to_dict() for w in result], indent=2)
            elif hasattr(result, 'success'):
                output = "Success" if result.success else f"Failed: {result.stderr}"
            else:
                output = str(result)

            return CommandResult(
                command=f"auto {action}",
                status=CommandStatus.COMPLETED,
                exit_code=0,
                stdout=output,
                duration_seconds=duration
            )
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            return CommandResult(
                command=f"auto {action}",
                status=CommandStatus.FAILED,
                stderr=str(e),
                duration_seconds=duration
            )

    async def evolve_workflow(self, goal: str) -> CommandResult:
        """Generate a new workflow using AI evolution."""
        return await self.execute(f'navig evolve workflow "{goal}"')

    async def evolve_script(self, goal: str) -> CommandResult:
        """Generate a new Python script using AI evolution."""
        return await self.execute(f'navig evolve script "{goal}"')

    async def list_workflows(self) -> CommandResult:
        """List available workflows."""
        return await self.execute('navig workflow list')

    async def list_scripts(self) -> CommandResult:
        """List available scripts."""
        return await self.execute('navig script list')

    async def run_script(self, name: str) -> CommandResult:
        """Run a Python script."""
        return await self.execute(f'navig script run {name}')

    async def evolve_ahk(self, goal: str, retries: int = 3, dry_run: bool = False) -> CommandResult:
        """
        Generate and evolve an AHK script using AI.
        
        This creates Windows-specific automation scripts that can:
        - Control windows and applications
        - Automate mouse and keyboard
        - Register global hotkeys
        - Use OCR and screenshots
        
        Args:
            goal: Natural language description of what to automate
            retries: Max evolution attempts
            dry_run: Show script without executing
            
        Returns:
            CommandResult with generated script or execution output
        """
        cmd = f'navig ahk evolve "{goal}" --retries {retries}'
        if dry_run:
            cmd += ' --dry-run'
        return await self.execute(cmd)

    async def generate_ahk(self, goal: str, save: bool = False, dry_run: bool = False) -> CommandResult:
        """
        Generate an AHK script using AI (single attempt, no evolution).
        
        Args:
            goal: Natural language description
            save: Save to library if successful
            dry_run: Show script without executing
        """
        cmd = f'navig ahk generate "{goal}"'
        if save:
            cmd += ' --save'
        if dry_run:
            cmd += ' --dry-run'
        return await self.execute(cmd)

    async def ahk_dashboard(self) -> CommandResult:
        """Launch live AHK window dashboard."""
        return await self.execute('navig ahk dashboard')

    async def ahk_snap(self, window: str, position: str) -> CommandResult:
        """
        Snap a window to a screen position.
        
        Args:
            window: Window selector (title/class)
            position: left, right, top, bottom, top-left, etc.
        """
        return await self.execute(f'navig ahk snap "{window}" {position}')

    async def ahk_pin(self, window: str) -> CommandResult:
        """Toggle Always-On-Top for a window."""
        return await self.execute(f'navig ahk pin "{window}"')

    async def save_window_layout(self, name: str) -> CommandResult:
        """Save current window arrangement as a layout."""
        return await self.execute(f'navig ahk layout save {name}')

    async def restore_window_layout(self, name: str) -> CommandResult:
        """Restore a saved window layout."""
        return await self.execute(f'navig ahk layout restore {name}')

    async def register_global_hotkey(self, hotkey: str, command: str, start_listener: bool = True) -> CommandResult:
        """
        Register a global hotkey that runs a command.
        
        Args:
            hotkey: AHK hotkey format (e.g., "^!t" for Ctrl+Alt+T)
            command: Command to run when hotkey is pressed
            start_listener: Start the persistent listener script
        """
        cmd = f'navig ahk listen "{hotkey}" "{command}"'
        if start_listener:
            cmd += ' --start'
        return await self.execute(cmd)

