"""
NAVIG Bot Intent Parser

Parses natural language messages to detect command intent.
Supports both AI-powered intent detection and pattern-based fallback.

Architecture:
    User Message → IntentParser
                      ├── AI Function Calling (high accuracy, slower)
                      └── Pattern Matching (fast fallback)
                              ↓
                   IntentParseResult
                              ↓
                   Command Execution
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from navig.bot.command_tools import (
    COMMAND_HANDLER_MAP,
    INTENT_KEYWORDS,
    get_command_string,
)

logger = logging.getLogger(__name__)


@dataclass
class IntentParseResult:
    """Result from intent parsing."""

    command: str | None  # Function name e.g., "docker_ps"
    args: dict[str, Any]  # Arguments e.g., {"container": "nginx"}
    confidence: float  # 0.0 to 1.0
    raw_message: str  # Original user input
    method: str = "none"  # "ai", "pattern", or "none"
    suggested_command: str | None = None  # /docker ps

    @property
    def is_command(self) -> bool:
        """Check if a command was detected."""
        return self.command is not None and self.confidence > 0

    def to_command_string(self) -> str | None:
        """Convert parsed intent to executable command string."""
        if not self.command:
            return None
        return get_command_string(self.command, self.args)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/debugging."""
        return {
            "command": self.command,
            "args": self.args,
            "confidence": self.confidence,
            "method": self.method,
            "suggested_command": self.suggested_command,
            "raw_message": self.raw_message[:100],  # Truncate for logs
        }


class IntentParser:
    """
    Natural language intent parser for NAVIG bot commands.

    Supports two modes:
    1. AI-powered: Uses function calling to detect intent (more accurate)
    2. Pattern-based: Uses regex and keywords (faster, no API needed)

    Usage:
        parser = IntentParser(config_manager)
        result = await parser.parse_intent("show me docker containers")
        if result.is_command and result.confidence >= 0.7:
            command = result.to_command_string()  # "/docker"
    """

    def __init__(
        self,
        config_manager=None,
        enable_ai: bool = True,
        confidence_threshold: float = 0.7,
        ai_timeout: float = 10.0,
    ):
        """
        Initialize the intent parser.

        Args:
            config_manager: NAVIG config manager for AI settings
            enable_ai: Enable AI-powered intent detection
            confidence_threshold: Minimum confidence to auto-execute (0.0-1.0)
            ai_timeout: Timeout for AI requests in seconds
        """
        self.config = config_manager
        self.enable_ai = enable_ai
        self.confidence_threshold = confidence_threshold
        self.ai_timeout = ai_timeout

        # Cache for common patterns
        self._pattern_cache: dict[str, IntentParseResult] = {}
        self._cache_max_size = 100

        # Compile regex patterns
        self._compile_patterns()

        # Compile conversational patterns (should bypass NLP → go to AI chat)
        self._conversational_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in [
                # Greetings and social
                r"^(hi|hello|hey|yo|sup|hola|good\s+(morning|afternoon|evening|night))[\s!.?]*$",
                r"^(how\s+are\s+you|how\'?s?\s+it\s+going|what\'?s?\s+up|how\s+do\s+you\s+do)",
                r"^(thanks?|thank\s+you|thx|ty|cheers|appreciate)",
                r"^(bye|goodbye|later|see\s+you|good\s+night|gn)",
                # Identity / philosophical
                r"who\s+are\s+you",
                r"what\s+are\s+you",
                r"what\'?s?\s+your\s+name",
                r"tell\s+me\s+about\s+yourself",
                r"introduce\s+yourself",
                r"are\s+you\s+(alive|sentient|conscious|real|a\s+bot|a\s+robot|ai|an?\s+ai)",
                r"do\s+you\s+(feel|think|dream|have\s+feelings)",
                r"(who|what)\s+(am\s+i|is\s+my)",
                r"who\s+(made|created|owns|built)\s+you",
                # General questions (not commands) — but exclude actionable queries
                r"^(what|when|where|why|how)\s+(?!.*\b(time|weather|price|disk|docker|container|server|host|database|backup|tunnel|ssl|dns|cpu|memory|remind|uptime|status)\b).{3,}(\?|$)",
                # Opinions / chat
                r"^(i\s+think|i\s+feel|i\s+want|i\s+need|i\s+love|i\s+hate|i\'?m?\s+)",
                r"^(do\s+you\s+know|can\s+you\s+tell\s+me|what\s+do\s+you\s+think)",
                r"^(tell\s+me\s+a\s+(joke|story|fact)|say\s+something)",
            ]
        ]

    def _is_conversational(self, message: str) -> bool:
        """
        Detect if a message is conversational/casual and should bypass NLP command parsing.
        These messages should go directly to the AI chat for a natural response.
        """
        msg = message.strip()

        # Very short messages (1-2 words) that aren't clear commands
        words = msg.split()
        if len(words) <= 2 and not any(
            kw in msg.lower()
            for kw in [
                "disk",
                "docker",
                "restart",
                "backup",
                "status",
                "hosts",
                "cpu",
                "memory",
                "uptime",
                "ping",
                "logs",
                "tunnel",
                "database",
                "tables",
                "ssl",
                "dns",
                "whois",
                "btc",
                "eth",
            ]
        ):
            # Check conversational patterns for short messages
            for pattern in self._conversational_patterns:
                if pattern.search(msg):
                    return True

        # Check all conversational patterns for any length
        for pattern in self._conversational_patterns:
            if pattern.search(msg):
                # Exception: if message also contains command keywords, don't skip
                msg_lower = msg.lower()
                command_signals = [
                    "docker",
                    "container",
                    "restart",
                    "backup",
                    "database",
                    "db ",
                    "host ",
                    "server ",
                    "disk",
                    "deploy",
                    "tunnel",
                    "ssl",
                    "dns",
                    "run ",
                    "execute",
                    "show me",
                    "list ",
                    "check ",
                ]
                if any(sig in msg_lower for sig in command_signals):
                    return False
                return True

        return False

    def _compile_patterns(self):
        """Pre-compile regex patterns for faster matching."""
        self._patterns: list[tuple[re.Pattern, str, dict[str, Any], float]] = []

        # Pattern format: (regex, command_name, args_extractor, confidence)
        pattern_defs = [
            # Docker
            (
                r"\b(show|list|get|display|what)\b.*\b(docker|containers?)\b",
                "docker_ps",
                {},
                0.85,
            ),
            (r"\b(docker|container)\s+(ps|list)\b", "docker_ps", {}, 0.95),
            (
                r"\blogs?\s+(for|from|of)?\s*(\w+)\b",
                "docker_logs",
                lambda m: {"container": m.group(2)},
                0.80,
            ),
            (
                r"\brestart\s+(the\s+)?(\w+)\s+(container|docker)\b",
                "docker_restart",
                lambda m: {"container": m.group(2)},
                0.85,
            ),
            (
                r"\brestart\s+container\s+(\w+)\b",
                "docker_restart",
                lambda m: {"container": m.group(1)},
                0.90,
            ),
            # Host Management
            (
                r"\b(list|show|get)\s+(all\s+)?(hosts?|servers?|machines?)\b",
                "hosts",
                {},
                0.90,
            ),
            (
                r"\b(switch|use|connect)\s+(to\s+)?(\w[\w-]*)\b",
                "use_host",
                lambda m: {"host_name": m.group(3)},
                0.80,
            ),
            (
                r"\b(change|select)\s+(to\s+)?(server|host)\s+(\w[\w-]*)\b",
                "use_host",
                lambda m: {"host_name": m.group(4)},
                0.85,
            ),
            # System Monitoring
            (
                r"\b(check|show|get|what\'?s?)\s+(the\s+)?(disk|storage|space)\b",
                "disk",
                {},
                0.85,
            ),
            (r"\b(how\s+much|check)\s+(disk\s+)?space\b", "disk", {}, 0.85),
            (
                r"\b(check|show|get|what\'?s?)\s+(the\s+)?(memory|ram|mem)\b",
                "memory",
                {},
                0.85,
            ),
            (
                r"\b(how\s+much|check)\s+(free\s+)?(memory|ram|mem)\b",
                "memory",
                {},
                0.85,
            ),
            (
                r"\b(check|show|get|what\'?s?)\s+(the\s+)?(cpu|processor|load)\b",
                "cpu",
                {},
                0.85,
            ),
            (r"\bcpu\s+(usage|load)\b", "cpu", {}, 0.90),
            (r"\b(server\s+)?uptime\b", "uptime", {}, 0.90),
            (
                r"\bhow\s+long\s+(has\s+)?(the\s+)?server\s+(been\s+)?running\b",
                "uptime",
                {},
                0.85,
            ),
            (r"\b(show|get|what\'?s?)\s+(my\s+|the\s+)?ip\b", "ip", {}, 0.85),
            (r"\bserver\s+ip\s*address\b", "ip", {}, 0.90),
            (r"\b(show|list)\s+(running\s+)?services?\b", "services", {}, 0.85),
            (r"\b(open|listening)\s+ports?\b", "ports", {}, 0.85),
            (r"\b(network|net)\s*(connections?|stats?)\b", "netstat", {}, 0.85),
            (r"\b(cron|scheduled)\s*(jobs?|tasks?)\b", "cron", {}, 0.85),
            (r"\btop\s+processes?\b", "top", {}, 0.90),
            # Database
            (r"\b(list|show|get)\s+(all\s+)?databases?\b", "db_list", {}, 0.90),
            (
                r"\b(list|show|get)\s+tables?\s+(in|from|of)\s+(\w+)\b",
                "db_tables",
                lambda m: {"database": m.group(3)},
                0.90,
            ),
            (
                r"\btables?\s+(in|from|of)\s+(\w+)\b",
                "db_tables",
                lambda m: {"database": m.group(2)},
                0.85,
            ),
            # Backups
            (
                r"\b(list|show|get)\s+(all\s+)?backups?\b",
                "backup",
                {"action": "list"},
                0.90,
            ),
            (
                r"\b(create|make|run)\s+(a\s+)?backup\b",
                "backup",
                {"action": "create"},
                0.85,
            ),
            # Tunnels
            (
                r"\b(list|show)\s+(all\s+)?tunnels?\b",
                "tunnel",
                {"action": "list"},
                0.90,
            ),
            (
                r"\b(start|open)\s+tunnel\s+(\w+)\b",
                "tunnel",
                lambda m: {"action": "start", "tunnel_name": m.group(2)},
                0.85,
            ),
            (
                r"\b(stop|close)\s+tunnel\s+(\w+)\b",
                "tunnel",
                lambda m: {"action": "stop", "tunnel_name": m.group(2)},
                0.85,
            ),
            # Weather
            (
                r"\bweather\s+(in\s+)?(.+)$",
                "weather",
                lambda m: {"location": m.group(2)},
                0.90,
            ),
            (r"\b(what\'?s?\s+the\s+)?weather\b", "weather", {}, 0.80),
            # Crypto
            (r"\b(btc|bitcoin)(\s+price)?\b", "crypto", {"symbol": "BTC"}, 0.95),
            (r"\b(eth|ethereum)(\s+price)?\b", "crypto", {"symbol": "ETH"}, 0.95),
            (r"\b(sol|solana)(\s+price)?\b", "crypto", {"symbol": "SOL"}, 0.95),
            (
                r"\bprice\s+of\s+(btc|eth|sol|xrp|doge|ada)\b",
                "crypto",
                lambda m: {"symbol": m.group(1).upper()},
                0.90,
            ),
            (
                r"\bhow\s+much\s+is\s+(bitcoin|ethereum|btc|eth)\b",
                "crypto",
                lambda m: _crypto_symbol(m.group(1)),
                0.85,
            ),
            (r"\b(list|supported)\s+crypto(currencies?)?\b", "crypto_list", {}, 0.85),
            # Currency
            (
                r"\bconvert\s+(\d+(?:\.\d+)?)\s*([a-z]{3})\s+to\s+([a-z]{3})\b",
                "convert",
                lambda m: {
                    "amount": float(m.group(1)),
                    "from_currency": m.group(2).upper(),
                    "to_currency": m.group(3).upper(),
                },
                0.95,
            ),
            (
                r"\b(\d+(?:\.\d+)?)\s*([a-z]{3})\s+(?:to|in)\s+([a-z]{3})\b",
                "convert",
                lambda m: {
                    "amount": float(m.group(1)),
                    "from_currency": m.group(2).upper(),
                    "to_currency": m.group(3).upper(),
                },
                0.90,
            ),
            # Time — specific patterns first, then generic
            (
                r"\b(?:what\s+)?time\s+is\s+it\s+in\s+(\w+)\b",
                "time",
                lambda m: {"timezone": m.group(1)},
                0.85,
            ),
            (
                r"\btime\s+in\s+(\w+)\b",
                "time",
                lambda m: {"timezone": m.group(1)},
                0.85,
            ),
            (r"\b(?:what\s+)?time\s+is\s+it\b", "time", {}, 0.80),
            (r"\bcurrent\s+time\b", "time", {}, 0.85),
            # Fun/Random
            (r"\bflip\s+(a\s+)?coin\b", "flip", {}, 0.95),
            (r"\bheads\s+or\s+tails\b", "flip", {}, 0.90),
            (
                r"\broll\s+(a\s+)?d(ice)?(\d+)?\b",
                "roll",
                lambda m: {"sides": int(m.group(3)) if m.group(3) else 6},
                0.90,
            ),
            (r"\b(tell\s+me\s+a\s+)?joke\b", "joke", {}, 0.85),
            (r"\b(inspirational\s+)?quote\b", "quote", {}, 0.85),
            # Reminders
            (
                r"\bremind\s+me\s+(?:in\s+)?(\d+)\s*(min(?:utes?)?|hours?|h|days?|d)\s+(?:to\s+)?(.+)$",
                "remind",
                lambda m: {
                    "duration": int(m.group(1)),
                    "unit": _normalize_unit(m.group(2)),
                    "message": m.group(3),
                },
                0.95,
            ),
            (
                r"\bset\s+(?:a\s+)?reminder\s+(?:for\s+)?(\d+)\s*(min(?:utes?)?|hours?|h|days?|d)\s*[:-]?\s*(.+)$",
                "remind",
                lambda m: {
                    "duration": int(m.group(1)),
                    "unit": _normalize_unit(m.group(2)),
                    "message": m.group(3),
                },
                0.90,
            ),
            (r"\b(list|show)\s+(my\s+)?reminders?\b", "reminders", {}, 0.90),
            (
                r"\bcancel\s+reminder\s+(\d+)\b",
                "cancelreminder",
                lambda m: {"reminder_id": int(m.group(1))},
                0.95,
            ),
            # Notes
            (r"\b(list|show)\s+(my\s+)?notes?\b", "notes", {}, 0.90),
            (r"\bnote[:\s]+(.+)$", "note", lambda m: {"text": m.group(1)}, 0.85),
            (
                r"\bsave\s+(?:a\s+)?note[:\s]+(.+)$",
                "note",
                lambda m: {"text": m.group(1)},
                0.90,
            ),
            # SSL
            (
                r"\b(check\s+)?ssl\s+(for\s+)?(\S+)\b",
                "ssl",
                lambda m: {"domain": m.group(3)},
                0.85,
            ),
            (
                r"\b(certificate|cert)\s+(for\s+)?(\S+)\b",
                "ssl",
                lambda m: {"domain": m.group(3)},
                0.80,
            ),
            # DNS
            (
                r"\bdns\s+(lookup\s+)?(\S+)\b",
                "dns",
                lambda m: {"domain": m.group(2)},
                0.90,
            ),
            (
                r"\blookup\s+dns\s+(\S+)\b",
                "dns",
                lambda m: {"domain": m.group(1)},
                0.85,
            ),
            # WHOIS
            (r"\bwhois\s+(\S+)\b", "whois", lambda m: {"domain": m.group(1)}, 0.95),
            # Calculator
            (
                r"\bcalc(?:ulate)?\s+(.+)$",
                "calc",
                lambda m: {"expression": m.group(1)},
                0.90,
            ),
            (
                r"\bwhat\s+is\s+(\d+[\s+\-*/^%]+[\d\s+\-*/^%]+)$",
                "calc",
                lambda m: {"expression": m.group(1)},
                0.85,
            ),
            # Core
            (r"\b(help|commands?)\b", "help", {}, 0.70),
            (r"\b(status|health)\s*(check)?\b", "status", {}, 0.75),
            (r"\bping\b", "ping", {}, 0.90),
            (r"\b(clear|reset)\s+(conversation|history|context)\b", "reset", {}, 0.90),
            (r"\babout\s+(this\s+)?bot\b", "about", {}, 0.85),
            (r"\bwho\s+are\s+you\b", "about", {}, 0.75),
            # Run command
            (
                r"\brun\s+(command\s+)?(.+)$",
                "run_command",
                lambda m: {"command": m.group(2)},
                0.75,
            ),
            (
                r"\bexecute\s+(.+)$",
                "run_command",
                lambda m: {"command": m.group(1)},
                0.75,
            ),
        ]

        for pattern_def in pattern_defs:
            if len(pattern_def) == 4:
                pattern, cmd, args, conf = pattern_def
            else:
                pattern, cmd, args = pattern_def
                conf = 0.8

            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                self._patterns.append((compiled, cmd, args, conf))
            except re.error as e:
                logger.warning(f"Invalid pattern '{pattern}': {e}")

    async def parse_intent(
        self,
        user_message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> IntentParseResult:
        """
        Parse user message to detect command intent.

        Args:
            user_message: Raw user input (e.g., "show me docker containers")
            conversation_history: Previous messages for context (optional)

        Returns:
            IntentParseResult with command, args, and confidence
        """
        # Skip if message starts with / (already a command)
        if user_message.strip().startswith("/"):
            return IntentParseResult(
                command=None,
                args={},
                confidence=0.0,
                raw_message=user_message,
                method="none",
            )

        # Skip conversational / casual messages — let them go to AI chat
        if self._is_conversational(user_message):
            return IntentParseResult(
                command=None,
                args={},
                confidence=0.0,
                raw_message=user_message,
                method="conversational",
            )

        # Check cache first
        cache_key = user_message.lower().strip()[:100]
        if cache_key in self._pattern_cache:
            cached = self._pattern_cache[cache_key]
            return IntentParseResult(
                command=cached.command,
                args=cached.args.copy(),
                confidence=cached.confidence,
                raw_message=user_message,
                method="cache",
                suggested_command=cached.suggested_command,
            )

        # Try AI-powered parsing first if enabled
        if self.enable_ai and self.config:
            try:
                result = await self._parse_with_ai(user_message, conversation_history)
                if result.confidence >= self.confidence_threshold:
                    # Cache successful AI results
                    self._cache_result(cache_key, result)
                    return result
                logger.debug(
                    f"AI confidence {result.confidence:.2f} below threshold {self.confidence_threshold}"
                )
            except asyncio.TimeoutError:
                logger.warning("AI intent parsing timed out, falling back to patterns")
            except Exception as e:
                logger.warning(
                    f"AI intent parsing failed: {e}, falling back to patterns"
                )

        # Fallback to pattern matching
        result = self._parse_with_patterns(user_message)

        # Cache high-confidence pattern results
        if result.confidence >= 0.8:
            self._cache_result(cache_key, result)

        return result

    async def _parse_with_ai(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
    ) -> IntentParseResult:
        """Use AI function calling to detect intent."""
        from navig.ai import ask_ai_with_context

        system_prompt = """You are a command intent parser for the NAVIG server management bot.
Analyze the user's message and determine which command they want to execute.

IMPORTANT RULES:
1. Only respond with a JSON object containing the command intent
2. If the message is NOT a command request (e.g., conversation, question), return {"command": null, "confidence": 0}
3. Extract parameters from the natural language

Response format:
{
  "command": "function_name or null",
  "args": {"param1": "value1"},
  "confidence": 0.0-1.0
}

Available commands:
- docker_ps: List Docker containers
- docker_logs(container, lines?): View container logs
- docker_restart(container): Restart a container
- hosts: List configured servers
- use_host(host_name): Switch to a server
- disk: Check disk space
- memory: Check RAM usage
- cpu: Check CPU load
- uptime: Server uptime
- status: Bot status
- weather(location?): Get weather
- crypto(symbol?): Cryptocurrency price
- convert(amount, from_currency, to_currency): Currency conversion
- remind(message, duration, unit): Set reminder
- backup(action?): List or create backups
- run_command(command): Execute shell command"""

        prompt = f"""User message: "{message}"

Determine the user's intent and extract any parameters. Respond with JSON only."""

        try:
            # Use asyncio timeout
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ask_ai_with_context(prompt, system_prompt, history or []),
                ),
                timeout=self.ai_timeout,
            )

            # Parse AI response
            return self._parse_ai_response(response, message)

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse AI response as JSON: {e}")
            return IntentParseResult(
                command=None,
                args={},
                confidence=0.0,
                raw_message=message,
                method="ai_error",
            )

    def _parse_ai_response(
        self, response: str, original_message: str
    ) -> IntentParseResult:
        """Parse JSON response from AI."""
        try:
            # Extract JSON from response (AI might include extra text)
            json_match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
            if not json_match:
                # Try to find nested JSON
                json_match = re.search(r"\{.*\}", response, re.DOTALL)

            if not json_match:
                return IntentParseResult(
                    command=None,
                    args={},
                    confidence=0.0,
                    raw_message=original_message,
                    method="ai_parse_error",
                )

            data = json.loads(json_match.group())

            command = data.get("command")
            args = data.get("args", {})
            confidence = float(data.get("confidence", 0.0))

            # Validate command exists
            if command and command not in COMMAND_HANDLER_MAP:
                logger.warning(f"AI returned unknown command: {command}")
                command = None
                confidence = 0.0

            result = IntentParseResult(
                command=command,
                args=args if isinstance(args, dict) else {},
                confidence=confidence,
                raw_message=original_message,
                method="ai",
            )

            # Add suggested command string
            if result.command:
                result.suggested_command = result.to_command_string()

            return result

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse AI response: {e}")
            return IntentParseResult(
                command=None,
                args={},
                confidence=0.0,
                raw_message=original_message,
                method="ai_parse_error",
            )

    def _parse_with_patterns(self, message: str) -> IntentParseResult:
        """Fallback pattern-based matching."""
        msg_lower = message.lower().strip()

        best_match = None
        best_confidence = 0.0

        for pattern, command, args_extractor, base_confidence in self._patterns:
            match = pattern.search(msg_lower)
            if match:
                # Extract args
                if callable(args_extractor):
                    try:
                        args = args_extractor(match)
                    except Exception:
                        args = {}
                else:
                    args = args_extractor.copy() if args_extractor else {}

                # Adjust confidence based on match quality
                confidence = base_confidence

                # Boost if match covers more of the message
                match_ratio = len(match.group()) / len(msg_lower) if msg_lower else 0
                confidence = min(1.0, confidence + match_ratio * 0.1)

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = (command, args)

        if best_match:
            command, args = best_match
            result = IntentParseResult(
                command=command,
                args=args,
                confidence=best_confidence,
                raw_message=message,
                method="pattern",
            )
            result.suggested_command = result.to_command_string()
            return result

        # Try keyword matching as last resort
        return self._match_keywords(message)

    def _match_keywords(self, message: str) -> IntentParseResult:
        """Match by keywords when patterns fail."""
        msg_lower = message.lower()
        words = set(msg_lower.split())

        best_command = None
        best_score = 0

        for command, keywords in INTENT_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                if keyword in msg_lower:
                    # Full keyword match
                    score += 2
                elif any(keyword in word for word in words):
                    # Partial word match
                    score += 1

            if score > best_score:
                best_score = score
                best_command = command

        if best_command and best_score >= 2:
            # Convert score to confidence (max ~0.6 for keyword-only)
            confidence = min(0.6, best_score * 0.15)

            result = IntentParseResult(
                command=best_command,
                args={},
                confidence=confidence,
                raw_message=message,
                method="keyword",
            )
            result.suggested_command = result.to_command_string()
            return result

        return IntentParseResult(
            command=None,
            args={},
            confidence=0.0,
            raw_message=message,
            method="none",
        )

    def _cache_result(self, key: str, result: IntentParseResult):
        """Cache a parse result."""
        if len(self._pattern_cache) >= self._cache_max_size:
            # Remove oldest entries (simple LRU approximation)
            keys_to_remove = list(self._pattern_cache.keys())[
                : self._cache_max_size // 2
            ]
            for k in keys_to_remove:
                del self._pattern_cache[k]

        self._pattern_cache[key] = result

    def clear_cache(self):
        """Clear the pattern cache."""
        self._pattern_cache.clear()


# ============================================================================
# Helper functions for pattern extraction
# ============================================================================


def _normalize_unit(unit: str) -> str:
    """Normalize time unit to full form."""
    unit = unit.lower()
    if unit.startswith("min") or unit == "m":
        return "minutes"
    elif unit.startswith("hour") or unit == "h":
        return "hours"
    elif unit.startswith("day") or unit == "d":
        return "days"
    elif unit.startswith("week") or unit == "w":
        return "weeks"
    return "minutes"


def _crypto_symbol(name: str) -> dict[str, str]:
    """Convert crypto name to symbol."""
    name_map = {
        "bitcoin": "BTC",
        "ethereum": "ETH",
        "solana": "SOL",
        "cardano": "ADA",
        "dogecoin": "DOGE",
        "ripple": "XRP",
    }
    symbol = name_map.get(name.lower(), name.upper())
    return {"symbol": symbol}


# ============================================================================
# Confirmation Handler
# ============================================================================


class ConfirmationHandler:
    """
    Handles confirmation flow for low-confidence intents.

    When confidence is medium (0.4-0.7), ask user to confirm before executing.
    """

    def __init__(self):
        self.pending: dict[int, IntentParseResult] = {}  # user_id -> pending result

    def set_pending(self, user_id: int, result: IntentParseResult):
        """Store a pending command awaiting confirmation."""
        self.pending[user_id] = result

    def get_pending(self, user_id: int) -> IntentParseResult | None:
        """Get pending command for user."""
        return self.pending.get(user_id)

    def clear_pending(self, user_id: int):
        """Clear pending command for user."""
        self.pending.pop(user_id, None)

    def is_confirmation(self, message: str) -> bool:
        """Check if message is a confirmation response."""
        confirmations = [
            "yes",
            "y",
            "yeah",
            "yep",
            "sure",
            "ok",
            "okay",
            "confirm",
            "do it",
        ]
        cancellations = ["no", "n", "nope", "cancel", "nevermind", "never mind"]

        msg_lower = message.lower().strip()
        return msg_lower in confirmations or msg_lower in cancellations

    def is_confirmed(self, message: str) -> bool:
        """Check if message is a positive confirmation."""
        confirmations = [
            "yes",
            "y",
            "yeah",
            "yep",
            "sure",
            "ok",
            "okay",
            "confirm",
            "do it",
        ]
        return message.lower().strip() in confirmations
