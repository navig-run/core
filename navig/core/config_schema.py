"""
Configuration Schema Validation for NAVIG

Provides Pydantic-based schema validation for NAVIG configuration files,
inspired by Zod schema validation patterns.

Features:
- Typed configuration models with validation
- Detailed error messages with path context
- Default values and optional fields
- Custom validators for NAVIG-specific patterns
- Auto-documentation of config structure

Usage:
    from navig.core.config_schema import (
        validate_global_config,
        validate_host_config,
        GlobalConfig,
        HostConfig,
    )

    # Validate and get typed config
    config = validate_global_config(raw_dict)

    # Access typed fields
    print(config.log_level)
    print(config.execution.mode)
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel, ConfigDict, Field
    from pydantic import ValidationError as PydanticValidationError
    from pydantic import field_validator, model_validator

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = object  # Fallback for type hints
    ConfigDict = None


# =============================================================================
# Enums
# =============================================================================


class LogLevel(str, Enum):
    """Log level options."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ExecutionMode(str, Enum):
    """Command execution mode."""

    INTERACTIVE = "interactive"
    AUTO = "auto"


class ConfirmationLevel(str, Enum):
    """Confirmation level for destructive operations."""

    CRITICAL = "critical"  # Only confirm critical operations
    STANDARD = "standard"  # Confirm standard operations
    VERBOSE = "verbose"  # Confirm all operations


class AuthMethod(str, Enum):
    """SSH authentication method."""

    KEY = "key"
    PASSWORD = "password"
    AGENT = "agent"


# =============================================================================
# Sub-Models
# =============================================================================

if PYDANTIC_AVAILABLE:

    class ExecutionConfig(BaseModel):
        """Execution mode configuration."""

        mode: ExecutionMode = ExecutionMode.INTERACTIVE
        confirmation_level: ConfirmationLevel = ConfirmationLevel.STANDARD
        auto_confirm_safe: bool = False
        timeout_seconds: int = Field(default=60, ge=1, le=3600)

    class AIModelSlotConfig(BaseModel):
        """Per-slot (small/big/coder_big) model configuration."""

        provider: str = ""
        model: str = ""
        base_url: str | None = None
        api_key: str | None = None
        defaults: dict[str, Any] = Field(default_factory=dict)

        model_config = ConfigDict(extra="allow")

    class AIModelsConfig(BaseModel):
        """Container for the three model slots."""

        small: AIModelSlotConfig = Field(default_factory=AIModelSlotConfig)
        big: AIModelSlotConfig = Field(default_factory=AIModelSlotConfig)
        coder_big: AIModelSlotConfig = Field(default_factory=AIModelSlotConfig)

        model_config = ConfigDict(extra="allow")

    class AIRoutingConfig(BaseModel):
        """Hybrid 3-tier model routing configuration.

        Supports both the new 'models' schema and legacy flat keys.
        """

        enabled: bool = False
        mode: str = Field(
            default="single",
            pattern=r"^(single|heuristic|router|rules_then_fallback|router_llm_json)$",
        )
        prefer_local: bool = True
        fallback_enabled: bool = True
        # New: nested model slots
        models: AIModelsConfig | None = None
        # Legacy flat keys (backward compat)
        small_model: str = ""
        big_model: str = ""
        small_provider: str = "ollama"
        big_provider: str = "ollama"
        small_max_tokens: int = Field(default=200, ge=1, le=128000)
        big_max_tokens: int = Field(default=800, ge=1, le=128000)
        small_temperature: float = Field(default=0.6, ge=0.0, le=2.0)
        big_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
        small_ctx: int = Field(default=2048, ge=256, le=131072)
        big_ctx: int = Field(default=4096, ge=256, le=131072)
        router_model: str | None = None
        router_max_tokens: int = Field(default=80, ge=1, le=1000)

        model_config = ConfigDict(extra="allow")

    class AIConfig(BaseModel):
        """AI/LLM configuration."""

        default_provider: str | None = None
        model_preference: list[str] = Field(
            default_factory=lambda: [
                "deepseek/deepseek-coder-33b-instruct",
                "google/gemini-flash-1.5",
                "qwen/qwen-2.5-72b-instruct",
            ]
        )
        temperature: float = Field(default=0.7, ge=0.0, le=2.0)
        max_tokens: int = Field(default=4096, ge=1, le=128000)
        api_key: str | None = None  # Should use ${ENV_VAR} syntax
        routing: AIRoutingConfig = Field(default_factory=AIRoutingConfig)

        @field_validator("api_key")
        @classmethod
        def check_api_key_security(cls, v: str | None) -> str | None:
            """Warn if API key appears to be hardcoded."""
            if v and not v.startswith("${") and len(v) > 20:
                import warnings

                warnings.warn(
                    "API key appears to be hardcoded. "
                    "Consider using environment variables: ${OPENROUTER_API_KEY}"
                )
            return v

    class TunnelConfig(BaseModel):
        """SSH tunnel configuration."""

        auto_cleanup: bool = True
        port_range: tuple[int, int] = Field(default=(3307, 3399))
        default_timeout: int = Field(default=300, ge=30, le=86400)

        @field_validator("port_range")
        @classmethod
        def validate_port_range(cls, v):
            """Ensure port range is valid."""
            if isinstance(v, (list, tuple)) and len(v) == 2:
                start, end = v
                if not (1024 <= start < end <= 65535):
                    raise ValueError(
                        f"Port range must be between 1024-65535, got {start}-{end}"
                    )
                return (start, end)
            raise ValueError("Port range must be a tuple of (start, end)")

    class LoggingConfig(BaseModel):
        """Logging configuration."""

        level: LogLevel = LogLevel.INFO
        file_enabled: bool = True
        console_enabled: bool = True
        redact_sensitive: bool = True
        max_file_size_mb: int = Field(default=10, ge=1, le=100)
        backup_count: int = Field(default=5, ge=1, le=20)

    class MemoryConfig(BaseModel):
        """Memory system configuration."""

        enabled: bool = True
        index_on_startup: bool = False
        embedding_provider: str = "openai"
        embedding_model: str = "text-embedding-3-small"
        batch_size: int = Field(default=100, ge=1, le=1000)
        max_chunks_per_file: int = Field(default=500, ge=10, le=5000)
        api_snapshot_policies: dict[str, Any] = Field(
            default_factory=dict,
            description=(
                "Per-tool snapshot storage policies. "
                "Keys are tool names, values are {store: bool, retention: str}. "
                "Example: {'infra.metrics.node_status': {'store': true, 'retention': '24h'}}"
            ),
        )

    class GatewayConfig(BaseModel):
        """Gateway configuration."""

        enabled: bool = False
        port: int = Field(default=8765, ge=1024, le=65535)
        host: str = "127.0.0.1"
        require_auth: bool = True
        allowed_origins: list[str] = Field(default_factory=list)

    class DeckConfig(BaseModel):
        """Deck Mini App configuration (tightly coupled to Telegram bot)."""

        enabled: bool = True
        port: int = Field(default=3080, ge=1024, le=65535)
        bind: str = "127.0.0.1"
        static_dir: str | None = Field(
            default=None,
            description="Override path to Deck SPA build (auto-detected if None)",
        )
        auth_max_age: int = Field(
            default=3600,
            ge=60,
            le=86400,
            description="Max age for Telegram initData auth_date (seconds)",
        )
        dev_mode: bool = Field(
            default=False,
            description="Allow X-Telegram-User fallback header (DISABLE in production)",
        )

    class TelegramConfig(BaseModel):
        """Telegram bot configuration."""

        model_config = ConfigDict(extra="allow")

        bot_token: str | None = None
        allowed_users: list[int] = Field(default_factory=list)
        allowed_groups: list[int] = Field(default_factory=list)
        require_auth: bool = True
        session_isolation: bool = True
        group_activation_mode: str = "mention"
        deck_url: str | None = None

    class ToolsConfig(BaseModel):
        """Tool Router & Registry configuration."""

        enabled: bool = True
        max_calls_per_turn: int = Field(default=10, ge=1, le=50)
        blocked_tools: list[str] = Field(
            default_factory=list,
            description="Tool names to block entirely (e.g. ['code_sandbox'])",
        )
        require_confirmation: list[str] = Field(
            default_factory=list,
            description="Tool names requiring human confirmation before execution",
        )
        enabled_domains: list[str] = Field(
            default_factory=lambda: ["web", "image", "code", "system", "data"],
            description="Enabled tool domains",
        )
        safety_mode: str = Field(
            default="standard",
            pattern=r"^(permissive|standard|strict)$",
            description="Safety policy: permissive|standard|strict",
        )

        model_config = ConfigDict(extra="allow")

    # =============================================================================
    # Main Config Models
    # =============================================================================

    class GlobalConfig(BaseModel):
        """
        Global NAVIG configuration schema.

        Located at: ~/.navig/config.yaml
        """

        # Config Version
        version: str = "1.0"

        # API Keys (should use env vars)
        openrouter_api_key: str | None = Field(
            default=None, description="OpenRouter API key (use ${OPENROUTER_API_KEY})"
        )

        # Default server
        default_server: str | None = Field(
            default=None, description="Default server to use when none specified"
        )

        # Logging
        log_level: LogLevel = LogLevel.INFO
        logging: LoggingConfig = Field(default_factory=LoggingConfig)

        # Execution
        execution: ExecutionConfig = Field(default_factory=ExecutionConfig)

        # AI Configuration
        ai: AIConfig = Field(default_factory=AIConfig)
        ai_model_preference: list[str] | None = None  # Legacy field

        # Tunnel
        tunnel_auto_cleanup: bool = True
        tunnel_port_range: tuple[int, int] = (3307, 3399)
        tunnel: TunnelConfig = Field(default_factory=TunnelConfig)

        # Memory
        memory: MemoryConfig = Field(default_factory=MemoryConfig)

        # Gateway
        gateway: GatewayConfig = Field(default_factory=GatewayConfig)

        # Telegram
        telegram: TelegramConfig = Field(default_factory=TelegramConfig)

        # Deck (Telegram Mini App — lifecycle tied to Telegram bot)
        deck: DeckConfig = Field(default_factory=DeckConfig)

        # Tools (Tool Router & Registry)
        tools: ToolsConfig = Field(default_factory=ToolsConfig)

        # Advanced
        debug_mode: bool = False
        allow_insecure: bool = False

        @model_validator(mode="after")
        def handle_legacy_fields(self):
            """Migrate legacy field names."""
            # Migrate ai_model_preference to ai.model_preference
            if self.ai_model_preference and not self.ai.model_preference:
                self.ai.model_preference = self.ai_model_preference
            return self

        @field_validator("openrouter_api_key")
        @classmethod
        def check_openrouter_key(cls, v):
            """Validate OpenRouter API key format."""
            if v and not v.startswith("${"):
                if not v.startswith("sk-or-"):
                    import warnings

                    warnings.warn("OpenRouter API keys typically start with 'sk-or-'")
            return v

    class HostConfig(BaseModel):
        """
        Host/Server configuration schema.

        Located at: ~/.navig/hosts/<name>.yaml
        """

        # Connection
        hostname: str = Field(..., description="SSH hostname or IP")
        port: int = Field(default=22, ge=1, le=65535)
        username: str = Field(..., description="SSH username")

        # Authentication
        auth_method: AuthMethod = AuthMethod.KEY
        ssh_key: str | None = Field(default=None, description="Path to SSH private key")
        password: str | None = Field(
            default=None, description="SSH password (use ${ENV_VAR} or key auth)"
        )

        # Display
        display_name: str | None = None
        description: str | None = None
        tags: list[str] = Field(default_factory=list)

        # Connection settings
        connect_timeout: int = Field(default=30, ge=5, le=300)
        keepalive_interval: int = Field(default=30, ge=0, le=300)

        # Features
        allow_agent_forwarding: bool = False
        default_working_dir: str | None = None

        # OS/Platform
        os_type: str = "linux"

        @field_validator("ssh_key")
        @classmethod
        def validate_ssh_key(cls, v):
            """Validate SSH key path exists."""
            if v and not v.startswith("${"):
                expanded = Path(v).expanduser()
                if not expanded.exists():
                    import logging

                    logging.getLogger("navig.config").debug(
                        f"SSH key not found: {expanded}"
                    )
            return v

        @field_validator("password")
        @classmethod
        def warn_hardcoded_password(cls, v):
            """Warn about hardcoded passwords."""
            if v and not v.startswith("${"):
                import warnings

                warnings.warn(
                    "Hardcoded password detected. "
                    "Consider using SSH keys or ${SSH_PASSWORD} syntax."
                )
            return v

        @model_validator(mode="after")
        def validate_auth(self):
            """Ensure at least one auth method is configured."""
            if self.auth_method == AuthMethod.KEY and not self.ssh_key:
                import warnings

                warnings.warn(
                    "Key auth selected but no ssh_key specified. "
                    "Will try default locations (~/.ssh/id_rsa, etc.)"
                )
            if self.auth_method == AuthMethod.PASSWORD and not self.password:
                raise ValueError("Password auth selected but no password specified")
            return self


# =============================================================================
# Validation Functions
# =============================================================================


class ConfigValidationError(Exception):
    """Configuration validation error with detailed context."""

    def __init__(self, errors: list[dict[str, Any]], config_type: str = "config"):
        self.errors = errors
        self.config_type = config_type

        # Build user-friendly message
        messages = []
        for error in errors:
            loc = ".".join(str(x) for x in error.get("loc", []))
            msg = error.get("msg", "Unknown error")
            messages.append(f"  - {loc}: {msg}")

        super().__init__(f"Invalid {config_type}:\n" + "\n".join(messages))


def validate_global_config(
    raw: dict[str, Any], strict: bool = False
) -> GlobalConfig | None:
    """
    Validate global configuration.

    Args:
        raw: Raw configuration dictionary
        strict: If True, raise on validation errors. If False, return None.

    Returns:
        Validated GlobalConfig or None if validation fails and not strict

    Raises:
        ConfigValidationError: If strict=True and validation fails
    """
    if not PYDANTIC_AVAILABLE:
        # Pydantic not installed - return raw dict as-is
        import warnings

        warnings.warn(
            "Pydantic not installed. Config validation disabled. "
            "Install with: pip install pydantic"
        )
        return None

    try:
        return GlobalConfig.model_validate(raw)
    except PydanticValidationError as e:
        if strict:
            raise ConfigValidationError(e.errors(), "global config") from e
        return None


def validate_host_config(
    raw: dict[str, Any], host_name: str = "host", strict: bool = False
) -> HostConfig | None:
    """
    Validate host configuration.

    Args:
        raw: Raw configuration dictionary
        host_name: Name of the host (for error messages)
        strict: If True, raise on validation errors.

    Returns:
        Validated HostConfig or None if validation fails

    Raises:
        ConfigValidationError: If strict=True and validation fails
    """
    if not PYDANTIC_AVAILABLE:
        return None

    try:
        return HostConfig.model_validate(raw)
    except PydanticValidationError as e:
        if strict:
            raise ConfigValidationError(e.errors(), f"host config ({host_name})") from e
        return None


def get_config_schema(config_type: str = "global") -> dict[str, Any] | None:
    """
    Get JSON schema for configuration.

    Useful for documentation and IDE autocompletion.

    Args:
        config_type: "global" or "host"

    Returns:
        JSON Schema dict or None if Pydantic not available
    """
    if not PYDANTIC_AVAILABLE:
        return None

    if config_type == "global":
        return GlobalConfig.model_json_schema()
    elif config_type == "host":
        return HostConfig.model_json_schema()
    else:
        raise ValueError(f"Unknown config type: {config_type}")


def validate_config_dict(
    config: dict[str, Any], show_warnings: bool = True
) -> tuple[bool, list[str]]:
    """
    Quick validation check that returns issues as strings.

    Useful for CLI feedback without raising exceptions.

    Args:
        config: Configuration dictionary to validate
        show_warnings: Include warnings in output

    Returns:
        Tuple of (is_valid, list of issue strings)
    """
    issues = []

    if not PYDANTIC_AVAILABLE:
        return True, ["Pydantic not installed - validation skipped"]

    try:
        GlobalConfig.model_validate(config)
        return True, []
    except PydanticValidationError as e:
        for error in e.errors():
            loc = ".".join(str(x) for x in error.get("loc", []))
            msg = error.get("msg", "Unknown error")
            issues.append(f"{loc}: {msg}")
        return False, issues
