"""
NAVIG Mode Manager
==================
Manages the active operating mode (node / builder / operator / architect).

Mode profiles are defined in builtin.yaml and control:
  - model preference (fast / strong / reasoning)
  - tool tier / permission gate
  - output style
  - PIN-protected switching for privileged modes

PIN storage: ~/.navig/.mode_pin  (PBKDF2-HMAC-SHA256 v2; legacy SHA-256 v1 hashes upgraded automatically)
Active mode: ~/.navig/config.yaml → active_mode
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path
from typing import Any

from navig import console_helper as ch
from navig.platform import paths

_BUILTIN_YAML = Path(__file__).parent / "builtin.yaml"
_DEFAULT_MODE = "builder"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class ModeProfile:
    """Parsed representation of one mode entry from builtin.yaml."""

    def __init__(self, name: str, data: dict[str, Any]) -> None:
        self.name = name
        self.label: str = data.get("label", name.upper())
        self.icon: str = data.get("icon", "◆")
        self.description: str = data.get("description", "")
        self.model_preference: str = data.get("model_preference", "fast")
        self.tool_tier: str = data.get("tool_tier", "safe")
        self.output_style: str = data.get("output_style", "simple")
        self.require_auth: bool = data.get("require_auth", False)
        self.color: str = data.get("color", "#6B7280")
        self.gated_commands: list[str] = data.get("gated_commands", [])
        self.formations_default: list[str] = data.get("formations_default", [])

    def __repr__(self) -> str:
        return f"<ModeProfile {self.name} tier={self.tool_tier}>"


# ---------------------------------------------------------------------------
# Mode registry (loaded once)
# ---------------------------------------------------------------------------

_registry: dict[str, ModeProfile] | None = None


def _load_registry() -> dict[str, ModeProfile]:
    global _registry
    if _registry is not None:
        return _registry

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        # Fallback: return a minimal NODE-only registry if PyYAML not available
        _registry = {
            "node": ModeProfile(
                "node",
                {
                    "label": "NODE",
                    "icon": "⬡",
                    "description": "Default",
                    "tool_tier": "safe",
                },
            ),
        }
        return _registry

    with open(_BUILTIN_YAML, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    _registry = {name: ModeProfile(name, data) for name, data in raw.get("modes", {}).items()}
    return _registry


def all_modes() -> dict[str, ModeProfile]:
    return _load_registry()


def get_mode(name: str) -> ModeProfile | None:
    return _load_registry().get(name)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _navig_home() -> Path:
    return paths.config_dir()


def _pin_path() -> Path:
    return _navig_home() / ".mode_pin"


def _config_path() -> Path:
    return _navig_home() / "config.yaml"


def get_active_mode_name() -> str:
    """Return the active mode name from ~/.navig/config.yaml, defaulting to 'builder'."""
    cfg = _config_path()
    if not cfg.exists():
        return _DEFAULT_MODE
    try:
        import yaml  # type: ignore[import-untyped]

        with open(cfg, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("active_mode", _DEFAULT_MODE)
    except Exception:
        return _DEFAULT_MODE


def get_active_mode() -> ModeProfile:
    name = get_active_mode_name()
    return get_mode(name) or get_mode(_DEFAULT_MODE) or ModeProfile("builder", {})


def set_active_mode(name: str) -> None:
    """Persist the active_mode key to ~/.navig/config.yaml."""
    _navig_home().mkdir(parents=True, exist_ok=True)
    cfg = _config_path()

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        # Minimal write without yaml: just append/replace the key
        _write_mode_key_fallback(cfg, name)
        return

    data: dict[str, Any] = {}
    if cfg.exists():
        with open(cfg, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    data["active_mode"] = name

    with open(cfg, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def _write_mode_key_fallback(cfg: Path, name: str) -> None:
    """Minimal config write without PyYAML (edge case)."""
    lines: list[str] = []
    if cfg.exists():
        lines = cfg.read_text(encoding="utf-8").splitlines()
    new_lines = [ln for ln in lines if not ln.startswith("active_mode:")]
    new_lines.append(f"active_mode: {name}")
    cfg.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# PIN management
# ---------------------------------------------------------------------------


# PIN hash format:
#   v1 (legacy): bare SHA-256 hex string.
#   v2 (current): "v2:<salt_b64>:<hash_b64>" using PBKDF2-HMAC-SHA256.
_HASH_ALGO = "sha256"
_HASH_ITERS = 260_000
_SALT_LEN = 16


def _hash_pin(pin: str) -> str:
    """Return a PBKDF2-HMAC-SHA256 hash of *pin* with a fresh random salt (v2 format)."""
    salt = os.urandom(_SALT_LEN)
    dk = hashlib.pbkdf2_hmac(_HASH_ALGO, pin.strip().encode(), salt, _HASH_ITERS)
    return f"v2:{base64.b64encode(salt).decode()}:{base64.b64encode(dk).decode()}"


def _verify_pin_hash(pin: str, stored: str) -> bool:
    """Verify *pin* against a stored hash, supporting both v1 and v2 formats."""
    if stored.startswith("v2:"):
        try:
            _, salt_b64, hash_b64 = stored.split(":", 2)
            salt = base64.b64decode(salt_b64)
            dk = hashlib.pbkdf2_hmac(_HASH_ALGO, pin.strip().encode(), salt, _HASH_ITERS)
            return hmac.compare_digest(base64.b64decode(hash_b64), dk)
        except Exception:
            return False
    # Legacy v1: bare unsalted SHA-256 -- verify only, never write new v1 hashes.
    expected = hashlib.sha256(pin.strip().encode()).hexdigest()
    return hmac.compare_digest(stored, expected)


def has_pin() -> bool:
    return _pin_path().exists()


def set_pin(pin: str) -> None:
    """Store a PBKDF2-HMAC-SHA256 hash of the 4-digit PIN (v2 format)."""
    if not pin.isdigit() or len(pin) != 4:
        raise ValueError("PIN must be exactly 4 digits.")
    _navig_home().mkdir(parents=True, exist_ok=True)
    _pin_path().write_text(_hash_pin(pin), encoding="utf-8")


def verify_pin(pin: str) -> bool:
    """Return True if the supplied PIN matches the stored hash."""
    if not _pin_path().exists():
        return False
    stored = _pin_path().read_text(encoding="utf-8").strip()
    if not _verify_pin_hash(pin, stored):
        return False
    # Opportunistically upgrade legacy v1 (SHA-256) hashes to v2 (PBKDF2).
    if not stored.startswith("v2:"):
        _pin_path().write_text(_hash_pin(pin), encoding="utf-8")
    return True


def prompt_pin(purpose: str = "switching to a privileged mode") -> bool:
    """Interactively prompt for the PIN. Returns True on success, False on cancel/failure."""
    if not has_pin():
        ch.warning(
            "\n⚠  No PIN set. Run  navig mode pin-set  to protect privileged modes.\n"
            "   Proceeding without PIN for this session.\n"
        )
        return True  # First-time: allow but nudge

    import getpass

    ch.info(f"\n🔐  PIN required for {purpose}.")
    for attempt in range(3):
        try:
            pin = getpass.getpass("   Enter 4-digit PIN: ")
        except (KeyboardInterrupt, EOFError):
            ch.warning("\nCancelled.")
            return False
        if verify_pin(pin):
            return True
        remaining = 2 - attempt
        if remaining > 0:
            ch.warning(f"   ✗ Wrong PIN. {remaining} attempt(s) remaining.")
        else:
            ch.warning("   ✗ Wrong PIN. Access denied.")
    return False
