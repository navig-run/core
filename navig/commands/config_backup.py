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

from navig import console_helper as ch


def _get_backup_dir() -> Path:
    """Get the directory for NAVIG config backups."""
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    backup_dir = config_manager.config_dir / "exports"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _collect_configs(include_global: bool = True) -> dict[str, Any]:
    """
    Collect all NAVIG configuration data.

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

    return data


def _create_archive(output_path: Path, include_secrets: bool = False) -> bool:
    """
    Create a compressed archive of all NAVIG configuration.

    Args:
        output_path: Path for the output archive
        include_secrets: If True, include unredacted secrets

    Returns:
        True if successful
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

        # Copy hosts directory
        hosts_dir = config_manager.config_dir / "hosts"
        if hosts_dir.exists():
            dst_hosts = tmpdir_path / "hosts"
            shutil.copytree(hosts_dir, dst_hosts)
            manifest["contents"].append("hosts")

        # Copy apps directory
        apps_dir = config_manager.config_dir / "apps"
        if apps_dir.exists():
            dst_apps = tmpdir_path / "apps"
            shutil.copytree(apps_dir, dst_apps)
            manifest["contents"].append("apps")

        # Copy global config
        global_config_file = config_manager.config_dir / "config.yaml"
        if global_config_file.exists():
            shutil.copy(global_config_file, tmpdir_path / "config.yaml")
            manifest["contents"].append("config.yaml")

        # Redact secrets if not including them
        if not include_secrets:
            _redact_secrets_in_dir(tmpdir_path)

        # Write manifest
        manifest_path = tmpdir_path / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # Create tarball
        with tarfile.open(output_path, "w:gz") as tar:
            tar.add(tmpdir_path, arcname="navig-config")

    return True


def _redact_secrets_in_dir(dir_path: Path):
    """Redact sensitive data in all YAML files in a directory."""
    import yaml

    from navig.core.security import redact_dict

    sensitive_keys = ["password", "api_key", "openrouter_api_key", "secret", "token"]

    for yaml_file in dir_path.rglob("*.yaml"):
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)

            if data and isinstance(data, dict):
                redacted = redact_dict(data, sensitive_keys=sensitive_keys)

                with open(yaml_file, "w", encoding="utf-8") as f:
                    yaml.dump(redacted, f, default_flow_style=False, sort_keys=False)
        except Exception:
            pass  # Skip files that can't be processed


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
            return

    if not json_output:
        ch.info("Exporting NAVIG configuration...")

    try:
        if fmt == "json":
            # Export as JSON
            data = _collect_configs(include_global=True)

            if include_secrets:
                # Re-collect without redaction
                from navig.config import get_config_manager

                config_manager = get_config_manager()

                # Override with unredacted data
                for host_name in config_manager.list_hosts():
                    try:
                        data["hosts"][host_name] = config_manager.load_host_config(host_name)
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical

                for host_name in config_manager.list_hosts():
                    for app_name in config_manager.list_apps(host_name):
                        try:
                            if host_name not in data["apps"]:
                                data["apps"][host_name] = {}
                            data["apps"][host_name][app_name] = config_manager.load_app_config(
                                host_name, app_name
                            )
                        except Exception:  # noqa: BLE001
                            pass  # best-effort; failure is non-critical

            with open(output, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        else:
            # Export as archive
            _create_archive(output, include_secrets=include_secrets)

        # Encrypt if requested
        if encrypt:
            encrypted_path = _encrypt_file(output, password)
            os.remove(output)  # Remove unencrypted file
            output = encrypted_path

        # Count items for summary
        hosts = config_manager.list_hosts()
        app_count = sum(len(config_manager.list_apps(h)) for h in hosts)

        if json_output:
            ch.raw_print(
                json.dumps(
                    {
                        "success": True,
                        "output": str(output),
                        "hosts": len(hosts),
                        "apps": app_count,
                        "encrypted": encrypt,
                        "include_secrets": include_secrets,
                    }
                )
            )
        else:
            ch.success(f"✓ Configuration exported to: {output}")
            ch.dim(f"  Hosts: {len(hosts)}")
            ch.dim(f"  Apps: {app_count}")
            if encrypt:
                ch.dim("  Encrypted: Yes")
            if include_secrets:
                ch.warning("  ⚠ Includes unredacted secrets")

    except Exception as e:
        if json_output:
            ch.raw_print(json.dumps({"success": False, "error": str(e)}))
        else:
            ch.error(f"Export failed: {e}")


# ============================================================================
# IMPORT COMMAND
# ============================================================================


def import_config(options: dict[str, Any]):
    """
    Import NAVIG configuration from a backup archive.

    Options:
        file: Input file path (required)
        merge: If True, merge with existing config; if False, replace
        password: Decryption password (prompted if file is encrypted)
    """
    import yaml

    from navig.config import get_config_manager

    config_manager = get_config_manager()

    input_file = Path(options.get("file"))
    merge = options.get("merge", True)
    password = options.get("password")
    json_output = options.get("json", False)

    if not input_file.exists():
        ch.error(f"File not found: {input_file}")
        return

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
            return

    try:
        # Determine format
        if input_file.suffix == ".json":
            # JSON format
            with open(input_file) as f:
                data = json.load(f)

            hosts_data = data.get("hosts", {})
            apps_data = data.get("apps", {})
        else:
            # Archive format
            with tempfile.TemporaryDirectory() as tmpdir:
                with tarfile.open(input_file, "r:gz") as tar:
                    tar.extractall(tmpdir)

                extract_path = Path(tmpdir) / "navig-config"

                # Load hosts
                hosts_data = {}
                hosts_dir = extract_path / "hosts"
                if hosts_dir.exists():
                    for yaml_file in hosts_dir.glob("*.yaml"):
                        with open(yaml_file) as f:
                            hosts_data[yaml_file.stem] = yaml.safe_load(f)

                # Load apps
                apps_data = {}
                apps_dir = extract_path / "apps"
                if apps_dir.exists():
                    for host_dir in apps_dir.iterdir():
                        if host_dir.is_dir():
                            apps_data[host_dir.name] = {}
                            for yaml_file in host_dir.glob("*.yaml"):
                                with open(yaml_file) as f:
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
        imported_hosts = 0
        for host_name, host_config in hosts_data.items():
            if merge and config_manager.host_exists(host_name):
                ch.dim(f"  Skipping existing host: {host_name}")
                continue
            config_manager.save_host_config(host_name, host_config)
            imported_hosts += 1

        # Import apps
        imported_apps = 0
        for host_name, apps in apps_data.items():
            for app_name, app_config in apps.items():
                if merge and config_manager.app_exists(host_name, app_name):
                    ch.dim(f"  Skipping existing app: {host_name}/{app_name}")
                    continue
                config_manager.save_app_config(host_name, app_name, app_config)
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
        # Cleanup decrypted file on error
        if is_encrypted and input_file.exists():
            try:
                os.remove(input_file)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical


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


def inspect_export(options: dict[str, Any]):
    """
    Inspect contents of a configuration export without importing.

    Options:
        file: Export file to inspect
        password: Decryption password if encrypted
    """
    import yaml

    input_file = Path(options.get("file"))
    password = options.get("password")
    json_output = options.get("json", False)

    if not input_file.exists():
        ch.error(f"File not found: {input_file}")
        return

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
            with open(input_file) as f:
                data = json.load(f)
        else:
            # Archive format
            with tempfile.TemporaryDirectory() as tmpdir:
                with tarfile.open(input_file, "r:gz") as tar:
                    tar.extractall(tmpdir)

                extract_path = Path(tmpdir) / "navig-config"

                # Read manifest
                manifest_path = extract_path / "manifest.json"
                manifest = {}
                if manifest_path.exists():
                    with open(manifest_path) as f:
                        manifest = json.load(f)

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
                        with open(yaml_file) as f:
                            data["hosts"][yaml_file.stem] = yaml.safe_load(f)

                apps_dir = extract_path / "apps"
                if apps_dir.exists():
                    for host_dir in apps_dir.iterdir():
                        if host_dir.is_dir():
                            data["apps"][host_dir.name] = {}
                            for yaml_file in host_dir.glob("*.yaml"):
                                with open(yaml_file) as f:
                                    data["apps"][host_dir.name][yaml_file.stem] = yaml.safe_load(f)

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
    input_file = Path(options.get("file"))
    json_output = options.get("json", False)

    if not input_file.exists():
        ch.error(f"File not found: {input_file}")
        return

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


# ============================================================================
# HELPERS
# ============================================================================


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
