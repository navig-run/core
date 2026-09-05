"""A destructive remote command must claim the host lock, not just `navig run`.

`host_lock` exists because of a real incident (#1099): two agent sessions worked on
`cybesis-vps` at once — one fixing two down websites, the other purging apache2 + php8.3 +
HestiaCP, rewriting root's crontab and rebooting. It first guarded `navig run`; #1100
widened it to `navig file add/remove/edit/mkdir` after finding "the same collision, one
command over".

It is still one command over. Measured across `core/navig/commands/` — functions that
reach a host via `execute_command`/`upload_file`:

    59 touch a host · 7 guarded · **52 unguarded**

and the unguarded set includes commands that are not merely mutating but *destructive*:

    docker exec      — runs an ARBITRARY command in a container on that host
    docker compose   — up/down a whole stack
    docker stop/start/restart
    security         — `sudo ufw --force enable` / `ufw disable` (the firewall)
    scaffold         — `rm -f <remote path>`

`navig run "docker restart x"` takes the lock; `navig docker restart x` did not. Same
effect on the same server, one verb apart — which is precisely the shape the lock exists
to stop.

This file pins the families whose commands were read and confirmed destructive. Reads are
deliberately NOT guarded (`docker ps/logs/inspect/stats`): blocking them is friction with
nothing to protect, which is the same call #1100 made for `file get/list/show/tree/test`.

⚠ Scope: the remaining unguarded families (maintenance · webserver · monitoring · matrix ·
ai · hestia) are NOT covered here. They were measured, not classified — asserting they are
read-only without reading every command string would be a guess, and a guard built on a
guess is worse than none.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

import pytest

COMMANDS = Path(__file__).resolve().parents[2] / "navig" / "commands"

#: Functions that MUTATE a remote host and must therefore claim the lock.
MUST_LOCK: dict[str, tuple[str, ...]] = {
    "docker.py": (
        "docker_exec",      # arbitrary command inside a container
        "docker_compose",   # up / down / restart a stack
        "docker_restart",
        "docker_stop",
        "docker_start",
    ),
    "scaffold.py": ("apply",),   # uploads, mkdir -p, tar -xzf and rm -f on the host
    "security.py": (
        "firewall_add_rule",
        "firewall_remove_rule",
        "firewall_enable",       # sudo ufw --force enable
        "firewall_disable",      # sudo ufw disable — turns the firewall OFF
        "fail2ban_unban",
        # `apt-get update` installs nothing but rewrites /var/lib/apt/lists and takes the
        # server's dpkg/apt lock, so it contends with another session's install or purge —
        # and an apt purge is the incident this whole mechanism exists for.
        "check_security_updates",
    ),
    "webserver.py": (
        "enable_site",       # a2ensite / ln -sf into sites-enabled
        "disable_site",      # a2dissite / rm -f from sites-enabled
        "enable_module",     # a2enmod
        "disable_module",    # a2dismod
        "reload_server",     # systemctl reload <web server>
    ),
    "maintenance.py": (
        "update_packages",   # apt update + upgrade — the incident's own family
        "clean_packages",    # apt clean / autoremove
        "cleanup_temp",
        "rotate_logs",       # logrotate -f
    ),
    "backup.py": (
        "backup_system_config",  # mkdir + tar on the host
        "backup_hestia",         # mkdir + tar + rm -f on the host
        "backup_web_config",
    ),
    # `monitoring` is otherwise read-only — this one function restarts a systemd unit,
    # which is why "the module looks like reads" is not a classification.
    "monitoring.py": ("restart_remote_service",),
    # HestiaCP — the control panel the #1099 incident actually purged. Every one of these
    # sends a `v-*` write through the shared `_execute_hestia_cmd` executor; the executor
    # itself is NOT guarded because `list_users_cmd`/`list_domains_cmd` share it and
    # locking a read would be friction with nothing to protect.
    "hestia.py": (
        "add_user_cmd",        # v-add-user
        "delete_user_cmd",     # v-delete-user — removes a user and their sites
        "add_domain_cmd",      # v-add-web-domain
        "delete_domain_cmd",   # v-delete-web-domain — removes a live vhost
        "renew_ssl_cmd",       # v-add-letsencrypt-domain
        "rebuild_web_cmd",     # v-rebuild-web-domains — rewrites vhost config
        "backup_user_cmd",     # v-backup-user
    ),
    "remote.py": (
        # `navig run` — the original guard from #1099. Pinned so it cannot regress.
        "run_remote_command",
        # apt-get/yum install on the host: takes the package-manager lock, so it
        # contends with another session's install or purge.
        "install_remote_package",
    ),
}

#: Host-touching functions verified to be READ-ONLY, with the command they send.
READ_ONLY: dict[str, str] = {
    "docker_ps": "docker ps — lists containers",
    "docker_logs": "docker logs — reads container output",
    "docker_inspect": "docker inspect — reads container metadata",
    "docker_stats": "docker stats — reads resource usage",
    "firewall_status": "sudo ufw status verbose — reads firewall rules",
    "fail2ban_status": "systemctl is-active / fail2ban-client status — reads",
    "ssh_audit": "greps sshd_config — reads",
    "audit_connections": "ss -tunap / -tuln — reads sockets",
    "list_vhosts": "ls of sites-enabled/available — reads",
    "test_config": "apache2ctl configtest / nginx -t — validates, changes nothing",
    "check_filesystem": "df -h — reads",
    "system_info": "reads host facts",
    "monitor_resources": "reads CPU/memory",
    "monitor_disk": "df -h — reads",
    "monitor_services": "reads unit status",
    "monitor_network": "reads interfaces/sockets",
    "view_service_logs": "journalctl -u — reads",
    "generate_report": "top/free — reads",
    "list_users_cmd": "v-list-users — reads",
    "list_domains_cmd": "v-list-users + v-list-web-domains — reads",
}


#: Methods that actually put a command on the wire to a host.
_WIRE = {"execute_command", "upload_file"}


def _host_touching(path: Path) -> dict[str, bool]:
    """{function name: does it call guard_remote} for every host-touching function.

    Reaching the host **indirectly** counts. `hestia.py` routes every command through a
    module-local `_execute_hestia_cmd(...)` wrapper, so a rule that only looked for a
    direct `remote_ops.execute_command` call saw the wrapper and none of its seven
    callers — the whole family would have been invisible to this guard while every one of
    them wrote to a live server. Resolved transitively within the module, which is where
    such wrappers live.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def called_names(fn) -> tuple[set[str], set[str]]:
        """(bare function names it calls, attribute names it calls)."""
        bare = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        attrs = {c.func.attr for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
        return bare, attrs

    info = {fn.name: called_names(fn) for fn in funcs}
    direct = {name for name, (_bare, attrs) in info.items() if attrs & _WIRE}

    # Propagate "reaches a host" back through module-local callers until it settles.
    reaching = set(direct)
    changed = True
    while changed:
        changed = False
        for name, (bare, _attrs) in info.items():
            if name not in reaching and bare & reaching:
                reaching.add(name)
                changed = True

    return {
        name: "guard_remote" in info[name][1]
        for name in reaching
    }


@cache
def _scan(module: str) -> tuple[tuple[str, bool], ...]:
    return tuple(sorted(_host_touching(COMMANDS / module).items()))


@pytest.mark.parametrize(
    ("module", "func"),
    [(m, f) for m, fs in MUST_LOCK.items() for f in fs],
)
def test_a_destructive_command_claims_the_host_lock(module: str, func: str):
    found = dict(_scan(module))
    assert func in found, (
        f"{module}::{func} no longer reaches a host — if it was renamed, update MUST_LOCK; "
        "if it stopped touching the host, remove it"
    )
    assert found[func], (
        f"{module}::{func} mutates a REMOTE host without calling "
        "host_lock.guard_remote(). A second agent session can change that server "
        "underneath this one — the collision #1099 exists to prevent."
    )


def test_the_scan_still_finds_these_modules():
    """A floor: an AST rule that silently matches nothing looks exactly like a clean run."""
    for module in MUST_LOCK:
        assert _scan(module), f"no host-touching functions found in {module} — rule broke"


def test_reads_are_left_alone():
    """Guarding a read would be friction with nothing to protect.

    Pinned so a well-meaning sweep does not lock `docker ps` and make status checks
    contend with a live deploy.
    """
    found: dict[str, bool] = {}
    for module in MUST_LOCK:
        found.update(dict(_scan(module)))
    for name, why in READ_ONLY.items():
        if name in found:
            assert not found[name], f"{name} is read-only ({why}) and must not take the lock"
