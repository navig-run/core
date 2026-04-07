"""NAVIG Blackbox Export — write .navbox archives, optionally encrypted."""

from __future__ import annotations

from pathlib import Path

from .bundle import write_bundle
from .types import Bundle

__all__ = ["export_bundle"]


def export_bundle(
    bundle: Bundle,
    output: Path,
    encrypted: bool = False,
) -> Path:
    """Write a Bundle to disk as a ``.navbox`` ZIP archive.

    Parameters
    ----------
    bundle    : The bundle to export.
    output    : Output file path.  ``.navbox`` extension added if missing.
    encrypted : If True, encrypt the archive bytes with the vault master key
                and write a ``.navbox.enc`` file instead.

    Returns
    -------
    Path
        The final output path.
    """
    # Write the standard ZIP first
    zip_path = write_bundle(bundle, output)

    if not encrypted:
        return zip_path

    # Encrypt with vault master key via CryptoEngine.seal()
    try:
        from navig.vault.core import get_vault
        from navig.vault.crypto import CryptoEngine

        vault = get_vault()
        master = vault.engine().derive_key()  # machine fingerprint
        raw = zip_path.read_bytes()
        sealed = CryptoEngine.seal(master, raw, b"navbox")

        enc_path = zip_path.with_suffix(".navbox.enc")
        enc_path.write_bytes(sealed)
        zip_path.unlink()  # remove plaintext ZIP after encryption
        return enc_path

    except Exception as exc:
        # If encryption fails for any reason, return the unencrypted archive
        # with a warning — never silently lose the data
        from rich.console import Console

        Console().print(
            f"[yellow]Warning:[/yellow] Encryption failed ({exc}). "
            "Bundle saved as unencrypted .navbox."
        )
        return zip_path
