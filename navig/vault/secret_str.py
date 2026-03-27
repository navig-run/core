"""
SecretStr - A string wrapper that prevents accidental secret logging.

This class ensures that sensitive values like API keys and passwords
are never accidentally printed, logged, or included in error messages.

Usage:
    secret = SecretStr("sk-super-secret-key")
    print(secret)  # Outputs: ***
    str(secret)    # Returns: ***
    repr(secret)   # Returns: SecretStr('***')

    # To get the actual value (use sparingly!):
    actual_value = secret.reveal()

    # For debugging (shows prefix only):
    secret.reveal_prefix(4)  # Returns: "sk-s...***"
"""

from typing import Any


class SecretStr:
    """
    A string wrapper that self-redacts in all string representations.

    Use this class to wrap any sensitive string data (API keys, passwords,
    tokens, etc.) to prevent accidental exposure in logs, stack traces,
    and error messages.

    The actual secret value is accessible via the reveal() method.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str):
        """
        Create a new SecretStr.

        Args:
            value: The secret string to wrap
        """
        if not isinstance(value, str):
            raise TypeError(f"SecretStr expects str, got {type(value).__name__}")
        self._value = value

    def __repr__(self) -> str:
        """Return a safe repr that hides the secret."""
        return "SecretStr('***')"

    def __str__(self) -> str:
        """Return a safe string that hides the secret."""
        return "***"

    def __eq__(self, other: object) -> bool:
        """Compare equality without exposing secrets."""
        if isinstance(other, SecretStr):
            return self._value == other._value
        return False

    def __ne__(self, other: object) -> bool:
        """Compare inequality."""
        return not self.__eq__(other)

    def __hash__(self) -> int:
        """Hash based on the secret value."""
        return hash(self._value)

    def __len__(self) -> int:
        """Return the length of the secret."""
        return len(self._value)

    def __bool__(self) -> bool:
        """Return True if the secret is non-empty."""
        return bool(self._value)

    def __format__(self, format_spec: str) -> str:
        """Format always returns redacted value."""
        return "***"

    def reveal(self) -> str:
        """
        Get the actual secret value.

        WARNING: Use this method sparingly and never log the result!
        Only call reveal() when you need to use the secret value
        (e.g., in an HTTP header or authentication request).

        Returns:
            The actual secret string
        """
        return self._value

    def reveal_prefix(self, count: int = 4) -> str:
        """
        Show the first N characters of the secret for debugging.

        This is useful for identifying which key is being used
        without exposing the full secret.

        Args:
            count: Number of prefix characters to show (default: 4)

        Returns:
            A partially redacted string like "sk-ab...***"

        Example:
            >>> secret = SecretStr("sk-abcdef123456")
            >>> secret.reveal_prefix(5)
            'sk-ab...***'
        """
        if len(self._value) <= count:
            return "***"
        return f"{self._value[:count]}...***"

    def copy(self) -> "SecretStr":
        """Create a copy of this SecretStr."""
        return SecretStr(self._value)

    @classmethod
    def from_env(cls, var_name: str, default: str = "") -> "SecretStr":
        """
        Create a SecretStr from an environment variable.

        Args:
            var_name: Name of the environment variable
            default: Default value if not set

        Returns:
            SecretStr containing the env var value or default
        """
        import os

        value = os.environ.get(var_name, default)
        return cls(value)


# Type alias for type hints
Secret = SecretStr


def mask_secret(value: Any, show_prefix: int = 4) -> str:
    """
    Safely mask a value that might be a secret.

    Args:
        value: Any value (will be converted to string if needed)
        show_prefix: Number of prefix chars to show (0 for full mask)

    Returns:
        Masked string representation
    """
    if value is None:
        return "<none>"

    if isinstance(value, SecretStr):
        return value.reveal_prefix(show_prefix) if show_prefix else "***"

    str_value = str(value)
    if not str_value:
        return "<empty>"

    if show_prefix and len(str_value) > show_prefix:
        return f"{str_value[:show_prefix]}...***"

    return "***"
