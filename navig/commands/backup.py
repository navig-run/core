"""Backup Commands - Comprehensive backup and restore system"""

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.table import Table

from navig import console_helper as ch
from navig.commands._db_utils import calculate_file_checksum, create_mysql_config_file


def _run_scp_command(
    ssh_key: str,
    user: str,
    host: str,
    remote_path: str,
    local_path: Path,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """
    Run SCP command securely without shell=True.

    Args:
        ssh_key: Path to SSH private key
        user: SSH username
        host: Remote host address
        remote_path: Path on remote server
        local_path: Local destination path
        capture_output: Whether to capture stdout/stderr

    Returns:
        CompletedProcess result
    """
    cmd = ["scp", "-i", str(ssh_key), f"{user}@{host}:{remote_path}", str(local_path)]
    return subprocess.run(cmd, check=True, capture_output=capture_output)


def _verify_disk_space(
    backup_dir: Path, estimated_size_mb: float = 100.0, safety_margin: float = 1.5
) -> tuple[bool, str]:
    """
    Verify sufficient disk space before backup.

    Args:
        backup_dir: Directory where backup will be stored
        estimated_size_mb: Estimated backup size in MB
        safety_margin: Multiplier for safety (1.5 = require 150% of estimated size)

    Returns:
        (success: bool, message: str)
    """
    try:
        stat = shutil.disk_usage(backup_dir.parent if not backup_dir.exists() else backup_dir)
        free_mb = stat.free / (1024 * 1024)
        required_mb = estimated_size_mb * safety_margin

        if free_mb < required_mb:
            return (
                False,
                f"Insufficient disk space: {free_mb:.0f} MB free, {required_mb:.0f} MB required",
            )

        return True, f"Disk space OK: {free_mb:.0f} MB free"
    except OSError as e:
        return False, f"Failed to check disk space: {e}"

def _result_stdout_text(result: subprocess.CompletedProcess | object) -> str:
    """Return normalized stdout text from command results."""
    return str(getattr(result, "stdout", "") or "")


def _result_indicates_missing(result: subprocess.CompletedProcess | object) -> bool:
    """Return True when a probe command returned the sentinel 'missing'."""
    return _result_stdout_text(result).strip() == "missing"


def backup_system_config(name: str | None, options: dict[str, Any]):
    """Backup system configuration files."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server_name = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    # Generate backup name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = name or f"{server_name}_config_{timestamp}"
    backup_dir = config_manager.backups_dir / backup_name / "configs"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if options.get("dry_run"):
        ch.info(f"[DRY RUN] Would create backup: {backup_dir}")
        ch.info("[DRY RUN] Would backup system configuration files")
        return

    ch.info(f"📦 Creating system configuration backup: {backup_name}")

    # Configuration files to backup
    config_files = [
        "/etc/ssh/sshd_config",
        "/etc/ufw/ufw.conf",
        "/etc/fail2ban/jail.local",
        "/etc/hosts",
        "/etc/hostname",
        "/etc/timezone",
        "/etc/fstab",
        "/etc/crontab",
    ]

    results = []
    success_count = 0

    for remote_file in config_files:
        # Check if file exists
        check_cmd = f'test -f {remote_file} && echo "exists" || echo "missing"'
        result = remote_ops.execute_command(check_cmd)

        if _result_indicates_missing(result):
            ch.warning(f"⊘ {remote_file} (not found)")
            results.append({"file": remote_file, "status": "skipped", "reason": "not found"})
            continue

        # Download file
        local_file = backup_dir / remote_file.replace("/", "_")

        try:
            _run_scp_command(
                server_config["ssh_key"],
                server_config["user"],
                server_config["host"],
                remote_file,
                local_file,
            )
            ch.success(f"{remote_file}")
            results.append({"file": remote_file, "status": "success"})
            success_count += 1
        except subprocess.CalledProcessError:
            ch.warning(f"✗ {remote_file} (access denied)")
            results.append({"file": remote_file, "status": "failed", "reason": "access denied"})

    # Save metadata
    metadata = {
        "timestamp": timestamp,
        "server": server_name,
        "type": "system-config",
        "files": results,
        "success_count": success_count,
        "total_files": len(config_files),
    }

    metadata_file = backup_dir.parent / "metadata.json"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    _tmp_m1: Path | None = None
    try:
        _fd_m1, _tmp_m1s = tempfile.mkstemp(dir=metadata_file.parent, suffix=".tmp")
        _tmp_m1 = Path(_tmp_m1s)
        with os.fdopen(_fd_m1, "w", encoding="utf-8") as _fh_m1:
            json.dump(metadata, _fh_m1, indent=2)
        os.replace(_tmp_m1, metadata_file)
        _tmp_m1 = None
    finally:
        if _tmp_m1 is not None:
            _tmp_m1.unlink(missing_ok=True)

    if options.get("json"):
        ch.raw_print(json.dumps(metadata))
    else:
        ch.success("✅ System configuration backup complete")
        ch.info(f"   Location: {backup_dir.parent}")
        ch.info(f"   Files backed up: {success_count}/{len(config_files)}")


def backup_all_databases(name: str | None, compress: str, options: dict[str, Any]):
    """Backup all databases with compression."""
    from navig.config import get_config_manager
    from navig.tunnel import TunnelManager

    config_manager = get_config_manager()
    tunnel_manager = TunnelManager(config_manager)

    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server_name = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(server_name)

    # Generate backup name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = name or f"{server_name}_databases_{timestamp}"
    backup_dir = config_manager.backups_dir / backup_name / "databases"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if options.get("dry_run"):
        ch.info(f"[DRY RUN] Would create database backup: {backup_dir}")
        ch.info(f"[DRY RUN] Compression: {compress}")
        return

    ch.info(f"🗄️  Creating database backup: {backup_name}")
    ch.info(f"   Compression: {compress}")

    # Verify disk space (estimate 100MB per database)
    space_ok, space_msg = _verify_disk_space(backup_dir, estimated_size_mb=100.0)
    if not space_ok:
        ch.error(space_msg)
        ch.error("Backup cancelled - free up disk space and try again")
        ch.info("")
        ch.info("Free up space:")
        ch.info("  1. Remove old backups: navig list-backups (delete oldest)")
        ch.info("  2. Clean system logs: navig cleanup-logs")
        ch.info("  3. Check disk usage: df -h")
        ch.info("  4. Remove temp files: rm -rf /tmp/*")
        ch.info("  5. Compress backups: Use --compress gzip flag")
        return

    if options.get("verbose"):
        ch.dim(f"   {space_msg}")

    # Ensure tunnel
    tunnel_info = tunnel_manager.get_tunnel_status(server_name)
    if not tunnel_info:
        ch.info("   Starting tunnel...")
        tunnel_info = tunnel_manager.start_tunnel(server_name)

    db_config = server_config["database"]

    # Create secure config file (prevents password in process listings)
    config_file = create_mysql_config_file(db_config["user"], db_config["password"])

    try:
        # Get list of databases
        list_cmd = [
            "mysql",
            f"--defaults-file={config_file}",
            "-h",
            "127.0.0.1",
            "-P",
            str(tunnel_info["local_port"]),
            "-e",
            "SHOW DATABASES;",
        ]

        try:
            result = subprocess.run(list_cmd, capture_output=True, text=True, check=True)
            databases = [
                db.strip()
                for db in result.stdout.split("\n")
                if db.strip()
                and db.strip() != "Database"
                and db.strip() not in ["information_schema", "performance_schema", "mysql", "sys"]
            ]
        except FileNotFoundError:
            ch.error("mysql client not found. Please install MySQL client tools.")
            return
        except subprocess.CalledProcessError as e:
            ch.error(f"Failed to list databases: {e.stderr}")
            return

        if not databases:
            ch.warning("No databases found")
            return

        results = []
        total_size = 0

        for db_name in databases:
            ch.info(f"   Backing up: {db_name}...")

            # Dump database
            dump_file = backup_dir / f"{db_name}.sql"
            dump_cmd = [
                "mysqldump",
                f"--defaults-file={config_file}",
                "-h",
                "127.0.0.1",
                "-P",
                str(tunnel_info["local_port"]),
                "--single-transaction",
                "--routines",
                "--triggers",
                db_name,
            ]

            try:
                with open(dump_file, "w", encoding="utf-8") as f:
                    subprocess.run(dump_cmd, stdout=f, stderr=subprocess.PIPE, check=True)

                # Compress if requested
                if compress in ["gzip", "zstd"]:
                    if compress == "gzip":
                        compress_cmd = ["gzip", str(dump_file)]
                        final_file = dump_file.with_suffix(".sql.gz")
                    else:  # zstd
                        compress_cmd = [
                            "zstd",
                            str(dump_file),
                            "-o",
                            str(dump_file.with_suffix(".sql.zst")),
                        ]
                        final_file = dump_file.with_suffix(".sql.zst")

                    try:
                        subprocess.run(compress_cmd, check=True, capture_output=True)
                        # Verify compressed file exists before deleting original
                        if final_file.exists() and final_file.stat().st_size > 0:
                            dump_file.unlink(missing_ok=True)  # Remove uncompressed file
                            file_size = final_file.stat().st_size / (1024 * 1024)
                        else:
                            ch.warning("   Compression verification failed, keeping uncompressed")
                            final_file = dump_file
                            file_size = dump_file.stat().st_size / (1024 * 1024)
                    except FileNotFoundError:
                        ch.warning(f"   {compress} not found, keeping uncompressed")
                        final_file = dump_file
                        file_size = dump_file.stat().st_size / (1024 * 1024)
                    except subprocess.CalledProcessError as e:
                        ch.warning(f"   Compression failed: {e}, keeping uncompressed")
                        final_file = dump_file
                        file_size = dump_file.stat().st_size / (1024 * 1024)
                else:
                    final_file = dump_file
                    file_size = dump_file.stat().st_size / (1024 * 1024)

                # Calculate checksum for integrity verification
                checksum = calculate_file_checksum(final_file)

                total_size += file_size
                ch.success(f"   ✓ {db_name} ({file_size:.2f} MB)")
                if options.get("verbose"):
                    ch.dim(f"     Checksum: {checksum[:16]}...")

                results.append(
                    {
                        "database": db_name,
                        "status": "success",
                        "size_mb": round(file_size, 2),
                        "checksum": checksum,
                        "file": final_file.name,
                    }
                )
            except subprocess.CalledProcessError as e:
                ch.error(f"   ✗ {db_name} (dump failed: {e})")
                results.append({"database": db_name, "status": "failed", "error": str(e)})
                # Continue with other databases even if one fails
    finally:
        # Always cleanup temp config file
        try:
            os.unlink(config_file)
        except OSError:
            pass  # best-effort cleanup

    # Save metadata
    metadata = {
        "timestamp": timestamp,
        "server": server_name,
        "type": "databases",
        "compression": compress,
        "databases": results,
        "total_size_mb": round(total_size, 2),
        "success_count": len([r for r in results if r["status"] == "success"]),
        "total_count": len(databases),
    }

    metadata_file = backup_dir.parent / "metadata.json"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    _tmp_m2: Path | None = None
    try:
        _fd_m2, _tmp_m2s = tempfile.mkstemp(dir=metadata_file.parent, suffix=".tmp")
        _tmp_m2 = Path(_tmp_m2s)
        with os.fdopen(_fd_m2, "w", encoding="utf-8") as _fh_m2:
            json.dump(metadata, _fh_m2, indent=2)
        os.replace(_tmp_m2, metadata_file)
        _tmp_m2 = None
    finally:
        if _tmp_m2 is not None:
            _tmp_m2.unlink(missing_ok=True)

    if options.get("json"):
        ch.raw_print(json.dumps(metadata))
    else:
        ch.success("✅ Database backup complete")
        ch.info(f"   Location: {backup_dir.parent}")
        ch.info(f"   Databases: {metadata['success_count']}/{metadata['total_count']}")
        ch.info(f"   Total size: {total_size:.2f} MB")


def backup_hestia(name: str | None, options: dict[str, Any]):
    """Backup comprehensive HestiaCP configuration."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server_name = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    # Check if HestiaCP is installed
    check_cmd = 'command -v v-list-users >/dev/null 2>&1 && echo "installed" || echo "missing"'
    result = remote_ops.execute_command(check_cmd)

    if _result_indicates_missing(result):
        ch.error("HestiaCP not detected on this server")
        return

    # Generate backup name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = name or f"{server_name}_hestia_{timestamp}"
    backup_dir = config_manager.backups_dir / backup_name / "hestia"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if options.get("dry_run"):
        ch.info(f"[DRY RUN] Would create HestiaCP backup: {backup_dir}")
        return

    ch.info(f"🎛️  Creating HestiaCP backup: {backup_name}")

    # Directories to backup
    hestia_dirs = [
        {
            "remote": "/usr/local/hestia/conf",
            "local": "conf",
            "desc": "Configuration files",
        },
        {
            "remote": "/usr/local/hestia/data/users",
            "local": "users",
            "desc": "User data",
        },
        {"remote": "/usr/local/hestia/ssl", "local": "ssl", "desc": "SSL certificates"},
        {
            "remote": "/usr/local/hestia/data/templates",
            "local": "templates",
            "desc": "Custom templates",
        },
        {
            "remote": "/usr/local/hestia/data/zones",
            "local": "zones",
            "desc": "DNS zone files",
        },
    ]

    results = []
    total_size = 0
    success_count = 0

    for dir_info in hestia_dirs:
        remote_path = dir_info["remote"]
        local_name = dir_info["local"]
        desc = dir_info["desc"]

        ch.info(f"   Backing up {desc}...")

        # Check if directory exists
        check_cmd = f'test -d {remote_path} && echo "exists" || echo "missing"'
        result = remote_ops.execute_command(check_cmd)

        if _result_indicates_missing(result):
            ch.warning(f"   ⊘ {remote_path} (not found)")
            results.append({"directory": remote_path, "desc": desc, "status": "skipped"})
            continue

        # Create tar archive on remote server
        tar_file = f"/tmp/hestia_{local_name}_{timestamp}.tar.gz"
        parent_dir = str(Path(remote_path).parent)
        dir_name = Path(remote_path).name

        tar_cmd = f"cd '{parent_dir}' && tar --exclude='*.log' --exclude='*.log.*' -czf '{tar_file}' '{dir_name}'"
        result = remote_ops.execute_command(tar_cmd)

        # Download archive
        local_dir = backup_dir / local_name
        local_dir.mkdir(parents=True, exist_ok=True)
        local_tar = local_dir / f"{local_name}.tar.gz"

        try:
            _run_scp_command(
                server_config["ssh_key"],
                server_config["user"],
                server_config["host"],
                tar_file,
                local_tar,
            )

            # Extract locally
            with tarfile.open(local_tar, "r:gz") as tar:
                tar.extractall(local_dir)

            # Remove tar file after extraction
            local_tar.unlink()

            # Get size
            size = sum(f.stat().st_size for f in local_dir.rglob("*") if f.is_file()) / (
                1024 * 1024
            )
            file_count = len(list(local_dir.rglob("*")))

            total_size += size
            success_count += 1

            ch.success(f"   ✓ {desc} - {file_count} files, {size:.2f} MB")
            results.append(
                {
                    "directory": remote_path,
                    "desc": desc,
                    "status": "success",
                    "files": file_count,
                    "size_mb": round(size, 2),
                }
            )

            # Cleanup remote tar
            remote_ops.execute_command(f"rm -f {tar_file}")

        except Exception as e:
            ch.error(f"   ✗ {desc} (failed: {str(e)})")
            results.append({"directory": remote_path, "desc": desc, "status": "failed"})

    # Save metadata
    metadata = {
        "timestamp": timestamp,
        "server": server_name,
        "type": "hestia",
        "directories": results,
        "total_size_mb": round(total_size, 2),
        "success_count": success_count,
        "total_count": len(hestia_dirs),
    }

    metadata_file = backup_dir.parent / "metadata.json"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    _tmp_m3: Path | None = None
    try:
        _fd_m3, _tmp_m3s = tempfile.mkstemp(dir=metadata_file.parent, suffix=".tmp")
        _tmp_m3 = Path(_tmp_m3s)
        with os.fdopen(_fd_m3, "w", encoding="utf-8") as _fh_m3:
            json.dump(metadata, _fh_m3, indent=2)
        os.replace(_tmp_m3, metadata_file)
        _tmp_m3 = None
    finally:
        if _tmp_m3 is not None:
            _tmp_m3.unlink(missing_ok=True)

    if options.get("json"):
        ch.raw_print(json.dumps(metadata))
    else:
        ch.success("✅ HestiaCP backup complete")
        ch.info(f"   Location: {backup_dir.parent}")
        ch.info(f"   Directories: {success_count}/{len(hestia_dirs)}")
        ch.info(f"   Total size: {total_size:.2f} MB")


def backup_web_config(name: str | None, options: dict[str, Any]):
    """Backup web server configurations."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server_name = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    # Generate backup name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = name or f"{server_name}_webserver_{timestamp}"
    backup_dir = config_manager.backups_dir / backup_name / "webservers"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if options.get("dry_run"):
        ch.info(f"[DRY RUN] Would create web server backup: {backup_dir}")
        return

    ch.info(f"🌐 Creating web server backup: {backup_name}")

    results = {"nginx": [], "apache": []}

    # Nginx configuration
    nginx_files = [
        "/etc/nginx/nginx.conf",
        "/etc/nginx/sites-available/",
        "/etc/nginx/sites-enabled/",
    ]

    nginx_dir = backup_dir / "nginx"
    nginx_dir.mkdir(exist_ok=True)

    for remote_path in nginx_files:
        is_dir = remote_path.endswith("/")
        check_cmd = f'test -{"d" if is_dir else "f"} {remote_path.rstrip("/")} && echo "exists" || echo "missing"'
        result = remote_ops.execute_command(check_cmd)

        if _result_indicates_missing(result):
            continue

        if is_dir:
            # Backup directory
            local_subdir = nginx_dir / Path(remote_path.rstrip("/")).name
            local_subdir.mkdir(exist_ok=True)

            # List files and download each
            files_cmd = f"find {remote_path.rstrip('/')} -type f"
            files_result = remote_ops.execute_command(files_cmd)

            for file_path in _result_stdout_text(files_result).strip().split("\n"):
                if file_path:
                    local_file = local_subdir / Path(file_path).name
                    try:
                        _run_scp_command(
                            server_config["ssh_key"],
                            server_config["user"],
                            server_config["host"],
                            file_path,
                            local_file,
                        )
                        results["nginx"].append({"file": file_path, "status": "success"})
                    except (OSError, subprocess.CalledProcessError):
                        pass  # Cleanup - operation may fail
        else:
            # Backup single file
            local_file = nginx_dir / Path(remote_path).name
            try:
                _run_scp_command(
                    server_config["ssh_key"],
                    server_config["user"],
                    server_config["host"],
                    remote_path,
                    local_file,
                )
                ch.success(f"   ✓ Nginx: {remote_path}")
                results["nginx"].append({"file": remote_path, "status": "success"})
            except (OSError, subprocess.CalledProcessError):
                pass  # Cleanup - operation may fail

    # Apache configuration
    apache_files = [
        "/etc/apache2/apache2.conf",
        "/etc/apache2/ports.conf",
        "/etc/apache2/sites-available/",
        "/etc/apache2/sites-enabled/",
    ]

    apache_dir = backup_dir / "apache"
    apache_dir.mkdir(exist_ok=True)

    for remote_path in apache_files:
        is_dir = remote_path.endswith("/")
        check_cmd = f'test -{"d" if is_dir else "f"} {remote_path.rstrip("/")} && echo "exists" || echo "missing"'
        result = remote_ops.execute_command(check_cmd)

        if _result_indicates_missing(result):
            continue

        if is_dir:
            local_subdir = apache_dir / Path(remote_path.rstrip("/")).name
            local_subdir.mkdir(exist_ok=True)

            files_cmd = f"find {remote_path.rstrip('/')} -type f"
            files_result = remote_ops.execute_command(files_cmd)

            for file_path in _result_stdout_text(files_result).strip().split("\n"):
                if file_path:
                    local_file = local_subdir / Path(file_path).name
                    try:
                        _run_scp_command(
                            server_config["ssh_key"],
                            server_config["user"],
                            server_config["host"],
                            file_path,
                            local_file,
                        )
                        results["apache"].append({"file": file_path, "status": "success"})
                    except (OSError, subprocess.CalledProcessError):
                        pass  # Cleanup - operation may fail
        else:
            local_file = apache_dir / Path(remote_path).name
            try:
                _run_scp_command(
                    server_config["ssh_key"],
                    server_config["user"],
                    server_config["host"],
                    remote_path,
                    local_file,
                )
                ch.success(f"   ✓ Apache: {remote_path}")
                results["apache"].append({"file": remote_path, "status": "success"})
            except (OSError, subprocess.CalledProcessError):
                pass  # Cleanup - operation may fail

    # Save metadata
    metadata = {
        "timestamp": timestamp,
        "server": server_name,
        "type": "webserver",
        "nginx_files": len(results["nginx"]),
        "apache_files": len(results["apache"]),
        "details": results,
    }

    metadata_file = backup_dir.parent / "metadata.json"
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    _tmp_m4: Path | None = None
    try:
        _fd_m4, _tmp_m4s = tempfile.mkstemp(dir=metadata_file.parent, suffix=".tmp")
        _tmp_m4 = Path(_tmp_m4s)
        with os.fdopen(_fd_m4, "w", encoding="utf-8") as _fh_m4:
            json.dump(metadata, _fh_m4, indent=2)
        os.replace(_tmp_m4, metadata_file)
        _tmp_m4 = None
    finally:
        if _tmp_m4 is not None:
            _tmp_m4.unlink(missing_ok=True)

    if options.get("json"):
        ch.raw_print(json.dumps(metadata))
    else:
        ch.success("✅ Web server backup complete")
        ch.info(f"   Location: {backup_dir.parent}")
        ch.info(f"   Nginx files: {len(results['nginx'])}")
        ch.info(f"   Apache files: {len(results['apache'])}")


def backup_all(name: str | None, compress: str, options: dict[str, Any]):
    """Comprehensive backup of all server components."""
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server_name = require_active_server(options, config_manager)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = name or f"{server_name}_full_{timestamp}"

    if options.get("dry_run"):
        ch.info(f"[DRY RUN] Would create comprehensive backup: {backup_name}")
        ch.info("[DRY RUN] Components: system config, databases, HestiaCP, web servers")
        return

    ch.info(f"📦 Creating comprehensive backup: {backup_name}")
    ch.info("")

    # Run all backup types
    backup_system_config(backup_name, options)
    ch.info("")

    backup_all_databases(backup_name, compress, options)
    ch.info("")

    backup_hestia(backup_name, options)
    ch.info("")

    backup_web_config(backup_name, options)
    ch.info("")

    ch.success(f"✅ Comprehensive backup complete: {backup_name}")
    ch.info(f"   Location: {config_manager.backups_dir / backup_name}")


def list_backups_cmd(options: dict[str, Any]):
    """List all available backups."""
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    backups_dir = config_manager.backups_dir

    if not backups_dir.exists():
        ch.warning("No backups directory found")
        return

    backups = sorted(
        [d for d in backups_dir.iterdir() if d.is_dir()],
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )

    if not backups:
        ch.warning("No backups found")
        return

    if options.get("json"):
        backup_list = []
        for backup in backups:
            metadata_file = backup / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file, encoding='utf-8') as f:
                    metadata = json.load(f)
                    metadata["name"] = backup.name
                    metadata["path"] = str(backup)
                    backup_list.append(metadata)
        ch.raw_print(json.dumps({"backups": backup_list}))
    else:
        table = Table(title="📦 Available Backups", show_header=True, header_style="bold cyan")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="yellow")
        table.add_column("Date", style="green")
        table.add_column("Size", style="magenta", justify="right")

        for backup in backups:
            metadata_file = backup / "metadata.json"

            if metadata_file.exists():
                with open(metadata_file, encoding='utf-8') as f:
                    metadata = json.load(f)
                    backup_type = metadata.get("type", "unknown")
                    timestamp = metadata.get("timestamp", "unknown")

                    # Calculate total size
                    total_size = sum(f.stat().st_size for f in backup.rglob("*") if f.is_file()) / (
                        1024 * 1024
                    )

                    table.add_row(backup.name, backup_type, timestamp, f"{total_size:.2f} MB")
            else:
                # No metadata, just show basic info
                size = sum(f.stat().st_size for f in backup.rglob("*") if f.is_file()) / (
                    1024 * 1024
                )
                mtime = datetime.fromtimestamp(backup.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                table.add_row(backup.name, "unknown", mtime, f"{size:.2f} MB")

        ch.print(table)
        ch.info(f"\nBackups directory: {backups_dir}")


def restore_backup_cmd(backup_name: str, component: str | None, options: dict[str, Any]):
    """Restore from backup (with confirmation)."""
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    backup_dir = config_manager.backups_dir / backup_name

    if not backup_dir.exists():
        ch.error(f"Backup not found: {backup_name}")
        return

    metadata_file = backup_dir / "metadata.json"
    if metadata_file.exists():
        with open(metadata_file, encoding='utf-8') as f:
            metadata = json.load(f)
    else:
        metadata = {"type": "unknown"}

    if options.get("dry_run"):
        ch.info(f"[DRY RUN] Would restore from: {backup_name}")
        ch.info(f"[DRY RUN] Type: {metadata.get('type', 'unknown')}")
        if component:
            ch.info(f"[DRY RUN] Component: {component}")
        return

    # Confirmation required
    if not options.get("force"):
        if options.get("json"):
            ch.error("Restore requires --force flag in JSON mode")
            return

        ch.warning("⚠️  RESTORE OPERATION")
        ch.warning(f"   Backup: {backup_name}")
        ch.warning(f"   Type: {metadata.get('type', 'unknown')}")
        if component:
            ch.warning(f"   Component: {component}")
        ch.warning("   This will overwrite existing files/databases")

        confirm = input("\nProceed with restore? [y/N]: ")
        if confirm.lower() != "y":
            ch.info("Restore cancelled")
            return

    ch.info(f"🔄 Restoring from backup: {backup_name}")
    ch.warning("⚠️  Restore functionality requires manual review")
    ch.warning("   Please review backup contents in:")
    ch.warning(f"   {backup_dir}")
    ch.warning("   Then manually restore files/databases as needed")


# ============================================================================
# TYPER SUB-APP — extracted from navig/cli/__init__.py
# ============================================================================

import typer  # noqa: E402

from navig.cli._callbacks import show_subcommand_help  # noqa: E402
from navig.deprecation import deprecation_warning  # noqa: E402

backup_app = typer.Typer(
    help="Backup and export NAVIG configuration",
    invoke_without_command=True,
    no_args_is_help=False,
)


@backup_app.callback()
def backup_callback(ctx: typer.Context):
    """Backup management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("backup", ctx)
        raise typer.Exit()


@backup_app.command("export")
def backup_export(
    ctx: typer.Context,
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Output file path (auto-generated if not provided)"
    ),
    format: str = typer.Option(
        "archive", "--format", "-f", help="Output format: 'archive' (tar.gz) or 'json'"
    ),
    include_secrets: bool = typer.Option(
        False,
        "--include-secrets",
        help="Include unredacted secrets (passwords, API keys)",
    ),
    encrypt: bool = typer.Option(
        False, "--encrypt", "-e", help="Encrypt the output with a password"
    ),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Encryption password (prompted if not provided)"
    ),
):
    """
    Export NAVIG configuration to a backup file.

    Creates a portable backup of all hosts, apps, and settings.
    By default, sensitive data (passwords, API keys) is redacted.

    Examples:
        navig backup export
        navig backup export --format json --output ~/my-backup.json
        navig backup export --include-secrets --encrypt
    """
    from navig.commands.config_backup import export_config

    export_config(
        {
            "output": output,
            "format": format,
            "include_secrets": include_secrets,
            "encrypt": encrypt,
            "password": password,
            "yes": ctx.obj.get("yes", False),
            "confirm": ctx.obj.get("confirm", False),
            "json": ctx.obj.get("json", False),
        }
    )


@backup_app.command("import")
def backup_import(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to import"),
    merge: bool = typer.Option(
        True,
        "--merge/--replace",
        help="Merge with existing config (default) or replace",
    ),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Decryption password (prompted if needed)"
    ),
):
    """
    Import NAVIG configuration from a backup file.

    Restores hosts, apps, and settings from a previous export.

    Examples:
        navig backup import navig-config-20241206.tar.gz
        navig backup import backup.json --replace
        navig backup import encrypted-backup.tar.gz.enc --password mypassword
    """
    from navig.commands.config_backup import import_config

    import_config(
        {
            "file": file,
            "merge": merge,
            "password": password,
            "yes": ctx.obj.get("yes", False),
            "confirm": ctx.obj.get("confirm", False),
            "json": ctx.obj.get("json", False),
        }
    )


@backup_app.command("show")
def backup_show(
    ctx: typer.Context,
    file: Path | None = typer.Argument(None, help="Backup file to inspect"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Decryption password if encrypted"
    ),
    plain: bool = typer.Option(False, "--plain", help="Output plain text for scripting"),
):
    """Show backup details or list all backups (canonical command)."""
    if file:
        from navig.commands.navig_backup import inspect_export

        inspect_export(
            {
                "file": file,
                "password": password,
                "json": ctx.obj.get("json", False),
            }
        )
    else:
        from navig.commands.navig_backup import list_exports

        list_exports(
            {
                "json": ctx.obj.get("json", False),
                "plain": plain,
            }
        )


@backup_app.command("run")
def backup_run(
    ctx: typer.Context,
    config: bool = typer.Option(False, "--config", help="Backup system configuration files"),
    db_all: bool = typer.Option(False, "--db-all", help="Backup all databases"),
    hestia: bool = typer.Option(False, "--hestia", help="Backup HestiaCP configuration"),
    web: bool = typer.Option(False, "--web", help="Backup web server configuration"),
    all: bool = typer.Option(False, "--all", help="Run comprehensive backup"),
    restore: str | None = typer.Option(
        None, "--restore", help="Restore from a comprehensive backup by name"
    ),
    component: str | None = typer.Option(None, "--component", help="Specific component to restore"),
    name: str | None = typer.Option(None, "--name", "-n", help="Custom backup name"),
    compress: str = typer.Option(
        "gzip",
        "--compress",
        "-c",
        help="Compression for database backups: none|gzip|zstd",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Run server backup/restore operations (system config, DBs, Hestia, web)."""
    selected_count = sum(
        1 for flag in [config, db_all, hestia, web, all, restore is not None] if flag
    )

    if selected_count != 1:
        ch.error(
            "Choose exactly one backup operation.",
            "Use one of: --config, --db-all, --hestia, --web, --all, or --restore <name>.",
        )
        raise typer.Exit(1)

    if restore is not None:
        ctx.obj["force"] = force
        restore_backup_cmd(restore, component, ctx.obj)
        return

    if config:
        backup_system_config(name, ctx.obj)
    elif db_all:
        backup_all_databases(name, compress, ctx.obj)
    elif hestia:
        backup_hestia(name, ctx.obj)
    elif web:
        backup_web_config(name, ctx.obj)
    else:
        backup_all(name, compress, ctx.obj)


@backup_app.command("restore")
def backup_restore(
    ctx: typer.Context,
    backup_name: str = typer.Argument(..., help="Backup name to restore from"),
    component: str | None = typer.Option(
        None, "--component", "-c", help="Specific component to restore"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Restore from a comprehensive backup by name."""
    ctx.obj["force"] = force
    restore_backup_cmd(backup_name, component, ctx.obj)


@backup_app.command("list", hidden=True)
def backup_list(
    ctx: typer.Context,
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one backup per line) for scripting"
    ),
):
    """[DEPRECATED: Use 'navig backup show'] List available backups."""
    deprecation_warning("navig backup list", "navig backup show")
    from navig.commands.config_backup import list_exports

    list_exports(
        {
            "json": ctx.obj.get("json", False),
            "plain": plain,
        }
    )


@backup_app.command("inspect", hidden=True)
def backup_inspect(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to inspect"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Decryption password if encrypted"
    ),
):
    """[DEPRECATED: Use 'navig backup show <file>'] Inspect backup contents."""
    deprecation_warning("navig backup inspect", "navig backup show <file>")
    from navig.commands.config_backup import inspect_export

    inspect_export(
        {
            "file": file,
            "password": password,
            "json": ctx.obj.get("json", False),
        }
    )


@backup_app.command("remove")
def backup_remove(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to delete"),
):
    """Remove/delete a backup file (canonical command)."""
    from navig.commands.config_backup import delete_export

    delete_export(
        {
            "file": file,
            "yes": ctx.obj.get("yes", False),
            "confirm": ctx.obj.get("confirm", False),
            "json": ctx.obj.get("json", False),
        }
    )


@backup_app.command("delete", hidden=True)
def backup_delete(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to delete"),
):
    """[DEPRECATED: Use 'navig backup remove'] Delete backup file."""
    deprecation_warning("navig backup delete", "navig backup remove")
    from navig.commands.config_backup import delete_export

    delete_export(
        {
            "file": file,
            "yes": ctx.obj.get("yes", False),
            "confirm": ctx.obj.get("confirm", False),
            "json": ctx.obj.get("json", False),
        }
    )


@backup_app.command("config")
def backup_config_cmd(
    ctx: typer.Context,
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Output file path (auto-generated if not provided)"
    ),
    format: str = typer.Option(
        "archive", "--format", "-f", help="Output format: 'archive' (tar.gz) or 'json'"
    ),
    include_secrets: bool = typer.Option(
        False,
        "--include-secrets",
        help="Include unredacted secrets (passwords, API keys)",
    ),
    encrypt: bool = typer.Option(
        False, "--encrypt", "-e", help="Encrypt the output with a password"
    ),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Encryption password (prompted if not provided)"
    ),
):
    """
    Backup/export NAVIG configuration (hosts, apps, settings).

    Canonical alias for: navig backup export
    The inverse of: navig backup import

    Examples:
        navig backup config
        navig backup config --format json --output ~/my-backup.json
        navig backup config --include-secrets --encrypt
    """
    obj = ctx.obj or {}
    from navig.commands.config_backup import export_config

    export_config(
        {
            "output": output,
            "format": format,
            "include_secrets": include_secrets,
            "encrypt": encrypt,
            "password": password,
            "yes": obj.get("yes", False),
            "confirm": obj.get("confirm", False),
            "json": obj.get("json", False),
        }
    )
