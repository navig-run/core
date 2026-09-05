"""
Scaffold Commands
"""

import os
from pathlib import Path

import typer

from navig import console_helper as ch
from navig.cli._callbacks import show_subcommand_help
from navig.config import get_config_manager
from navig.core.scaffolder import Scaffolder
from navig.remote import RemoteOperations

scaffold_app = typer.Typer(
    help="Scaffold project structures from templates",
    invoke_without_command=True,
    no_args_is_help=False,
)


@scaffold_app.callback(invoke_without_command=True)
def scaffold_callback(ctx: typer.Context):
    """Scaffold management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("scaffold", ctx)
        raise typer.Exit()


def _show_preview(
    scaffolder: Scaffolder,
    template_data: dict,
    variables: dict,
    template_path: Path,
) -> None:
    """Print what `--dry-run` would create, or explain why it cannot.

    This used to iterate ``template_data.get("files", [])`` — a key the template schema
    does not have; `validate_template` requires ``structure``, and "files" appeared
    nowhere else in the subsystem. So the loop was always empty and every dry run
    printed the header "Files to be created:" followed by nothing, for every template.

    `Scaffolder.preview` renders for real into a staging directory, so the list
    reflects conditions and rendered path names, and a template that would fail says so
    here — which is the whole reason to run `--dry-run` before committing to a target.
    """
    try:
        entries = scaffolder.preview(
            template_data, variables, template_dir=template_path.parent
        )
    except Exception as e:
        ch.error(f"Generation would fail: {e}")
        raise typer.Exit(1) from e

    if not entries:
        ch.warning(
            "This template would create nothing — every item's condition evaluated "
            "to false for these variables."
        )
        return

    ch.info("\nFiles to be created:")
    for relative_path, kind in entries:
        ch.info(f"  - {relative_path} ({kind})")


@scaffold_app.command("apply")
def apply(
    template_path: Path = typer.Argument(..., help="Path to YAML template file", exists=True),
    target_dir: str = typer.Option(
        ".", "--target-dir", "-d", help="Target directory (local or remote)"
    ),
    host: str = typer.Option(
        None, "--host", "-h", help="Remote host to deploy to (defaults to local)"
    ),
    set_var: list[str] = typer.Option(None, "--set", help="Set variable like key=value"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without creating files"),
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
            _show_preview(scaffolder, template_data, variables, template_path)
            return

        ch.step(f"Generating locally at {target_path}...")
        try:
            scaffolder.generate(template_data, target_path, variables, template_dir=template_path.parent)
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
            _show_preview(scaffolder, template_data, variables, template_path)
            return

        # Multi-agent safety: claim the host before mutating it (navig.core.host_lock).
        # Placed AFTER the dry-run return: a dry run writes nothing, so it must not
        # contend for the lock. Below this line we upload, mkdir, untar and rm on the host.
        from navig.core import host_lock  # noqa: PLC0415

        host_lock.guard_remote(config_manager, host, f"navig scaffold apply: {target_dir}")

        ch.step(f"Preparing scaffold for remote host {host}...")

        # Generate to a local temp tarball
        try:
            archive_path = scaffolder.generate_to_temp_archive(template_data, variables, template_dir=template_path.parent)
            ch.dim(f"Created temporary archive: {archive_path}")
        except Exception as e:
            ch.error(f"Failed to create archive: {e}")
            raise typer.Exit(1) from e

        try:
            # Upload
            remote_archive_path = f"/tmp/{archive_path.name}"
            ch.step(f"Uploading to {host}...")

            with ch.create_spinner("Uploading..."):
                success = remote_ops.upload_file(archive_path, remote_archive_path, server_config)

            if not success:
                ch.error("Upload failed")
                raise typer.Exit(1)

            import shlex

            target_dir_safe = shlex.quote(target_dir)
            remote_archive_safe = shlex.quote(remote_archive_path)

            # Extract
            ch.step("Extracting on remote host...")

            # Ensure target directory exists
            mkdir_cmd = f"mkdir -p {target_dir_safe}"
            remote_ops.execute_command(mkdir_cmd, server_config)

            # Extract tar
            # -C changes dir before extracting
            tar_cmd = f"tar -xzf {remote_archive_safe} -C {target_dir_safe}"
            result = remote_ops.execute_command(tar_cmd, server_config)

            if result.returncode != 0:
                ch.error(f"Extraction failed: {result.stderr}")
                # Cleanup remote tmp
                remote_ops.execute_command(f"rm -f {remote_archive_safe}", server_config)
                raise typer.Exit(1)

            # Cleanup remote tmp
            remote_ops.execute_command(f"rm -f {remote_archive_safe}", server_config)

            ch.success(f"Scaffold deployed to {host}:{target_dir}")

        finally:
            # Cleanup local tmp
            if archive_path.exists():
                os.unlink(archive_path)


@scaffold_app.command("validate")
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


# Backward compatibility for older imports expecting `app`
app = scaffold_app
