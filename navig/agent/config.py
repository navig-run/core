"""
Agent Configuration System

Manages agent mode configuration with support for:
- Multiple personality profiles
- Component-specific settings
- Environment variable substitution
- Default fallbacks
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


def _substitute_env_vars(value: Any) -> Any:
    """Substitute ${VAR} patterns with environment variables."""
    if isinstance(value, str):
        pattern = r'\$\{([^}]+)\}'

        def replacer(match):
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))

        return re.sub(pattern, replacer, value)
    elif isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    return value


@dataclass
class BrainConfig:
    """Brain (AI) configuration."""

    model: str = "openrouter:google/gemini-2.5-flash"
    temperature: float = 0.7
    max_tokens: int = 4096
    reasoning_enabled: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BrainConfig':
        return cls(
            model=data.get('model', cls.model),
            temperature=data.get('temperature', cls.temperature),
            max_tokens=data.get('max_tokens', cls.max_tokens),
            reasoning_enabled=data.get('reasoning_enabled', cls.reasoning_enabled),
        )


@dataclass
class EyesConfig:
    """Eyes (monitoring) configuration."""

    monitoring_interval: int = 60  # seconds
    disk_threshold: int = 85  # percent
    memory_threshold: int = 90
    cpu_threshold: int = 80
    log_paths: List[str] = field(default_factory=list)
    watch_paths: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EyesConfig':
        return cls(
            monitoring_interval=data.get('monitoring_interval', cls.monitoring_interval),
            disk_threshold=data.get('disk_threshold', cls.disk_threshold),
            memory_threshold=data.get('memory_threshold', cls.memory_threshold),
            cpu_threshold=data.get('cpu_threshold', cls.cpu_threshold),
            log_paths=data.get('log_paths', []),
            watch_paths=data.get('watch_paths', []),
        )


@dataclass
class TelegramConfig:
    """Telegram integration configuration."""

    enabled: bool = False
    bot_token: Optional[str] = None
    allowed_users: List[int] = field(default_factory=list)
    admin_users: List[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TelegramConfig':
        return cls(
            enabled=data.get('enabled', False),
            bot_token=data.get('bot_token'),
            allowed_users=data.get('allowed_users', []),
            admin_users=data.get('admin_users', []),
        )


@dataclass
class MCPConfig:
    """MCP server configuration."""

    enabled: bool = True
    port: int = 8765
    host: str = "127.0.0.1"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MCPConfig':
        return cls(
            enabled=data.get('enabled', True),
            port=data.get('port', cls.port),
            host=data.get('host', cls.host),
        )


@dataclass
class WebhooksConfig:
    """Webhook receiver configuration."""

    enabled: bool = False
    port: int = 9000
    host: str = "127.0.0.1"
    secret: Optional[str] = None
    endpoints: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WebhooksConfig':
        return cls(
            enabled=data.get('enabled', False),
            port=data.get('port', cls.port),
            host=data.get('host', cls.host),
            secret=data.get('secret'),
            endpoints=data.get('endpoints', {}),
        )


@dataclass
class EmailAccountConfig:
    """Single email account configuration."""

    enabled: bool = True
    provider: str = "gmail"  # gmail, outlook, fastmail, imap
    address: str = ""
    password: str = ""  # App password (use env var substitution: ${VAR})
    label: str = ""  # Friendly label: personal, work, etc.
    category: str = ""  # Category for filtering
    imap_host: Optional[str] = None  # Only for generic IMAP
    smtp_host: Optional[str] = None
    imap_port: int = 993
    smtp_port: int = 465
    check_interval: int = 60  # seconds between checks

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        d: Dict[str, Any] = {
            'enabled': self.enabled,
            'provider': self.provider,
            'address': self.address,
            'password': self.password,
            'label': self.label,
            'category': self.category,
            'check_interval': self.check_interval,
        }
        if self.imap_host:
            d['imap_host'] = self.imap_host
        if self.smtp_host:
            d['smtp_host'] = self.smtp_host
        if self.imap_port != 993:
            d['imap_port'] = self.imap_port
        if self.smtp_port != 465:
            d['smtp_port'] = self.smtp_port
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmailAccountConfig':
        return cls(
            enabled=data.get('enabled', True),
            provider=data.get('provider', 'gmail'),
            address=data.get('address', ''),
            password=data.get('password', ''),
            label=data.get('label', ''),
            category=data.get('category', ''),
            imap_host=data.get('imap_host'),
            smtp_host=data.get('smtp_host'),
            imap_port=data.get('imap_port', 993),
            smtp_port=data.get('smtp_port', 465),
            check_interval=data.get('check_interval', 60),
        )


@dataclass
class EarsConfig:
    """Ears (input listeners) configuration."""

    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    webhooks: WebhooksConfig = field(default_factory=WebhooksConfig)
    email_accounts: List[EmailAccountConfig] = field(default_factory=list)
    api_enabled: bool = True
    api_port: int = 8790

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EarsConfig':
        email_list = data.get('email_accounts', [])
        accounts = [EmailAccountConfig.from_dict(a) for a in email_list]
        return cls(
            telegram=TelegramConfig.from_dict(data.get('telegram', {})),
            mcp=MCPConfig.from_dict(data.get('mcp', {})),
            webhooks=WebhooksConfig.from_dict(data.get('webhooks', {})),
            email_accounts=accounts,
            api_enabled=data.get('api_enabled', True),
            api_port=data.get('api_port', cls.api_port),
        )


@dataclass
class HandsConfig:
    """Hands (execution) configuration."""

    command_timeout: int = 300  # seconds
    require_confirmation: List[str] = field(default_factory=lambda: [
        'restart', 'delete', 'drop', 'rm -rf', 'shutdown', 'reboot',
        'systemctl stop', 'kill', 'pkill', 'truncate',
    ])
    safe_mode: bool = True  # Extra safety checks
    sudo_allowed: bool = False
    max_concurrent_commands: int = 5

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HandsConfig':
        return cls(
            command_timeout=data.get('command_timeout', cls.command_timeout),
            require_confirmation=data.get('require_confirmation', cls().require_confirmation),
            safe_mode=data.get('safe_mode', cls.safe_mode),
            sudo_allowed=data.get('sudo_allowed', cls.sudo_allowed),
            max_concurrent_commands=data.get('max_concurrent_commands', cls.max_concurrent_commands),
        )


@dataclass
class HeartConfig:
    """Heart (orchestrator) configuration."""

    heartbeat_interval: int = 300  # seconds
    component_restart_delay: int = 5
    max_restart_attempts: int = 3
    health_check_interval: int = 60

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HeartConfig':
        return cls(
            heartbeat_interval=data.get('heartbeat_interval', cls.heartbeat_interval),
            component_restart_delay=data.get('component_restart_delay', cls.component_restart_delay),
            max_restart_attempts=data.get('max_restart_attempts', cls.max_restart_attempts),
            health_check_interval=data.get('health_check_interval', cls.health_check_interval),
        )


@dataclass
class MemoryConfig:
    """Memory configuration."""

    storage_path: Path = field(default_factory=lambda: Path.home() / '.navig' / 'agent' / 'memory.db')
    max_history_messages: int = 1000
    context_window: int = 50
    enable_embeddings: bool = False

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MemoryConfig':
        storage = data.get('storage', str(cls().storage_path))
        return cls(
            storage_path=Path(storage).expanduser(),
            max_history_messages=data.get('max_history_messages', cls.max_history_messages),
            context_window=data.get('context_window', cls.context_window),
            enable_embeddings=data.get('enable_embeddings', cls.enable_embeddings),
        )


@dataclass
class PersonalityConfig:
    """Personality/Soul configuration."""

    name: str = "NAVIG"
    profile: str = "friendly"  # professional, friendly, witty, paranoid
    system_prompt: str = ""
    behavioral_rules: List[str] = field(default_factory=list)
    emotional_responses: Dict[str, str] = field(default_factory=dict)
    proactive: bool = True
    emoji_enabled: bool = True
    verbosity: str = "normal"  # minimal, normal, verbose

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PersonalityConfig':
        return cls(
            name=data.get('name', cls.name),
            profile=data.get('profile', cls.profile),
            system_prompt=data.get('system_prompt', ''),
            behavioral_rules=data.get('behavioral_rules', []),
            emotional_responses=data.get('emotional_responses', {}),
            proactive=data.get('proactive', cls.proactive),
            emoji_enabled=data.get('emoji_enabled', cls.emoji_enabled),
            verbosity=data.get('verbosity', cls.verbosity),
        )

    @classmethod
    def load_profile(cls, profile_name: str, profiles_dir: Path) -> 'PersonalityConfig':
        """Load a personality profile from file."""
        profile_path = profiles_dir / f"{profile_name}.yaml"

        if not profile_path.exists():
            # Try built-in profiles
            builtin_dir = Path(__file__).parent / 'personalities'
            profile_path = builtin_dir / f"{profile_name}.yaml"

        if profile_path.exists():
            with open(profile_path) as f:
                data = yaml.safe_load(f)
            config = cls.from_dict(data)
            config.profile = profile_name
            return config

        # Return default with profile name
        config = cls()
        config.profile = profile_name
        return config


@dataclass
class AgentConfig:
    """Complete agent configuration."""

    enabled: bool = True
    mode: str = "autonomous"  # autonomous, supervised, observe-only
    workspace: Path = field(default_factory=lambda: Path.home() / '.navig' / 'agent' / 'workspace')

    brain: BrainConfig = field(default_factory=BrainConfig)
    eyes: EyesConfig = field(default_factory=EyesConfig)
    ears: EarsConfig = field(default_factory=EarsConfig)
    hands: HandsConfig = field(default_factory=HandsConfig)
    heart: HeartConfig = field(default_factory=HeartConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    personality: PersonalityConfig = field(default_factory=PersonalityConfig)

    # Resource limits
    max_cpu_percent: float = 10.0
    max_memory_mb: int = 512

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentConfig':
        """Create config from dictionary (with env var substitution)."""
        data = _substitute_env_vars(data)
        agent_data = data.get('agent', data)

        workspace = agent_data.get('workspace', str(cls().workspace))

        return cls(
            enabled=agent_data.get('enabled', True),
            mode=agent_data.get('mode', 'autonomous'),
            workspace=Path(workspace).expanduser(),
            brain=BrainConfig.from_dict(agent_data.get('brain', {})),
            eyes=EyesConfig.from_dict(agent_data.get('eyes', {})),
            ears=EarsConfig.from_dict(agent_data.get('ears', {})),
            hands=HandsConfig.from_dict(agent_data.get('hands', {})),
            heart=HeartConfig.from_dict(agent_data.get('heart', {})),
            memory=MemoryConfig.from_dict(agent_data.get('memory', {})),
            personality=PersonalityConfig.from_dict(agent_data.get('personality', {})),
            max_cpu_percent=agent_data.get('max_cpu_percent', cls.max_cpu_percent),
            max_memory_mb=agent_data.get('max_memory_mb', cls.max_memory_mb),
        )

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> 'AgentConfig':
        """Load configuration from ConfigManager."""
        from navig.config import get_config_manager

        # Use ConfigManager to get the 'agent' section from global config
        manager = get_config_manager()
        agent_data = manager.global_config.get('agent', {})

        return cls.from_dict(agent_data)

    def save(self, config_path: Optional[Path] = None) -> None:
        """Save configuration to ConfigManager."""
        from navig.config import get_config_manager

        manager = get_config_manager()
        data = self.to_dict()
        manager.update_global_config({'agent': data})

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'enabled': self.enabled,
            'mode': self.mode,
            'workspace': str(self.workspace),
            'brain': {
                'model': self.brain.model,
                'temperature': self.brain.temperature,
                'max_tokens': self.brain.max_tokens,
            },
            'eyes': {
                'monitoring_interval': self.eyes.monitoring_interval,
                'disk_threshold': self.eyes.disk_threshold,
                'memory_threshold': self.eyes.memory_threshold,
                'cpu_threshold': self.eyes.cpu_threshold,
                'log_paths': self.eyes.log_paths,
            },
            'ears': {
                'telegram': {
                    'enabled': self.ears.telegram.enabled,
                    'allowed_users': self.ears.telegram.allowed_users,
                },
                'mcp': {
                    'enabled': self.ears.mcp.enabled,
                    'port': self.ears.mcp.port,
                },
                'email_accounts': [
                    acct.to_dict() for acct in self.ears.email_accounts
                ],
            },
            'hands': {
                'command_timeout': self.hands.command_timeout,
                'safe_mode': self.hands.safe_mode,
            },
            'heart': {
                'heartbeat_interval': self.heart.heartbeat_interval,
            },
            'personality': {
                'profile': self.personality.profile,
                'name': self.personality.name,
                'proactive': self.personality.proactive,
            },
        }
