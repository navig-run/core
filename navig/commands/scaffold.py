"""
Scaffold Commands
"""

import os
from pathlib import Path

import typer

from navig import console_helper as ch
from navig.config import get_config_manager
from navig.core.scaffolder import Scaffolder
from navig.remote import RemoteOperations

app = typer.Typer(help="Scaffold project structures from templates")


@app.command("apply")
def apply(
    template_path: Path = typer.Argument(
        ..., help="Path to YAML template file", exists=True
    ),
    target_dir: str = typer.Option(
        ".", "--target-dir", "-d", help="Target directory (local or remote)"
    ),
    host: str = typer.Option(
        None, "--host", "-h", help="Remote host to deploy to (defaults to local)"
    ),
    set_var: list[str] = typer.Option(
        None, "--set", help="Set variable like key=value"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate without creating files"
    ),
):
    """
    Generate files/directories from a template.
    """
    scaffolder = Scaffolder()

    # 1. Parse Variables
    variables = {}
    if set_var:
        for item in set_var:
            if "=" in item:
                k, v = item.split("=", 1)
                variables[k.strip()] = v.strip()
            else:
                ch.warning(f"Ignoring invalid variable format: {item}")

    # 2. Add system variables
    from datetime import datetime

    variables["scaffold_date"] = datetime.now().strftime("%Y-%m-%d")

    # 3. Load Template
    try:
        template_data = scaffolder.validate_template(template_path)
    except ValueError as e:
        ch.error(f"Template error: {e}")
        raise typer.Exit(1) from e

    template_name = template_data.get("meta", {}).get("name", template_path.stem)
    ch.info(f"Applying template: [bold]{template_name}[/bold]")

    # 4. Handle Execution
    if not host:
        # Local Generation
        target_path = Path(target_dir).resolve()

        if dry_run:
            ch.info(f"[DRY RUN] Would generate to: {target_path}")
            ch.info("\nFiles to be created:")
            for file_spec in template_data.get("files", []):
                file_path = file_spec.get("path", "unknown")
                file_type = file_spec.get("type", "file")
                ch.info(f"  - {file_path} ({file_type})")
            return

        ch.step(f"Generating locally at {target_path}...")
        try:
            scaffolder.generate(template_data, target_path, variables)
            ch.success(f"Scaffold complete: {target_path}")
        except Exception as e:
            ch.error(f"Generation failed: {e}")
            raise typer.Exit(1) from e

    else:
        # Remote Generation
        config_manager = get_config_manager()
        server_config = config_manager.load_server_config(host)
        remote_ops = RemoteOperations(config_manager)

        if dry_run:
            ch.info(f"[DRY RUN] Would generate to {host}:{target_dir}")
            return

        ch.step(f"Preparing scaffold for remote host {host}...")

        # Generate to a local temp tarball
        try:
            archive_path = scaffolder.generate_to_temp_archive(template_data, variables)
            ch.dim(f"Created temporary archive: {archive_path}")
        except Exception as e:
            ch.error(f"Failed to create archive: {e}")
            raise typer.Exit(1) from e

        try:
            # Upload
            remote_archive_path = f"/tmp/{archive_path.name}"
            ch.step(f"Uploading to {host}...")

            with ch.create_spinner("Uploading..."):
                success = remote_ops.upload_file(
                    archive_path, remote_archive_path, server_config
                )

            if not success:
                ch.error("Upload failed")
                raise typer.Exit(1)

            # Extract
            ch.step("Extracting on remote host...")

            # Ensure target directory exists
            mkdir_cmd = f"mkdir -p {target_dir}"
            remote_ops.execute_command(mkdir_cmd, server_config)

            # Extract tar
            # -C changes dir before extracting
            tar_cmd = f"tar -xzf {remote_archive_path} -C {target_dir}"
            result = remote_ops.execute_command(tar_cmd, server_config)

            if result.returncode != 0:
                ch.error(f"Extraction failed: {result.stderr}")
                # Cleanup remote tmp
                remote_ops.execute_command(f"rm {remote_archive_path}", server_config)
                raise typer.Exit(1)

            # Cleanup remote tmp
            remote_ops.execute_command(f"rm {remote_archive_path}", server_config)

            ch.success(f"Scaffold deployed to {host}:{target_dir}")

        finally:
            # Cleanup local tmp
            if archive_path.exists():
                os.unlink(archive_path)


@app.command("validate")
def validate(template_path: Path):
    """Validate a template file syntax."""
    scaffolder = Scaffolder()
    try:
        data = scaffolder.validate_template(template_path)
        name = data.get("meta", {}).get("name", "Unknown")
        ch.success(f"✓ Valid template: {name}")
        ch.info(f"Structure items: {len(data.get('structure', []))}")
    except ValueError as e:
        ch.error(str(e))
        raise typer.Exit(1) from e
