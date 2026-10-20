"""Vault-backed host secrets (Gate 0F) — host YAML stops holding plaintext passwords.

Host configuration files (``~/.navig/hosts/<name>.yaml``) used to persist SSH and
database passwords verbatim: ``create_server_config`` wrote ``ssh_password`` into the
dict and ``HostManager.save`` ``atomic_write_yaml``'d it to disk. This module moves
every such value into the existing NAVIG vault (``navig.vault``) and leaves only a
*reference* behind::

    ssh_password_ref: "vault:host/<name>/ssh_password"

Public API
----------
``put_host_secret(name, value, key=...)``
    Encrypt *value* in the vault, return the ``vault:…`` reference string.
``resolve_host_secret(entry, key)``
    Backward compatible: plaintext key wins (legacy files keep working unchanged),
    then ``key + "_ref"`` resolves through the vault, else ``None``.
``HOST_SECRET_KEYS``
    The real set of password-ish fields a host YAML may carry (dotted paths).
``secrets_for_storage(config, host_name)`` / ``resolve_host_secrets(config)``
    The write-side / read-side bulk forms used by the choke points below.
``HostSecretError``
    The only failure mode: raised whenever a secret cannot be stored or resolved.
    **It never embeds the secret value**, and no code path here logs one.

Compatibility shim (the routing decision, documented per plan)
--------------------------------------------------------------
There are ~10 readers of ``ssh_password`` / ``database.root_password`` /
``database.password`` across ``core/`` (discovery, connection_pool, commands/db,
commands/host, commands/server, agent devops tools, deck routes, AppManager's
legacy "host config IS the app config" fallback…). Routing them one by one would
touch every SSH and DB call site, and several (``SSHConnectionPool``, the deck's
``ssh_config`` dicts) need the dict shape *unchanged*. So the routing happens at
the single choke point instead:

* ``HostManager.load`` (``navig.core.hosts``) calls ``resolve_host_secrets`` —
  every reader that builds its ``ssh_config``/``server_config`` from
  ``load_host_config`` receives the plaintext under the ORIGINAL key, exactly the
  shape it always had. Legacy plaintext files resolve with zero changes (plaintext
  wins; no vault is touched at all).
* ``HostManager.save`` calls ``secrets_for_storage`` — no writer can persist
  plaintext again. Writers that bypass ``HostManager`` (``navig.migration.save_config``)
  call it themselves, and ``ConfigManager.create_server_config`` sanitises its dict
  before the first save.

Fail-safe: if the vault is unavailable, ``put_host_secret`` RAISES
``HostSecretError`` instead of falling back to plaintext — a save that cannot
vault a password refuses to write the file at all.

Out of scope (documented, deliberate): individual app files
(``apps/<name>.yaml``) and the embedded ``apps:`` block of a host YAML keep their
own fields — their readers do not go through ``HostManager``. Host-level
``database.*`` above IS in scope. A vault reference itself (``vault:host/…``) is a
label, not a secret; ``ssh_password_ref`` is already scrubbed by the MCP inventory
redaction (key contains "password").
"""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

#: Password-ish fields of a host YAML, as dotted paths from the mapping root.
#: Identified from ``create_server_config`` (``ssh_password``, the default
#: ``database.password``) and ``commands/host.py``'s ``database.root_password``
#: store — the fields ``host.schema.json`` and the MCP inventory already treat
#: as credentials.
HOST_SECRET_KEYS: tuple[str, ...] = (
    "ssh_password",
    "database.root_password",
    "database.password",
)

#: Suffix marking a vault reference for the key of the same path.
REF_SUFFIX = "_ref"

#: Prefix of every reference string this module writes.
REF_PREFIX = "vault:"


class HostSecretError(RuntimeError):
    """A host secret could not be stored in, or resolved from, the vault.

    Messages name the host, the key and the failure class — never the value.
    """


# ---------------------------------------------------------------------------
# Vault seam
# ---------------------------------------------------------------------------


def _vault_core():
    """Import ``navig.vault.core`` at call time.

    The package ``navig.vault.core`` aliases itself onto ``navig_vault.core``
    (module identity is load-bearing: tests assign ``_vault = None`` and patch
    ``get_vault`` by dotted path), so the attribute must be fetched at call time
    through THIS module, not re-exported at import time.
    """
    from navig.vault import core as vault_core  # noqa: PLC0415 — lazy: keep `navig help` fast

    return vault_core


# ---------------------------------------------------------------------------
# Dotted-path helpers (HOST_SECRET_KEYS carries nested paths)
# ---------------------------------------------------------------------------


def _get_path(entry: dict[str, Any], key: str) -> Any:
    node: Any = entry
    for part in key.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _set_path(entry: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    node = entry
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _del_path(entry: dict[str, Any], key: str) -> None:
    parts = key.split(".")
    node = entry
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            return
    node.pop(parts[-1], None)


def _label(host_name: str, key: str) -> str:
    return f"host/{host_name}/{key}"


def _ref_to_label(ref: str) -> str:
    """``vault:host/x/y`` (what this module writes) or ``vault://hosts/x/ssh`` → label."""
    if ref.startswith("vault://"):
        from navig_vault.refs import parse_ref  # noqa: PLC0415 — one grammar for both forms

        return parse_ref(ref).label
    return ref[len(REF_PREFIX) :] if ref.startswith(REF_PREFIX) else ref


# ---------------------------------------------------------------------------
# Write side
# ---------------------------------------------------------------------------


def put_host_secret(name: str, value: str, key: str = "ssh_password") -> str:
    """Encrypt *value* in the vault under this host and return its reference.

    Returns a reference string like ``vault:host/<name>/ssh_password``. The vault
    label is ``host/<name>/<key>`` (the ``vault:`` prefix is presentation only).

    Raises:
        HostSecretError: the vault could not store the value. Never falls back
            to returning/plaintext — the caller must not write the secret.
    """
    if not isinstance(value, str) or value == "":
        raise HostSecretError(f"refusing to vault an empty secret for host {name!r} ({key})")
    label = _label(name, key)
    payload = json.dumps({"value": value}).encode("utf-8")
    try:
        vault = _vault_core().get_vault()
        vault.put(label, payload)
    except HostSecretError:
        raise
    except Exception as exc:  # noqa: BLE001 — any vault failure is a clear, typed error
        raise HostSecretError(
            f"cannot store host secret {key!r} for host {name!r} in the vault "
            f"({type(exc).__name__}) — refusing to write it in plaintext"
        ) from exc
    return f"{REF_PREFIX}{label}"


def secrets_for_storage(config: dict[str, Any], host_name: str) -> dict[str, Any]:
    """Return a copy of *config* with every plaintext host secret vaulted.

    For each :data:`HOST_SECRET_KEYS` path holding a non-empty value: put it in
    the vault, write ``<key>_ref`` at the same path, delete the plaintext key.
    Already-migrated (ref-only) entries are left untouched; absent/empty values
    are skipped (no vault noise for ``ssh_password: null`` defaults).

    Raises:
        HostSecretError: the vault refused one of the values — the returned dict
            is discarded by the caller and NOTHING is written to disk.
    """
    out = copy.deepcopy(config)
    for key in HOST_SECRET_KEYS:
        plain = _get_path(out, key)
        if plain is None or plain == "":
            continue
        value = plain if isinstance(plain, str) else str(plain)
        ref = put_host_secret(host_name, value, key=key)
        _set_path(out, f"{key}{REF_SUFFIX}", ref)
        _del_path(out, key)
    return out


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------


def _reveal_ref(ref: str, key: str, host_hint: str) -> str:
    """Decrypt the vault item *ref* points at. Raises HostSecretError, no values."""
    label = _ref_to_label(ref)
    try:
        vault = _vault_core().get_vault()
        payload = vault.get_bytes(label)
    except KeyError as exc:
        # Not in this machine's vault: an install enrolled with a vault authority
        # asks it (grant-scoped, audited there); an unenrolled one fails as before.
        remote = _resolve_from_authority(label, key, host_hint)
        if remote is not None:
            return remote
        raise HostSecretError(
            f"vault item for host {host_hint!r} key {key!r} is missing "
            f"(label {label!r}) — the secret was deleted or never stored"
        ) from exc
    except Exception as exc:  # noqa: BLE001 — locked/unreadable vault → typed error
        raise HostSecretError(
            f"vault unavailable while resolving host {host_hint!r} key {key!r} "
            f"({type(exc).__name__})"
        ) from exc

    value: Any = None
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        # Tolerate a raw-text payload rather than crashing on an older store.
        data = payload.decode("utf-8", errors="replace")
    if isinstance(data, dict):
        value = data.get("value")
    elif isinstance(data, str):
        value = data
    if not value:
        raise HostSecretError(
            f"vault item for host {host_hint!r} key {key!r} (label {label!r}) "
            "holds no usable value"
        )
    return str(value)


def _resolve_from_authority(label: str, key: str, host_hint: str) -> str | None:
    """The value from the vault authority, ``None`` when this install is not enrolled.

    Raises HostSecretError (class names only, never a value) when enrolled but the
    authority refuses or cannot be reached with nothing valid cached.
    """
    try:
        from navig.vault.federation_client import client  # noqa: PLC0415

        fed = client()
        if not fed.enrolled():
            return None
        return fed.resolve(f"vault:{label}")
    except KeyError:
        return None
    except Exception as exc:  # noqa: BLE001 — refused / unreachable → typed, value-free
        raise HostSecretError(
            f"vault authority could not provide host {host_hint!r} key {key!r} "
            f"({type(exc).__name__}: {exc})"
        ) from exc


def resolve_host_secret(entry: dict[str, Any], key: str) -> str | None:
    """Resolve *key* from a host mapping — plaintext first, then its vault ref.

    Backward compatible by construction:

    * plaintext ``key`` present → returned unchanged (legacy files keep working);
    * else ``key + "_ref"`` present → decrypted from the vault;
    * else ``None`` (nothing configured).

    ``key`` may be a dotted path (``database.root_password``).

    Raises:
        HostSecretError: a ref exists but the vault cannot deliver the value —
            silently returning ``None`` would demote a password-auth host to a
            confusing key-auth failure, so the operator gets the real reason.
    """
    plain = _get_path(entry, key)
    if isinstance(plain, str) and plain:
        return plain
    ref = _get_path(entry, f"{key}{REF_SUFFIX}")
    if isinstance(ref, str) and ref:
        host_hint = str(entry.get("name") or "<unknown>")
        return _reveal_ref(ref, key, host_hint)
    return None


def resolve_host_secrets(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve every :data:`HOST_SECRET_KEYS` ref in *config*, in place.

    Called by ``HostManager.load`` — the single routing point every reader of a
    host config passes through. Returns *config* for chaining. Entries with no
    ref and no plaintext (the common case) never touch the vault.
    """
    for key in HOST_SECRET_KEYS:
        value = resolve_host_secret(config, key)
        if value is not None:
            _set_path(config, key, value)
    return config


# ---------------------------------------------------------------------------
# Migration primitives (drive `navig security migrate-host-secrets`)
# ---------------------------------------------------------------------------

#: Backup suffix written by ``migrate_host_file``: ``<name>.yaml.bak.<stamp>``.
#: The stamp carries microseconds so two applies in the same second never
#: overwrite each other's backup, and lexical sort is chronological.
BACKUP_INFIX = ".yaml.bak."


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read one YAML mapping with the house retrying reader."""
    from navig.core.yaml_io import read_text_retrying  # noqa: PLC0415

    data = yaml.safe_load(read_text_retrying(path))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise HostSecretError(f"{path.name}: expected a YAML mapping at the root")
    return data


def scan_plaintext_host_secrets(hosts_dir: Path) -> list[dict[str, Any]]:
    """List every plaintext :data:`HOST_SECRET_KEYS` value under *hosts_dir*.

    Each row is report-safe by construction — it carries the **length** of the
    value, never the value:

        {host, key, file (Path), ref, length}
    """
    rows: list[dict[str, Any]] = []
    if not hosts_dir.is_dir():
        return rows
    for path in sorted(hosts_dir.glob("*.yaml")):
        if BACKUP_INFIX in path.name:
            continue  # a .bak. file never matches *.yaml, but be explicit
        try:
            data = _load_yaml(path)
        except (HostSecretError, OSError, yaml.YAMLError):
            continue  # unreadable/foreign file — never a secret carrier we invent
        for key in HOST_SECRET_KEYS:
            plain = _get_path(data, key)
            if plain is None or plain == "":
                continue
            value = plain if isinstance(plain, str) else str(plain)
            rows.append(
                {
                    "host": path.stem,
                    "key": key,
                    "file": path,
                    "ref": f"{REF_PREFIX}{_label(path.stem, key)}",
                    "length": len(value),
                }
            )
    return rows


def migrate_host_file(path: Path) -> list[dict[str, Any]]:
    """Vault every plaintext secret in ONE host YAML and rewrite it with refs.

    Order is the approved one: put in the vault → ``<name>.yaml.bak.<stamp>``
    backup → atomic rewrite carrying only ``*_ref``. If any put fails the backup
    and the rewrite never happen, so the file stays exactly as it was.

    Returns the rows migrated (value lengths only). Raises ``HostSecretError``
    or ``OSError`` on failure — never partially written.
    """
    from navig.core.yaml_io import atomic_write_yaml  # noqa: PLC0415

    data = _load_yaml(path)
    plaintext: list[tuple[str, Any]] = []
    for key in HOST_SECRET_KEYS:
        plain = _get_path(data, key)
        if plain is None or plain == "":
            continue
        plaintext.append((key, plain))
    if not plaintext:
        return []

    rows: list[dict[str, Any]] = []
    refs: list[tuple[str, str]] = []
    for key, plain in plaintext:
        value = plain if isinstance(plain, str) else str(plain)
        ref = put_host_secret(path.stem, value, key=key)
        refs.append((key, ref))
        rows.append(
            {
                "host": path.stem,
                "key": key,
                "file": path,
                "ref": ref,
                "length": len(value),
            }
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    # `<host>.yaml.bak.<stamp>` — i.e. `<filename>.bak.<stamp>`, which is also
    # what BACKUP_INFIX (".yaml.bak.") splits on when rolling back.
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup)

    for key, ref in refs:
        _set_path(data, f"{key}{REF_SUFFIX}", ref)
        _del_path(data, key)
    atomic_write_yaml(data, path, allow_unicode=True)
    return rows


def find_host_backups(hosts_dir: Path) -> dict[str, Path]:
    """Newest ``<name>.yaml.bak.*`` per host file → the backup path to restore."""
    newest: dict[str, Path] = {}
    if not hosts_dir.is_dir():
        return newest
    for backup in sorted(hosts_dir.glob(f"*{BACKUP_INFIX}*")):
        base = backup.name.split(BACKUP_INFIX, 1)[0]
        # `<name>.yaml.bak.<stamp>` → base is the stem; tolerate a stray name
        # that already carries the extension.
        host_file = base if base.endswith(".yaml") else f"{base}.yaml"
        # sorted() order + plain overwrite: lexical order of the stamp is
        # chronological, so the last one seen is the newest.
        newest[host_file] = backup
    return newest


def restore_host_backup(hosts_dir: Path, host_file: str, backup: Path) -> Path:
    """Copy *backup* over ``hosts_dir/<host_file>`` byte-for-byte."""
    target = hosts_dir / host_file
    shutil.copy2(backup, target)
    return target


def purge_host_backups(hosts_dir: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Delete every ``<name>.yaml.bak.*`` whose host file is PROVEN served by the vault.

    A migration backup is a byte-for-byte copy of the plaintext original, so it is
    the one place the secret still sits on disk after ``--apply``. It is also the
    only rollback path — so a backup is deleted only when its host file exists,
    parses, and every ``*_ref`` in it decrypts from the vault right now. Anything
    less keeps the backup and reports why (reasons never carry a value).

    Returns ``(purged, kept)`` where ``kept`` is ``[(backup, reason), ...]``.
    """
    purged: list[Path] = []
    kept: list[tuple[Path, str]] = []
    if not hosts_dir.is_dir():
        return purged, kept
    for backup in sorted(hosts_dir.glob(f"*{BACKUP_INFIX}*")):
        base = backup.name.split(BACKUP_INFIX, 1)[0]
        host_file = hosts_dir / (base if base.endswith(".yaml") else f"{base}.yaml")
        if not host_file.is_file():
            kept.append((backup, f"{host_file.name} is missing — the backup is the only copy"))
            continue
        try:
            data = _load_yaml(host_file)
            for key in HOST_SECRET_KEYS:
                ref = _get_path(data, f"{key}{REF_SUFFIX}")
                if isinstance(ref, str) and ref:
                    # The ref itself, not resolve_host_secret: a leftover plaintext
                    # value would win there and hide a vault that cannot deliver.
                    _reveal_ref(ref, key, host_file.stem)
        except (HostSecretError, OSError, yaml.YAMLError) as exc:
            kept.append((backup, f"{host_file.name} does not resolve from the vault ({type(exc).__name__})"))
            continue
        try:
            backup.unlink()
        except OSError as exc:
            kept.append((backup, f"could not delete ({type(exc).__name__})"))
            continue
        purged.append(backup)
    return purged, kept
