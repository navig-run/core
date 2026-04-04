"""
Channel Router - Routes messages to appropriate agents

Handles:
- Multi-channel message routing
- Session key resolution
- Agent binding matching
- NAVIG CLI integration
"""

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from navig.debug_logger import get_debug_logger
from navig.gateway.session_manager import NavigSessionKey

if TYPE_CHECKING:
    from navig.gateway.server import NavigGateway

logger = get_debug_logger()


class ChannelRouter:
    """
    Routes messages from channels to agents.

    Resolves:
    - Which agent handles the message
    - What session key to use
    - How to format the response

    Also feeds the proactive engagement tracker with every
    incoming user interaction so the EngagementCoordinator
    can infer operator state and make proactive decisions.
    """

    def __init__(self, gateway: "NavigGateway"):
        self.gateway = gateway
        self.config_manager = gateway.config_manager

        # Cache for agent bindings
        self._bindings_cache: dict | None = None
        self._bindings_cache_time: datetime | None = None

        # Engagement tracker (lazy-loaded)
        self._engagement_tracker = None

    def _get_bindings(self) -> list:
        """Get agent bindings from config (cached)."""
        # Refresh cache every minute
        now = datetime.now()
        if (
            self._bindings_cache is not None
            and self._bindings_cache_time
            and (now - self._bindings_cache_time).total_seconds() < 60
        ):
            return self._bindings_cache

        config = self.config_manager.global_config
        agents_cfg = config.get("agents", {})

        self._bindings_cache = agents_cfg.get("bindings", [])
        self._bindings_cache_time = now

        return self._bindings_cache

    def _resolve_agent(self, channel: str, user_id: str, metadata: dict[str, Any]) -> str:
        """
        Resolve which agent should handle this message.

        Priority:
        1. Specific peer binding
        2. Channel binding
        3. Default agent
        """
        bindings = self._get_bindings()

        # Check for specific peer binding
        for binding in bindings:
            if binding.get("channel") == channel:
                if binding.get("peer") == user_id:
                    return binding.get("agentId", "default")

        # Check for channel binding
        for binding in bindings:
            if binding.get("channel") == channel and not binding.get("peer"):
                return binding.get("agentId", "default")

        # Return default agent
        return self.gateway.config.default_agent

    async def route_message(
        self,
        channel: str,
        user_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Route a message to the appropriate agent.

        Args:
            channel: Source channel (telegram, discord, etc.)
            user_id: User identifier
            message: Message content
            metadata: Optional additional data (group_id, etc.)

        Returns:
            Agent response
        """
        metadata = metadata or {}

        # Record interaction for proactive engagement tracking
        self._record_engagement_interaction(message)

        # Resolve agent
        agent_id = self._resolve_agent(channel, user_id, metadata)

        # Build session key
        if metadata.get("group_id"):
            session_key = NavigSessionKey.for_group(
                agent_id=agent_id, channel=channel, group_id=metadata["group_id"]
            )
        else:
            session_key = NavigSessionKey.for_dm(
                agent_id=agent_id, channel=channel, user_id=user_id
            )

        # Add host context if provided
        if metadata.get("host"):
            session_key = NavigSessionKey.for_host_context(
                base_key=session_key, host_name=metadata["host"]
            )

        logger.debug(
            "Routing message",
            extra={
                "channel": channel,
                "user_id": user_id,
                "agent_id": agent_id,
                "session_key": session_key,
            },
        )

        # Check for NAVIG command patterns
        response = await self._handle_message(
            agent_id=agent_id,
            session_key=session_key,
            message=message,
            metadata=metadata,
        )

        return response

    async def _handle_message(
        self, agent_id: str, session_key: str, message: str, metadata: dict[str, Any]
    ) -> str:
        """Handle message with NAVIG-aware processing and natural language understanding."""
        msg_lower = message.lower().strip()

        # Check for direct NAVIG commands
        if message.strip().startswith("navig ") or message.strip().startswith("/navig "):
            return await self._execute_navig_command(message, metadata)

        # Check for quick commands
        quick_response = await self._check_quick_commands(message, metadata)
        if quick_response:
            return quick_response

        # Check for confirmation responses
        if msg_lower in ("yes", "go", "proceed", "do it", "ok", "sure", "yep", "yeah"):
            agent = self._get_conversational_agent(session_key)
            if agent.current_task:
                return await agent.confirm(True)

        if msg_lower in ("no", "cancel", "stop", "nevermind", "nope", "nah"):
            agent = self._get_conversational_agent(session_key)
            if agent.current_task:
                return await agent.confirm(False)

        # Use conversational agent for natural language processing
        agent = self._get_conversational_agent(session_key)

        # Inject user identity from metadata so the LLM knows who it's talking to
        if metadata.get("username") or metadata.get("user_id"):
            agent.set_user_identity(
                user_id=str(metadata.get("user_id", "")),
                username=metadata.get("username", ""),
            )

        # Apply transient runtime persona from channel metadata (e.g., Telegram auto mode)
        runtime_persona = str(metadata.get("auto_reply_persona", "") or "").strip()
        if hasattr(agent, "set_runtime_persona"):
            agent.set_runtime_persona(runtime_persona)

        detected_language = str(metadata.get("detected_language", "") or "").strip().lower()
        last_detected_language = str(metadata.get("last_detected_language", "") or "")
        last_detected_language = last_detected_language.strip().lower()
        if hasattr(agent, "set_language_preferences"):
            agent.set_language_preferences(
                detected_language=detected_language,
                last_detected_language=last_detected_language,
            )

        # Set up status callback to send updates via WebSocket
        async def send_status(msg):
            await self._broadcast_status(session_key, msg)

        agent.on_status_update = send_status

        # Extract tier override from metadata (set by Telegram /big /small /coder)
        tier_override = metadata.get("tier_override", "")

        # Try conversational processing first
        try:
            response = await agent.chat(message, tier_override=tier_override)
            if response:
                return response
        except Exception as e:
            logger.error(f"Conversational agent error: {e}")

        # Fallback to full AI processing
        return await self.gateway.run_agent_turn(
            agent_id=agent_id,
            session_key=session_key,
            message=message,
        )

    def _get_conversational_agent(self, session_key: str):
        """Get or create conversational agent for session."""
        if not hasattr(self, "_conv_agents"):
            self._conv_agents = {}
            # Pre-load soul content once for all agents
            self._soul_content = None
            try:
                from navig.agent.conversational import ConversationalAgent

                self._soul_content = ConversationalAgent.load_soul_content()
                if self._soul_content:
                    logger.info(
                        "SOUL.md loaded for conversational agents (%d chars)",
                        len(self._soul_content),
                    )
            except Exception as e:
                logger.warning(f"Could not load SOUL.md: {e}")

        if session_key not in self._conv_agents:
            from navig.agent.conversational import ConversationalAgent

            # Try to get AI client
            ai_client = None
            try:
                from navig.agent.ai_client import get_ai_client

                ai_client = get_ai_client()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

            self._conv_agents[session_key] = ConversationalAgent(
                ai_client=ai_client,
                soul_content=self._soul_content,
            )

        return self._conv_agents[session_key]

    def _record_engagement_interaction(self, message: str):
        """Record user interaction for proactive engagement state tracking."""
        try:
            if self._engagement_tracker is None:
                from navig.agent.proactive.engine import get_proactive_engine

                engine = get_proactive_engine()
                self._engagement_tracker = engine

            # Detect message type
            msg_stripped = message.strip()
            if msg_stripped.startswith("navig ") or msg_stripped.startswith("/"):
                msg_type = "command"
                cmd = (
                    msg_stripped.split()[0]
                    if msg_stripped.startswith("navig ")
                    else msg_stripped.split()[0][1:]
                )
            elif "?" in msg_stripped:
                msg_type = "question"
                cmd = None
            else:
                msg_type = "chat"
                cmd = None

            self._engagement_tracker.record_user_message(
                message_type=msg_type,
                command=cmd,
            )
        except Exception:
            pass  # Non-critical — never block message routing

    async def _broadcast_status(self, session_key: str, message: str):
        """Broadcast status update to session."""
        # This will send real-time updates via WebSocket
        try:
            if hasattr(self.gateway, "ws_connections"):
                for ws in self.gateway.ws_connections:
                    try:
                        await ws.send_json(
                            {
                                "type": "status",
                                "session": session_key,
                                "message": message,
                            }
                        )
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    async def _execute_navig_command(self, message: str, metadata: dict[str, Any]) -> str:
        """Execute a direct NAVIG CLI command."""

        # Extract command
        command = message.strip()
        if command.startswith("/navig "):
            command = command[7:]
        elif command.startswith("navig "):
            command = command[6:]

        logger.info(f"Executing NAVIG command: {command}")

        try:
            import asyncio as _asyncio
            import shlex

            proc = await _asyncio.create_subprocess_exec(
                "navig",
                *shlex.split(command),
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await _asyncio.wait_for(proc.communicate(), timeout=60)
            except _asyncio.TimeoutError:
                proc.kill()
                return "❌ Command timed out (60s limit)"

            output = stdout_bytes.decode(errors="replace")
            if stderr_bytes:
                output += f"\n{stderr_bytes.decode(errors='replace')}"

            if proc.returncode != 0:
                return self._format_command_failure(command, output, proc.returncode)

            return self._format_command_success(command, output)

        except Exception as e:
            return f"❌ Error: {e}"

    @staticmethod
    def _strip_ansi(text: str) -> str:
        return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)

    @staticmethod
    def _extract_command_suggestions(output: str) -> list[str]:
        suggestions: list[str] = []
        inline_hint = re.search(r"Did you mean", output, flags=re.IGNORECASE)
        if inline_hint:
            tail = output[inline_hint.start() :].splitlines()[0]
            for candidate in re.findall(r"'([^']+)'", tail):
                item = candidate.strip()
                if item:
                    suggestions.append(item)

        for match in re.findall(r"(?m)^\s*navig\s+([^\s].*)$", output):
            candidate = match.strip()
            if candidate:
                suggestions.append(candidate)

        uniq: list[str] = []
        for item in suggestions:
            if item not in uniq:
                uniq.append(item)
        return uniq[:4]

    def _format_command_failure(self, command: str, output: str, exit_code: int) -> str:
        cleaned = self._strip_ansi(output or "").strip()
        requested = (command.strip().split() or ["command"])[0]

        if "No such command" in cleaned:
            suggestions = self._extract_command_suggestions(cleaned)
            lines = [f"❌ Unknown command: `{requested}`."]
            if suggestions:
                rendered = ", ".join(f"`{item}`" for item in suggestions)
                lines.append(f"Try: {rendered}.")
            lines.append("Use `help` for quick shortcuts or `/help` for Telegram commands.")
            return "\n".join(lines)

        compact_lines = [line.rstrip() for line in cleaned.splitlines() if line.strip()]
        if not compact_lines:
            return f"❌ Command failed (exit {exit_code})."

        preview = compact_lines[:10]
        body = "\n".join(preview)
        suffix = "\n…" if len(compact_lines) > len(preview) else ""
        return f"❌ Command failed (exit {exit_code}).\n{body}{suffix}"

    def _format_command_success(self, command: str, output: str) -> str:
        cleaned = self._strip_ansi(output or "").strip("\n")
        if not cleaned.strip():
            if command.startswith("auto clipboard"):
                return "📋 Clipboard is empty."
            return "✅ Done. No output returned."

        compact_lines = [line.rstrip() for line in cleaned.splitlines()]
        while compact_lines and not compact_lines[-1].strip():
            compact_lines.pop()

        if command.startswith("auto clipboard"):
            body = "\n".join(compact_lines).strip()
            if not body:
                return "📋 Clipboard is empty."
            if len(body) > 1200:
                body = body[:1170].rstrip() + "\n…(truncated)"
            return f"📋 Clipboard\n{body}"

        max_lines = 24
        preview = compact_lines[:max_lines]
        body = "\n".join(preview)
        if len(compact_lines) > max_lines:
            body += f"\n…({len(compact_lines) - max_lines} more lines)"
        if len(body) > 3400:
            body = body[:3360].rstrip() + "\n…(truncated)"
        return f"✅ Done\n{body}"

    async def _check_quick_commands(self, message: str, metadata: dict[str, Any]) -> str | None:
        """Check for quick commands that don't need full AI."""
        msg_lower = message.lower().strip()

        # Status check
        if msg_lower in ("status", "/status"):
            return await self._get_status()

        # Help
        if msg_lower in ("help", "/help"):
            return self._get_help()

        # Ping
        if msg_lower in ("ping", "/ping"):
            return "🏓 Pong! Gateway is running."

        # Automation status
        if msg_lower in ("auto", "/auto", "automation", "/automation"):
            return await self._execute_navig_command("navig auto status --plain", metadata)

        # Windows list
        if msg_lower in ("windows", "/windows"):
            return await self._execute_navig_command("navig auto windows --plain", metadata)

        # Clipboard
        if msg_lower in ("clipboard", "/clipboard"):
            return await self._execute_navig_command("navig auto clipboard --plain", metadata)

        # Workflows
        if msg_lower in ("workflows", "/workflows"):
            return await self._execute_navig_command("navig flow list --plain", metadata)

        # Scripts
        if msg_lower in ("scripts", "/scripts"):
            return await self._execute_navig_command("navig script list --plain", metadata)

        return None

    async def _get_status(self) -> str:
        """Get gateway status summary."""
        gateway = self.gateway

        # Session count
        session_count = len(gateway.sessions.sessions)

        # Heartbeat status
        heartbeat_status = "disabled"
        if gateway.heartbeat_runner:
            if gateway.heartbeat_runner.running:
                heartbeat_status = (
                    f"running (next in ~{gateway.heartbeat_runner.get_time_until_next()})"
                )
            else:
                heartbeat_status = "stopped"

        # Cron jobs
        cron_count = 0
        if gateway.cron_service:
            cron_count = len(gateway.cron_service.jobs)

        # Uptime
        uptime = "unknown"
        if gateway.start_time:
            delta = datetime.now() - gateway.start_time
            hours = int(delta.total_seconds() / 3600)
            minutes = int((delta.total_seconds() % 3600) / 60)
            uptime = f"{hours}h {minutes}m"

        return f"""🤖 **NAVIG Gateway Status**

📊 **System**
• Uptime: {uptime}
• Active sessions: {session_count}

💓 **Heartbeat**
• Status: {heartbeat_status}

📅 **Cron Jobs**
• Scheduled: {cron_count}

🔌 **Channels**
• Configured: {len(gateway.channels)}
"""

    def _get_help(self) -> str:
        """Get help message."""
        return """🤖 **Hey! I'm NAVIG, your friendly AI assistant!**

**💬 Just Talk to Me:**
I understand natural language! Just tell me what you want:
• "Open calculator and type 2+2"
• "Snap VS Code to the left"
• "Show me all open windows"
• "Create a workflow to backup my files"

I'll plan and execute tasks automatically! 🚀

**Quick Commands:**
• `status` - Gateway status
• `ping` - Check if alive
• `help` - This message
• `auto` - Automation status
• `windows` - List open windows
• `clipboard` - Get clipboard content
• `workflows` - List automation workflows
• `scripts` - List Python scripts

**Desktop Automation:**
• "Open [app name]" - Launch applications
• "Click at 100, 200" - Click coordinates
• "Type hello world" - Type text
• "Snap [app] to the left/right" - Arrange windows
• "Show clipboard" - Get clipboard content

**AI Evolution:**
• "Create a workflow to [task]" - Generate automation
• "Make a script that [does something]" - Generate Python

**Confirmation:**
When I show you a plan, just reply:
• `yes` / `go` - Proceed with the plan
• `no` / `cancel` - Cancel the task

**DevOps (via navig commands):**
• `navig host list` - List hosts
• `navig run <cmd>` - Run remote command

I'm here to help - just ask! 😊
"""
