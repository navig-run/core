"""Root-allowlisted filesystem policy for the MCP ``desktop_filesystem`` tool (S3).

The tool is one dangerous operation with eight modes; before this module it accepted any
absolute path the process could reach. That is acceptable for a trusted single-operator
stdio server and unacceptable for a remotely reachable one, so enforcement is driven by
*exposure* and an explicit operator policy:

* remote exposure (network transport, or stdio declared ``--exposure remote``) always
  enforces — ``enforce: never`` cannot switch it off remotely;
* local stdio enforces when the operator configured roots (``auto`` defaults to that), or
  always with ``enforce: always``;
* no roots configured + enforced = deny everything with an actionable message. There is
  no fallback to "all paths" — the failure mode of a misconfiguration must be closed.

Containment is resolved-path based (``Path.resolve()``): ``..``, symlinks and junctions
that escape a root are refused. On top of containment, NAVIG-internal directories
(config, vault, state/databases, logs) and the Space deny-list (vault/sessions/state,
``.env*``, ``*credentials*``, ``*.db``/``*.sqlite*``, ``*.log``, ``secrets.*``) are denied
**even inside an allowed root** — the containment policy is primary, the deny list is the
second line, never the only one.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from navig.platform import paths
from navig.spaces.linked_context import DENY_BASENAMES, DENY_SEGMENTS

#: Credential-bearing directory names denied on top of the Space list. Matched on any
#: path segment (lower-cased), never on a substring of the whole path.
EXTRA_DENY_SEGMENTS: frozenset[str] = frozenset(
    {".ssh", ".aws", ".gnupg", ".kube", ".docker", "credentials", "secrets"}
)

#: Credential-bearing basenames (fnmatch).
EXTRA_DENY_BASENAMES: tuple[str, ...] = (
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "*.kdbx",
)

#: Modes whose second argument is a path that must also be contained.
_TWO_PATH_MODES = frozenset({"copy", "move"})


@dataclass(frozen=True)
class FilesPolicy:
    """Resolved policy for one server process."""

    roots: tuple[Path, ...] = ()
    enforce: bool = False
    remote: bool = False

    def check(self, value: str | os.PathLike[str], *, label: str = "path") -> str | None:
        """Return a human-readable denial, or None when the path is allowed."""
        if not self.enforce:
            return None
        raw_text = str(value or "").strip()
        if not raw_text:
            return f"{label} is required"
        raw = Path(os.path.expandvars(os.path.expanduser(_strip_windows_prefix(raw_text))))
        if not raw.is_absolute():
            return f"{label} must be an absolute path"
        if not self.roots:
            return (
                "filesystem access is denied: no allowed roots are configured. Set "
                "`mcp.files.roots` to the directories this instance may touch "
                "(for example `navig config set mcp.files.roots '[\"/srv/work\"]'`)."
            )
        target = _canonical(raw)
        inside = any(target == root or target.is_relative_to(root) for root in self.roots)
        if not inside:
            allowed = ", ".join(str(root) for root in self.roots)
            return f"{label} is outside the allowed roots ({allowed})"
        reason = denied_reason(target)
        if reason:
            return f"{label}: {reason}"
        return None


def _strip_windows_prefix(text: str) -> str:
    """Reduce Win32 extended-length/device prefixes to the path they denote.

    ``Path.resolve()`` does not reliably collapse ``\\\\?\\`` or ``\\\\.\\`` on every
    Python build, and a prefix that survives comparison is a containment bypass. UNC
    extended paths are converted back to ``\\\\server\\share`` form.
    """
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[len("\\\\?\\UNC\\") :]
    if text.startswith(("\\\\?\\", "\\\\.\\")):
        return text[4:]
    return text


def _canonical(path: Path) -> Path:
    """Resolve symlinks/.. without requiring existence (writes target new files)."""
    path = Path(_strip_windows_prefix(str(path)))
    try:
        return path.resolve(strict=False)
    except OSError:
        # A broken link or an unreadable component: use the lexical absolute form so
        # containment still compares something deterministic.
        return Path(os.path.abspath(os.path.normpath(str(path))))


def _normcase(path: Path) -> str:
    return os.path.normcase(str(_canonical(path)))


def _internal_dirs() -> tuple[Path, ...]:
    found: list[Path] = []
    for getter in (
        paths.config_dir,
        paths.vault_dir,
        paths.data_dir,
        paths.store_dir,
        paths.log_dir,
    ):
        try:
            found.append(_canonical(Path(getter())))
        except Exception:  # noqa: BLE001 - a missing path helper must not open access
            continue
    return tuple(found)


def denied_reason(path: Path) -> str | None:
    """Why *path* is intrinsically sensitive (independent of the roots)."""
    target = _canonical(path)
    for internal in _internal_dirs():
        try:
            if target == internal or target.is_relative_to(internal):
                return f"it is inside NAVIG's internal directory ({internal})"
        except OSError:
            continue
    segments = [part.lower() for part in target.parts[:-1]]
    if any(part in DENY_SEGMENTS or part in EXTRA_DENY_SEGMENTS for part in segments):
        return "it passes through a denied directory (vault, sessions, state, credentials, …)"
    name = target.name.lower()
    patterns = (*DENY_BASENAMES, *EXTRA_DENY_BASENAMES)
    if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
        return "it matches a denied credential/database/log filename"
    return None


def policy_from_config(config: object, transport: object) -> FilesPolicy:
    """Build the policy for this process from config + transport context."""

    def _get(key: str, default: object) -> object:
        try:
            return config.get(key, default)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return default

    raw_roots = _get("mcp.files.roots", []) or []
    if isinstance(raw_roots, str):
        raw_roots = [raw_roots]
    roots: list[Path] = []
    for item in raw_roots if isinstance(raw_roots, (list, tuple)) else []:
        text = str(item or "").strip()
        if not text:
            continue
        roots.append(_canonical(Path(os.path.expandvars(os.path.expanduser(text)))))
    setting = str(_get("mcp.files.enforce", "auto") or "auto").strip().lower()
    exposure = str(getattr(transport, "exposure", "local"))
    mode = str(getattr(transport, "mode", "stdio"))
    remote = exposure == "remote" or mode != "stdio"
    if setting == "always":
        enforce = True
    elif setting == "never":
        # Remote exposure must fail closed even if the local config says never.
        enforce = remote
    else:
        enforce = remote or bool(roots)
    return FilesPolicy(roots=tuple(roots), enforce=enforce, remote=remote)
