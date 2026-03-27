"""
NAVIG Bot Start Menu - Interactive category navigation matching VS Code sidebar.

Provides a rich /start menu with drill-down category buttons:
  Main -> Infrastructure -> {Hosts, Apps, Tunnels, Files}
  Main -> Docker -> actions
  Main -> Database -> actions
  Main -> Monitoring -> actions
  Main -> Tools -> actions
  Main -> Utilities -> actions
  Main -> Core -> actions
"""


def build_main_menu(user_name: str = "") -> dict:
    """Build the main /start menu."""
    greeting = f"Hey {user_name}! " if user_name else ""
    text = (
        f"{greeting}I'm **NAVIG** \U0001f991 \u2014 your personal Kraken.\n\n"
        "AI is always on. Just talk to me naturally.\n\n"
        "**\U0001f4da Command Categories:**\n"
        "\u26a1 **Core** \u2014 Status, help, ping\n"
        "\U0001f5a5\ufe0f **Infrastructure** \u2014 Hosts, apps, tunnels, files\n"
        "\U0001f4ca **Monitoring** \u2014 CPU, disk, memory, services, ports\n"
        "\U0001f433 **Docker** \u2014 Containers, logs, compose\n"
        "\U0001f5c4\ufe0f **Database** \u2014 Query, dump, restore, optimize\n"
        "\U0001f527 **Tools** \u2014 Backup, HestiaCP, run commands\n"
        "\U0001f6e0\ufe0f **Utilities** \u2014 AI, crypto, weather, reminders, web tools\n\n"
        "**\U0001f4ac Or just ask naturally:**\n"
        '\u2022 "Show me docker containers"\n'
        '\u2022 "What\'s the price of Bitcoin?"\n'
        '\u2022 "Remind me in 30 min to check logs"\n'
        '\u2022 "Explain this error message"'
    )
    buttons = [
        [("\u26a1 Core", "nav:core"), ("\U0001f5a5\ufe0f Infrastructure", "nav:infra")],
        [("\U0001f4ca Monitoring", "nav:monitor"), ("\U0001f433 Docker", "nav:docker")],
        [("\U0001f5c4\ufe0f Database", "nav:db"), ("\U0001f527 Tools", "nav:tools")],
        [("\U0001f6e0\ufe0f Utilities", "nav:utils")],
    ]
    return {"text": text, "buttons": buttons}


def build_section(section_id: str) -> dict | None:
    """Build a submenu for a given section ID."""
    sections = _get_all_sections()
    return sections.get(section_id)


def _get_all_sections() -> dict[str, dict]:
    """Return all menu sections."""
    return {
        # --- Core ---
        "core": {
            "text": "\u26a1 **Core Commands**\n\nEssential bot commands.",
            "buttons": [
                [("\U0001f4ca Status", "act:status"), ("\U0001f3d3 Ping", "act:ping")],
                [("\u2753 Help", "act:help"), ("\U0001f4c8 Stats", "act:stats")],
                [
                    ("\U0001f504 Reset Chat", "act:reset"),
                    ("\u2139\ufe0f About", "act:about"),
                ],
                [("\u00ab Back", "nav:main")],
            ],
        },
        # --- Infrastructure (parent) ---
        "infra": {
            "text": "\U0001f5a5\ufe0f **Infrastructure**\n\nManage remote hosts, applications, SSH tunnels, and files.",
            "buttons": [
                [
                    ("\U0001f5a5\ufe0f Hosts", "nav:hosts"),
                    ("\U0001f4e6 Applications", "nav:apps"),
                ],
                [
                    ("\U0001f517 Tunnels", "nav:tunnels"),
                    ("\U0001f4c1 Files", "nav:files"),
                ],
                [("\u00ab Back", "nav:main")],
            ],
        },
        # --- Hosts ---
        "hosts": {
            "text": "\U0001f5a5\ufe0f **Hosts**\n\nManage your remote servers.",
            "buttons": [
                [
                    ("\U0001f4cb Show All", "act:hosts"),
                    ("\u2795 Add Host", "act:host_add"),
                ],
                [
                    ("\U0001f517 Use Host", "act:host_use"),
                    ("\U0001f4cc Current", "act:host_current"),
                ],
                [
                    ("\U0001f50d Test SSH", "act:host_test"),
                    ("\u2139\ufe0f Host Info", "act:host_info"),
                ],
                [
                    ("\u270f\ufe0f Edit", "act:host_edit"),
                    ("\U0001f4cb Clone", "act:host_clone"),
                ],
                [
                    ("\U0001f50d Inspect", "act:host_inspect"),
                    ("\U0001f527 Maintain", "act:host_maint"),
                ],
                [
                    ("\U0001f310 Discover", "act:host_discover"),
                    ("\U0001f5d1\ufe0f Remove", "act:host_remove"),
                ],
                [("\u00ab Back", "nav:infra")],
            ],
        },
        # --- Applications ---
        "apps": {
            "text": "\U0001f4e6 **Applications**\n\nManage deployed applications.",
            "buttons": [
                [
                    ("\U0001f4cb Show All", "act:app_list"),
                    ("\u2795 Add App", "act:app_add"),
                ],
                [
                    ("\U0001f517 Use App", "act:app_use"),
                    ("\U0001f4cc Current", "act:app_current"),
                ],
                [
                    ("\u2139\ufe0f App Info", "act:app_info"),
                    ("\u270f\ufe0f Edit", "act:app_edit"),
                ],
                [
                    ("\U0001f4cb Clone", "act:app_clone"),
                    ("\U0001f50d Search", "act:app_search"),
                ],
                [("\U0001f5d1\ufe0f Remove", "act:app_remove")],
                [("\u00ab Back", "nav:infra")],
            ],
        },
        # --- Tunnels ---
        "tunnels": {
            "text": "\U0001f517 **SSH Tunnels**\n\nManage port forwarding and SSH tunnels.",
            "buttons": [
                [
                    ("\u25b6 Start", "act:tunnel_start"),
                    ("\u23f9 Stop", "act:tunnel_stop"),
                ],
                [
                    ("\U0001f504 Restart", "act:tunnel_restart"),
                    ("\U0001f4ca Status", "act:tunnel_status"),
                ],
                [("\U0001f916 Auto", "act:tunnel_auto")],
                [("\u00ab Back", "nav:infra")],
            ],
        },
        # --- Files ---
        "files": {
            "text": "\U0001f4c1 **Remote Files**\n\nUpload, download, and manage remote files.",
            "buttons": [
                [
                    ("\u2b06\ufe0f Upload", "act:file_upload"),
                    ("\u2b07\ufe0f Download", "act:file_download"),
                ],
                [
                    ("\U0001f4cb List", "act:file_list"),
                    ("\U0001f441\ufe0f Show", "act:file_show"),
                ],
                [
                    ("\u270f\ufe0f Edit", "act:file_edit"),
                    ("\U0001f5d1\ufe0f Remove", "act:file_remove"),
                ],
                [("\u00ab Back", "nav:infra")],
            ],
        },
        # --- Monitoring ---
        "monitor": {
            "text": "\U0001f4ca **Monitoring**\n\nCheck system resources and health.",
            "buttons": [
                [
                    ("\U0001f4be Disk", "act:disk"),
                    ("\U0001f9e0 Memory", "act:memory"),
                    ("\u26a1 CPU", "act:cpu"),
                ],
                [("\U0001f4c8 Top", "act:top"), ("\u23f1\ufe0f Uptime", "act:uptime")],
                [
                    ("\U0001f310 Ports", "act:ports"),
                    ("\U0001f50c Netstat", "act:netstat"),
                ],
                [
                    ("\u2699\ufe0f Services", "act:services"),
                    ("\u23f0 Cron", "act:cron"),
                ],
                [
                    ("\U0001f512 SSL Check", "act:ssl"),
                    ("\U0001f4ca Disk Detail", "act:df"),
                ],
                [("\u00ab Back", "nav:main")],
            ],
        },
        # --- Docker ---
        "docker": {
            "text": "\U0001f433 **Docker**\n\nManage containers on remote hosts.",
            "buttons": [
                [
                    ("\U0001f4cb Containers", "act:docker"),
                    ("\U0001f4dd View Logs", "act:docker_logs"),
                ],
                [
                    ("\U0001f4bb Exec", "act:docker_exec"),
                    ("\U0001f4ca Stats", "act:docker_stats"),
                ],
                [
                    ("\u25b6 Start", "act:docker_start"),
                    ("\u23f9 Stop", "act:docker_stop"),
                ],
                [
                    ("\U0001f504 Restart", "act:docker_restart"),
                    ("\U0001f50d Inspect", "act:docker_inspect"),
                ],
                [("\U0001f3bc Compose", "act:docker_compose")],
                [("\u00ab Back", "nav:main")],
            ],
        },
        # --- Database ---
        "db": {
            "text": "\U0001f5c4\ufe0f **Database**\n\nQuery, manage, and backup databases.",
            "buttons": [
                [
                    ("\U0001f4cb List DBs", "act:db"),
                    ("\U0001f4ca Tables", "act:tables"),
                ],
                [
                    ("\U0001f50d DB Info", "act:db_info"),
                    ("\U0001f4bb Run SQL", "act:db_run"),
                ],
                [
                    ("\U0001f4be Dump", "act:db_dump"),
                    ("\U0001f4e5 Restore", "act:db_restore"),
                ],
                [
                    ("\u26a1 Optimize", "act:db_optimize"),
                    ("\U0001f527 Repair", "act:db_repair"),
                ],
                [("\U0001f433 DB Containers", "act:db_containers")],
                [("\u00ab Back", "nav:main")],
            ],
        },
        # --- Tools ---
        "tools": {
            "text": "\U0001f527 **Tools**\n\nBackup, HestiaCP, and remote commands.",
            "buttons": [
                [
                    ("\U0001f517 Tunnels", "act:tunnel"),
                    ("\U0001f4be Backup", "act:backup"),
                ],
                [
                    ("\U0001f3e2 HestiaCP", "act:hestia"),
                    ("\U0001f4bb Run Cmd", "act:run"),
                ],
                [("\u00ab Back", "nav:main")],
            ],
        },
        # --- Utilities ---
        "utils": {
            "text": "\U0001f6e0\ufe0f **Utilities**\n\nAI, crypto, web tools, productivity.",
            "buttons": [
                [
                    ("\U0001f916 AI Status", "act:ai_status"),
                    ("\U0001f3ad AI Persona", "act:ai_persona"),
                ],
                [
                    ("\U0001f4b0 Crypto", "act:crypto"),
                    ("\U0001f324\ufe0f Weather", "act:weather"),
                ],
                [("\U0001f50d WHOIS", "act:whois"), ("\U0001f310 DNS", "act:dns")],
                [("\U0001f512 SSL", "act:ssl"), ("\U0001f4e1 Curl", "act:curl")],
                [("\U0001f550 Time", "act:time"), ("\U0001f30d IP", "act:ip")],
                [("\U0001f5a5\ufe0f Env", "act:env"), ("\U0001f9ee Calc", "act:calc")],
                [
                    ("\u23f0 Remind", "nav:remind"),
                    ("\U0001f4dd Notes", "nav:notes_section"),
                ],
                [
                    ("\U0001f3b2 Fun", "nav:fun"),
                    ("\U0001f510 Encode", "nav:encode_section"),
                ],
                [("\u00ab Back", "nav:main")],
            ],
        },
        # --- Utilities sub-sections ---
        "remind": {
            "text": "\u23f0 **Reminders & Productivity**",
            "buttons": [
                [
                    ("\u23f0 Set Reminder", "act:remind"),
                    ("\U0001f4cb My Reminders", "act:reminders"),
                ],
                [("\u274c Cancel Reminder", "act:cancelreminder")],
                [("\u00ab Back", "nav:utils")],
            ],
        },
        "notes_section": {
            "text": "\U0001f4dd **Notes**",
            "buttons": [
                [
                    ("\U0001f4dd Save Note", "act:note"),
                    ("\U0001f4cb My Notes", "act:notes"),
                ],
                [("\U0001f4ac Random Quote", "act:quote")],
                [("\u00ab Back", "nav:utils")],
            ],
        },
        "fun": {
            "text": "\U0001f3b2 **Fun & Random**",
            "buttons": [
                [
                    ("\U0001fa99 Flip Coin", "act:flip"),
                    ("\U0001f3b2 Roll Dice", "act:roll"),
                ],
                [("\U0001f3af Pick", "act:pick"), ("\U0001f602 Joke", "act:joke")],
                [
                    ("\U0001f464 Profile", "act:profile"),
                    ("\U0001f64f Respect", "act:respect"),
                ],
                [("\u00ab Back", "nav:utils")],
            ],
        },
        "encode_section": {
            "text": "\U0001f510 **Encoding & Hashing**",
            "buttons": [
                [
                    ("\U0001f510 Encode B64", "act:encode"),
                    ("\U0001f513 Decode B64", "act:decode"),
                ],
                [("#\ufe0f\u20e3 Hash", "act:hash")],
                [("\U0001f4b1 Convert $", "act:convert")],
                [("\u00ab Back", "nav:utils")],
            ],
        },
    }


# ---- Action -> CLI command mapping ----
# Maps callback_data (without 'act:' prefix) to command info

ACTION_COMMANDS: dict[str, dict] = {
    # Core
    "status": {"cmd": "/status", "type": "slash"},
    "ping": {"cmd": "/ping", "type": "slash"},
    "help": {"cmd": "/help", "type": "slash"},
    "stats": {"cmd": "/stats", "type": "slash"},
    "reset": {"cmd": "/reset", "type": "slash"},
    "about": {"cmd": "/about", "type": "slash"},
    # Hosts
    "hosts": {"cmd": "host list --plain", "type": "navig"},
    "host_add": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Send me the host details:\n`navig host add <name> <user@ip>`\n\nExample: `navig host add myserver root@1.2.3.4`",
    },
    "host_use": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Which host? Send:\n`/use <hostname>`",
    },
    "host_current": {"cmd": "host show --plain", "type": "navig"},
    "host_test": {"cmd": "host test --plain", "type": "navig"},
    "host_info": {"cmd": "host info --plain", "type": "navig"},
    "host_edit": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Which host to edit? Send:\n`navig host edit <hostname>`",
    },
    "host_clone": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Clone which host? Send:\n`navig host clone <source> <new-name>`",
    },
    "host_inspect": {"cmd": "host inspect --plain", "type": "navig"},
    "host_maint": {"cmd": "host maintenance --plain", "type": "navig"},
    "host_discover": {"cmd": "host discover --plain", "type": "navig"},
    "host_remove": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Which host to remove? Send:\n`navig host remove <hostname>`",
    },
    # Applications
    "app_list": {"cmd": "app list --plain", "type": "navig"},
    "app_add": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Add an app:\n`navig app add <name> --domain <domain>`",
    },
    "app_use": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Which app? Send:\n`navig app use <name>`",
    },
    "app_current": {"cmd": "app show --plain", "type": "navig"},
    "app_info": {"cmd": "app info --plain", "type": "navig"},
    "app_edit": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Which app to edit? Send:\n`navig app edit <name>`",
    },
    "app_clone": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Clone which app? Send:\n`navig app clone <source> <new-name>`",
    },
    "app_search": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Search for apps:\n`navig app search <query>`",
    },
    "app_remove": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Which app to remove? Send:\n`navig app remove <name>`",
    },
    # Tunnels
    "tunnel": {"cmd": "tunnel list --plain", "type": "navig"},
    "tunnel_start": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Start which tunnel? Send:\n`/tunnel start <name>`",
    },
    "tunnel_stop": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Stop which tunnel? Send:\n`/tunnel stop <name>`",
    },
    "tunnel_restart": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Restart which tunnel? Send:\n`/tunnel restart <name>`",
    },
    "tunnel_status": {"cmd": "tunnel status --plain", "type": "navig"},
    "tunnel_auto": {"cmd": "tunnel auto --plain", "type": "navig"},
    # Files
    "file_upload": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Upload a file:\n`navig file upload <local> <remote>`",
    },
    "file_download": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Download a file:\n`navig file download <remote-path>`",
    },
    "file_list": {
        "cmd": None,
        "type": "prompt",
        "prompt": "List remote files:\n`/run ls -la <path>`",
    },
    "file_show": {
        "cmd": None,
        "type": "prompt",
        "prompt": "View a file:\n`navig file show <remote-path>`",
    },
    "file_edit": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Edit a file:\n`navig file edit <remote-path>`",
    },
    "file_remove": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Delete a file:\n`navig file remove <remote-path>`",
    },
    # Monitoring
    "disk": {"cmd": "/disk", "type": "slash"},
    "memory": {"cmd": "/memory", "type": "slash"},
    "cpu": {"cmd": "/cpu", "type": "slash"},
    "top": {"cmd": "/top", "type": "slash"},
    "uptime": {"cmd": "/uptime", "type": "slash"},
    "ports": {"cmd": "/ports", "type": "slash"},
    "netstat": {"cmd": "/netstat", "type": "slash"},
    "services": {"cmd": "/services", "type": "slash"},
    "cron": {"cmd": "/cron", "type": "slash"},
    "ssl": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Check SSL for which domain? Send:\n`/ssl <domain>`",
    },
    "df": {"cmd": "/df", "type": "slash"},
    # Docker
    "docker": {"cmd": "docker ps --plain", "type": "navig"},
    "docker_logs": {
        "cmd": None,
        "type": "prompt",
        "prompt": "View logs for which container? Send:\n`/logs <container> [lines]`",
    },
    "docker_exec": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Execute in container:\n`navig docker exec <container> <command>`",
    },
    "docker_stats": {"cmd": "docker stats --plain", "type": "navig"},
    "docker_start": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Start which container?\n`navig docker start <container>`",
    },
    "docker_stop": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Stop which container?\n`navig docker stop <container>`",
    },
    "docker_restart": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Restart which container? Send:\n`/restart <container>`",
    },
    "docker_inspect": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Inspect which container?\n`navig docker inspect <container>`",
    },
    "docker_compose": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Compose action:\n`navig docker compose up`\n`navig docker compose down`\n`navig docker compose ps`",
    },
    # Database
    "db": {"cmd": "db list --plain", "type": "navig"},
    "tables": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Show tables for which database? Send:\n`/tables <database>`",
    },
    "db_info": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Database info:\n`navig db show <database>`",
    },
    "db_run": {
        "cmd": None,
        "type": "prompt",
        "prompt": 'Run SQL query:\n`navig db run <database> "<query>"`',
    },
    "db_dump": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Dump which database?\n`navig db dump <database>`",
    },
    "db_restore": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Restore a database:\n`navig db restore <database> <file>`",
    },
    "db_optimize": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Optimize which database?\n`navig db optimize <database>`",
    },
    "db_repair": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Repair which database?\n`navig db repair <database>`",
    },
    "db_containers": {"cmd": "db containers --plain", "type": "navig"},
    # Tools
    "backup": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Backup options:\n`/backup` \u2014 list backups\n`/backup create` \u2014 create backup\n`/backup create <target>`",
    },
    "hestia": {
        "cmd": None,
        "type": "prompt",
        "prompt": "HestiaCP:\n`/hestia` \u2014 overview\n`/hestia domains <user>`\n`/hestia web <user>`",
    },
    "run": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Run a remote command:\n`/run <command>`\n\nExample: `/run systemctl status nginx`",
    },
    # Utilities
    "ai_status": {"cmd": "/ai_status", "type": "slash"},
    "ai_persona": {"cmd": "/ai_persona", "type": "slash"},
    "crypto": {"cmd": "/crypto", "type": "slash"},
    "weather": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Weather for where? Send:\n`/weather <city>`",
    },
    "whois": {
        "cmd": None,
        "type": "prompt",
        "prompt": "WHOIS lookup:\n`/whois <domain>`",
    },
    "dns": {"cmd": None, "type": "prompt", "prompt": "DNS lookup:\n`/dns <domain>`"},
    "curl": {"cmd": None, "type": "prompt", "prompt": "HTTP request:\n`/curl <url>`"},
    "time": {"cmd": "/time", "type": "slash"},
    "ip": {"cmd": "/ip", "type": "slash"},
    "env": {"cmd": "/env", "type": "slash"},
    "calc": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Calculate:\n`/calc <expression>`\n\nExample: `/calc 2**10`",
    },
    "remind": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Set a reminder:\n`/remind <time> <message>`\n\nExamples:\n`/remind 30m check backup`\n`/remind 2h review deploy`",
    },
    "reminders": {"cmd": "/reminders", "type": "slash"},
    "cancelreminder": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Cancel which reminder?\n`/cancelreminder <id>`",
    },
    "note": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Save a note:\n`/note <text>`\n\nOr reply to any message with `/note`",
    },
    "notes": {"cmd": "/notes", "type": "slash"},
    "quote": {"cmd": "/quote", "type": "slash"},
    "flip": {"cmd": "/flip", "type": "slash"},
    "roll": {"cmd": "/roll", "type": "slash"},
    "pick": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Pick between options:\n`/pick <option1> <option2> [option3...]`",
    },
    "joke": {"cmd": "/joke", "type": "slash"},
    "profile": {"cmd": "/profile", "type": "slash"},
    "respect": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Reply to someone's message with `/respect` to give respect.",
    },
    "encode": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Base64 encode:\n`/encode <text>`",
    },
    "decode": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Base64 decode:\n`/decode <base64>`",
    },
    "hash": {"cmd": None, "type": "prompt", "prompt": "Hash text:\n`/hash <text>`"},
    "convert": {
        "cmd": None,
        "type": "prompt",
        "prompt": "Convert currency:\n`/convert <amount> <from> <to>`\n\nExample: `/convert 100 USD EUR`",
    },
}


def get_action_info(action_id: str) -> dict | None:
    """Get the action info for a callback action ID."""
    return ACTION_COMMANDS.get(action_id)
