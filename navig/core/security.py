"""
Security Module for NAVIG

Provides security utilities inspired by advanced agent patterns:
- Sensitive data redaction (for logs and output)
- Environment variable substitution in configs
- Executable safety validation
- Security audit utilities

Based on patterns from standard security modules and redaction systems.
"""

import os
import re
from pathlib import Path
from typing import Any

# =============================================================================
# Sensitive Data Redaction
# =============================================================================

# Default patterns for detecting sensitive data
# These are expanded from standard patterns to cover more cases
DEFAULT_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ENV-style assignments (KEY=value, KEY: value)
    (
        re.compile(
            r'\b[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|AUTH)\b\s*[=:]\s*(["\']?)([^\s"\'\n\\]+)\1',
            re.IGNORECASE,
        ),
        r"***REDACTED***",
    ),
    # JSON fields with sensitive names
    (
        re.compile(
            r'["\'](api[_-]?key|token|secret|password|passwd|access[_-]?token|refresh[_-]?token|auth[_-]?token|private[_-]?key)["\']\s*:\s*["\']([^"\']+)["\']',
            re.IGNORECASE,
        ),
        r'"\1": "***REDACTED***"',
    ),
    # CLI flags for sensitive values
    (
        re.compile(
            r'--(?:api[-_]?key|token|secret|password|passwd|auth)\s+(["\']?)([^\s"\']+)\1',
            re.IGNORECASE,
        ),
        r"--\1 ***REDACTED***",
    ),
    (
        re.compile(r"-p\s+([^\s]+)", re.IGNORECASE),
        r"-p ***REDACTED***",
    ),  # MySQL password
    # Authorization headers
    (
        re.compile(r"Authorization\s*[:=]\s*Bearer\s+([A-Za-z0-9._\-+=]+)", re.IGNORECASE),
        r"Authorization: Bearer ***REDACTED***",
    ),
    (
        re.compile(r"Authorization\s*[:=]\s*Basic\s+([A-Za-z0-9+/=]+)", re.IGNORECASE),
        r"Authorization: Basic ***REDACTED***",
    ),
    (
        re.compile(r"\bBearer\s+([A-Za-z0-9._\-+=]{18,})\b", re.IGNORECASE),
        r"Bearer ***REDACTED***",
    ),
    # PEM blocks (SSH keys, certificates)
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        r"-----BEGIN PRIVATE KEY-----\n***REDACTED***\n-----END PRIVATE KEY-----",
    ),
    # Common API key prefixes
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b"), r"sk-***REDACTED***"),  # OpenAI
    (
        re.compile(r"\b(sk-proj-[A-Za-z0-9_-]{8,})\b"),
        r"sk-proj-***REDACTED***",
    ),  # OpenAI project
    (
        re.compile(r"\b(sk-ant-[A-Za-z0-9_-]{8,})\b"),
        r"sk-ant-***REDACTED***",
    ),  # Anthropic
    (re.compile(r"\b(ghp_[A-Za-z0-9]{20,})\b"), r"ghp_***REDACTED***"),  # GitHub PAT
    (
        re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b"),
        r"github_pat_***REDACTED***",
    ),  # GitHub fine-grained
    (
        re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"),
        r"xox*-***REDACTED***",
    ),  # Slack
    (re.compile(r"\b(xapp-[A-Za-z0-9-]{10,})\b"), r"xapp-***REDACTED***"),  # Slack app
    (re.compile(r"\b(gsk_[A-Za-z0-9_-]{10,})\b"), r"gsk_***REDACTED***"),  # Groq
    (re.compile(r"\b(AIza[0-9A-Za-z\-_]{20,})\b"), r"AIza***REDACTED***"),  # Google
    (
        re.compile(r"\b(pplx-[A-Za-z0-9_-]{10,})\b"),
        r"pplx-***REDACTED***",
    ),  # Perplexity
    (re.compile(r"\b(npm_[A-Za-z0-9]{10,})\b"), r"npm_***REDACTED***"),  # npm
    (re.compile(r"\b(\d{6,}:[A-Za-z0-9_-]{20,})\b"), r"***REDACTED***"),  # Telegram bot
    # SSH connection strings with passwords
    (re.compile(r"(ssh://[^:]+:)([^@]+)(@)", re.IGNORECASE), r"\1***REDACTED***\3"),
    # MySQL/Database connection strings
    (re.compile(r"(mysql://[^:]+:)([^@]+)(@)", re.IGNORECASE), r"\1***REDACTED***\3"),
    (
        re.compile(r"(postgres://[^:]+:)([^@]+)(@)", re.IGNORECASE),
        r"\1***REDACTED***\3",
    ),
    (re.compile(r"(mongodb://[^:]+:)([^@]+)(@)", re.IGNORECASE), r"\1***REDACTED***\3"),
    (re.compile(r"MYSQL_PWD=([^\s]+)", re.IGNORECASE), r"MYSQL_PWD=***REDACTED***"),
]


def redact_sensitive_text(
    text: str,
    patterns: list[tuple[re.Pattern, str]] | None = None,
    mode: str = "tools",
) -> str:
    """
    Redact sensitive information from text.

    Inspired by standard redactSensitiveText function.

    Args:
        text: Text to redact
        patterns: Custom patterns to use (defaults to DEFAULT_REDACT_PATTERNS)
        mode: Redaction mode - "off" to disable, "tools" for tool output only

    Returns:
        Text with sensitive data redacted
    """
    if not text or mode == "off":
        return text

    if patterns is None:
        patterns = DEFAULT_REDACT_PATTERNS

    result = text
    for pattern, replacement in patterns:
        try:
            result = pattern.sub(replacement, result)
        except re.error:
            # Skip invalid patterns
            continue

    return result


def redact_dict(data: dict[str, Any], sensitive_keys: list[str] | None = None) -> dict[str, Any]:
    """
    Recursively redact sensitive values from a dictionary.

    Args:
        data: Dictionary to redact
        sensitive_keys: Keys to always redact (case-insensitive)

    Returns:
        New dictionary with sensitive values redacted
    """
    if sensitive_keys is None:
        sensitive_keys = [
            "password",
            "passwd",
            "pwd",
            "secret",
            "token",
            "api_key",
            "apikey",
            "auth",
            "authorization",
            "credential",
            "ssh_password",
            "ssh_key",
            "private_key",
            "access_token",
            "refresh_token",
        ]

    sensitive_set = {k.lower() for k in sensitive_keys}

    def _redact_value(key: str, value: Any) -> Any:
        key_lower = key.lower()

        # Check if key matches sensitive patterns
        if key_lower in sensitive_set or any(
            s in key_lower for s in ["password", "secret", "token", "key", "auth"]
        ):
            if isinstance(value, str) and value:
                return "***REDACTED***"
            return value

        if isinstance(value, dict):
            return {k: _redact_value(k, v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_redact_value(key, item) for item in value]
        elif isinstance(value, str):
            return redact_sensitive_text(value)

        return value

    return {k: _redact_value(k, v) for k, v in data.items()}


# =============================================================================
# Environment Variable Substitution
# =============================================================================


class MissingEnvVarError(Exception):
    """Raised when a required environment variable is not set."""

    def __init__(self, var_name: str, config_path: str):
        self.var_name = var_name
        self.config_path = config_path
        super().__init__(f'Missing env var "${var_name}" referenced at config path: {config_path}')


# Pattern for valid env var names (uppercase letters, digits, underscore)
ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def substitute_env_vars(
    config: Any,
    env: dict[str, str] | None = None,
    path: str = "",
    strict: bool = True,
) -> Any:
    """
    Substitute ${VAR_NAME} references in config values.

    Inspired by standard resolveConfigEnvVars function.

    Supports:
    - ${VAR_NAME} - substitute with env var value
    - $${VAR_NAME} - escape to literal ${VAR_NAME}

    Args:
        config: Config value (dict, list, or scalar)
        env: Environment variables (defaults to os.environ)
        path: Current config path for error messages
        strict: If True, raise error on missing vars; if False, leave unreplaced

    Returns:
        Config with env vars substituted

    Raises:
        MissingEnvVarError: If strict=True and env var is not set
    """
    if env is None:
        env = dict(os.environ)

    if isinstance(config, str):
        return _substitute_string(config, env, path, strict)
    elif isinstance(config, dict):
        return {
            k: substitute_env_vars(v, env, f"{path}.{k}" if path else k, strict)
            for k, v in config.items()
        }
    elif isinstance(config, list):
        return [
            substitute_env_vars(item, env, f"{path}[{i}]", strict) for i, item in enumerate(config)
        ]
    else:
        # Primitives pass through unchanged
        return config


def _substitute_string(value: str, env: dict[str, str], path: str, strict: bool) -> str:
    """Substitute env vars in a single string value."""
    if "$" not in value:
        return value

    result = []
    i = 0

    while i < len(value):
        char = value[i]

        if char != "$":
            result.append(char)
            i += 1
            continue

        # Check for escape sequence: $${VAR}
        if i + 1 < len(value) and value[i + 1] == "$":
            if i + 2 < len(value) and value[i + 2] == "{":
                # Find closing brace
                end = value.find("}", i + 3)
                if end != -1:
                    var_name = value[i + 3 : end]
                    if ENV_VAR_PATTERN.match(var_name):
                        # Escaped - output literal ${VAR_NAME}
                        result.append(f"${{{var_name}}}")
                        i = end + 1
                        continue

        # Check for substitution: ${VAR}
        if i + 1 < len(value) and value[i + 1] == "{":
            end = value.find("}", i + 2)
            if end != -1:
                var_name = value[i + 2 : end]
                if ENV_VAR_PATTERN.match(var_name):
                    env_value = env.get(var_name, "")
                    if not env_value and strict:
                        raise MissingEnvVarError(var_name, path)
                    result.append(env_value if env_value else f"${{{var_name}}}")
                    i = end + 1
                    continue

        # Not a recognized pattern, keep the character
        result.append(char)
        i += 1

    return "".join(result)


# =============================================================================
# Executable Safety Validation
# =============================================================================

# Safe executables that are allowed without path validation
SAFE_EXECUTABLES = {
    # Standard Unix tools
    "ls",
    "cat",
    "echo",
    "grep",
    "awk",
    "sed",
    "head",
    "tail",
    "wc",
    "sort",
    "uniq",
    "cut",
    "tr",
    "find",
    "xargs",
    "tee",
    "touch",
    "mkdir",
    "rmdir",
    "cp",
    "mv",
    "ln",
    "chmod",
    "chown",
    "pwd",
    "cd",
    "date",
    "time",
    "sleep",
    "true",
    "false",
    "which",
    "whereis",
    "type",
    "file",
    "stat",
    # System info
    "uname",
    "hostname",
    "whoami",
    "id",
    "groups",
    "env",
    "printenv",
    "uptime",
    "free",
    "df",
    "du",
    "mount",
    "lsblk",
    "ps",
    "top",
    "htop",
    # Network tools
    "ping",
    "curl",
    "wget",
    "nc",
    "netcat",
    "ssh",
    "scp",
    "rsync",
    "ifconfig",
    "ip",
    "netstat",
    "ss",
    "dig",
    "nslookup",
    "host",
    # Package managers (read operations)
    "apt",
    "apt-get",
    "apt-cache",
    "dpkg",
    "yum",
    "dnf",
    "pacman",
    "brew",
    "pip",
    "npm",
    "yarn",
    "pnpm",
    # Development tools
    "python",
    "python3",
    "node",
    "npx",
    "git",
    "make",
    "cargo",
    "rustc",
    "go",
    "java",
    "javac",
    # Editors (non-interactive context)
    "vim",
    "nano",
    "code",
    "subl",
    # NAVIG/system commands
    "navig",
    "systemctl",
    "service",
    "journalctl",
    # Windows equivalents
    "cmd",
    "powershell",
    "pwsh",
    "dir",
    "copy",
    "move",
    "del",
    "where",
    "tasklist",
    "netsh",
    "ipconfig",
}

# Dangerous patterns that should be blocked
DANGEROUS_PATTERNS = [
    re.compile(r";\s*rm\s+-rf\s+/", re.IGNORECASE),  # Destructive rm
    re.compile(r"\|\s*bash", re.IGNORECASE),  # Piped execution
    re.compile(r"\|\s*sh\b", re.IGNORECASE),  # Piped shell
    re.compile(r">\s*/etc/", re.IGNORECASE),  # Write to /etc
    re.compile(r">\s*/dev/", re.IGNORECASE),  # Write to /dev
    re.compile(r"mkfs\.", re.IGNORECASE),  # Format filesystem
    re.compile(r"dd\s+.+of=/dev/", re.IGNORECASE),  # dd to device
    re.compile(r":(){.*};:", re.IGNORECASE),  # Fork bomb
]


class UnsafeExecutableError(Exception):
    """Raised when an executable fails safety validation."""

    pass


def is_safe_executable(executable: str) -> bool:
    """
    Check if an executable is considered safe.

    Args:
        executable: The executable name or path

    Returns:
        True if executable is in the safe list
    """
    # Extract just the executable name from path
    exe_name = Path(executable).name.lower()

    # Remove .exe extension on Windows
    if exe_name.endswith(".exe"):
        exe_name = exe_name[:-4]

    return exe_name in SAFE_EXECUTABLES


def validate_command_safety(command: str, allow_unsafe: bool = False) -> tuple[bool, str | None]:
    """
    Validate a command for safety before execution.

    Args:
        command: The command string to validate
        allow_unsafe: If True, skip validation (for trusted contexts)

    Returns:
        Tuple of (is_safe, reason) where reason explains why it's unsafe
    """
    if allow_unsafe:
        return True, None

    # Check for dangerous patterns
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return False, f"Command matches dangerous pattern: {pattern.pattern}"

    # Extract executable from command
    parts = command.strip().split()
    if not parts:
        return False, "Empty command"

    executable = parts[0]

    # Check if it's a safe executable
    if not is_safe_executable(executable):
        # Not in safe list - could still be allowed, but requires caution
        return (
            True,
            f"Executable '{executable}' not in safe list (proceed with caution)",
        )

    return True, None


# =============================================================================
# Security Audit Utilities
# =============================================================================


class SecurityFinding:
    """A security audit finding."""

    def __init__(
        self,
        check_id: str,
        severity: str,  # "critical", "warn", "info"
        title: str,
        detail: str,
        remediation: str | None = None,
    ):
        self.check_id = check_id
        self.severity = severity
        self.title = title
        self.detail = detail
        self.remediation = remediation

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "remediation": self.remediation,
        }


def check_file_permissions(file_path: Path) -> list[SecurityFinding]:
    """
    Check file permissions for security issues.

    Args:
        file_path: Path to check

    Returns:
        List of security findings
    """
    findings = []

    if not file_path.exists():
        return findings

    try:
        stat_info = file_path.stat()
        mode = stat_info.st_mode

        # Check if file is world-readable (Unix only)
        if hasattr(os, "stat") and os.name != "nt":
            # Other read permission (0o004)
            if mode & 0o004:
                findings.append(
                    SecurityFinding(
                        check_id="file-world-readable",
                        severity="warn",
                        title=f"File is world-readable: {file_path.name}",
                        detail=f"The file {file_path} has permissions that allow any user to read it.",
                        remediation=f"chmod 600 {file_path}",
                    )
                )

            # Check SSH key permissions (should be 0600)
            if file_path.suffix in [".pem", ".key"] or "id_" in file_path.name:
                if mode & 0o077:  # Any group or other permissions
                    findings.append(
                        SecurityFinding(
                            check_id="ssh-key-permissions",
                            severity="critical",
                            title=f"SSH key has insecure permissions: {file_path.name}",
                            detail="SSH keys should only be readable by the owner (mode 0600).",
                            remediation=f"chmod 600 {file_path}",
                        )
                    )

    except (PermissionError, OSError):
        pass  # best-effort cleanup; ignore access/IO errors

    return findings


def check_config_security(config: dict[str, Any]) -> list[SecurityFinding]:
    """
    Check configuration for security issues.

    Args:
        config: Configuration dictionary

    Returns:
        List of security findings
    """
    findings = []

    # Check for hardcoded credentials
    def _check_value(key: str, value: Any, path: str):
        if isinstance(value, str) and value:
            # Check for common credential patterns that shouldn't be hardcoded
            patterns = [
                (r"^sk-[a-zA-Z0-9]{20,}$", "API key"),
                (r"^ghp_[a-zA-Z0-9]{36}$", "GitHub token"),
                (r"^Bearer\s+", "Authorization header"),
            ]
            for pattern, key_type in patterns:
                if re.match(pattern, value):
                    findings.append(
                        SecurityFinding(
                            check_id="hardcoded-credential",
                            severity="critical",
                            title=f"Hardcoded {key_type} detected",
                            detail=f"A {key_type} appears to be hardcoded at config path: {path}",
                            remediation="Use environment variables: ${ENV_VAR_NAME}",
                        )
                    )
        elif isinstance(value, dict):
            for k, v in value.items():
                _check_value(k, v, f"{path}.{k}")

    for key, value in config.items():
        _check_value(key, value, key)

    # Check for insecure settings
    if config.get("allow_insecure", False):
        findings.append(
            SecurityFinding(
                check_id="allow-insecure",
                severity="warn",
                title="Insecure connections allowed",
                detail="The configuration allows insecure connections.",
                remediation="Set 'allow_insecure: false' for production",
            )
        )

    return findings


def run_security_audit(config: dict[str, Any], config_dir: Path | None = None) -> dict[str, Any]:
    """
    Run a comprehensive security audit.

    Args:
        config: Configuration dictionary
        config_dir: Configuration directory to check

    Returns:
        Audit report with findings and summary
    """
    findings = []

    # Check configuration
    findings.extend(check_config_security(config))

    # Check file permissions if config_dir provided
    if config_dir and config_dir.exists():
        for file_path in config_dir.rglob("*"):
            if file_path.is_file():
                findings.extend(check_file_permissions(file_path))

    # Build summary
    summary = {
        "critical": sum(1 for f in findings if f.severity == "critical"),
        "warn": sum(1 for f in findings if f.severity == "warn"),
        "info": sum(1 for f in findings if f.severity == "info"),
    }

    return {
        "timestamp": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
        "summary": summary,
        "findings": [f.to_dict() for f in findings],
        "passed": summary["critical"] == 0,
    }
