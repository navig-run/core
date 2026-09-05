"""
NAVIG Configuration Backup & Export Commands

Backup and restore NAVIG's own configuration (hosts, apps, settings).
NOT to be confused with server-side database backups.
"""

import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from navig import console_helper as ch
from navig.console_helper import format_bytes as _format_size


def _redact_dict(data: dict[str, Any], sensitive_keys: list[str]) -> None:
    """Backward-compatible in-place redaction helper for tests/callers."""
    sensitive = [entry.lower() for entry in sensitive_keys]

    def _redact(value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, child in value.items():
                key_lower = str(key).lower()
                if any(marker in key_lower for marker in sensitive):
                    redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = _redact(child)
            return redacted
        if isinstance(value, list):
            return [_redact(child) for child in value]
        return value

    redacted = _redact(data)
    data.clear()
    data.update(redacted)


def _require_input_file(options: dict[str, Any]) -> Path | None:
    """Resolve required input file option and emit user-friendly error if missing."""
    file_value = options.get("file")
    if not file_value:
        ch.error("Input file is required. Use --file <path>.")
        return None
    return Path(file_value)


def _safe_extract_tar(tar: tarfile.TarFile, destination: Path) -> None:
    """Safely extract tar members, blocking path traversal outside destination."""
    destination = destination.resolve()
    for member in tar.getmembers():
        member_path = (destination / member.name).resolve()
        if destination != member_path and destination not in member_path.parents:
            raise ValueError(f"Unsafe archive entry: {member.name}")
    tar.extractall(destination)


def _get_backup_dir() -> Path:
    """Get the directory for NAVIG config backups."""
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    backup_dir = config_manager.config_dir / "exports"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _collect_configs(
    include_global: bool = True, failures: list[str] | None = None
) -> dict[str, Any]:
    """
    Collect all NAVIG configuration data.

    Args:
        include_global: include the global config section
        failures: optional list, appended with a description of every host/app whose
            config could not be read. Pass one if you are going to tell the user what
            the export actually contains — a per-item ``ch.warning`` scrolls off the
            screen above the final ``✓`` and the summary counted the config directory
            instead of the collected data, so an export missing three hosts still
            reported all five.

    Returns:
        Dictionary containing all configuration data
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    data = {
        "version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "hosts": {},
        "apps": {},
    }

    # Include global config
    if include_global:
        data["global_config"] = config_manager.global_config.copy()
        # Remove sensitive data from global config
        if "openrouter_api_key" in data["global_config"]:
            data["global_config"]["openrouter_api_key"] = "[REDACTED]"

    # Collect host configs
    for host_name in config_manager.list_hosts():
        try:
            host_config = config_manager.load_host_config(host_name)
            # Redact sensitive data
            safe_config = host_config.copy()
            if "database" in safe_config and "password" in safe_config["database"]:
                safe_config["database"] = safe_config["database"].copy()
                safe_config["database"]["password"] = "[REDACTED]"
            data["hosts"][host_name] = safe_config
        except Exception as e:
            ch.warning(f"Could not load host config '{host_name}': {e}")
            if failures is not None:
                failures.append(f"host {host_name}: {e}")

    # Collect app configs
    for host_name in config_manager.list_hosts():
        data["apps"][host_name] = {}
        for app_name in config_manager.list_apps(host_name):
            try:
                app_config = config_manager.load_app_config(host_name, app_name)
                # Redact sensitive data
                safe_config = app_config.copy()
                if "database" in safe_config and "password" in safe_config.get("database", {}):
                    safe_config["database"] = safe_config["database"].copy()
                    safe_config["database"]["password"] = "[REDACTED]"
                data["apps"][host_name][app_name] = safe_config
            except Exception as e:
                ch.warning(f"Could not load app config '{app_name}': {e}")
                if failures is not None:
                    failures.append(f"app {host_name}/{app_name}: {e}")

    return data


def _create_archive(output_path: Path, include_secrets: bool = False) -> dict[str, Any]:
    """
    Create a compressed archive of all NAVIG configuration.

    Args:
        output_path: Path for the output archive
        include_secrets: If True, include unredacted secrets

    Returns:
        ``{"dropped": [...], "hosts": N, "apps": N}`` — the files EXCLUDED because they
        could not be redacted, and the counts of what actually went into the archive.
        Previously returned a bool that was always ``True`` and was discarded at the
        call site, so there was no channel for "the archive is incomplete" to reach the
        user, and the caller counted the config directory instead. Raises rather than
        shipping an archive that still contains plaintext secrets.
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create manifest
        manifest = {
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "include_secrets": include_secrets,
            "contents": [],
        }

        # Copy hosts and apps from EVERY config directory, in priority order.
        #
        # This used to copy only `config_manager.config_dir` — a single directory —
        # while `list_hosts()` merges app-specific AND global config
        # (`get_config_directories()`). Run inside a project, the two disagreed:
        #
        #     list_hosts()      -> ['globalhost', 'projhost']
        #     archive contained -> projhost only
        #
        # So `--format json` (which iterates list_hosts) and `--format archive`
        # exported different sets of hosts from the same command, and the archive
        # silently dropped every global host. Measured before this was changed.
        #
        # Priority order is preserved: `get_config_directories()` returns app config
        # first, and a higher-priority file WINS — which is how NAVIG resolves a host
        # today, so the archive now contains exactly the config that is in effect.
        # A lower-priority file it shadows is not a failure, but it is not in the
        # archive either, so it is recorded rather than dropped in silence.
        config_dirs = config_manager.get_config_directories()
        shadowed: list[str] = []

        for section in ("hosts", "apps"):
            dst_section = tmpdir_path / section
            copied_any = False
            for src_root in config_dirs:
                src_section = src_root / section
                if not src_section.is_dir():
                    continue
                for src in sorted(src_section.rglob("*")):
                    if not src.is_file():
                        continue
                    rel = src.relative_to(src_section)
                    dst = dst_section / rel
                    if dst.exists():
                        shadowed.append(f"{section}/{rel.as_posix()} in {src_root}")
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    copied_any = True
            if copied_any:
                manifest["contents"].append(section)

        if shadowed:
            manifest["shadowed"] = shadowed

        # Copy global config. NOT merged across directories: config.yaml is a single
        # settings document, and merging two of them is a semantic decision this
        # command has no basis to make. The one in effect (base_dir's) is exported,
        # and the manifest records which directory it came from so a restore is not
        # guesswork.
        global_config_file = config_manager.config_dir / "config.yaml"
        if global_config_file.exists():
            shutil.copy(global_config_file, tmpdir_path / "config.yaml")
            manifest["contents"].append("config.yaml")
        manifest["config_yaml_source"] = str(config_manager.config_dir)
        manifest["config_directories"] = [str(d) for d in config_dirs]

        # Redact secrets if not including them
        dropped: list[str] = []
        if not include_secrets:
            dropped = _redact_secrets_in_dir(tmpdir_path)
            if dropped:
                # Recorded IN the archive as well as printed: whoever opens this file
                # later is the one who needs to know it is incomplete, and they will
                # not have the terminal output.
                manifest["dropped_unredactable"] = dropped

        # Count what is ACTUALLY in the staging dir, after redaction dropped anything
        # it could not prove clean. Counting the config directory again instead — which
        # is what the summary used to do — reports files that never made it in.
        counts = {
            "hosts": len(list((tmpdir_path / "hosts").glob("*.yaml")))
            if (tmpdir_path / "hosts").is_dir()
            else 0,
            "apps": len(list((tmpdir_path / "apps").rglob("*.yaml")))
            if (tmpdir_path / "apps").is_dir()
            else 0,
        }
        manifest["counts"] = counts

        # Write manifest
        manifest_path = tmpdir_path / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # Create tarball
        with tarfile.open(output_path, "w:gz") as tar:
            tar.add(tmpdir_path, arcname="navig-config")

    return {"dropped": dropped, "shadowed": shadowed, **counts}


def _redact_secrets_in_dir(dir_path: Path) -> list[str]:
    """Redact sensitive data in all YAML files in a staging directory.

    Returns the names of files that had to be DROPPED because they could not be
    proven clean.

    This used to ``except Exception: pass`` — "skip files that can't be processed" —
    which left the file in the archive with its plaintext credentials intact. A single
    YAML typo in a hand-edited host file was enough: ``safe_load`` raises, the handler
    swallows it, and an export the user asked to be redacted shipped a real password.
    Verified before this was fixed, with a host file whose only fault was an unclosed
    bracket.

    So: a file we cannot prove is clean does not ship. That trade is deliberate —
    a missing file is visible and recoverable (the original is untouched on disk),
    a leaked credential is neither. If it cannot even be dropped, the export fails
    rather than shipping the secret.
    """
    import yaml

    from navig.core.security import redact_dict
    from navig.core.yaml_io import atomic_write_yaml

    sensitive_keys = ["password", "api_key", "openrouter_api_key", "secret", "token"]
    dropped: list[str] = []

    for yaml_file in dir_path.rglob("*.yaml"):
        rel = yaml_file.relative_to(dir_path).as_posix()
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if data is None or data == "":
                continue  # empty file — provably nothing to redact
            if not isinstance(data, dict):
                # redact_dict only understands mappings, so a non-mapping document
                # cannot be proven clean either. Same rule as a parse failure.
                raise ValueError(f"top-level YAML is {type(data).__name__}, not a mapping")

            atomic_write_yaml(redact_dict(data, sensitive_keys=sensitive_keys), yaml_file)
        except Exception as exc:  # noqa: BLE001 - any failure means "cannot prove clean"
            try:
                yaml_file.unlink()
            except OSError as unlink_exc:
                raise RuntimeError(
                    f"Refusing to build a redacted export: {rel} could not be redacted "
                    f"({exc}) and could not be removed from the staging copy "
                    f"({unlink_exc}). Shipping it would leak its plaintext secrets."
                ) from unlink_exc
            dropped.append(rel)

    return dropped


# Placeholders written into an export made WITHOUT --include-secrets: redact_dict (archive
# path) writes "***REDACTED***"; the JSON export path writes "[REDACTED]". Neither is a real
# secret and neither may ever be persisted back into config.
_REDACTION_MARKERS = ("***REDACTED***", "[REDACTED]")


def _is_redaction_marker(value: object) -> bool:
    return value in _REDACTION_MARKERS


def _restore_redacted_secrets(new_cfg: Any, live_cfg: Any) -> Any:
    """Never persist a redaction placeholder when importing.

    Importing an export made without ``--include-secrets`` with ``--replace`` would
    otherwise write the redaction marker straight over a LIVE secret (secret loss) — or
    poison a fresh host with the placeholder. Replace any marker value with the current
    live value at the same key (recursively); drop the key when there is no live value.
    """
    if not isinstance(new_cfg, dict):
        return new_cfg
    live = live_cfg if isinstance(live_cfg, dict) else {}
    out: dict[str, Any] = {}
    for key, value in new_cfg.items():
        if isinstance(value, dict):
            out[key] = _restore_redacted_secrets(value, live.get(key))
        elif _is_redaction_marker(value):
            live_value = live.get(key)
            if live_value is not None and not _is_redaction_marker(live_value):
                out[key] = live_value  # keep the real live secret
            # else: drop the placeholder — never write a redaction marker into config
        else:
            out[key] = value
    return out


def _encrypt_file(file_path: Path, password: str) -> Path:
    """
    Encrypt a file using Fernet symmetric encryption.

    Args:
        file_path: Path to file to encrypt
        password: Encryption password

    Returns:
        Path to encrypted file
    """
    try:
        import base64

        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError:
        ch.error("Encryption requires 'cryptography' package.")
        ch.info("Install with: pip install cryptography")
        raise

    # Generate key from password
    salt = os.urandom(16)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    fernet = Fernet(key)

    # Read and encrypt file
    with open(file_path, "rb") as f:
        data = f.read()

    encrypted = fernet.encrypt(data)

    # Write encrypted file with salt prefix
    encrypted_path = file_path.with_suffix(file_path.suffix + ".enc")
    with open(encrypted_path, "wb") as f:
        f.write(salt + encrypted)

    return encrypted_path


def _decrypt_file(file_path: Path, password: str) -> Path:
    """
    Decrypt a file encrypted with _encrypt_file.

    Args:
        file_path: Path to encrypted file
        password: Decryption password

    Returns:
        Path to decrypted file
    """
    try:
        import base64

        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError:
        ch.error("Decryption requires 'cryptography' package.")
        ch.info("Install with: pip install cryptography")
        raise

    # Read encrypted file
    with open(file_path, "rb") as f:
        content = f.read()

    # Extract salt and encrypted data
    salt = content[:16]
    encrypted = content[16:]

    # Regenerate key from password
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    fernet = Fernet(key)

    # Decrypt
    decrypted = fernet.decrypt(encrypted)

    # Write decrypted file
    decrypted_path = file_path.with_suffix("")  # Remove .enc suffix
    with open(decrypted_path, "wb") as f:
        f.write(decrypted)

    return decrypted_path


# ============================================================================
# EXPORT COMMAND
# ============================================================================


def export_config(options: dict[str, Any]):
    """
    Export NAVIG configuration to a backup archive.

    Options:
        output: Output file path (optional, auto-generated if not provided)
        format: Output format - 'archive' (tar.gz) or 'json'
        include_secrets: If True, include unredacted secrets
        encrypt: If True, encrypt the output
        password: Encryption password (prompted if encrypt=True and not provided)
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()

    output = options.get("output")
    fmt = options.get("format", "archive")
    include_secrets = options.get("include_secrets", False)
    encrypt = options.get("encrypt", False)
    password = options.get("password")
    json_output = options.get("json", False)

    # Generate default output path
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = _get_backup_dir()

    if output is None:
        if fmt == "json":
            output = backup_dir / f"navig-config-{timestamp}.json"
        else:
            output = backup_dir / f"navig-config-{timestamp}.tar.gz"
    else:
        output = Path(output)

    # Ensure output directory exists
    output.parent.mkdir(parents=True, exist_ok=True)

    # Check for confirmation if including secrets
    if include_secrets:
        if not ch.confirm_operation(
            operation_name="Export configuration WITH SECRETS",
            operation_type="critical",
            details="Sensitive data (passwords, API keys) will be included",
            auto_confirm=options.get("yes", False),
            force_confirm=options.get("confirm", False),
        ):
            ch.warning("Export cancelled.")
            return

    # Get password for encryption
    if encrypt and not password:
        password = ch.prompt_input("Enter encryption password", password=True)
        confirm_password = ch.prompt_input("Confirm password", password=True)
        if password != confirm_password:
            ch.error("Passwords do not match.")
            raise typer.Exit(2)

    if not json_output:
        ch.info("Exporting NAVIG configuration...")

    # Everything this export could not do. It is reported in the summary and in
    # --json, because a per-item warning printed minutes earlier and then followed by
    # a green ✓ reads as "fine".
    failures: list[str] = []
    dropped: list[str] = []
    # Lower-priority copies of a file a higher-priority directory already provided.
    # NOT a failure — that is how NAVIG resolves config — but they are not in the
    # archive, so saying so beats letting the user discover it on restore.
    shadowed: list[str] = []

    try:
        if fmt == "json":
            # Export as JSON
            data = _collect_configs(include_global=True, failures=failures)

            if include_secrets:
                # Re-collect without redaction
                from navig.config import get_config_manager

                config_manager = get_config_manager()

                # Override with unredacted data
                for host_name in config_manager.list_hosts():
                    try:
                        data["hosts"][host_name] = config_manager.load_host_config(host_name)
                    except Exception as exc:  # noqa: BLE001
                        # NOT non-critical: `data` still holds the REDACTED copy, so the
                        # export the user explicitly asked to contain secrets silently
                        # contains "[REDACTED]" for this host instead. Restoring from it
                        # would look complete and produce a host that cannot connect.
                        failures.append(f"host {host_name} (secrets not re-read): {exc}")

                for host_name in config_manager.list_hosts():
                    for app_name in config_manager.list_apps(host_name):
                        try:
                            if host_name not in data["apps"]:
                                data["apps"][host_name] = {}
                            data["apps"][host_name][app_name] = config_manager.load_app_config(
                                host_name, app_name
                            )
                        except Exception as exc:  # noqa: BLE001
                            failures.append(
                                f"app {host_name}/{app_name} (secrets not re-read): {exc}"
                            )

            with open(output, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        else:
            # Export as archive
            archive = _create_archive(output, include_secrets=include_secrets)
            dropped = archive["dropped"]
            shadowed = archive["shadowed"]

        # Encrypt if requested
        if encrypt:
            encrypted_path = _encrypt_file(output, password)
            os.remove(output)  # Remove unencrypted file
            output = encrypted_path

        # Count items for the summary.
        #
        # `found_*` is what is on disk; `exported_*` is what actually made it into the
        # file. The summary used to print only the on-disk numbers, so an export that
        # silently dropped three of five hosts still reported "Hosts: 5" — and --json
        # reported `"success": true, "hosts": 5`. A restore from that file looks
        # complete and is not.
        # Enumerating the config dir can itself fail (a malformed host file makes
        # `list_hosts()` raise) — and that must not turn an archive that was written
        # successfully into "Export failed". It only supplies the "of N" denominator.
        try:
            hosts = config_manager.list_hosts()
            found_hosts = len(hosts)
            found_apps = sum(len(config_manager.list_apps(h)) for h in hosts)
        except Exception as exc:  # noqa: BLE001
            found_hosts = found_apps = 0
            failures.append(f"could not enumerate the config directory: {exc}")

        if fmt == "json":
            exported_hosts = len(data.get("hosts", {}))
            exported_apps = sum(len(a) for a in data.get("apps", {}).values())
        else:
            # Counted from the staging dir the tarball was built from — never by
            # walking the config directory a second time, which is how the summary
            # came to report hosts that were never written.
            exported_hosts = archive["hosts"]
            exported_apps = archive["apps"]
        found_hosts = max(found_hosts, exported_hosts)
        found_apps = max(found_apps, exported_apps)

        complete = not failures and not dropped

        if json_output:
            ch.raw_print(
                json.dumps(
                    {
                        # `success` tracks the EXIT CODE (false exactly when the
                        # zero-hosts-exported path below exits 1); `complete` is the
                        # finer signal — true only if nothing was skipped or dropped.
                        "success": found_hosts == 0 or exported_hosts > 0,
                        "complete": complete,
                        "output": str(output),
                        "hosts": exported_hosts,
                        "apps": exported_apps,
                        "hosts_found": found_hosts,
                        "apps_found": found_apps,
                        "failures": failures,
                        "dropped_unredactable": dropped,
                        "shadowed": shadowed,
                        "encrypted": encrypt,
                        "include_secrets": include_secrets,
                    }
                )
            )
        else:
            if complete:
                ch.success(f"✓ Configuration exported to: {output}")
            else:
                ch.warning(f"Configuration exported INCOMPLETE to: {output}")
            ch.dim(f"  Hosts: {exported_hosts}" + (f" of {found_hosts}" if not complete else ""))
            ch.dim(f"  Apps: {exported_apps}" + (f" of {found_apps}" if not complete else ""))
            if encrypt:
                ch.dim("  Encrypted: Yes")
            if include_secrets:
                ch.warning("  ⚠ Includes unredacted secrets")
            for item in failures:
                ch.warning(f"  ✗ not exported — {item}")
            for item in dropped:
                ch.warning(
                    f"  ✗ dropped — {item} could not be redacted, so it was excluded "
                    "rather than shipped with plaintext secrets"
                )
            for item in shadowed:
                ch.dim(f"  · shadowed — {item} (a higher-priority copy was exported)")
            if not complete:
                ch.info(
                    "  Fix the listed items and re-run, or use --include-secrets "
                    "if you intend a full unredacted copy."
                )

        # Exit honesty, the same rule as backup_system_config / backup_all_databases:
        # a partial export still exits 0 (it is a usable file and the gaps are printed),
        # but an export that had hosts to write and wrote NONE of them is not a success
        # in any sense a script could act on.
        if found_hosts > 0 and exported_hosts == 0:
            if not json_output:
                ch.error(
                    f"❌ Export FAILED — no host config could be read (0/{found_hosts})"
                )
            raise typer.Exit(1)

    except typer.Exit:
        # `typer.Exit` derives from RuntimeError, so the handler below would catch the
        # deliberate exit above and rewrite it into "Export failed: 1".
        raise
    except Exception as e:
        if json_output:
            ch.raw_print(json.dumps({"success": False, "error": str(e)}))
        else:
            ch.error(f"Export failed: {e}")
        # An export that raised is not a success. This used to fall off the end of the
        # function, so the user saw ✗ and the shell saw exit 0 — and `navig config
        # export && upload-backup` uploaded nothing while reporting that it had.
        raise typer.Exit(1) from e


# ============================================================================
# IMPORT COMMAND
# ============================================================================


def import_config(options: dict[str, Any]):
    """
    Import NAVIG configuration from a backup archive.

    Options:
        file: Input file path (required)
        merge: True = skip hosts/apps that already exist; False = overwrite them.
            NOTE: "replace" overwrites entries the backup CONTAINS; it does not
            replace the configuration. Nothing is ever deleted, so a host that
            exists locally but not in the backup survives either way. Naming it
            --replace invites the opposite reading, which is why both the flag
            help and this docstring spell it out.
        password: Decryption password (prompted if file is encrypted)
    """
    import yaml

    from navig.config import get_config_manager

    config_manager = get_config_manager()

    input_file = _require_input_file(options)
    if input_file is None:
        # _require_input_file already said which option is missing. Returning here
        # exited 0, so `navig config import` with no --file reported success.
        raise typer.Exit(2)
    merge = options.get("merge", True)
    password = options.get("password")
    json_output = options.get("json", False)

    if not input_file.exists():
        ch.error(f"File not found: {input_file}")
        raise typer.Exit(2)

    # Check if encrypted
    is_encrypted = input_file.suffix == ".enc"

    # Decrypt if needed
    if is_encrypted:
        if not password:
            password = ch.prompt_input("Enter decryption password", password=True)
        try:
            input_file = _decrypt_file(input_file, password)
        except Exception as e:
            ch.error(f"Decryption failed: {e}")
            ch.info("Check your password and try again.")
            raise typer.Exit(1) from e

    # Declared BEFORE the try: the handler below reports them, and a NameError
    # raised while reporting a failure would replace the real error with noise.
    imported_hosts = 0
    imported_apps = 0

    try:
        # Determine format
        if input_file.suffix == ".json":
            # JSON format
            with open(input_file, encoding='utf-8') as f:
                data = json.load(f)

            hosts_data = data.get("hosts", {})
            apps_data = data.get("apps", {})
        else:
            # Archive format
            with tempfile.TemporaryDirectory() as tmpdir:
                with tarfile.open(input_file, "r:gz") as tar:
                    _safe_extract_tar(tar, Path(tmpdir))

                extract_path = Path(tmpdir) / "navig-config"

                # Load hosts
                hosts_data = {}
                hosts_dir = extract_path / "hosts"
                if hosts_dir.exists():
                    for yaml_file in hosts_dir.glob("*.yaml"):
                        with open(yaml_file, encoding='utf-8') as f:
                            hosts_data[yaml_file.stem] = yaml.safe_load(f)

                # Load apps
                apps_data = {}
                apps_dir = extract_path / "apps"
                if apps_dir.exists():
                    for host_dir in apps_dir.iterdir():
                        if host_dir.is_dir():
                            apps_data[host_dir.name] = {}
                            for yaml_file in host_dir.glob("*.yaml"):
                                with open(yaml_file, encoding='utf-8') as f:
                                    apps_data[host_dir.name][yaml_file.stem] = yaml.safe_load(f)

        # Confirm import
        if not ch.confirm_operation(
            operation_name="Import NAVIG configuration",
            operation_type="standard" if merge else "critical",
            details=f"Hosts: {len(hosts_data)}, Apps: {sum(len(a) for a in apps_data.values())}",
            auto_confirm=options.get("yes", False),
            force_confirm=options.get("confirm", False),
        ):
            ch.warning("Import cancelled.")
            return

        # Import hosts
        for host_name, host_config in hosts_data.items():
            if merge and config_manager.host_exists(host_name):
                ch.dim(f"  Skipping existing host: {host_name}")
                continue
            live = config_manager.load_host_config(host_name) if config_manager.host_exists(host_name) else None
            config_manager.save_host_config(host_name, _restore_redacted_secrets(host_config, live))
            imported_hosts += 1

        # Import apps
        for host_name, apps in apps_data.items():
            for app_name, app_config in apps.items():
                if merge and config_manager.app_exists(host_name, app_name):
                    ch.dim(f"  Skipping existing app: {host_name}/{app_name}")
                    continue
                live = (config_manager.load_app_config(host_name, app_name)
                        if config_manager.app_exists(host_name, app_name) else None)
                config_manager.save_app_config(host_name, app_name, _restore_redacted_secrets(app_config, live))
                imported_apps += 1

        if json_output:
            ch.raw_print(
                json.dumps(
                    {
                        "success": True,
                        "imported_hosts": imported_hosts,
                        "imported_apps": imported_apps,
                        "merge_mode": merge,
                    }
                )
            )
        else:
            ch.success("✓ Configuration imported successfully")
            ch.dim(f"  Imported hosts: {imported_hosts}")
            ch.dim(f"  Imported apps: {imported_apps}")

        # Cleanup decrypted file if we created one
        if is_encrypted and input_file.exists():
            os.remove(input_file)

    except Exception as e:
        ch.error(f"Import failed: {e}")
        # This handler wraps the WHOLE import, including the per-host and per-app
        # write loops, so it can fire when part of the config is already on disk.
        # Exiting 0 left the operator believing nothing had happened; saying
        # nothing about the partial state left them unable to find out.
        if imported_hosts or imported_apps:
            ch.warning(
                f"  The config was PARTIALLY modified before the failure: "
                f"{imported_hosts} host(s), {imported_apps} app(s) already written."
            )
            ch.info("  Re-run the import to finish, or inspect with `navig host list`.")
        # Cleanup decrypted file on error
        if is_encrypted and input_file.exists():
            try:
                os.remove(input_file)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        raise typer.Exit(1) from e


# ============================================================================
# LIST EXPORTS COMMAND
# ============================================================================


def list_exports(options: dict[str, Any]):
    """List available configuration exports."""
    json_output = options.get("json", False)
    plain_output = options.get("plain", False)

    backup_dir = _get_backup_dir()

    exports = []
    for f in sorted(backup_dir.iterdir(), reverse=True):
        if f.is_file() and (f.suffix in [".json", ".gz", ".enc"] or ".tar" in f.name):
            stat = f.stat()
            exports.append(
                {
                    "name": f.name,
                    "path": str(f),
                    "size_bytes": stat.st_size,
                    "size_human": _format_size(stat.st_size),
                    "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "encrypted": f.suffix == ".enc",
                }
            )

    if json_output:
        ch.raw_print(json.dumps(exports, indent=2))
        return

    if plain_output:
        # Plain text output - one backup per line for scripting
        for exp in exports:
            ch.raw_print(exp["name"])
        return

    if not exports:
        ch.info("No configuration exports found.")
        ch.dim(f"  Export directory: {backup_dir}")
        return

    ch.header("NAVIG Configuration Exports")

    table = ch.create_table(
        columns=[
            {"name": "Name", "style": "cyan"},
            {"name": "Size", "style": "green"},
            {"name": "Created", "style": "yellow"},
            {"name": "Encrypted", "style": "magenta"},
        ]
    )

    for exp in exports:
        table.add_row(
            exp["name"],
            exp["size_human"],
            exp["created"][:10],
            "🔒" if exp["encrypted"] else "",
        )

    ch.print_table(table)


# ============================================================================
# INSPECT EXPORT COMMAND
# ============================================================================


def _safe_archive_yaml(path: Path) -> dict[str, Any]:
    """Load one host/app YAML from an extracted export archive, tolerating a
    corrupt or non-mapping file.

    ``navig config-backup inspect`` scans EVERY host/app in the archive; an
    unguarded parse let a single damaged file abort the whole inspect (→
    "Failed to inspect export", showing nothing) — exactly when you are inspecting
    a backup to decide what is still recoverable. Returns a marker dict so the item
    is still surfaced (flagged unreadable) rather than hiding every other host/app.
    """
    import yaml

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return {"_unreadable": True}
    return data if isinstance(data, dict) else {"_unreadable": True}


def inspect_export(options: dict[str, Any]):
    """
    Inspect contents of a configuration export without importing.

    Options:
        file: Export file to inspect
        password: Decryption password if encrypted
    """
    input_file = _require_input_file(options)
    if input_file is None:
        # _require_input_file already said which option is missing. Returning here
        # exited 0, so `navig config import` with no --file reported success.
        raise typer.Exit(2)
    password = options.get("password")
    json_output = options.get("json", False)

    if not input_file.exists():
        ch.error(f"File not found: {input_file}")
        raise typer.Exit(2)

    # Check if encrypted
    is_encrypted = input_file.suffix == ".enc"
    decrypted_file = None

    try:
        # Decrypt if needed
        if is_encrypted:
            if not password:
                password = ch.prompt_input("Enter decryption password", password=True)
            decrypted_file = _decrypt_file(input_file, password)
            input_file = decrypted_file

        # Read contents
        if input_file.suffix == ".json" or str(input_file).endswith(".json"):
            with open(input_file, encoding='utf-8') as f:
                data = json.load(f)
        else:
            # Archive format
            with tempfile.TemporaryDirectory() as tmpdir:
                with tarfile.open(input_file, "r:gz") as tar:
                    _safe_extract_tar(tar, Path(tmpdir))

                extract_path = Path(tmpdir) / "navig-config"

                # Read manifest (a corrupt manifest must not abort the whole
                # inspect — version/exported_at simply fall back to "unknown").
                manifest_path = extract_path / "manifest.json"
                manifest = {}
                if manifest_path.exists():
                    try:
                        with open(manifest_path, encoding='utf-8') as f:
                            manifest = json.load(f)
                    except (OSError, json.JSONDecodeError):
                        manifest = {}

                # Collect data from files
                data = {
                    "version": manifest.get("version", "unknown"),
                    "exported_at": manifest.get("exported_at", "unknown"),
                    "hosts": {},
                    "apps": {},
                }

                hosts_dir = extract_path / "hosts"
                if hosts_dir.exists():
                    for yaml_file in hosts_dir.glob("*.yaml"):
                        data["hosts"][yaml_file.stem] = _safe_archive_yaml(yaml_file)

                apps_dir = extract_path / "apps"
                if apps_dir.exists():
                    for host_dir in apps_dir.iterdir():
                        if host_dir.is_dir():
                            data["apps"][host_dir.name] = {}
                            for yaml_file in host_dir.glob("*.yaml"):
                                data["apps"][host_dir.name][yaml_file.stem] = _safe_archive_yaml(
                                    yaml_file
                                )

        if json_output:
            ch.raw_print(json.dumps(data, indent=2, default=str))
            return

        # Display summary
        ch.header(f"Export: {options.get('file')}")
        ch.dim(f"Version: {data.get('version', 'unknown')}")
        ch.dim(f"Exported: {data.get('exported_at', 'unknown')}")
        ch.console.print()

        # List hosts
        hosts = data.get("hosts", {})
        ch.subheader(f"Hosts ({len(hosts)})")
        for host_name, host_config in hosts.items():
            if not isinstance(host_config, dict) or host_config.get("_unreadable"):
                ch.warning(f"  {host_name}: unreadable (corrupt file in archive)")
                continue
            host_addr = host_config.get("host", "N/A")
            user = host_config.get("user", "N/A")
            ch.info(f"  {host_name}: {user}@{host_addr}")

        ch.console.print()

        # List apps
        apps = data.get("apps", {})
        total_apps = sum(len(a) for a in apps.values())
        ch.subheader(f"Apps ({total_apps})")
        for host_name, host_apps in apps.items():
            for app_name in host_apps:
                ch.info(f"  {host_name}/{app_name}")

    except Exception as e:
        ch.error(f"Failed to inspect export: {e}")

    finally:
        # Cleanup decrypted file
        if decrypted_file and decrypted_file.exists():
            try:
                os.remove(decrypted_file)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical


# ============================================================================
# DELETE EXPORT COMMAND
# ============================================================================


def delete_export(options: dict[str, Any]):
    """
    Delete a configuration export file.

    Options:
        file: Export file to delete
    """
    input_file = _require_input_file(options)
    if input_file is None:
        # _require_input_file already said which option is missing. Returning here
        # exited 0, so `navig config import` with no --file reported success.
        raise typer.Exit(2)
    json_output = options.get("json", False)

    if not input_file.exists():
        ch.error(f"File not found: {input_file}")
        raise typer.Exit(2)

    if not ch.confirm_operation(
        operation_name=f"Delete export: {input_file.name}",
        operation_type="standard",
        auto_confirm=options.get("yes", False),
        force_confirm=options.get("confirm", False),
    ):
        ch.warning("Cancelled.")
        return

    try:
        os.remove(input_file)
        if json_output:
            ch.raw_print(json.dumps({"success": True, "deleted": str(input_file)}))
        else:
            ch.success(f"✓ Deleted: {input_file.name}")
    except Exception as e:
        if json_output:
            ch.raw_print(json.dumps({"success": False, "error": str(e)}))
        else:
            ch.error(f"Failed to delete: {e}")
