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
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# QUANTUM VELOCITY K2 — Shadow Execution anomaly logger
# Writes JSON-lines to ~/.navig/perf/shadow_config.jsonl when the fast pickle
# cache result diverges from the canonical slow YAML parse.
# ─────────────────────────────────────────────────────────────────────────────
def _log_shadow_anomaly(event_type: str, data: dict) -> None:
    """Append a shadow-execution anomaly to the performance log."""
    try:
        import json
        import time

        perf_dir = Path.home() / ".navig" / "perf"
        perf_dir.mkdir(parents=True, exist_ok=True)
        log_file = perf_dir / "shadow_config.jsonl"
        entry = {"ts": time.time(), "event": event_type, "data": data}
        with open(log_file, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Logging failure must never affect the main code path


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

        # Global config directory (always ~/.navig)
        self.global_config_dir = Path.home() / ".navig"

        # Explicit config dir tracking
        self._explicit_config_dir = config_dir
        self._paths_resolved = False

        # In-memory caches initialized here since they don't depend on paths directly
        self._host_config_cache: dict[str, dict[str, Any]] = {}
        self._app_config_cache: dict[str, dict[str, Any]] = {}
        self._hosts_list_cache: tuple[list, tuple[float, int]] | None = None
        self._apps_list_cache: dict[str, tuple[list, float]] = {}

        # global_config is loaded lazily on first access (see @property below)
        self._global_config = None
        self._global_config_loaded = False

        # Phase 1 Stability: Resolve static paths immediately on initialization
        # to ensure any filesystem or permission failures crash the app immediately
        # (fail-fast) instead of delaying errors until mid-operation deep in async code.
        self._resolve_paths()

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
            self._app_root = self._find_app_root()
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

    def _find_app_root(self) -> Path | None:
        """
        Find app root by searching upward for .navig/ directory.

        Starts from current working directory and searches upward through
        parent directories until .navig/ folder is found or filesystem root
        is reached.

        Returns:
            Path to directory containing .navig/ folder, or None if not found
        """
        current = Path.cwd()

        # Search upward through parent directories
        while True:
            navig_dir = current / ".navig"

            try:
                # Check if .navig/ directory exists and is accessible
                if navig_dir.exists() and navig_dir.is_dir():
                    # Verify we can actually access it
                    if self._is_directory_accessible(navig_dir):
                        return current
                    else:
                        # Directory exists but is not accessible - skip it
                        if self.verbose:
                            from navig import console_helper as ch

                            ch.warning(
                                f"Found .navig at {navig_dir} but cannot access it (permission denied)"
                            )
            except (PermissionError, OSError) as e:
                # Permission error checking directory - skip it
                if self.verbose:
                    from navig import console_helper as ch

                    ch.warning(f"Cannot check {navig_dir}: {e}")

            # Check if we've reached filesystem root
            parent = current.parent
            if parent == current:
                # Reached root without finding accessible .navig/
                return None

            current = parent

    def _is_directory_accessible(self, directory: Path) -> bool:
        """
        Check if a directory is accessible (can read/write).

        Args:
            directory: Path to check

        Returns:
            True if directory is accessible, False otherwise
        """
        try:
            # If the directory doesn't exist yet, try to create it.
            # Fresh installs and test environments commonly start without ~/.navig.
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)

            # Try to list directory contents (basic read access check)
            if directory.is_dir():
                list(directory.iterdir())
                return True
        except (PermissionError, OSError):
            pass  # best-effort cleanup; ignore access/IO errors
        return False

    def _get_config_directories(self) -> list[Path]:
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
        import platform
        import socket

        local_host_file = self.hosts_dir / "local.yaml"

        if local_host_file.exists():
            return local_host_file

        # Auto-detect OS
        os_name = platform.system().lower()
        if os_name == "darwin":
            os_name = "macos"

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
            "description": f"Local machine ({platform.system()} {platform.release()})",
            "created": datetime.now().isoformat(),
            "tags": ["local", os_name],
        }

        # Ensure hosts directory exists
        self.hosts_dir.mkdir(parents=True, exist_ok=True)

        # Write configuration
        with open(local_host_file, "w", encoding="utf-8") as f:
            yaml.dump(local_config, f, default_flow_style=False, sort_keys=False)

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
            return host_config.get("type", "").lower() == "local"
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
                    import threading

                    def _shadow_verify(fr: dict, cfg_file: Path, cfgmgr: "ConfigManager") -> None:
                        try:
                            slow_result = cfgmgr._load_global_config(validate=False)
                            # Compare top-level keys as a lightweight diff
                            fr_keys = set(fr.keys()) - {"_mtime", "_config"}
                            sr_keys = set(slow_result.keys())
                            if fr_keys != sr_keys:
                                _log_shadow_anomaly(
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
                    with open(global_config_file, "w", encoding="utf-8") as f:  # P1-3
                        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
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
        with open(global_config_file, "w", encoding="utf-8") as f:  # P1-3
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
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
    # EXECUTION MODE CONFIGURATION
    # ========================================================================

    def get_execution_mode(self) -> str:
        """
        Get the current execution mode.

        Checks project-local config first, then falls back to global config.

        Returns:
            'interactive' (default) or 'auto'
        """
        # Check project-local config first
        local_config_file = Path.cwd() / ".navig" / "config.yaml"
        if local_config_file.exists():
            try:
                with open(local_config_file, encoding="utf-8") as f:
                    local_config = yaml.safe_load(f) or {}
                    execution = local_config.get("execution", {})
                    if "mode" in execution:
                        return execution["mode"]
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        # Fall back to global config
        execution = self.global_config.get("execution", {})
        return execution.get("mode", "interactive")

    def set_execution_mode(self, mode: str) -> None:
        """
        Set the execution mode.

        Args:
            mode: 'interactive' or 'auto'

        Raises:
            ValueError: If mode is not valid
        """
        valid_modes = ["interactive", "auto"]
        if mode not in valid_modes:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of: {', '.join(valid_modes)}")

        if "execution" not in self.global_config:
            self.global_config["execution"] = {}
        self.global_config["execution"]["mode"] = mode
        self._save_global_config(self.global_config)

    def get_confirmation_level(self) -> str:
        """
        Get the current confirmation level.

        Checks project-local config first, then falls back to global config.

        Returns:
            'critical', 'standard' (default), or 'verbose'
        """
        # Check project-local config first
        local_config_file = Path.cwd() / ".navig" / "config.yaml"
        if local_config_file.exists():
            try:
                with open(local_config_file, encoding="utf-8") as f:
                    local_config = yaml.safe_load(f) or {}
                    execution = local_config.get("execution", {})
                    if "confirmation_level" in execution:
                        return execution["confirmation_level"]
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        # Fall back to global config
        execution = self.global_config.get("execution", {})
        return execution.get("confirmation_level", "standard")

    def set_confirmation_level(self, level: str) -> None:
        """
        Set the confirmation level.

        Args:
            level: 'critical', 'standard', or 'verbose'

        Raises:
            ValueError: If level is not valid
        """
        valid_levels = ["critical", "standard", "verbose"]
        if level not in valid_levels:
            raise ValueError(f"Invalid level '{level}'. Must be one of: {', '.join(valid_levels)}")

        if "execution" not in self.global_config:
            self.global_config["execution"] = {}
        self.global_config["execution"]["confirmation_level"] = level
        self._save_global_config(self.global_config)

    def get_execution_settings(self) -> dict[str, str]:
        """
        Get all execution settings.

        Returns:
            Dict with 'mode' and 'confirmation_level' keys
        """
        execution = self.global_config.get("execution", {})
        return {
            "mode": execution.get("mode", "interactive"),
            "confirmation_level": execution.get("confirmation_level", "standard"),
        }

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
            with open(local_config_file, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        except Exception as e:
            logger.error("Failed to write local config %s: %s", local_config_file, e)
            raise PermissionError(f"Cannot write local config file: {e}") from e

    # =========================================================================
    # Context Management (Hosts and Apps)
    # =========================================================================

    def get_active_host(self, return_source: bool = False) -> str | None | tuple[str | None, str]:
        """
        Get currently active host name with hierarchical resolution.

        Priority:
        1. NAVIG_ACTIVE_HOST environment variable (for CI/CD and scripting)
        2. .navig/config.yaml:active_host (project-local preference)
        3. .navig file (legacy format: host or host:app) - deprecated
        4. ~/.navig/cache/active_host.txt (global cache for quick switching)
        5. default_host from global config (fallback)

        Args:
            return_source: If True, returns tuple (host_name, source) where source is
                          'env', 'local', 'legacy', 'global', 'default', or 'none'

        Returns:
            Active host name or None (or tuple if return_source=True)
        """
        # Priority 1: Check NAVIG_ACTIVE_HOST environment variable
        env_host = os.environ.get("NAVIG_ACTIVE_HOST", "").strip()
        if env_host and self.host_exists(env_host):
            return (env_host, "env") if return_source else env_host

        # Priority 2: Check .navig/config.yaml for project-local active_host
        local_navig_dir = Path.cwd() / ".navig"
        if local_navig_dir.exists() and local_navig_dir.is_dir():
            local_config = self.get_local_config()
            local_host = local_config.get("active_host")
            if local_host and self.host_exists(local_host):
                return (local_host, "project") if return_source else local_host

        # Priority 3: Check for .navig file (legacy format) - deprecated
        local_navig = Path.cwd() / ".navig"
        if local_navig.exists() and local_navig.is_file():
            try:
                content = local_navig.read_text(encoding="utf-8").strip()
                if ":" in content:
                    host_name, _ = content.split(":", 1)
                else:
                    host_name = content

                if host_name and self.host_exists(host_name):
                    return (host_name, "legacy") if return_source else host_name
            except (PermissionError, OSError):
                pass  # best-effort cleanup; ignore access/IO errors

        # Priority 4: Check global cache (set by `navig host use`)
        if self.active_host_file.exists():
            try:
                host_name = self.active_host_file.read_text(encoding="utf-8").strip()
                if host_name and self.host_exists(host_name):
                    return (host_name, "user") if return_source else host_name
            except (PermissionError, OSError):
                pass  # best-effort cleanup; ignore access/IO errors

        # Priority 5: Fall back to default host from global config
        default_host = self.global_config.get("default_host")
        if default_host and self.host_exists(default_host):
            return (default_host, "default") if return_source else default_host

        return (None, "none") if return_source else None

    def get_active_app(self, return_source: bool = False) -> str | None | tuple[str | None, str]:
        """
        Get currently active app name with hierarchical resolution.

        Priority:
        1. NAVIG_ACTIVE_APP environment variable (per-terminal session)
        2. Local active app (.navig/config.yaml in current directory)
        3. .navig file in current directory (legacy format: host:app)
        4. Cached active app (~/.navig/cache/active_app.txt)
        5. Default app from active host config

        Args:
            return_source: If True, returns tuple (app_name, source) where source is 'session', 'local', 'legacy', 'global', or 'default'

        Returns:
            Active app name or None (or tuple if return_source=True)
        """
        # Priority 0: Check NAVIG_ACTIVE_APP environment variable (per-terminal session)
        env_app = os.environ.get("NAVIG_ACTIVE_APP", "").strip()
        if env_app:
            # Validate that env app exists on current host
            active_host = self.get_active_host()
            if active_host and self.app_exists(active_host, env_app):
                return (env_app, "session") if return_source else env_app

        # Priority 1: Check for local active app in .navig/config.yaml
        local_navig_dir = Path.cwd() / ".navig"
        if local_navig_dir.exists() and local_navig_dir.is_dir():
            local_config = self.get_local_config()
            local_app = local_config.get("active_app")
            if local_app:
                # Validate that local app exists on current host
                active_host = self.get_active_host()
                if active_host and self.app_exists(active_host, local_app):
                    return (local_app, "project") if return_source else local_app
                else:
                    # Local app invalid - show warning and fall through to user config
                    if self.verbose:
                        from navig import console_helper as ch

                        ch.warning(
                            f"Project active app '{local_app}' not found on host '{active_host}'",
                            "Falling back to user active app",
                        )

        # Priority 2: Check for .navig file in current directory (legacy format)
        # NOTE: .navig can be either a FILE (legacy) or DIRECTORY (new hierarchical config)
        local_navig = Path.cwd() / ".navig"
        if local_navig.exists() and local_navig.is_file():
            try:
                content = local_navig.read_text(encoding="utf-8").strip()
                if ":" in content:
                    _, app_name = content.split(":", 1)
                    return (app_name, "legacy") if return_source else app_name
            except (PermissionError, OSError):
                # Cannot read .navig file - skip it
                pass

        # Priority 3: Check cached active app (project cache or user cache)
        if self.active_app_file.exists():
            try:
                app_name = self.active_app_file.read_text(encoding="utf-8").strip()
                if app_name:
                    # Determine if this is project or user cache
                    local_navig_dir = Path.cwd() / ".navig"
                    if local_navig_dir.exists() and self.active_app_file.is_relative_to(
                        local_navig_dir
                    ):
                        source = "project"
                    else:
                        source = "user"
                    return (app_name, source) if return_source else app_name
            except (PermissionError, OSError):
                pass  # best-effort cleanup; ignore access/IO errors

        # Priority 4: Auto-detect from project's .navig/apps/ (if only one app exists)
        local_navig_dir = Path.cwd() / ".navig"
        local_apps_dir = local_navig_dir / "apps"
        if local_apps_dir.exists() and local_apps_dir.is_dir():
            host_name = self.get_active_host()
            if host_name:
                local_apps = []
                for app_file in local_apps_dir.glob("*.yaml"):
                    try:
                        with open(app_file) as f:
                            app_data = yaml.safe_load(f) or {}
                        if app_data.get("host") == host_name:
                            local_apps.append(app_file.stem)
                    except Exception:
                        continue
                if len(local_apps) == 1:
                    # Single app in project - use it as the active app
                    return (local_apps[0], "project") if return_source else local_apps[0]

        # Priority 5: Fall back to default app from active host
        host_name = self.get_active_host()
        if host_name:
            try:
                host_config = self.load_host_config(host_name)
                default_app = host_config.get("default_app")
                if default_app:
                    return (default_app, "default") if return_source else default_app
            except FileNotFoundError:
                pass  # file already gone; expected

        return (None, "none") if return_source else None

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
        """
        Set active host.

        Args:
            host_name: Host name to set as active
            local: If True, set in local .navig/config.yaml only
                   If False, set in global cache only
                   If None (default), set in both local (if exists) and global

        Raises:
            ValueError: If host doesn't exist
        """
        if not self.host_exists(host_name):
            raise ValueError(f"Host '{host_name}' not found")

        # Determine if we should update local config
        local_navig_dir = Path.cwd() / ".navig"
        has_local_config = local_navig_dir.exists() and local_navig_dir.is_dir()

        # Update local .navig/config.yaml if applicable
        if has_local_config and local is not False:
            self._set_active_host_local(host_name, local_navig_dir)

        # Update global cache if applicable
        if local is not True:
            self.active_host_file.write_text(host_name, encoding="utf-8")

    def _set_active_host_local(self, host_name: str, local_navig_dir: Path):
        """
        Set active host in local .navig/config.yaml.

        Args:
            host_name: Host name to set as active
            local_navig_dir: Path to the local .navig/ directory
        """
        # Load existing config or create new one
        local_config = self.get_local_config(
            local_navig_dir.parent
        )  # Pass parent dir to get_local_config

        # Set active_host
        local_config["active_host"] = host_name

        # Save local config
        # Write comment header if file is new/empty (this logic is now handled by set_local_config)
        self.set_local_config(local_config, local_navig_dir.parent)

    def set_active_app(self, app_name: str, local: bool = False):
        """
        Set active app (global or local scope).

        Args:
            app_name: App name to set as active
            local: If True, set as local active app (current directory only)
                  If False, set as global active app (default)

        Raises:
            FileNotFoundError: If local=True and .navig/ directory doesn't exist in current directory
            ValueError: If local=True and app doesn't exist on current host

        Note: Global mode does not validate if app exists on active host
        """
        if local:
            self.set_active_app_local(app_name)
        else:
            self.active_app_file.write_text(app_name, encoding="utf-8")

    def set_active_app_local(self, app_name: str, directory: Path | None = None):
        """
        Set active app for a specific directory (local scope).

        Args:
            app_name: Name of the app to set as active
            directory: Directory path (defaults to current working directory)

        Raises:
            FileNotFoundError: If .navig/ directory doesn't exist in target directory
            ValueError: If app_name doesn't exist on current host
        """
        target_dir = directory or Path.cwd()
        local_navig_dir = target_dir / ".navig"

        # Validate .navig/ directory exists
        if not local_navig_dir.exists() or not local_navig_dir.is_dir():
            raise FileNotFoundError(
                f"Cannot set local active app: No .navig/ directory found in {target_dir}\n"
                f"Run 'navig init' first or use 'navig app use {app_name}' without --local flag."
            )

        # Validate app exists on current host
        active_host = self.get_active_host()
        if not active_host:
            raise ValueError(
                "No active host set. Please select a host first with 'navig host use <name>'"
            )

        if not self.app_exists(active_host, app_name):
            raise ValueError(
                f"App '{app_name}' not found on host '{active_host}'\n"
                f"Available apps: {', '.join(self.list_apps(active_host))}"
            )

        # Load or create local config
        local_config = self.get_local_config(target_dir)

        if not local_config:  # If get_local_config returned empty, it means the file didn't exist or was empty/invalid
            local_config = {
                "app": {
                    "name": target_dir.name,
                    "initialized": datetime.now().isoformat(),
                    "version": "1.0",
                }
            }

        # Set active app in local config
        local_config["active_app"] = app_name
        self.set_local_config(local_config, target_dir)

    def clear_active_app_local(self, directory: Path | None = None):
        """
        Clear local active app setting.

        Args:
            directory: Directory path (defaults to current working directory)

        Raises:
            FileNotFoundError: If .navig/ directory doesn't exist in target directory
        """
        target_dir = directory or Path.cwd()
        local_navig_dir = target_dir / ".navig"

        if not local_navig_dir.exists() or not local_navig_dir.is_dir():
            raise FileNotFoundError(
                f"Cannot clear local active app: No .navig/ directory found in {target_dir}"
            )

        local_config_file = local_navig_dir / "config.yaml"
        if not local_config_file.exists():
            return  # Nothing to clear

        local_config = self.get_local_config(target_dir)
        if "active_app" in local_config:
            del local_config["active_app"]
            self.set_local_config(local_config, target_dir)

    def set_active_context(self, host_name: str, app_name: str):
        """
        Set both active host and app.

        Args:
            host_name: Host name to set as active
            app_name: App name to set as active

        Raises:
            ValueError: If host doesn't exist or app doesn't exist on host
        """
        if not self.host_exists(host_name):
            raise ValueError(f"Host '{host_name}' not found")

        if not self.app_exists(host_name, app_name):
            raise ValueError(f"App '{app_name}' not found on host '{host_name}'")

        self.set_active_host(host_name)
        self.set_active_app(app_name)

    def host_exists(self, host_name: str) -> bool:
        """
        Check if host configuration exists in app-specific or global configs.

        Checks both new format (hosts/) and legacy format (apps/) in
        both app-specific and global directories.

        Handles permission errors gracefully.

        Args:
            host_name: Host name to check

        Returns:
            True if host exists, False otherwise
        """
        config_dirs = self._get_config_directories()

        for config_dir in config_dirs:
            try:
                # Check new format
                host_file = config_dir / "hosts" / f"{host_name}.yaml"
                if host_file.exists():
                    return True

                # Check legacy format (backward compatibility)
                legacy_file = config_dir / "apps" / f"{host_name}.yaml"
                if legacy_file.exists():
                    return True
            except (PermissionError, OSError):
                # Skip inaccessible directories
                continue

        return False

    def app_exists(self, host_name: str, app_name: str) -> bool:
        """
        Check if app exists on host (checks both individual files and embedded format).

        Args:
            host_name: Host name
            app_name: App name

        Returns:
            True if app exists on host, False otherwise
        """
        # 1. Check individual files (new format)
        config_dirs = self._get_config_directories()
        for config_dir in config_dirs:
            app_config = self.load_app_from_file(app_name, config_dir)
            if app_config and app_config.get("host") == host_name:
                return True

        # 2. Check embedded format (legacy)
        try:
            host_config = self.load_host_config(host_name)
            return "apps" in host_config and app_name in host_config["apps"]
        except FileNotFoundError:
            return False

    def list_hosts(self) -> list:
        """
        List all configured hosts from both app-specific and global configs.

        Merges hosts from app-specific and global directories, with
        app-specific hosts taking precedence (appearing first) if duplicates exist.

        Handles permission errors gracefully - skips inaccessible directories.
        Uses caching with directory mtime invalidation for performance.

        Returns:
            Sorted list of host names
        """
        # Check cache validity based on hosts directory mtime
        config_dirs = self._get_config_directories()

        # Build a signature from mtimes + file count.
        # Directory mtimes can be too coarse on some platforms (notably Windows),
        # so include YAML file mtimes to reliably detect changes.
        max_mtime = 0.0
        file_count = 0
        for config_dir in config_dirs:
            hosts_dir = config_dir / "hosts"
            apps_dir = config_dir / "apps"
            for d in [hosts_dir, apps_dir]:
                if d.exists():
                    try:
                        max_mtime = max(max_mtime, d.stat().st_mtime)
                    except (OSError, PermissionError):
                        pass

                    try:
                        for yaml_file in d.glob("*.yaml"):
                            file_count += 1
                            try:
                                max_mtime = max(max_mtime, yaml_file.stat().st_mtime)
                            except (OSError, PermissionError):
                                pass
                    except (OSError, PermissionError):
                        pass

        signature = (max_mtime, file_count)

        # Return cached result if still valid
        if self._hosts_list_cache is not None:
            cached_hosts, cached_signature = self._hosts_list_cache
            if cached_signature == signature:
                return cached_hosts.copy()

        hosts = set()

        # Collect hosts from all accessible config directories
        for config_dir in config_dirs:
            try:
                # New format hosts
                hosts_dir = config_dir / "hosts"
                if hosts_dir.exists() and self._is_directory_accessible(hosts_dir):
                    try:
                        for yaml_file in hosts_dir.glob("*.yaml"):
                            hosts.add(yaml_file.stem)
                    except (PermissionError, OSError) as e:
                        if self.verbose:
                            from navig import console_helper as ch

                            ch.warning(f"Cannot read hosts from {hosts_dir}: {e}")

                # Legacy format hosts (backward compatibility)
                # Only include files from apps/ that are actually host configs, not app configs
                # A host config has an IP address in 'host' field, while app config references a host name
                apps_dir = config_dir / "apps"
                if apps_dir.exists() and self._is_directory_accessible(apps_dir):
                    try:
                        for yaml_file in apps_dir.glob("*.yaml"):
                            # Skip backup files
                            if ".backup." not in yaml_file.name:
                                # Check if this is actually a host config (not an app config)
                                try:
                                    with open(yaml_file, encoding="utf-8") as f:
                                        config_data = yaml.safe_load(f) or {}
                                    host_value = config_data.get("host", "")
                                    # It's a legacy host if:
                                    # 1. It has no 'host' field, OR
                                    # 2. The 'host' field looks like an IP address or FQDN (contains dots)
                                    #    AND doesn't reference an existing host in hosts/ folder
                                    if not host_value:
                                        hosts.add(yaml_file.stem)
                                    elif "." in str(host_value):
                                        # Looks like IP or domain, treat as legacy host
                                        hosts.add(yaml_file.stem)
                                    # If host_value is a simple name (no dots), it's an app referencing a host
                                except Exception:
                                    # If we can't read the file, skip it
                                    pass
                    except (PermissionError, OSError) as e:
                        if self.verbose:
                            from navig import console_helper as ch

                            ch.warning(f"Cannot read hosts from {apps_dir}: {e}")
            except (PermissionError, OSError) as e:
                if self.verbose:
                    from navig import console_helper as ch

                    ch.warning(f"Cannot access config directory {config_dir}: {e}")

        result = sorted(list(hosts))
        # Cache the result with current signature
        self._hosts_list_cache = (result.copy(), signature)
        return result

    def list_apps(self, host_name: str) -> list:
        """
        List all apps on a host (supports both individual files and embedded format).

        Args:
            host_name: Host name

        Returns:
            Sorted list of app names

        Raises:
            FileNotFoundError: If host doesn't exist
        """
        apps = set()

        # 1. Get apps from individual files (new format)
        config_dirs = self._get_config_directories()
        for config_dir in config_dirs:
            apps_dir = config_dir / "apps"
            if apps_dir.exists():
                for app_file in apps_dir.glob("*.yaml"):
                    try:
                        with open(app_file) as f:
                            app_data = yaml.safe_load(f) or {}
                        # Only include if this app belongs to the specified host
                        if app_data.get("host") == host_name:
                            apps.add(app_file.stem)
                    except Exception:
                        continue  # Skip invalid files

        # 2. Get apps from host YAML (legacy embedded format)
        try:
            host_config = self.load_host_config(host_name)
            if "apps" in host_config:
                apps.update(host_config["apps"].keys())
        except FileNotFoundError:
            # Host doesn't exist - only return apps from individual files
            pass

        return sorted(list(apps))

    def find_hosts_with_app(self, app_name: str) -> list:
        """
        Find all hosts that contain a specific app.

        Args:
            app_name: App name to search for

        Returns:
            List of host names that contain the app
        """
        hosts_with_app = []

        # Search through all hosts
        for host_name in self.list_hosts():
            try:
                if self.app_exists(host_name, app_name):
                    hosts_with_app.append(host_name)
            except Exception:
                # Skip hosts that can't be loaded
                continue

        return hosts_with_app

    def load_host_config(self, host_name: str, use_cache: bool = True) -> dict[str, Any]:
        """
        Load host configuration with hierarchical support.

        Searches for host configuration in priority order:
        1. App-specific hosts/ directory (if in app context)
        2. Global hosts/ directory
        3. Legacy apps/ directory (backward compatibility)

        Args:
            host_name: Host name
            use_cache: Whether to use cached config (default True)

        Returns:
            Host configuration dictionary

        Raises:
            FileNotFoundError: If host configuration not found
        """
        # Check cache first
        if use_cache and host_name in self._host_config_cache:
            return self._host_config_cache[host_name]

        config_dirs = self._get_config_directories()

        try:
            from navig.core.config_loader import load_config
        except ImportError:
            load_config = None

        # Search in priority order
        for config_dir in config_dirs:
            # Try new format (hosts/)
            host_file = config_dir / "hosts" / f"{host_name}.yaml"
            if host_file.exists():
                if load_config:
                    config = load_config(host_file, schema_type="host", strict=False)
                else:
                    with open(host_file, encoding="utf-8") as f:
                        config = yaml.safe_load(f)

                # Expand user paths (config_loader doesn't do ~/ expansion)
                if "ssh_key" in config and config["ssh_key"]:
                    config["ssh_key"] = os.path.expanduser(config["ssh_key"])

                if self.verbose:
                    from navig import console_helper as ch

                    source = "app" if config_dir == self.app_config_dir else "global"
                    ch.dim(f"✓ Loaded host '{host_name}' from {source} config")

                # Cache the result
                self._host_config_cache[host_name] = config
                return config

            # Try legacy format (apps/)
            legacy_file = config_dir / "apps" / f"{host_name}.yaml"
            if legacy_file.exists():
                if load_config:
                    config = load_config(legacy_file, schema_type="host", strict=False)
                else:
                    with open(legacy_file, encoding="utf-8") as f:
                        config = yaml.safe_load(f)

                # Expand user paths
                if "ssh_key" in config and config["ssh_key"]:
                    config["ssh_key"] = os.path.expanduser(config["ssh_key"])

                if self.verbose:
                    from navig import console_helper as ch

                    source = "app" if config_dir == self.app_config_dir else "global"
                    ch.dim(f"✓ Loaded host '{host_name}' from {source} config (legacy format)")

                # Cache the result
                self._host_config_cache[host_name] = config
                return config

        raise FileNotFoundError(f"Host configuration not found: {host_name}")

    def load_app_config(self, host_name: str, app_name: str) -> dict[str, Any]:
        """
        Load app configuration (supports both individual files and embedded format).

        Priority:
        1. Individual app file (.navig/apps/<name>.yaml) - NEW FORMAT
        2. Embedded in host YAML (host['apps'][name]) - LEGACY FORMAT

        Args:
            host_name: Host name
            app_name: App name

        Returns:
            App configuration dictionary

        Raises:
            FileNotFoundError: If host or app not found
            ValueError: If webserver.type is missing (required field)
        """
        # 1. Try loading from individual file (new format) first
        config_dirs = self._get_config_directories()
        for config_dir in config_dirs:
            app_config = self.load_app_from_file(app_name, config_dir)
            if app_config and app_config.get("host") == host_name:
                # Validate required field
                if "webserver" not in app_config or "type" not in app_config.get("webserver", {}):
                    raise ValueError(
                        f"App '{app_name}' is missing required field 'webserver.type'. "
                        f"Please edit the app configuration and add this field."
                    )
                return app_config

        # 2. Fall back to legacy format (embedded in host YAML)
        host_config = self.load_host_config(host_name)

        # Check if this is legacy format (has 'apps' field)
        if "apps" in host_config:
            # Legacy format: Extract app from host config
            if app_name not in host_config["apps"]:
                raise FileNotFoundError(
                    f"App '{app_name}' not found on host '{host_name}'. "
                    f"Available apps: {', '.join(host_config['apps'].keys())}"
                )

            app_config = host_config["apps"][app_name]

            # Validate webserver.type exists (REQUIRED field)
            if "webserver" not in app_config or "type" not in app_config.get("webserver", {}):
                raise ValueError(
                    f"Missing 'webserver.type' in configuration for app '{app_name}' on host '{host_name}'. "
                    f"Please add 'webserver.type: nginx' or 'webserver.type: apache2' to your app config."
                )

            return app_config
        else:
            # Legacy format: Entire config is the app
            # In legacy format, the host config IS the app config
            # We treat the host_name as both host and app

            # For legacy format, webserver type might be in services.web
            # We don't enforce webserver.type for legacy format (backward compat)
            return host_config

    def save_host_config(self, host_name: str, config: dict[str, Any]):
        """
        Save host configuration.

        Saves to app-specific config if in app context,
        otherwise saves to global config.

        Args:
            host_name: Host name
            config: Host configuration dictionary
        """
        # Invalidate caches
        if host_name in self._host_config_cache:
            del self._host_config_cache[host_name]
        self._hosts_list_cache = None  # Invalidate hosts list cache

        # Determine where to save (app-specific or global)
        if self.app_config_dir:
            host_file = self.app_config_dir / "hosts" / f"{host_name}.yaml"
        else:
            host_file = self.global_config_dir / "hosts" / f"{host_name}.yaml"

        # Add timestamp to metadata
        if "metadata" not in config:
            config["metadata"] = {}
        config["metadata"]["last_updated"] = datetime.now().isoformat()

        # Ensure hosts directory exists
        host_file.parent.mkdir(parents=True, exist_ok=True)

        with open(host_file, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        if self.verbose:
            from navig import console_helper as ch

            location = "app" if self.app_config_dir else "global"
            ch.dim(f"✓ Saved host '{host_name}' to {location} config")

    def save_app_config(
        self,
        host_name: str,
        app_name: str,
        app_config: dict[str, Any],
        use_individual_file: bool = True,
    ):
        """
        Save app configuration (uses individual files by default, legacy embedded format optional).

        Args:
            host_name: Host name
            app_name: App name
            app_config: App configuration dictionary
            use_individual_file: If True (default), save to individual file; if False, use legacy embedded format

        Raises:
            FileNotFoundError: If host doesn't exist
        """
        if use_individual_file:
            # NEW FORMAT: Save to individual file
            # Ensure 'host' field is set
            app_config["host"] = host_name
            app_config["name"] = app_name

            # Use app-specific config dir if available, otherwise global
            navig_dir = self.app_config_dir if self.app_config_dir else self.base_dir
            self.save_app_to_file(app_name, app_config, navig_dir)
        else:
            # LEGACY FORMAT: Save embedded in host YAML
            # Load host configuration
            host_config = self.load_host_config(host_name)

            # Ensure apps field exists
            if "apps" not in host_config:
                host_config["apps"] = {}

            # Update app
            host_config["apps"][app_name] = app_config

            # Save host configuration
            self.save_host_config(host_name, host_config)

    def delete_host_config(self, host_name: str):
        """
        Delete host configuration from app-specific or global config.

        Deletes from app-specific config if it exists there,
        otherwise deletes from global config.

        Args:
            host_name: Host name to delete
        """
        # Invalidate caches
        self._host_config_cache.pop(host_name, None)
        self._hosts_list_cache = None  # Invalidate hosts list cache

        deleted = False
        config_dirs = self._get_config_directories()

        for config_dir in config_dirs:
            # Delete from new format
            host_file = config_dir / "hosts" / f"{host_name}.yaml"
            if host_file.exists():
                host_file.unlink()
                deleted = True
                if self.verbose:
                    from navig import console_helper as ch

                    location = "app" if config_dir == self.app_config_dir else "global"
                    ch.dim(f"✓ Deleted host '{host_name}' from {location} config")
                break  # Delete from first location found only

            # Delete from legacy format (if exists)
            legacy_file = config_dir / "apps" / f"{host_name}.yaml"
            if legacy_file.exists():
                legacy_file.unlink()
                deleted = True
                if self.verbose:
                    from navig import console_helper as ch

                    location = "app" if config_dir == self.app_config_dir else "global"
                    ch.dim(f"✓ Deleted host '{host_name}' from {location} config (legacy format)")
                break  # Delete from first location found only

    def delete_app_config(self, host_name: str, app_name: str):
        """
        Delete app configuration from host (legacy embedded format) or individual file (new format).

        Args:
            host_name: Host name
            app_name: App name to delete

        Raises:
            FileNotFoundError: If host doesn't exist
        """
        # Try deleting from new format (individual file) first
        config_dirs = self._get_config_directories()
        deleted = False

        for config_dir in config_dirs:
            app_file = config_dir / "apps" / f"{app_name}.yaml"
            if app_file.exists():
                # Verify this app belongs to the specified host
                try:
                    with open(app_file) as f:
                        app_data = yaml.safe_load(f) or {}
                    if app_data.get("host") == host_name:
                        app_file.unlink()
                        deleted = True
                        if self.verbose:
                            from navig import console_helper as ch

                            location = "app" if config_dir == self.app_config_dir else "global"
                            ch.dim(
                                f"✓ Deleted app '{app_name}' from {location} config (individual file)"
                            )
                        return
                except Exception:
                    pass  # Continue to legacy format

        # Fall back to legacy format (embedded in host YAML)
        if not deleted:
            host_config = self.load_host_config(host_name)

            # Remove app from embedded format
            if "apps" in host_config and app_name in host_config["apps"]:
                del host_config["apps"][app_name]
                self.save_host_config(host_name, host_config)
                if self.verbose:
                    from navig import console_helper as ch

                    ch.dim(
                        f"✓ Deleted app '{app_name}' from host '{host_name}' (legacy embedded format)"
                    )

    # ============================================================================
    # NEW: Individual App File Support (v2.1 Architecture)
    # ============================================================================

    def get_app_file_path(self, app_name: str, navig_dir: Path | None = None) -> Path:
        """
        Get path to individual app file (.navig/apps/<name>.yaml).

        Args:
            app_name: App name
            navig_dir: Optional .navig directory path (defaults to current working directory)

        Returns:
            Path to app file
        """
        if navig_dir is None:
            # Use app-specific config if available, otherwise global
            navig_dir = self.app_config_dir if self.app_config_dir else self.base_dir

        return navig_dir / "apps" / f"{app_name}.yaml"

    def load_app_from_file(
        self, app_name: str, navig_dir: Path | None = None
    ) -> dict[str, Any] | None:
        """
        Load app configuration from individual file (.navig/apps/<name>.yaml).

        Args:
            app_name: App name
            navig_dir: Optional .navig directory path

        Returns:
            App configuration dictionary or None if file doesn't exist
        """
        app_file = self.get_app_file_path(app_name, navig_dir)

        if not app_file.exists():
            return None

        try:
            with open(app_file) as f:
                app_config = yaml.safe_load(f) or {}

            # Validate required fields
            if "name" not in app_config:
                raise ValueError(f"App file missing required field 'name': {app_file}")
            if "host" not in app_config:
                raise ValueError(f"App file missing required field 'host': {app_file}")

            # Validate name matches filename
            if app_config["name"] != app_name:
                raise ValueError(
                    f"App name mismatch: filename is '{app_name}.yaml' but name field is '{app_config['name']}'"
                )

            return app_config
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in app file {app_file}: {e}") from e

    def save_app_to_file(
        self,
        app_name: str,
        app_config: dict[str, Any],
        navig_dir: Path | None = None,
    ):
        """
        Save app configuration to individual file (.navig/apps/<name>.yaml).

        Args:
            app_name: App name
            app_config: App configuration dictionary
            navig_dir: Optional .navig directory path

        Raises:
            ValueError: If required fields are missing or invalid
        """
        # Validate required fields
        if "name" not in app_config:
            app_config["name"] = app_name
        if "host" not in app_config:
            raise ValueError("App configuration must include 'host' field")

        # Validate name matches
        if app_config["name"] != app_name:
            raise ValueError(
                f"App name mismatch: parameter is '{app_name}' but config['name'] is '{app_config['name']}'"
            )

        # Get app file path
        app_file = self.get_app_file_path(app_name, navig_dir)

        # Ensure apps directory exists
        app_file.parent.mkdir(parents=True, exist_ok=True)

        # Add metadata if not present
        if "metadata" not in app_config:
            app_config["metadata"] = {}
        if "created" not in app_config["metadata"]:
            app_config["metadata"]["created"] = datetime.now().isoformat()
        app_config["metadata"]["updated"] = datetime.now().isoformat()

        # Save to file
        with open(app_file, "w", encoding="utf-8") as f:
            yaml.dump(app_config, f, default_flow_style=False, sort_keys=False)

        if self.verbose:
            from navig import console_helper as ch

            location = "app" if navig_dir == self.app_config_dir else "global"
            ch.dim(f"✓ Saved app '{app_name}' to {location} config (individual file)")

    def list_apps_from_files(self, navig_dir: Path | None = None) -> list:
        """
        List all apps from .navig/apps/ directory (individual files).

        Args:
            navig_dir: Optional .navig directory path

        Returns:
            List of app names from individual files
        """
        if navig_dir is None:
            navig_dir = self.app_config_dir if self.app_config_dir else self.base_dir

        apps_dir = navig_dir / "apps"

        if not apps_dir.exists():
            return []

        apps = []
        for app_file in apps_dir.glob("*.yaml"):
            # Extract app name from filename
            app_name = app_file.stem

            # Validate it's a valid app file (has 'host' field)
            try:
                with open(app_file) as f:
                    app_data = yaml.safe_load(f) or {}
                if "host" in app_data:
                    apps.append(app_name)
            except Exception:
                # Skip invalid files
                continue

        return sorted(apps)

    def migrate_apps_to_files(
        self,
        host_name: str,
        navig_dir: Path | None = None,
        remove_from_host: bool = True,
    ) -> dict[str, Any]:
        """
        Migrate apps from host YAML (legacy embedded format) to individual files (new format).

        Args:
            host_name: Host name to migrate apps from
            navig_dir: Optional .navig directory path (defaults to current working directory)
            remove_from_host: If True, remove apps from host YAML after migration

        Returns:
            Dictionary with migration results:
            {
                'migrated': ['app1', 'app2'],
                'skipped': ['app3'],  # Already exists as individual file
                'errors': {'app4': 'error message'}
            }
        """
        if navig_dir is None:
            navig_dir = self.app_config_dir if self.app_config_dir else self.base_dir

        results = {"migrated": [], "skipped": [], "errors": {}}

        try:
            # Load host configuration
            host_config = self.load_host_config(host_name)

            # Check if host has embedded apps
            if "apps" not in host_config or not host_config["apps"]:
                return results  # No apps to migrate

            # Migrate each app
            for app_name, app_config in host_config["apps"].items():
                try:
                    # Check if individual file already exists
                    app_file = self.get_app_file_path(app_name, navig_dir)
                    if app_file.exists():
                        results["skipped"].append(app_name)
                        continue

                    # Add required fields
                    app_config["name"] = app_name
                    app_config["host"] = host_name

                    # Save to individual file
                    self.save_app_to_file(app_name, app_config, navig_dir)
                    results["migrated"].append(app_name)

                except Exception as e:
                    results["errors"][app_name] = str(e)

            # Remove apps from host YAML if requested
            if remove_from_host and results["migrated"]:
                host_config["apps"] = {
                    name: config
                    for name, config in host_config["apps"].items()
                    if name not in results["migrated"]
                }

                # If no apps left, remove the apps field entirely
                if not host_config["apps"]:
                    del host_config["apps"]

                # Save updated host config
                self.save_host_config(host_name, host_config)

        except Exception as e:
            results["errors"]["_migration"] = str(e)

        return results


# =============================================================================
# Singleton Pattern for Performance Optimization
# =============================================================================

_config_manager_instance: ConfigManager | None = None
_config_manager_config_dir: Path | None = None
_config_manager_force_new: bool = False


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
        _config_manager_instance = ConfigManager(config_dir=config_dir, verbose=verbose)
        _config_manager_config_dir = config_dir

    return _config_manager_instance


def set_config_cache_bypass(enabled: bool) -> None:
    """Enable or disable process-wide config-manager cache bypass."""
    global _config_manager_force_new
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
    _config_manager_instance = None
    _config_manager_config_dir = None
