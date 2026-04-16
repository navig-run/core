"""
Security utilities for NAVIG.

- Sensitive-data redaction (logs, output, formatter)
- Environment-variable substitution in config values
- Executable and command-string safety validation
- Security audit helpers (file permissions, hardcoded credentials)
- Prompt-injection / context-file scanning
- PII hashing for log-safe session identifiers
- Managed-mode detection
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

# =============================================================================
# Sensitive-data Redaction
# =============================================================================

# Each entry is (compiled_pattern, replacement_string).
# Patterns are applied in order; earlier patterns take precedence for overlapping matches.
DEFAULT_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # ENV-style assignments: KEY=value or KEY: value
    (
        re.compile(
            r'\b[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|AUTH)\b\s*[=:]\s*'
            r'(["\']?)([^\s"\'\\n\\\\]+)\1',
            re.IGNORECASE,
        ),
        r"***REDACTED***",
    ),
    # JSON fields with sensitive names
    (
        re.compile(
            r'["\']'
            r'(api[_-]?key|token|secret|password|passwd|access[_-]?token|'
            r'refresh[_-]?token|auth[_-]?token|private[_-]?key)'
            r'["\']\s*:\s*["\']([^"\']+)["\']',
            re.IGNORECASE,
        ),
        r'"\1": "***REDACTED***"',
    ),
    # CLI flags: --api-key <value>, --token <value>, etc.
    (
        re.compile(
            r'--(?:api[-_]?key|token|secret|password|passwd|auth)\s+(["\']?)([^\s"\']+)\1',
            re.IGNORECASE,
        ),
        r"--\1 ***REDACTED***",
    ),
    # MySQL short password flag: -p <value>
    (re.compile(r"-p\s+([^\s]+)", re.IGNORECASE), r"-p ***REDACTED***"),
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
    # PEM private-key blocks
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        r"-----BEGIN PRIVATE KEY-----\n***REDACTED***\n-----END PRIVATE KEY-----",
    ),
    # Provider-specific API key prefixes
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,})\b"), r"sk-***REDACTED***"),            # OpenAI
    (re.compile(r"\b(sk-proj-[A-Za-z0-9_-]{8,})\b"), r"sk-proj-***REDACTED***"),  # OpenAI project
    (re.compile(r"\b(sk-ant-[A-Za-z0-9_-]{8,})\b"), r"sk-ant-***REDACTED***"),    # Anthropic
    (re.compile(r"\b(ghp_[A-Za-z0-9]{20,})\b"), r"ghp_***REDACTED***"),           # GitHub PAT
    (re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b"), r"github_pat_***REDACTED***"),  # GitHub fine-grained
    (re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"), r"xox*-***REDACTED***"),  # Slack
    (re.compile(r"\b(xapp-[A-Za-z0-9-]{10,})\b"), r"xapp-***REDACTED***"),        # Slack app
    (re.compile(r"\b(gsk_[A-Za-z0-9_-]{10,})\b"), r"gsk_***REDACTED***"),         # Groq
    (re.compile(r"\b(AIza[0-9A-Za-z\-_]{20,})\b"), r"AIza***REDACTED***"),        # Google
    (re.compile(r"\b(pplx-[A-Za-z0-9_-]{10,})\b"), r"pplx-***REDACTED***"),       # Perplexity
    (re.compile(r"\b(npm_[A-Za-z0-9]{10,})\b"), r"npm_***REDACTED***"),            # npm
    (re.compile(r"\b(\d{6,}:[A-Za-z0-9_-]{20,})\b"), r"***REDACTED***"),           # Telegram bot
    (re.compile(r"\b(tvly-[A-Za-z0-9_-]{10,})\b"), r"tvly-***REDACTED***"),        # Tavily
    (re.compile(r"\b(exa_[A-Za-z0-9_-]{10,})\b"), r"exa_***REDACTED***"),          # Exa
    (re.compile(r"\b(hf_[A-Za-z0-9]{10,})\b"), r"hf_***REDACTED***"),              # HuggingFace
    (re.compile(r"\b(r8_[A-Za-z0-9]{10,})\b"), r"r8_***REDACTED***"),              # Replicate
    (re.compile(r"\b(syt_[A-Za-z0-9_-]{10,})\b"), r"syt_***REDACTED***"),          # Suno
    (re.compile(r"\b(hsk-[A-Za-z0-9_-]{10,})\b"), r"hsk-***REDACTED***"),          # Hyperbolic
    (re.compile(r"\b(mem0_[A-Za-z0-9_-]{10,})\b"), r"mem0_***REDACTED***"),        # Mem0
    (re.compile(r"\b(brv_[A-Za-z0-9_-]{10,})\b"), r"brv_***REDACTED***"),          # Brave
    (re.compile(r"\b(dop_v1_[A-Za-z0-9_-]{10,})\b"), r"dop_v1_***REDACTED***"),   # DigitalOcean
    (re.compile(r"\b(doo_v1_[A-Za-z0-9_-]{10,})\b"), r"doo_v1_***REDACTED***"),   # DigitalOcean OAuth
    (re.compile(r"\b(fc-[A-Za-z0-9_-]{10,})\b"), r"fc-***REDACTED***"),            # Firecrawl
    (re.compile(r"\b(fal_[A-Za-z0-9_-]{10,})\b"), r"fal_***REDACTED***"),          # fal.ai
    (re.compile(r"\b(bb_live_[A-Za-z0-9_-]{10,})\b"), r"bb_live_***REDACTED***"),  # Blackbird
    (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), r"AKIA***REDACTED***"),                  # AWS access key
    (re.compile(r"\bsk_live_[A-Za-z0-9]{10,}\b"), r"sk_live_***REDACTED***"),      # Stripe live
    # Connection strings with embedded passwords
    (re.compile(r"(ssh://[^:]+:)([^@]+)(@)", re.IGNORECASE), r"\1***REDACTED***\3"),
    (re.compile(r"(mysql://[^:]+:)([^@]+)(@)", re.IGNORECASE), r"\1***REDACTED***\3"),
    (re.compile(r"(postgres://[^:]+:)([^@]+)(@)", re.IGNORECASE), r"\1***REDACTED***\3"),
    (re.compile(r"(mongodb://[^:]+:)([^@]+)(@)", re.IGNORECASE), r"\1***REDACTED***\3"),
    (re.compile(r"MYSQL_PWD=([^\s]+)", re.IGNORECASE), r"MYSQL_PWD=***REDACTED***"),
]


def redact_sensitive_text(
    text: str,
    patterns: list[tuple[re.Pattern[str], str]] | None = None,
    mode: str = "tools",
) -> str:
    """Redact sensitive information from *text*.

    Args:
        text:     Input text.
        patterns: Override the default pattern list.
        mode:     ``"off"`` disables all redaction; any other value applies patterns.

    Returns:
        Text with sensitive tokens replaced by ``***REDACTED***``.
    """
    if not text or mode == "off":
        return text

    active = DEFAULT_REDACT_PATTERNS if patterns is None else patterns
    result = text
    for pattern, replacement in active:
        try:
            result = pattern.sub(replacement, result)
        except re.error:
            continue  # Skip invalid patterns rather than crashing
    return result


def redact_dict(
    data: dict[str, Any],
    sensitive_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Recursively redact sensitive values from *data*.

    Keys whose names contain ``password``, ``secret``, ``token``, ``key``, or
    ``auth`` (case-insensitive) are fully replaced; all other string values are
    passed through :func:`redact_sensitive_text`.

    Args:
        data:           Source dictionary.
        sensitive_keys: Additional exact key names (case-insensitive) to redact.

    Returns:
        A new dictionary with sensitive values replaced.
    """
    _DEFAULT_SENSITIVE = {
        "password", "passwd", "pwd", "secret", "token",
        "api_key", "apikey", "auth", "authorization", "credential",
        "ssh_password", "ssh_key", "private_key", "access_token", "refresh_token",
    }
    sensitive_set = _DEFAULT_SENSITIVE | {k.lower() for k in (sensitive_keys or [])}
    _SENSITIVE_SUBSTRINGS = ("password", "secret", "token", "key", "auth")

    def _redact_value(key: str, value: Any) -> Any:
        key_lower = key.lower()
        if key_lower in sensitive_set or any(s in key_lower for s in _SENSITIVE_SUBSTRINGS):
            return "***REDACTED***" if isinstance(value, str) and value else value
        if isinstance(value, dict):
            return {k: _redact_value(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [_redact_value(key, item) for item in value]
        if isinstance(value, str):
            return redact_sensitive_text(value)
        return value

    return {k: _redact_value(k, v) for k, v in data.items()}


# =============================================================================
# RedactingFormatter — drop-in logging.Formatter that scrubs secrets
# =============================================================================


class RedactingFormatter(logging.Formatter):
    """A :class:`logging.Formatter` that scrubs secrets from every log record.

    Usage::

        handler = logging.FileHandler("app.log")
        handler.setFormatter(RedactingFormatter("%(asctime)s %(message)s"))
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        return redact_sensitive_text(super().format(record))


# =============================================================================
# Token masking
# =============================================================================


def _mask_token(token: str) -> str:
    """Return a display-safe masked version of *token*.

    Short tokens (< 18 chars) are fully masked.  Longer tokens show the first
    six and last four characters to aid identification without leaking secrets.

    Examples::

        _mask_token("sk-abc123verylongkey")  # -> "sk-abc...ykey"
        _mask_token("short")                 # -> "***"
    """
    return f"{token[:6]}...{token[-4:]}" if len(token) >= 18 else "***"


# =============================================================================
# Environment-variable substitution
# =============================================================================


class MissingEnvVarError(Exception):
    """Raised when a required environment variable is not set."""

    def __init__(self, var_name: str, config_path: str) -> None:
        self.var_name = var_name
        self.config_path = config_path
        super().__init__(
            f'Missing env var "${var_name}" referenced at config path: {config_path}'
        )


# Pattern for valid env-var names (must start with a letter or underscore).
_ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def substitute_env_vars(
    config: Any,
    env: dict[str, str] | None = None,
    path: str = "",
    strict: bool = True,
) -> Any:
    """Substitute ``${VAR_NAME}`` references in *config* values.

    Supports:
    - ``${VAR_NAME}``  — replaced by the environment variable's value.
    - ``$${VAR_NAME}`` — escaped; the literal string ``${VAR_NAME}`` is emitted.

    Args:
        config: Config value (dict, list, or scalar).
        env:    Environment dict (defaults to ``os.environ``).
        path:   Current config path for error messages.
        strict: When ``True``, raise :exc:`MissingEnvVarError` for unset vars.

    Returns:
        Config with env vars substituted.

    Raises:
        MissingEnvVarError: If ``strict=True`` and a referenced var is unset.
    """
    if env is None:
        env = dict(os.environ)

    if isinstance(config, str):
        return _substitute_string(config, env, path, strict)
    if isinstance(config, dict):
        return {
            k: substitute_env_vars(v, env, f"{path}.{k}" if path else k, strict)
            for k, v in config.items()
        }
    if isinstance(config, list):
        return [
            substitute_env_vars(item, env, f"{path}[{i}]", strict)
            for i, item in enumerate(config)
        ]
    return config  # Primitives pass through unchanged


def _substitute_string(value: str, env: dict[str, str], path: str, strict: bool) -> str:
    """Substitute env-var references within a single string."""
    if "$" not in value:
        return value

    result: list[str] = []
    i = 0
    n = len(value)

    while i < n:
        ch = value[i]
        if ch != "$":
            result.append(ch)
            i += 1
            continue

        # Escaped reference: $${VAR} → literal ${VAR}
        if i + 1 < n and value[i + 1] == "$":
            if i + 2 < n and value[i + 2] == "{":
                end = value.find("}", i + 3)
                if end != -1:
                    var_name = value[i + 3: end]
                    if _ENV_VAR_PATTERN.match(var_name):
                        result.append(f"${{{var_name}}}")
                        i = end + 1
                        continue

        # Substitution reference: ${VAR}
        if i + 1 < n and value[i + 1] == "{":
            end = value.find("}", i + 2)
            if end != -1:
                var_name = value[i + 2: end]
                if _ENV_VAR_PATTERN.match(var_name):
                    env_value = env.get(var_name, "")
                    if not env_value and strict:
                        raise MissingEnvVarError(var_name, path)
                    result.append(env_value if env_value else f"${{{var_name}}}")
                    i = end + 1
                    continue

        result.append(ch)
        i += 1

    return "".join(result)


# =============================================================================
# Executable safety validation
# =============================================================================

SAFE_EXECUTABLES: frozenset[str] = frozenset({
    # Core Unix tools
    "ls", "cat", "echo", "grep", "awk", "sed", "head", "tail", "wc",
    "sort", "uniq", "cut", "tr", "find", "xargs", "tee", "touch",
    "mkdir", "rmdir", "cp", "mv", "ln", "chmod", "chown", "pwd", "cd",
    "date", "time", "sleep", "true", "false", "which", "whereis", "type",
    "file", "stat",
    # System info
    "uname", "hostname", "whoami", "id", "groups", "env", "printenv",
    "uptime", "free", "df", "du", "mount", "lsblk", "ps", "top", "htop",
    # Network
    "ping", "curl", "wget", "nc", "netcat", "ssh", "scp", "rsync",
    "ifconfig", "ip", "netstat", "ss", "dig", "nslookup", "host",
    # Package managers
    "apt", "apt-get", "apt-cache", "dpkg", "yum", "dnf", "pacman",
    "brew", "pip", "npm", "yarn", "pnpm",
    # Development
    "python", "python3", "node", "npx", "git", "make", "cargo",
    "rustc", "go", "java", "javac",
    # Editors (non-interactive context)
    "vim", "nano", "code", "subl",
    # NAVIG / system
    "navig", "systemctl", "service", "journalctl",
    # Windows
    "cmd", "powershell", "pwsh", "dir", "copy", "move", "del",
    "where", "tasklist", "netsh", "ipconfig",
})

DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r";\s*rm\s+-rf\s+/", re.IGNORECASE),
    re.compile(r"\|\s*bash", re.IGNORECASE),
    re.compile(r"\|\s*sh\b", re.IGNORECASE),
    re.compile(r">\s*/etc/", re.IGNORECASE),
    re.compile(r">\s*/dev/", re.IGNORECASE),
    re.compile(r"mkfs\.", re.IGNORECASE),
    re.compile(r"dd\s+.+of=/dev/", re.IGNORECASE),
    re.compile(r":\(\)\{.*\};:", re.IGNORECASE),  # fork bomb
]


class UnsafeExecutableError(Exception):
    """Raised when an executable fails safety validation."""


def is_safe_executable(executable: str) -> bool:
    """Return ``True`` when *executable* is in the safe list."""
    exe_name = Path(executable).name.lower()
    if exe_name.endswith(".exe"):
        exe_name = exe_name[:-4]
    return exe_name in SAFE_EXECUTABLES


def validate_command_safety(
    command: str, allow_unsafe: bool = False
) -> tuple[bool, str | None]:
    """Validate *command* for safety before execution.

    Args:
        command:      Command string to inspect.
        allow_unsafe: When ``True``, skip validation (for explicitly trusted contexts).

    Returns:
        ``(is_safe, reason)`` where *reason* is ``None`` on success or a
        message explaining the issue.
    """
    if allow_unsafe:
        return True, None

    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return False, f"Command matches dangerous pattern: {pattern.pattern}"

    parts = command.strip().split()
    if not parts:
        return False, "Empty command"

    if not is_safe_executable(parts[0]):
        return (
            True,
            f"Executable '{parts[0]}' is not in the safe list (proceed with caution)",
        )

    return True, None


# =============================================================================
# Security audit utilities
# =============================================================================


class SecurityFinding:
    """A single security audit finding."""

    def __init__(
        self,
        check_id: str,
        severity: str,  # "critical" | "warn" | "info"
        title: str,
        detail: str,
        remediation: str | None = None,
    ) -> None:
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
    """Return security findings for *file_path*'s Unix permissions."""
    findings: list[SecurityFinding] = []
    if not file_path.exists() or os.name == "nt":
        return findings

    try:
        mode = file_path.stat().st_mode
        if mode & 0o004:  # world-readable
            findings.append(
                SecurityFinding(
                    check_id="file-world-readable",
                    severity="warn",
                    title=f"File is world-readable: {file_path.name}",
                    detail=f"{file_path} can be read by any user.",
                    remediation=f"chmod 600 {file_path}",
                )
            )
        if file_path.suffix in (".pem", ".key") or "id_" in file_path.name:
            if mode & 0o077:  # any group or other permission
                findings.append(
                    SecurityFinding(
                        check_id="ssh-key-permissions",
                        severity="critical",
                        title=f"SSH key has insecure permissions: {file_path.name}",
                        detail="SSH keys must only be readable by the owner (mode 0600).",
                        remediation=f"chmod 600 {file_path}",
                    )
                )
    except (PermissionError, OSError):
        pass  # best-effort; ignore unreadable entries

    return findings


def check_config_security(config: dict[str, Any]) -> list[SecurityFinding]:
    """Inspect *config* for hardcoded credentials and insecure settings."""
    findings: list[SecurityFinding] = []

    _CREDENTIAL_PATTERNS: list[tuple[str, str]] = [
        (r"^sk-[a-zA-Z0-9]{20,}$", "API key"),
        (r"^ghp_[a-zA-Z0-9]{36}$", "GitHub token"),
        (r"^Bearer\s+", "Authorization header"),
    ]

    def _check_value(key: str, value: Any, path: str) -> None:
        if isinstance(value, str) and value:
            for pattern, credential_type in _CREDENTIAL_PATTERNS:
                if re.match(pattern, value):
                    findings.append(
                        SecurityFinding(
                            check_id="hardcoded-credential",
                            severity="critical",
                            title=f"Hardcoded {credential_type} detected",
                            detail=f"A {credential_type} appears hardcoded at: {path}",
                            remediation="Use environment variables: ${ENV_VAR_NAME}",
                        )
                    )
        elif isinstance(value, dict):
            for k, v in value.items():
                _check_value(k, v, f"{path}.{k}")

    for key, value in config.items():
        _check_value(key, value, key)

    if config.get("allow_insecure", False):
        findings.append(
            SecurityFinding(
                check_id="allow-insecure",
                severity="warn",
                title="Insecure connections allowed",
                detail="The configuration permits insecure connections.",
                remediation="Set allow_insecure: false for production",
            )
        )

    return findings


def run_security_audit(
    config: dict[str, Any],
    config_dir: Path | None = None,
) -> dict[str, Any]:
    """Run a comprehensive security audit and return a report dict.

    Args:
        config:     Configuration dictionary to inspect.
        config_dir: Optional directory; all files within are permission-checked.

    Returns:
        ``{"timestamp", "summary", "findings", "passed"}``
    """
    from datetime import datetime, timezone

    findings: list[SecurityFinding] = list(check_config_security(config))

    if config_dir and config_dir.exists():
        for file_path in config_dir.rglob("*"):
            if file_path.is_file():
                findings.extend(check_file_permissions(file_path))

    summary = {
        "critical": sum(1 for f in findings if f.severity == "critical"),
        "warn": sum(1 for f in findings if f.severity == "warn"),
        "info": sum(1 for f in findings if f.severity == "info"),
    }

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "findings": [f.to_dict() for f in findings],
        "passed": summary["critical"] == 0,
    }


# =============================================================================
# Prompt-injection / context-file scanning
# =============================================================================

_CONTEXT_THREAT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ignore (all )?previous instructions?", re.IGNORECASE), "threat:override"),
    (re.compile(r"disregard (all )?previous (system )?prompt", re.IGNORECASE), "threat:override"),
    (re.compile(r"you are now (?!NAVIG)", re.IGNORECASE), "threat:persona_swap"),
    (re.compile(r"new (system )?prompt:", re.IGNORECASE), "threat:prompt_injection"),
    (re.compile(r"print your (system )?prompt", re.IGNORECASE), "threat:exfiltration"),
    (re.compile(r"reveal your instructions", re.IGNORECASE), "threat:exfiltration"),
    (re.compile(r"<SYSTEM>.*?</SYSTEM>", re.IGNORECASE | re.DOTALL), "threat:fake_tag"),
    (re.compile(r"\[JAILBREAK\]", re.IGNORECASE), "threat:jailbreak"),
    (re.compile(r"DAN\s*mode", re.IGNORECASE), "threat:jailbreak"),
]

# Unicode zero-width / bidirectional characters used in invisible injection.
_INVISIBLE_CHARS: frozenset[str] = frozenset(
    "\u200b\u200c\u200d\u200e\u200f"   # zero-width spaces / joiners
    "\u202a\u202b\u202c\u202d\u202e"   # LRE, RLE, PDF, LRO, RLO
    "\u2060\u2061\u2062\u2063\u2064"   # word-joiner, function-application
    "\ufeff"                           # BOM / ZWNBSP
)


def scan_context_file(content: str, filename: str = "<unknown>") -> str:
    """Scan *content* for prompt-injection attempts.

    Returns a risk label (``"safe"`` or a ``"threat:*"`` string) and logs a
    warning when a threat is found.

    Args:
        content:  Text content of the file being fed to the agent context.
        filename: Name/path shown in log messages.

    Returns:
        ``"safe"`` when no patterns match, otherwise the first matched label.
    """
    _log = logging.getLogger(__name__)

    invisible_count = sum(1 for ch in content if ch in _INVISIBLE_CHARS)
    if invisible_count > 3:
        _log.warning(
            "security.scan_context_file: %d invisible chars in %s — possible injection",
            invisible_count,
            filename,
        )
        return "threat:invisible_chars"

    for pattern, label in _CONTEXT_THREAT_PATTERNS:
        if pattern.search(content):
            _log.warning(
                "security.scan_context_file: pattern '%s' matched in %s",
                label,
                filename,
            )
            return label

    return "safe"


# =============================================================================
# Managed-mode detection
# =============================================================================

_MANAGED_MARKERS: tuple[str, ...] = (
    "NAVIG_MANAGED",
    "NAVIG_MANAGED_SYSTEM",
    "HERMES_MANAGED",
)


def get_managed_system() -> str | None:
    """Return the managed-system name if running inside a managed deployment.

    Checks ``NAVIG_MANAGED_SYSTEM`` (and legacy ``HERMES_MANAGED``).  Returns
    the value of the first non-empty variable found, or ``None``.
    """
    for var in _MANAGED_MARKERS:
        value = os.getenv(var, "").strip()
        if value:
            return value
    return None


def is_managed() -> bool:
    """Return ``True`` when running inside a managed deployment.

    Reads ``NAVIG_MANAGED`` (or legacy ``HERMES_MANAGED``).  Any value other
    than ``""``, ``"0"``, ``"false"``, ``"no"``, or ``"off"`` is truthy.
    """
    raw = os.getenv("NAVIG_MANAGED", os.getenv("HERMES_MANAGED", "")).strip().lower()
    return raw not in ("", "0", "false", "no", "off")


# =============================================================================
# PII hashing — log-safe session identifiers
# =============================================================================


def _hash_id(value: str) -> str:
    """Return a deterministic 12-character hex hash of *value* (SHA-256 truncated)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def hash_user_id(value: str) -> str:
    """Return a log-safe ``user_<12hex>`` representation of a user identifier."""
    return f"user_{_hash_id(value)}"


def hash_chat_id(value: str) -> str:
    """Return a log-safe hash of a chat identifier, preserving the platform prefix.

    If *value* is ``platform:id``, only the id portion is hashed so the
    platform is still identifiable in logs::

        hash_chat_id("telegram:9876543")  # -> "telegram:<12hex>"
    """
    colon = value.find(":")
    if colon > 0:
        prefix = value[:colon]
        return f"{prefix}:{_hash_id(value[colon + 1:])}"
    return _hash_id(value)


def log_safe_sid(session_id: str) -> str:
    """Return a shortened, log-safe representation of *session_id*.

    UUID-like strings (≥32 hex chars) → ``first8...last4``.
    Shorter strings → first 12 characters.

    Examples::

        log_safe_sid("550e8400-e29b-41d4-a716-446655440000")  # -> "550e8400...0000"
        log_safe_sid("abc123")                                 # -> "abc123"
    """
    stripped = session_id.replace("-", "")
    if len(stripped) >= 32:
        return f"{session_id[:8]}...{session_id[-4:]}"
    return session_id[:12]
