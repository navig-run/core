"""HestiaCP Integration Commands

Provides comprehensive HestiaCP management capabilities.
Requires HestiaCP installed on the remote server.
"""

import json
import shlex
from typing import Any

from rich.table import Table

from navig import console_helper as ch


def _execute_hestia_cmd(
    command: str, server_config: dict, options: dict[str, Any]
) -> dict:
    """Execute HestiaCP command via API or CLI.

    Args:
        command: HestiaCP CLI command
        server_config: Server configuration
        options: Command options

    Returns:
        Dict with success status and output/error
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    # Execute via SSH
    result = remote_ops.execute_command(command, server_config)

    return {
        "success": result.returncode == 0,
        "output": result.stdout.strip(),
        "error": result.stderr.strip(),
    }


def list_users_cmd(options: dict[str, Any]):
    """List HestiaCP users.

    Args:
        options: Command options (app, json)
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    server_name = options.get("app") or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return

    server_config = config_manager.load_server_config(server_name)

    # Check if HestiaCP is installed
    check_cmd = "command -v v-list-users"
    result = _execute_hestia_cmd(check_cmd, server_config, options)

    if not result["success"]:
        ch.error(f"HestiaCP not found on server '{server_name}'")
        ch.dim("  The command 'v-list-users' is not available.")
        ch.dim("")
        ch.dim("  Possible causes:")
        ch.dim("  • HestiaCP is not installed on this server")
        ch.dim("  • HestiaCP binaries are not in the system PATH")
        ch.dim("  • Current user lacks permissions to run HestiaCP commands")
        ch.dim("")
        ch.dim("  To verify installation:")
        ch.dim(f'    navig --host {server_name} run "command -v v-list-users"')
        return

    # List users
    cmd = "v-list-users json"
    result = _execute_hestia_cmd(cmd, server_config, options)

    if not result["success"]:
        ch.error(f"Failed to list HestiaCP users on '{server_name}'")
        if result["error"]:
            ch.dim(f"  {result['error']}")
        return

    try:
        users_data = json.loads(result["output"])

        if options.get("json"):
            ch.raw_print(json.dumps({"users": users_data, "count": len(users_data)}))
        elif options.get("plain"):
            # Plain text output - one user per line for scripting
            for username in users_data.keys():
                ch.raw_print(username)
        else:
            table = Table(title=f"HestiaCP Users on {server_name}")
            table.add_column("User", style="cyan")
            table.add_column("Package", style="yellow")
            table.add_column("Email", style="green")
            table.add_column("Web Domains", justify="right")
            table.add_column("Databases", justify="right")

            for username, user_info in users_data.items():
                table.add_row(
                    username,
                    user_info.get("PACKAGE", "default"),
                    user_info.get("CONTACT", ""),
                    str(user_info.get("U_WEB_DOMAINS", 0)),
                    str(user_info.get("U_DATABASES", 0)),
                )

            ch.console.print(table)
            ch.dim(f"\nTotal: {len(users_data)} users")

    except json.JSONDecodeError:
        ch.error("Failed to parse HestiaCP output")


def list_domains_cmd(user: str | None, options: dict[str, Any]):
    """List HestiaCP domains.

    Args:
        user: Optional username to filter domains
        options: Command options (app, json)
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    server_name = options.get("app") or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return

    server_config = config_manager.load_server_config(server_name)

    # List domains
    if user:
        cmd = f"v-list-web-domains {user} json"
    else:
        # Get all users first, then domains for each
        users_cmd = "v-list-users json"
        users_result = _execute_hestia_cmd(users_cmd, server_config, options)

        if not users_result["success"]:
            ch.error(f"HestiaCP not found on server '{server_name}'")
            ch.dim("  Unable to list users - HestiaCP may not be installed.")
            ch.dim("")
            ch.dim("  To verify installation:")
            ch.dim(f'    navig --host {server_name} run "command -v v-list-users"')
            return

        try:
            users_data = json.loads(users_result["output"])
            all_domains = {}

            for username in users_data.keys():
                cmd = f"v-list-web-domains {username} json"
                domains_result = _execute_hestia_cmd(cmd, server_config, options)

                if domains_result["success"]:
                    try:
                        user_domains = json.loads(domains_result["output"])
                        for domain, info in user_domains.items():
                            info["USER"] = username
                            all_domains[domain] = info
                    except json.JSONDecodeError:
                        pass  # malformed JSON; skip line

            if options.get("json"):
                ch.raw_print(
                    json.dumps({"domains": all_domains, "count": len(all_domains)})
                )
            elif options.get("plain"):
                # Plain text output - one domain per line for scripting
                for domain in all_domains:
                    ch.raw_print(domain)
            else:
                table = Table(title=f"HestiaCP Domains on {server_name}")
                table.add_column("Domain", style="cyan")
                table.add_column("User", style="yellow")
                table.add_column("IP", style="green")
                table.add_column("SSL", justify="center")

                for domain, info in all_domains.items():
                    ssl_status = "✓" if info.get("SSL", "no") == "yes" else "✗"
                    table.add_row(
                        domain, info.get("USER", ""), info.get("IP", ""), ssl_status
                    )

                ch.console.print(table)
                ch.dim(f"\nTotal: {len(all_domains)} domains")

            return

        except json.JSONDecodeError:
            ch.error("Failed to parse users data")
            return

    # Single user domains
    result = _execute_hestia_cmd(cmd, server_config, options)

    if not result["success"]:
        ch.error(f"Failed to list domains for user '{user}' on '{server_name}'")
        if result["error"]:
            ch.dim(f"  {result['error']}")
        ch.dim("")
        ch.dim("  Possible causes:")
        ch.dim(f"  • User '{user}' does not exist in HestiaCP")
        ch.dim("  • HestiaCP is not installed on this server")
        ch.dim("")
        ch.dim("  To verify user exists:")
        ch.dim("    navig hestia users")
        return

    try:
        domains_data = json.loads(result["output"])

        if options.get("json"):
            ch.raw_print(
                json.dumps(
                    {"user": user, "domains": domains_data, "count": len(domains_data)}
                )
            )
        elif options.get("plain"):
            # Plain text output - one domain per line for scripting
            for domain in domains_data.keys():
                ch.raw_print(domain)
        else:
            table = Table(title=f"Domains for {user}")
            table.add_column("Domain", style="cyan")
            table.add_column("IP", style="green")
            table.add_column("SSL", justify="center")
            table.add_column("PHP", style="yellow")

            for domain, info in domains_data.items():
                ssl_status = "✓" if info.get("SSL", "no") == "yes" else "✗"
                table.add_row(
                    domain, info.get("IP", ""), ssl_status, info.get("BACKEND", "")
                )

            ch.console.print(table)
            ch.dim(f"\nTotal: {len(domains_data)} domains")

    except json.JSONDecodeError:
        ch.error("Failed to parse domains data")


def add_user_cmd(username: str, password: str, email: str, options: dict[str, Any]):
    """Add new HestiaCP user.

    Args:
        username: Username to create
        password: User password
        email: User email address
        options: Command options (app, dry_run, json)
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    server_name = options.get("app") or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    # SECURITY: Use stdin to pass password instead of command-line argument
    # This prevents password from appearing in process listings (ps aux, top, etc.)
    # HestiaCP v-add-user reads from stdin when password is '-'
    cmd = f"printf '%s\\n' {shlex.quote(password)} | v-add-user {shlex.quote(username)} - {shlex.quote(email)}"

    # Dry run
    if options.get("dry_run"):
        if options.get("json"):
            ch.raw_print(
                json.dumps(
                    {
                        "success": True,
                        "dry_run": True,
                        "action": f"v-add-user {username} <password> {email}",
                    }
                )
            )
        else:
            ch.info(f"[DRY RUN] Would create user: {username} ({email})")
        return True

    # Execute
    result = _execute_hestia_cmd(cmd, server_config, options)

    if result["success"]:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "user": username}))
        else:
            ch.success(f"✓ Created user: {username}")
        return True
    else:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": result["error"]}))
        else:
            ch.error(f"Failed: {result['error']}")
        return False


def delete_user_cmd(username: str, options: dict[str, Any]):
    """Delete HestiaCP user.

    Args:
        username: Username to delete
        options: Command options (app, force, dry_run, json)
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    server_name = options.get("app") or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    # Dry run
    if options.get("dry_run"):
        if options.get("json"):
            ch.raw_print(
                json.dumps({"success": True, "dry_run": True, "user": username})
            )
        else:
            ch.info(f"[DRY RUN] Would delete user: {username}")
        return True

    # Confirmation
    if not options.get("force"):
        if not options.get("json"):
            if not ch.confirm_action(f"Delete user {username} and ALL their data?"):
                ch.warning("Cancelled.")
                return False
        else:
            ch.raw_print(
                json.dumps({"success": False, "error": "Use --force in JSON mode"})
            )
            return False

    # Execute
    cmd = f"v-delete-user {username}"
    result = _execute_hestia_cmd(cmd, server_config, options)

    if result["success"]:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "user": username}))
        else:
            ch.success(f"✓ Deleted user: {username}")
        return True
    else:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": result["error"]}))
        else:
            ch.error(f"Failed: {result['error']}")
        return False


def add_domain_cmd(user: str, domain: str, options: dict[str, Any]):
    """Add domain to HestiaCP user.

    Args:
        user: Username
        domain: Domain name to add
        options: Command options (app, dry_run, json)
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    server_name = options.get("app") or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    cmd = f"v-add-web-domain {user} {domain}"

    if options.get("dry_run"):
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "dry_run": True, "action": cmd}))
        else:
            ch.info(f"[DRY RUN] Would add domain: {domain} to user: {user}")
        return True

    result = _execute_hestia_cmd(cmd, server_config, options)

    if result["success"]:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "user": user, "domain": domain}))
        else:
            ch.success(f"✓ Added domain: {domain}")
        return True
    else:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": result["error"]}))
        else:
            ch.error(f"Failed: {result['error']}")
        return False


def delete_domain_cmd(user: str, domain: str, options: dict[str, Any]):
    """Delete domain from HestiaCP.

    Args:
        user: Username
        domain: Domain name to delete
        options: Command options (app, force, dry_run, json)
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    server_name = options.get("app") or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    if options.get("dry_run"):
        if options.get("json"):
            ch.raw_print(
                json.dumps({"success": True, "dry_run": True, "domain": domain})
            )
        else:
            ch.info(f"[DRY RUN] Would delete domain: {domain} from user: {user}")
        return True

    if not options.get("force"):
        if not options.get("json"):
            if not ch.confirm_action(f"Delete domain {domain}?"):
                ch.warning("Cancelled.")
                return False
        else:
            ch.raw_print(
                json.dumps({"success": False, "error": "Use --force in JSON mode"})
            )
            return False

    cmd = f"v-delete-web-domain {user} {domain}"
    result = _execute_hestia_cmd(cmd, server_config, options)

    if result["success"]:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "user": user, "domain": domain}))
        else:
            ch.success(f"✓ Deleted domain: {domain}")
        return True
    else:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": result["error"]}))
        else:
            ch.error(f"Failed: {result['error']}")
        return False


def renew_ssl_cmd(user: str, domain: str, options: dict[str, Any]):
    """Renew SSL certificate for domain.

    Args:
        user: Username
        domain: Domain name
        options: Command options (app, dry_run, json)
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    server_name = options.get("app") or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    cmd = f"v-add-letsencrypt-domain {user} {domain}"

    if options.get("dry_run"):
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "dry_run": True, "action": cmd}))
        else:
            ch.info(f"[DRY RUN] Would renew SSL for: {domain}")
        return True

    result = _execute_hestia_cmd(cmd, server_config, options)

    if result["success"]:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "domain": domain}))
        else:
            ch.success(f"✓ SSL renewed for: {domain}")
        return True
    else:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": result["error"]}))
        else:
            ch.error(f"Failed: {result['error']}")
        return False


def rebuild_web_cmd(user: str, options: dict[str, Any]):
    """Rebuild web configuration for user.

    Args:
        user: Username
        options: Command options (app, dry_run, json)
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    server_name = options.get("app") or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    cmd = f"v-rebuild-web-domains {user}"

    if options.get("dry_run"):
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "dry_run": True, "action": cmd}))
        else:
            ch.info(f"[DRY RUN] Would rebuild web config for: {user}")
        return True

    result = _execute_hestia_cmd(cmd, server_config, options)

    if result["success"]:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "user": user}))
        else:
            ch.success(f"✓ Web config rebuilt for: {user}")
        return True
    else:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": result["error"]}))
        else:
            ch.error(f"Failed: {result['error']}")
        return False


def backup_user_cmd(user: str, options: dict[str, Any]):
    """Backup HestiaCP user.

    Args:
        user: Username to backup
        options: Command options (app, dry_run, json)
    """
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    server_name = options.get("app") or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    cmd = f"v-backup-user {user}"

    if options.get("dry_run"):
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "dry_run": True, "action": cmd}))
        else:
            ch.info(f"[DRY RUN] Would backup user: {user}")
        return True

    ch.info(f"Creating backup for: {user}")
    result = _execute_hestia_cmd(cmd, server_config, options)

    if result["success"]:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "user": user}))
        else:
            ch.success(f"✓ Backup created for: {user}")
        return True
    else:
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": result["error"]}))
        else:
            ch.error(f"Failed: {result['error']}")
        return False
