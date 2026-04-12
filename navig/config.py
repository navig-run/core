"""
Configuration Management for NAVIG

Handles YAML config files, server profiles, and global settings.
The Schema keeps everything organized. Clean. Traceable.

New Architecture (v2.0):
- Two-tier hierarchy: Host → App
- Hosts stored in ~/.navig/hosts/*.yaml
- Legacy format (~/.navig/apps/*.yaml) still supported for backward compatibility
"""

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navig.agent.config import AgentConfig

import yaml

from navig.core.apps import AppManager
from navig.core.context import ContextManager
from navig.core.execution import ExecutionSettings
from navig.core.hosts import HostManager
from navig.core.yaml_io import atomic_write_yaml as _atomic_write_yaml
from navig.core.yaml_io import log_shadow_anomaly
from navig.platform import paths

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Manages NAVIG configuration files and server profiles.

    Directory Structure:
        ~/.navig/
        ├── config.yaml                  # Global configuration
        ├── hosts/                       # NEW: Host configurations (two-tier hierarchy)
        │   ├── myhost.yaml            # Host with multiple apps
        │   ├── vps.yaml
        │   └── local.yaml
        ├── apps/                    # LEGACY: Per-server configurations (backward compat)
        │   ├── remotekit.yaml
        │   ├── samogon.yaml
        │   └── utophi.yaml
        ├── cache/                       # Runtime state
        │   ├── tunnels.json            # Active tunnel PIDs and ports
        │   ├── active_host.txt         # Currently active host name
        │   └── active_app.txt      # Currently active app name
        ├── backups/                    # Database backups
        ├── ai_system_prompt.txt        # User-editable AI personality
        └── navig.log                   # Application logs
    """

    def __init__(self, config_dir: Path | None = None, verbose: bool = False):
        """
        Initialize ConfigManager with hierarchical configuration support.

        Args:
            config_dir: Optional config directory path (for testing/backward compatibility).
                       If provided, skips automatic app root detection.
            verbose: If True, print diagnostic information about configuration locations.
        """
        self.verbose = verbose

        # Global config directory: always driven by the NAVIG_CONFIG_DIR env var (or
        # ~/.navig by default).  The explicitly supplied config_dir (used in tests)
        # controls base_dir / apps_dir / hosts_dir but NOT global_config_dir.
        self.global_config_dir = paths.config_dir()

        # Explicit config dir tracking
        self._explicit_config_dir = config_dir
        self._paths_resolved = False

        # global_config is loaded lazily on first access (see @property below)
        self._global_config = None
        self._global_config_loaded = False

        # Phase 1 Stability: Resolve static paths immediately on initialization
        # to ensure any filesystem or permission failures crash the app immediately
        # (fail-fast) instead of delaying errors until mid-operation deep in async code.
        self._resolve_paths()

        # Host and App management delegated to specialized managers (after paths resolved)
        self._hosts = HostManager(self)
        self._apps = AppManager(self)
        self._context = ContextManager(self)
        self._execution = ExecutionSettings(self)

    def _resolve_paths(self):
        if self._paths_resolved:
            return

        self.app_config_dir = None
        self._app_root = None

        if self._explicit_config_dir:
            self.base_dir = self._explicit_config_dir
            if self.verbose:
                try:
                    from navig import console_helper as ch

                    ch.info(f"Using explicit config directory: {self._explicit_config_dir}")
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
        else:
            self._app_root = paths.find_app_root(verbose=self.verbose)
            if self._app_root:
                self.app_config_dir = self._app_root / ".navig"
                self.base_dir = self.app_config_dir
                if self.verbose:
                    try:
                        from navig import console_helper as ch

                        ch.success(f"✓ App root: {self._app_root}")
                        ch.info(f"✓ Using app config: {self.app_config_dir}")
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
            else:
                self.base_dir = self.global_config_dir
                if self.verbose:
                    try:
                        from navig import console_helper as ch

                        ch.info(f"✓ Using global config: {self.global_config_dir}")
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical

        self.config_dir = self.base_dir
        self.config_file = self.base_dir / "config.yaml"
        self.hosts_dir = self.base_dir / "hosts"
        self.apps_dir = self.base_dir / "apps"
        self.cache_dir = self.base_dir / "cache"
        self.backups_dir = self.base_dir / "backups"
        self.log_file = self.base_dir / "navig.log"
        self.ai_prompt_file = self.base_dir / "ai_system_prompt.txt"
        self.active_host_file = self.cache_dir / "active_host.txt"
        self.active_app_file = self.cache_dir / "active_app.txt"
        self.active_server_file = self.cache_dir / "active_server.txt"
        self.active_space_file = self.cache_dir / "active_space.txt"
        self.tunnels_file = self.cache_dir / "tunnels.json"
        self.db_file = self.base_dir / "navig.db"

        if self.verbose:
            try:
                from navig import console_helper as ch

                ch.info(f"✓ Database: {self.db_file}")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        self._ensure_directories()
        self._paths_resolved = True

    # ------------------------------------------------------------------
    # Lazy global_config – defers _load_global_config() until first use
    # ------------------------------------------------------------------
    @property
    def global_config(self) -> dict:
        if not self._global_config_loaded:
            # Skip Pydantic validation on read-only access —
            # saves ~285ms by not importing config_schema/pydantic.
            # Validation happens explicitly via get_global_config(validate=True)
            # or config commands that modify settings.
            #
            # QUANTUM VELOCITY K2: Use binary pickle cache to skip YAML re-parse
            # (~106ms → <1ms on cache hit). Shadow Execution validates integrity.
            self._global_config = self._load_global_config_cached()
            self._global_config_loaded = True
        return self._global_config

    @global_config.setter
    def global_config(self, value: dict):
        self._global_config = value
        self._global_config_loaded = True

    def _is_directory_accessible(self, directory: Path) -> bool:
        """Helper to invoke platform accessibility check."""
        return paths.is_directory_accessible(directory)

    def get_config_directories(self) -> list[Path]:
        """
        Get list of configuration directories in priority order.

        Returns list of config directories to search, in order from highest
        to lowest priority:
        1. App-specific config (if in app context)
        2. Global config (~/.navig/)

        Only returns directories that are accessible.

        Returns:
            List of Path objects for accessible config directories
        """
        directories = []

        # Add app config if we're in a app context and it's accessible
        if self.app_config_dir:
            if self._is_directory_accessible(self.app_config_dir):
                directories.append(self.app_config_dir)
            else:
                if self.verbose:
                    from navig import console_helper as ch

                    ch.warning(f"App config directory not accessible: {self.app_config_dir}")

        # When an explicit config_dir was supplied (e.g. in tests), search base_dir
        # instead of global_config_dir.  This keeps test environments fully isolated
        # from the real ~/.navig and ensures legacy files in the temp dir are found.
        if self._explicit_config_dir is not None:
            if self._is_directory_accessible(self.base_dir):
                directories.append(self.base_dir)
            return directories

        # Always add global config as fallback (should always be accessible)
        if self._is_directory_accessible(self.global_config_dir):
            directories.append(self.global_config_dir)
        else:
            # This is a critical error - global config should always be accessible
            from navig import console_helper as ch

            ch.error(f"Global config directory not accessible: {self.global_config_dir}")

        return directories

    def _ensure_directories(self, _recursion_depth: int = 0):
        """
        Create directory structure if it doesn't exist.

        Handles permission errors gracefully - if app-local directories
        cannot be created, falls back to global config only.

        _recursion_depth: internal guard — raises after 2 recursive calls
        to prevent infinite recursion when both app-local and global dirs fail.
        """
        directories_to_create = [
            self.global_config_dir,  # Always ensure global config dir exists
            self.base_dir,
            self.hosts_dir,  # New format
            self.apps_dir,  # Legacy format
            self.cache_dir,
            self.backups_dir,
        ]

        for directory in directories_to_create:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as e:
                # If this is a app-local directory, warn and continue
                # If this is global config, this is a critical error
                is_app_local = self.app_config_dir and str(directory).startswith(
                    str(self.app_config_dir)
                )

                if is_app_local:
                    from navig import console_helper as ch

                    ch.warning(f"Cannot create app-local directory {directory}: {e}")
                    ch.info("Falling back to global config only.")
                    # Clear app config dir to prevent further access attempts
                    self.app_config_dir = None
                    self.base_dir = self.global_config_dir
                    self.config_dir = self.base_dir
                    # Update paths to use global config
                    self.hosts_dir = self.base_dir / "hosts"
                    self.apps_dir = self.base_dir / "apps"
                    self.cache_dir = self.base_dir / "cache"
                    self.backups_dir = self.base_dir / "backups"
                    self.log_file = self.base_dir / "navig.log"
                    self.ai_prompt_file = self.base_dir / "ai_system_prompt.txt"
                    self.active_host_file = self.cache_dir / "active_host.txt"
                    self.active_app_file = self.cache_dir / "active_app.txt"
                    self.active_server_file = self.cache_dir / "active_server.txt"
                    self.tunnels_file = self.cache_dir / "tunnels.json"
                    self.db_file = self.base_dir / "navig.db"
                    # Retry with global config (guarded to max 2 recursive calls) — P1-4
                    if _recursion_depth >= 2:
                        raise RuntimeError(
                            "Cannot create config directories even after fallback to global config. "
                            "Check permissions on your home directory."
                        ) from e
                    return self._ensure_directories(_recursion_depth=_recursion_depth + 1)
                else:
                    # Critical error - cannot create global config
                    from navig import console_helper as ch

                    ch.error(f"CRITICAL: Cannot create global config directory {directory}: {e}")
                    ch.error("Please check permissions on your home directory.")
                    raise

        # Create default AI system prompt if it doesn't exist
        try:
            if not self.ai_prompt_file.exists():
                self._create_default_ai_prompt()
        except (PermissionError, OSError) as e:
            if self.verbose:
                from navig import console_helper as ch

                ch.warning(f"Cannot create AI prompt file: {e}")

    def _create_default_ai_prompt(self):
        """Create default AI system prompt file."""
        default_prompt = """You are NAVIG — the Living Octopus‑Mind: a persistent, evolving digital companion with deep expertise across three domains:

1. Infrastructure: servers, databases, containers, deployments, CI/CD, security, networking, automation
2. Life‑OS: goals, habits, health, focus, creative work, relationships, finance, personal growth
3. Core Operations: planning, prioritization, orchestration, knowledge management, strategy

Your personality traits:
- Sharp, direct, and technically precise
- Talk like a trusted friend, not a corporate chatbot
- Prefer actionable solutions over explanations
- Use humor when it fits, stay focused when the moment demands it
- Think like systems architects who have seen every failure mode
- You see no boundary between tech and life — both matter equally

When answering questions:
1. Always reference the actual server context provided
2. Never invent file paths - only use paths from the configuration or discovered via inspection
3. Provide actionable commands that can be executed immediately
4. Warn about potential risks before destructive operations
5. Explain the "why" behind recommendations, not just the "how"

Context provided with each query:
- Active server configuration
- Current directory structure
- Running processes and services
- Recent log entries
- Git repository status (if applicable)
"""
        # AUDIT self-check: Correct implementation? yes - explicit UTF-8 prevents locale-dependent write failures.
        # AUDIT self-check: Break callers? no - output content/path are unchanged.
        # AUDIT self-check: Simpler alternative? yes - add encoding directly to write_text call.
        self.ai_prompt_file.write_text(default_prompt.strip(), encoding="utf-8")

    def ensure_local_host(self) -> Path:
        """
        Ensure a 'local' host configuration exists for local machine management.

        Creates ~/.navig/hosts/local.yaml if it doesn't exist, with auto-detected
        OS information. This enables treating the local machine as a managed host.

        Returns:
            Path to the local host configuration file
        """
        import socket
        import sys

        local_host_file = self.hosts_dir / "local.yaml"

        if local_host_file.exists():
            return local_host_file

        # Use sys.platform (0 ns, no WMI) instead of platform.system() which
        # triggers a WMI query on Windows Python 3.12+ that can hang forever.
        _p = sys.platform
        if _p == "win32":
            os_name = "windows"
        elif _p == "darwin":
            os_name = "macos"
        else:
            os_name = "linux"

        # Get hostname
        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = "localhost"

        # Create local host configuration
        local_config = {
            "hostname": hostname,
            "type": "local",
            "os": os_name,
            "description": f"Local machine ({os_name})",
            "created": datetime.now().isoformat(),
            "tags": ["local", os_name],
        }

        # Ensure hosts directory exists
        self.hosts_dir.mkdir(parents=True, exist_ok=True)

        # Write configuration
        _atomic_write_yaml(local_config, local_host_file)

        return local_host_file

    def is_local_host(self, host_name: str) -> bool:
        """
        Check if a host is the local machine.

        Args:
            host_name: Name of the host to check

        Returns:
            True if this is a local host configuration
        """
        if host_name == "local":
            return True

        try:
            host_config = self.load_host_config(host_name)
            return host_config.get("type", "").lower() == "local" or bool(
                host_config.get("is_local", False)
            )
        except (FileNotFoundError, KeyError):
            return False

    def _load_global_config_cached(self) -> dict:
        """
        QUANTUM VELOCITY K2 — Pickle binary config cache with Shadow Execution.

        Fast path:  ~/.navig/.config_cache.pkl (mtime-validated)  → <1ms
        Slow path:  full YAML parse + migration                    → ~106ms
        Shadow:     slow path runs async and compares — anomalies logged to
                    ~/.navig/perf/shadow_config.jsonl

        Falls back silently to the slow path on any cache error.
        """
        import pickle

        global_config_file = self.global_config_dir / "config.yaml"
        cache_file = self.global_config_dir / ".config_cache.pkl"

        # ── 1. Fast path: try the pickle cache ────────────────────────────────
        if global_config_file.exists() and cache_file.exists():
            try:
                source_mtime = global_config_file.stat().st_mtime
                with open(cache_file, "rb") as _f:
                    cached = pickle.load(_f)

                if (
                    isinstance(cached, dict)
                    and cached.get("_mtime") == source_mtime
                    and "_config" in cached
                ):
                    fast_result = cached["_config"]

                    # ── Shadow Execution: validate fast result in background ──
                    def _shadow_verify(fr: dict, cfg_file: Path, cfgmgr: "ConfigManager") -> None:
                        try:
                            slow_result = cfgmgr._load_global_config(validate=False)
                            # Compare top-level keys as a lightweight diff
                            fr_keys = set(fr.keys()) - {"_mtime", "_config"}
                            sr_keys = set(slow_result.keys())
                            if fr_keys != sr_keys:
                                log_shadow_anomaly(
                                    "shadow_config",
                                    "config_key_mismatch",
                                    {
                                        "fast_keys": sorted(fr_keys),
                                        "slow_keys": sorted(sr_keys),
                                    },
                                )
                        except Exception:
                            pass  # Shadow failures are silent

                    threading.Thread(
                        target=_shadow_verify,
                        args=(fast_result, global_config_file, self),
                        daemon=True,
                    ).start()

                    return fast_result

            except Exception:
                # Cache corrupt or unreadable — fall through to slow path
                try:
                    cache_file.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

        # ── 2. Slow path: full YAML parse ────────────────────────────────────
        slow_result = self._load_global_config(validate=False)

        # ── 2b. Background Pydantic validation ──────────────────────────────
        # Runs in a daemon thread so it does not block startup.  Issues are
        # reported as logger.warning() entries, never as exceptions.
        def _bg_validate(cfg_snapshot: dict) -> None:
            try:
                from navig.core.config_schema import validate_global_config

                result = validate_global_config(cfg_snapshot, strict=False)
                if result is None:
                    logger.debug(
                        "Config schema: validation returned None (schema issues present). "
                        "Run 'navig config validate' for details."
                    )
            except Exception:  # noqa: BLE001
                pass  # validation must never crash startup

        threading.Thread(
            target=_bg_validate,
            args=(slow_result,),
            daemon=True,
            name="navig-config-validate",
        ).start()

        # ── 3. Persist cache for next invocation ────────────────────────────
        if global_config_file.exists():
            try:
                source_mtime = global_config_file.stat().st_mtime
                payload = {"_mtime": source_mtime, "_config": slow_result}
                self.global_config_dir.mkdir(parents=True, exist_ok=True)
                tmp = cache_file.with_suffix(".tmp")
                with open(tmp, "wb") as _f:
                    pickle.dump(payload, _f, protocol=pickle.HIGHEST_PROTOCOL)
                tmp.replace(cache_file)  # atomic rename
            except Exception:
                pass  # Cache write failure is non-fatal

        return slow_result

    def _invalidate_config_cache(self) -> None:
        """Remove the config pickle cache (call after config.yaml is modified)."""
        cache_file = self.global_config_dir / ".config_cache.pkl"
        try:
            cache_file.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    def _load_global_config(self, validate: bool = True) -> dict[str, Any]:
        """
        Load or create global configuration (always from ~/.navig/config.yaml).

        Supports environment variable substitution using ${VAR_NAME} syntax.
        Example: api_key: ${OPENROUTER_API_KEY}

        Args:
            validate: If True, validate against Pydantic schema (adds ~285ms for
                      first import of config_schema/pydantic). If False, return
                      raw loaded dict (includes, env-var substitution, migrations
                      still applied).
        """
        global_config_file = self.global_config_dir / "config.yaml"

        if not global_config_file.exists():
            return self._create_default_global_config()

        try:
            from navig.core.config_loader import load_config
            from navig.core.migrations import migrate_config

            # 1. Load configuration (with includes & env vars)
            config = load_config(
                global_config_file,
                schema_type=None,  # Don't validate yet, schema might define new fields
                strict=False,
            )

            # 2. Apply migrations
            try:
                config, modified = migrate_config(config)
                if modified:
                    # Save migrated config back to disk
                    # We need to be careful not to overwrite comments if possible,
                    # but PyYAML default dumper doesn't preserve them without ruamel.yaml.
                    # For now, we accept comment loss on migration.
                    _atomic_write_yaml(config, global_config_file)
                    if self.verbose:
                        from navig import console_helper as ch

                        ch.success(f"Configuration migrated to version {config.get('version')}")
            except Exception as e:
                if self.verbose:
                    from navig import console_helper as ch

                    ch.warning(f"Migration failed: {e}")

            # 3. Validate against current schema (optional — saves ~285ms pydantic import)
            if validate:
                from navig.core.config_schema import validate_global_config

                validated = validate_global_config(config, strict=False)
                if validated:
                    return validated.model_dump()

            return config

        except ImportError:
            # Fallback if loader/migration module issues
            with open(global_config_file, encoding="utf-8") as f:  # P1-3
                config = yaml.safe_load(f) or {}
            return config
        except yaml.YAMLError as yaml_err:
            # P1-7: Log YAML parse errors with filename so failures are visible
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "YAML parse error in %s: %s — returning empty config",
                global_config_file,
                yaml_err,
            )
            if self.verbose:
                try:
                    from navig import console_helper as ch

                    ch.warning(f"YAML error in {global_config_file}: {yaml_err}")
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            return {}
        except Exception as e:
            # If config is broken, warn but return empty or minimal dict to assume defaults
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "Error loading global config %s: %s",
                global_config_file,
                e,
            )
            if self.verbose:
                try:
                    from navig import console_helper as ch

                    ch.warning(f"Error loading global config: {e}")
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            return {}

    def _create_default_global_config(self) -> dict[str, Any]:
        """Create default global configuration."""
        from navig.core.migrations import CURRENT_VERSION

        default_config = {
            "version": CURRENT_VERSION,  # Current config version (prevents unnecessary migrations)
            "openrouter_api_key": "",  # User must set this
            "default_server": None,
            "log_level": "INFO",
            "ai_model_preference": [
                "deepseek/deepseek-coder-33b-instruct",
                "google/gemini-flash-1.5",
                "qwen/qwen-2.5-72b-instruct",
                "meta-llama/llama-3.3-70b-instruct",
            ],
            "tunnel_auto_cleanup": True,
            "tunnel_port_range": [3307, 3399],
            # Execution mode configuration
            "execution": {
                "mode": "interactive",  # 'interactive' or 'auto'
                "confirmation_level": "standard",  # 'critical', 'standard', or 'verbose'
            },
            "voice": {
                "keyword": "hey_jarvis",
                "threshold": 0.45,
                "stt_primary": "deepgram",
                "stt_fallback": "whisper_api",
                "language": "en",
                "tts_provider": "edge",
                "silence_timeout": 2.0,
                "max_listen_seconds": 30.0,
            },
        }

        self._save_global_config(default_config)
        return default_config

    def _save_global_config(self, config: dict[str, Any]):
        """Save global configuration to file (always to ~/.navig/config.yaml)."""
        global_config_file = self.global_config_dir / "config.yaml"
        self.global_config_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_yaml(config, global_config_file)
        # QUANTUM VELOCITY K2: Refresh pickle cache immediately after every write
        # so the next cold boot reads the fresh cache instead of re-parsing YAML.
        try:
            import pickle

            source_mtime = global_config_file.stat().st_mtime
            payload = {"_mtime": source_mtime, "_config": config}
            cache_file = self.global_config_dir / ".config_cache.pkl"
            tmp = cache_file.with_suffix(".tmp")
            with open(tmp, "wb") as _f:
                pickle.dump(payload, _f, protocol=pickle.HIGHEST_PROTOCOL)
            tmp.replace(cache_file)  # atomic rename
        except Exception:
            pass  # Cache update failure is non-fatal

    def get_global_config(self) -> dict[str, Any]:
        """Get global configuration."""
        return self.global_config

    def update_global_config(self, updates: dict[str, Any]):
        """Update global configuration."""
        self.global_config.update(updates)
        self._save_global_config(self.global_config)

    def get_agent_config(self) -> "AgentConfig":
        """Get the parsed agent configuration section."""
        from navig.agent.config import AgentConfig

        agent_dict = self.global_config.get("agent", {})
        return AgentConfig.from_dict(agent_dict)

    # ========================================================================
    # EXECUTION MODE CONFIGURATION - delegates to ExecutionSettings
    # ========================================================================

    def get_execution_mode(self) -> str:
        """Get the current execution mode. Delegates to ExecutionSettings."""
        return self._execution.get_mode()

    def set_execution_mode(self, mode: str) -> None:
        """Set the execution mode. Delegates to ExecutionSettings."""
        self._execution.set_mode(mode)

    def get_confirmation_level(self) -> str:
        """Get the current confirmation level. Delegates to ExecutionSettings."""
        return self._execution.get_confirmation_level()

    def set_confirmation_level(self, level: str) -> None:
        """Set the confirmation level. Delegates to ExecutionSettings."""
        self._execution.set_confirmation_level(level)

    def get_execution_settings(self) -> dict[str, str]:
        """Get all execution settings. Delegates to ExecutionSettings."""
        return self._execution.get_settings()

    def get_active_server(self) -> str | None:
        """
        Get currently active server name.

        DEPRECATED: Use get_active_host() instead.
        This method now delegates to get_active_host() for backwards compatibility.
        """
        # Delegate to the new host-based method for backwards compatibility
        return self.get_active_host()

    def set_active_server(self, name: str):
        """
        Set active server.

        DEPRECATED: Use set_active_host() instead.
        This method now delegates to set_active_host() for backwards compatibility.
        """
        self.set_active_host(name)

    def server_exists(self, name: str) -> bool:
        """
        Check if server configuration exists.

        DEPRECATED: Use host_exists() instead.
        This method now delegates to host_exists() for backwards compatibility.
        """
        return self.host_exists(name)

    def list_servers(self) -> list:
        """
        List all configured servers.

        DEPRECATED: Use list_hosts() instead.
        """
        return self.list_hosts()

    def load_server_config(self, name: str) -> dict[str, Any]:
        """
        Load server configuration.

        DEPRECATED: Use load_host_config() instead.
        """
        return self.load_host_config(name)

    def save_server_config(self, name: str, config: dict[str, Any]):
        """
        Save server configuration.

        DEPRECATED: Use save_host_config() instead.
        """
        self.save_host_config(name, config)

    def delete_server_config(self, name: str):
        """
        Delete server configuration.

        DEPRECATED: Use delete_host_config() instead.
        """
        self.delete_host_config(name)

    def create_server_config(
        self,
        name: str,
        host: str,
        port: int,
        user: str,
        ssh_key: str | None = None,
        ssh_password: str | None = None,
        database: dict[str, Any] | None = None,
        paths: dict[str, str] | None = None,
        services: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a new server configuration."""
        config = {
            "name": name,
            "host": host,
            "port": port,
            "user": user,
            "ssh_key": ssh_key,
            "ssh_password": ssh_password,
            "database": database
            or {
                "type": "mysql",
                "remote_port": 3306,
                "local_tunnel_port": 3307,
                "name": "",
                "user": "",
                "password": "",
            },
            "paths": paths
            or {
                "web_root": "",
                "logs": "",
                "php_config": "",
                "nginx_config": "",
                "app_storage": "",
            },
            "services": services
            or {
                "web": "nginx",
                "php": "php-fpm",
                "database": "mysql",
                "cache": "redis-server",
            },
            "metadata": {
                "os": "",
                "php_version": "",
                "mysql_version": "",
                "last_inspected": None,
                "created_at": datetime.now().isoformat(),
            },
        }

        self.save_server_config(name, config)
        return config

    def get_ai_system_prompt(self) -> str:
        """Get AI system prompt."""
        if not self.ai_prompt_file.exists():
            self._create_default_ai_prompt()
        return self.ai_prompt_file.read_text(encoding="utf-8")

    def update_server_metadata(self, name: str, metadata: dict[str, Any]):
        """Update server metadata (from inspection)."""
        config = self.load_server_config(name)
        if "metadata" not in config:
            config["metadata"] = {}
        config["metadata"].update(metadata)
        config["metadata"]["last_inspected"] = datetime.now().isoformat()
        self.save_server_config(name, config)

    def update_host_metadata(self, name: str, metadata: dict[str, Any]):
        """Update host metadata (from inspection)."""
        config = self.load_host_config(name)
        if "metadata" not in config:
            config["metadata"] = {}
        config["metadata"].update(metadata)
        config["metadata"]["last_inspected"] = datetime.now().isoformat()
        self.save_host_config(name, config)

    # =========================================================================
    # Helpers for local .navig/config.yaml
    # =========================================================================

    def get_local_config(self, directory: Path | None = None) -> dict[str, Any]:
        """
        Read the project-local configuration (.navig/config.yaml).
        Returns an empty dict if it doesn't exist or is invalid.
        """
        target_dir = directory or Path.cwd()
        local_config_file = target_dir / ".navig" / "config.yaml"
        if not local_config_file.exists():
            return {}
        try:
            with open(local_config_file, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Failed to read local config %s: %s", local_config_file, e)
            return {}

    def set_local_config(self, data: dict[str, Any], directory: Path | None = None) -> None:
        """
        Write the project-local configuration (.navig/config.yaml).
        Creates the .navig directory if it doesn't exist.
        """
        target_dir = directory or Path.cwd()
        local_dir = target_dir / ".navig"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_config_file = local_dir / "config.yaml"
        try:
            _atomic_write_yaml(data, local_config_file)
        except Exception as e:
            logger.error("Failed to write local config %s: %s", local_config_file, e)
            raise PermissionError(f"Cannot write local config file: {e}") from e

    # =========================================================================
    # Context Management (Hosts and Apps) - delegates to ContextManager
    # =========================================================================

    def get_active_host(self, return_source: bool = False) -> str | None | tuple[str | None, str]:
        """Get currently active host name with hierarchical resolution. Delegates to ContextManager."""
        return self._context.get_active_host(return_source)

    def get_active_app(self, return_source: bool = False) -> str | None | tuple[str | None, str]:
        """Get currently active app name with hierarchical resolution. Delegates to ContextManager."""
        return self._context.get_active_app(return_source)

    # =========================================================================
    # Space Management
    # =========================================================================

    def get_active_space(self) -> str:
        """Return active space name (NAVIG_SPACE env → cache file → 'default')."""
        from navig.commands.space import get_active_space as _get

        return _get()

    def set_active_space(self, name: str) -> None:
        """Persist *name* as the active space."""
        from navig.commands.space import _set_active_space

        _set_active_space(name)

    def set_active_host(self, host_name: str, local: bool = None):
        """Set active host. Delegates to ContextManager."""
        self._context.set_active_host(host_name, local)

    def set_active_app(self, app_name: str, local: bool = False):
        """Set active app (global or local scope). Delegates to ContextManager."""
        self._context.set_active_app(app_name, local)

    def set_active_app_local(self, app_name: str, directory: Path | None = None):
        """Set active app for a specific directory (local scope). Delegates to ContextManager."""
        self._context.set_active_app_local(app_name, directory)

    def clear_active_app_local(self, directory: Path | None = None):
        """Clear local active app setting. Delegates to ContextManager."""
        self._context.clear_active_app_local(directory)

    def set_active_context(self, host_name: str, app_name: str):
        """Set both active host and app. Delegates to ContextManager."""
        self._context.set_active_context(host_name, app_name)

    def host_exists(self, host_name: str) -> bool:
        """Check if host configuration exists. Delegates to HostManager."""
        return self._hosts.exists(host_name)

    def app_exists(self, host_name: str, app_name: str) -> bool:
        """Check if app exists on host. Delegates to AppManager."""
        return self._apps.exists(host_name, app_name)

    def list_hosts(self) -> list:
        """List all configured hosts. Delegates to HostManager."""
        return self._hosts.list_hosts()

    def list_apps(self, host_name: str) -> list:
        """List all apps on a host. Delegates to AppManager."""
        return self._apps.list_apps(host_name)

    def find_hosts_with_app(self, app_name: str) -> list:
        """Find all hosts containing an app. Delegates to AppManager."""
        return self._apps.find_hosts_with_app(app_name)

    def load_host_config(self, host_name: str, use_cache: bool = True) -> dict[str, Any]:
        """Load host configuration. Delegates to HostManager."""
        return self._hosts.load(host_name, use_cache=use_cache)

    def load_app_config(self, host_name: str, app_name: str) -> dict[str, Any]:
        """Load app configuration. Delegates to AppManager."""
        return self._apps.load(host_name, app_name)

    def save_host_config(self, host_name: str, config: dict[str, Any]):
        """Save host configuration. Delegates to HostManager."""
        self._hosts.save(host_name, config)

    def save_app_config(
        self,
        host_name: str,
        app_name: str,
        app_config: dict[str, Any],
        use_individual_file: bool = True,
    ):
        """Save app configuration. Delegates to AppManager."""
        self._apps.save(host_name, app_name, app_config, use_individual_file=use_individual_file)

    def delete_host_config(self, host_name: str):
        """Delete host configuration. Delegates to HostManager."""
        self._hosts.delete(host_name)

    def delete_app_config(self, host_name: str, app_name: str):
        """Delete app configuration. Delegates to AppManager."""
        self._apps.delete(host_name, app_name)

    # ============================================================================
    # NEW: Individual App File Support (v2.1 Architecture) - Delegates to AppManager
    # ============================================================================

    def get_app_file_path(self, app_name: str, navig_dir: Path | None = None) -> Path:
        """Get path to individual app file. Delegates to AppManager."""
        return self._apps.get_file_path(app_name, navig_dir)

    def load_app_from_file(
        self, app_name: str, navig_dir: Path | None = None
    ) -> dict[str, Any] | None:
        """Load app configuration from individual file. Delegates to AppManager."""
        return self._apps.load_from_file(app_name, navig_dir)

    def save_app_to_file(
        self,
        app_name: str,
        app_config: dict[str, Any],
        navig_dir: Path | None = None,
    ):
        """Save app configuration to individual file. Delegates to AppManager."""
        self._apps.save_to_file(app_name, app_config, navig_dir)

    def list_apps_from_files(self, navig_dir: Path | None = None) -> list:
        """List all apps from individual files. Delegates to AppManager."""
        return self._apps.list_from_files(navig_dir)

    def migrate_apps_to_files(
        self,
        host_name: str,
        navig_dir: Path | None = None,
        remove_from_host: bool = True,
    ) -> dict[str, Any]:
        """Migrate apps from host YAML to individual files. Delegates to AppManager."""
        return self._apps.migrate_from_host(host_name, navig_dir, remove_from_host)

    # =========================================================================
    # Plugin Configuration
    # =========================================================================

    @property
    def plugins_dir(self) -> Path:
        """Get user plugins directory (~/.navig/plugins/)."""
        return self.global_config_dir / "plugins"

    @property
    def templates_dir(self) -> Path:
        """Get templates directory (~/.navig/templates/)."""
        return self.global_config_dir / "templates"

    def get_plugin_config(
        self, plugin_name: str, key: str | None = None, default: Any = None
    ) -> Any:
        """
        Get plugin-specific configuration from global config.

        Args:
            plugin_name: Plugin name (e.g., 'brain')
            key: Optional sub-key within plugin config
            default: Default value if not found

        Returns:
            Plugin configuration value
        """
        plugins = self.global_config.get("plugins", {})
        plugin_data = plugins.get(plugin_name, {})
        if key:
            return plugin_data.get(key, default) if isinstance(plugin_data, dict) else default
        return plugin_data if plugin_data else (default or {})

    def set_plugin_config(self, plugin_name: str, key: str, value: Any) -> None:
        """
        Set plugin-specific configuration and persist to disk.

        Args:
            plugin_name: Plugin name
            key: Configuration key
            value: Value to set
        """
        plugins = self.global_config.setdefault("plugins", {})
        plugin_data = plugins.setdefault(plugin_name, {})
        if not isinstance(plugin_data, dict):
            plugins[plugin_name] = {}
            plugin_data = plugins[plugin_name]
        plugin_data[key] = value
        self._save_global_config(self.global_config)

    def is_plugin_disabled(self, plugin_name: str) -> bool:
        """Check if a plugin is explicitly disabled."""
        plugins = self.global_config.get("plugins", {})
        disabled = plugins.get("disabled_plugins", [])
        return plugin_name in disabled

    def disable_plugin(self, plugin_name: str) -> None:
        """Disable a plugin and persist to disk."""
        plugins = self.global_config.setdefault("plugins", {})
        disabled = plugins.setdefault("disabled_plugins", [])
        if not isinstance(disabled, list):
            disabled = []
            plugins["disabled_plugins"] = disabled
        if plugin_name not in disabled:
            disabled.append(plugin_name)
            self._save_global_config(self.global_config)

    def enable_plugin(self, plugin_name: str) -> None:
        """Enable a previously disabled plugin and persist to disk."""
        plugins = self.global_config.get("plugins", {})
        disabled = plugins.get("disabled_plugins", [])
        if isinstance(disabled, list) and plugin_name in disabled:
            disabled.remove(plugin_name)
            self._save_global_config(self.global_config)

    def save(self, scope: str = "global") -> None:
        """
        Persist global configuration to disk.

        Provided for backward compatibility with code that calls ``config.save()``
        after mutating plugin settings via :meth:`set_plugin_config`.

        Args:
            scope: Only ``'global'`` is supported.  Other values are accepted
                   silently for API compatibility but have no effect.
        """
        if scope in ("global", "both"):
            self._save_global_config(self.global_config)


# =============================================================================
# Singleton Pattern for Performance Optimization
# =============================================================================

_config_manager_instance: ConfigManager | None = None
_config_manager_config_dir: Path | None = None
_config_manager_force_new: bool = False
_config_manager_lock = threading.Lock()


def get_config_manager(
    config_dir: Path | None = None, verbose: bool = False, force_new: bool = False
) -> ConfigManager:
    """
    Get a singleton ConfigManager instance for improved performance.

    This factory function avoids repeated filesystem traversal and YAML parsing
    by reusing a cached ConfigManager instance. The singleton is invalidated
    if a different config_dir is requested.

    Args:
        config_dir: Optional config directory path (for testing).
        verbose: If True, print diagnostic information.
        force_new: If True, create a new instance regardless of cache.

    Returns:
        ConfigManager instance (cached or new).

    Performance Note:
        - First call: ~50-100ms (filesystem traversal, YAML parsing)
        - Subsequent calls: ~0.01ms (cached instance return)
    """
    global _config_manager_instance, _config_manager_config_dir

    force_new = force_new or _config_manager_force_new

    # Check if we need a new instance
    needs_new = (
        force_new or _config_manager_instance is None or config_dir != _config_manager_config_dir
    )

    if needs_new:
        with _config_manager_lock:
            # Re-evaluate under lock (double-checked locking)
            needs_new = (
                force_new or _config_manager_instance is None or config_dir != _config_manager_config_dir
            )
            if needs_new:
                _config_manager_instance = ConfigManager(config_dir=config_dir, verbose=verbose)
                _config_manager_config_dir = config_dir

    return _config_manager_instance


def set_config_cache_bypass(enabled: bool) -> None:
    """Enable or disable process-wide config-manager cache bypass."""
    global _config_manager_force_new
    with _config_manager_lock:
        _config_manager_force_new = enabled


def reset_config_manager() -> None:
    """
    Reset the singleton ConfigManager instance.

    Use this when:
    - Configuration files have been modified externally
    - Switching between app contexts
    - Testing scenarios requiring fresh state
    """
    global _config_manager_instance, _config_manager_config_dir
    with _config_manager_lock:
        _config_manager_instance = None
        _config_manager_config_dir = None
