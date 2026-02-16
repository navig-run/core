"""File Operation Commands"""
from navig import console_helper as ch
from pathlib import Path
from typing import Dict, Any, Optional
import json

def upload_file_cmd(local: Path, remote: Optional[str], options: Dict[str, Any]):
    """Upload file/directory."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations
    
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)
    
    server_name = options.get('app') or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return
    
    if not local.exists():
        ch.error(f"Local file not found: {local}")
        return
    
    json_enabled = options.get('json', False)
    
    # Smart path detection
    if remote is None:
        server_config = config_manager.load_server_config(server_name)
        web_root = server_config.get('paths', {}).get('web_root', '/tmp')
        remote = f"{web_root}/{local.name}"
        if not json_enabled:
            ch.dim(f"Auto-detected remote path: {remote}")
    
    # Check if upload requires confirmation (uploads are state-changing)
    if not ch.confirm_operation(
        operation_name=f"Upload: {local.name} → {remote}",
        operation_type='standard',
        host=server_name,
        details=f"Size: {local.stat().st_size:,} bytes",
        auto_confirm=options.get('yes', False),
        force_confirm=options.get('confirm', False),
    ):
        ch.warning("Cancelled.")
        return
    
    if not json_enabled:
        ch.info(f"Uploading: {local} -> {remote}")
    
    server_config = config_manager.load_server_config(server_name)
    
    # Show progress for uploads
    if json_enabled:
        success = remote_ops.upload_file(local, remote, server_config)
    else:
        with ch.create_spinner("Transferring file..."):
            success = remote_ops.upload_file(local, remote, server_config)
    
    if success:
        if json_enabled:
            ch.raw_print(json.dumps({"success": True, "local": str(local), "remote": remote, "size_bytes": local.stat().st_size}))
        else:
            ch.success("Upload complete. No traces left.")  # void: except in logs. always in logs.
    else:
        if json_enabled:
            ch.raw_print(json.dumps({"success": False, "error": "Upload failed"}))
        else:
            ch.error("Upload failed.")
        ch.info("")
        ch.info("Common causes:")
        ch.info("  1. Permission denied: Check remote directory ownership")
        ch.info("     Fix: navig run \"chown -R $(whoami) /remote/path\"")
        ch.info("  2. Directory not found: Create it first")
        ch.info("     Fix: navig mkdir /remote/path --parents")
        ch.info("  3. Disk full: Check space with 'df -h'")
        ch.info("  4. SSH connection: Test with 'navig run \"echo test\"'")


def download_file_cmd(remote: str, local: Optional[Path], options: Dict[str, Any]):
    """Download file/directory."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations
    
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)
    
    server_name = options.get('app') or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return
    
    if local is None:
        local = Path.cwd() / Path(remote).name
        ch.dim(f"Auto-detected local path: {local}")
    
    ch.info(f"Downloading: {remote} -> {local}")
    
    server_config = config_manager.load_server_config(server_name)
    
    # Show progress for downloads
    with ch.create_spinner("Transferring file..."):
        success = remote_ops.download_file(remote, local, server_config)
    
    if success:
        ch.success(f"✓ Download complete: {local}")
    else:
        ch.error("Download failed.")
        ch.info("")
        ch.info("Common causes:")
        ch.info("  1. File not found: Check path with 'navig list /path'")
        ch.info("  2. Permission denied: Check file permissions")
        ch.info("     Fix: navig run \"chmod 644 /remote/file\"")
        ch.info("  3. Local disk full: Check space with 'df -h' (Unix) or 'dir' (Windows)")
        ch.info("  4. Network timeout: Check connection with 'navig tunnel status'")


def list_remote_directory(remote_path: str, options: Dict[str, Any]):
    """List remote directory contents."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations
    
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)
    
    server_name = options.get('app') or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return
    
    server_config = config_manager.load_server_config(server_name)
    result = remote_ops.execute_command(f"ls -lah {remote_path}", server_config)
    
    if result.returncode == 0:
        ch.raw_print(result.stdout)
    else:
        ch.error(f"Error: {result.stderr}")


