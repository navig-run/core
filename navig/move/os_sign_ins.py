"""NAVIG OS sign-ins across a move — through NAVIG OS's own code, never a copy of its crypto.

``~/.navig-os/credentials.enc`` is sealed with a key derived from the machine's hardware id,
so the file opens nowhere else. NAVIG OS ships a small CLI
(``packages/shared/src/credentials/transfer-cli.ts``) that decrypts it through
``SecureStorageBackend`` (export) and re-seals each sign-in on the new machine (import).
This module only finds and runs it; the plaintext crosses a pipe into the encrypted move
bundle and back, and never touches disk.

The runner is found in a prebuilt server tree (its own bundled bun) or a source checkout
(bun on PATH). When neither exists the sign-ins stay behind and the move says which
connections to sign in to again — the same outcome as before this existed, said plainly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLI_REL = Path("packages") / "shared" / "src" / "credentials" / "transfer-cli.ts"
# PBKDF2 (100k iterations) runs per store open, sometimes twice: seconds, not minutes.
_TIMEOUT_S = 120


class OsCredentialsError(Exception):
    """The NAVIG OS transfer ran and failed — the message is for the person."""


@dataclass(frozen=True)
class Runner:
    """``[bun, transfer-cli.ts]`` plus where it was found (for the report)."""

    argv: tuple[str, ...]
    where: str

    def _run(self, command: str, os_dir: Path, stdin: bytes | None = None) -> bytes:
        # The CLI takes the dir explicitly: NAVIG_CONFIG_DIR means ~/.navig to the brain
        # and ~/.navig-os to NAVIG OS, so inheriting it would point at the wrong store.
        env = {k: v for k, v in os.environ.items() if k != "NAVIG_CONFIG_DIR"}
        try:
            proc = subprocess.run(
                [*self.argv, command, "--dir", str(os_dir)],
                input=stdin,
                capture_output=True,
                timeout=_TIMEOUT_S,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OsCredentialsError(f"NAVIG OS sign-in {command} could not run: {exc}") from exc
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip() or f"exit {proc.returncode}"
            raise OsCredentialsError(f"NAVIG OS sign-in {command} failed: {detail}")
        return proc.stdout

    def export(self, os_dir: Path) -> bytes:
        """The decrypted transfer document, as bytes. Raises on any unreadable sign-in."""
        out = self._run("export", os_dir)
        doc = json.loads(out.decode("utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("credentials"), list):
            raise OsCredentialsError("NAVIG OS sign-in export returned something unexpected")
        return out

    def import_(self, os_dir: Path, payload: bytes) -> int:
        """Re-seal every sign-in in *payload* on this machine; the count that read back."""
        out = self._run("import", os_dir, stdin=payload)
        try:
            return int(json.loads(out.decode("utf-8"))["imported"])
        except (ValueError, KeyError, TypeError) as exc:
            raise OsCredentialsError(
                "NAVIG OS sign-in import returned something unexpected"
            ) from exc


def find_runner() -> Runner | None:
    """The transfer CLI with a bun to run it, or ``None`` when NAVIG OS is not here."""
    from navig.commands.os_cmd import find_installed_server, find_os_dir

    tree = find_installed_server()
    if tree is not None:
        bun = tree / "vendor" / "bun" / ("bun.exe" if os.name == "nt" else "bun")
        cli = tree / CLI_REL
        if bun.is_file() and cli.is_file():
            return Runner((str(bun), str(cli)), str(tree))
    checkout = find_os_dir()
    if checkout is not None:
        cli = checkout / CLI_REL
        bun = shutil.which("bun")
        if bun and cli.is_file():
            return Runner((bun, str(cli)), str(checkout))
    return None


def carried_slugs(payload: bytes) -> set[str]:
    """Connection slugs a transfer document carries — ids only, never a secret value."""
    doc: Any = json.loads(payload.decode("utf-8"))
    slugs = set()
    for entry in doc.get("credentials") or []:
        ident = entry.get("id") if isinstance(entry, dict) else None
        slug = ident.get("connectionSlug") if isinstance(ident, dict) else None
        if slug:
            slugs.add(str(slug))
    return slugs


__all__ = ["CLI_REL", "OsCredentialsError", "Runner", "carried_slugs", "find_runner"]
