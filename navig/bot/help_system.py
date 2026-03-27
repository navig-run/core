"""
NAVIG Bot Help System - Centralized command documentation.

Provides:
- Command metadata with categories, descriptions, examples
- Interactive help with inline keyboard navigation
- Command search functionality
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CommandInfo:
    """Information about a single command."""

    name: str
    short_desc: str
    description: str
    syntax: str
    examples: List[str]
    category: str
    permissions: str = "Everyone"
    aliases: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)


# Command categories with display info
CATEGORIES = {
    "core": {
        "name": "Core Commands",
        "emoji": "⚡",
        "description": "Essential bot commands for getting started",
    },
    "hosts": {
        "name": "Host Management",
        "emoji": "🖥️",
        "description": "Manage and switch between remote servers",
    },
    "monitoring": {
        "name": "Monitoring",
        "emoji": "📊",
        "description": "Check system resources and health",
    },
    "docker": {
        "name": "Docker",
        "emoji": "🐳",
        "description": "Container management and logs",
    },
    "database": {
        "name": "Database",
        "emoji": "🗃️",
        "description": "Database operations and queries",
    },
    "tools": {
        "name": "Tools",
        "emoji": "🔧",
        "description": "Tunnels, backups, and utilities",
    },
    "utilities": {
        "name": "Utilities",
        "emoji": "🛠️",
        "description": "Helpful utilities and info commands",
    },
}


def get_all_commands() -> Dict[str, CommandInfo]:
    """Get all available commands with their documentation."""
    return {
        # Core Commands
        "start": CommandInfo(
            name="/start",
            short_desc="Initialize the bot",
            description="Start your conversation with NAVIG. Get a welcome message and overview of capabilities.",
            syntax="/start",
            examples=["/start"],
            category="core",
        ),
        "help": CommandInfo(
            name="/help",
            short_desc="Interactive help system",
            description="Browse all available commands organized by category with detailed examples.",
            syntax="/help [category]",
            examples=["/help", "/help docker", "/help database"],
            category="core",
        ),
        "ping": CommandInfo(
            name="/ping",
            short_desc="Check bot status",
            description="Quick health check showing bot latency, active host, and system status.",
            syntax="/ping",
            examples=["/ping"],
            category="core",
        ),
        "status": CommandInfo(
            name="/status",
            short_desc="Detailed bot status",
            description="Show AI model, loaded skills, typing settings, and conversation history stats.",
            syntax="/status",
            examples=["/status"],
            category="core",
        ),
        "reset": CommandInfo(
            name="/reset",
            short_desc="Clear conversation history",
            description="Reset your conversation memory. Useful if the AI context becomes confused.",
            syntax="/reset",
            examples=["/reset"],
            category="core",
        ),
        "stats": CommandInfo(
            name="/stats",
            short_desc="Usage statistics",
            description="View command usage statistics, error counts, and performance metrics.",
            syntax="/stats",
            examples=["/stats"],
            category="core",
        ),
        # Host Management
        "hosts": CommandInfo(
            name="/hosts",
            short_desc="List configured hosts",
            description="Show all configured remote servers. Displays host names that can be used with /use.",
            syntax="/hosts",
            examples=["/hosts"],
            category="hosts",
            related=["use"],
        ),
        "use": CommandInfo(
            name="/use",
            short_desc="Switch active host",
            description="Change the currently active server. All subsequent commands will run on this host.",
            syntax="/use <hostname>",
            examples=["/use production", "/use staging", "/use dev-server"],
            category="hosts",
            related=["hosts"],
        ),
        # Monitoring
        "disk": CommandInfo(
            name="/disk",
            short_desc="Check disk space",
            description="Show disk usage on the current host using df -h.",
            syntax="/disk",
            examples=["/disk"],
            category="monitoring",
            related=["memory", "cpu"],
        ),
        "memory": CommandInfo(
            name="/memory",
            short_desc="Check memory usage",
            description="Show memory usage on the current host using free -h.",
            syntax="/memory",
            examples=["/memory"],
            category="monitoring",
            related=["disk", "cpu"],
        ),
        "cpu": CommandInfo(
            name="/cpu",
            short_desc="Check CPU load",
            description="Show CPU load average and uptime on the current host.",
            syntax="/cpu",
            examples=["/cpu"],
            category="monitoring",
            related=["disk", "memory"],
        ),
        # Docker
        "docker": CommandInfo(
            name="/docker",
            short_desc="List containers",
            description="Show all Docker containers on the current host with their status.",
            syntax="/docker",
            examples=["/docker"],
            category="docker",
            related=["logs", "restart"],
        ),
        "logs": CommandInfo(
            name="/logs",
            short_desc="View container logs",
            description="Fetch recent logs from a Docker container. Default 50 lines, max 200.",
            syntax="/logs <container> [lines]",
            examples=["/logs nginx", "/logs postgres 100", "/logs app-web 50"],
            category="docker",
            related=["docker", "restart"],
        ),
        "restart": CommandInfo(
            name="/restart",
            short_desc="Restart container",
            description="Restart a Docker container. Requires confirmation before executing.",
            syntax="/restart <container>",
            examples=["/restart nginx", "/restart postgres"],
            category="docker",
            related=["docker", "logs"],
        ),
        # Database
        "db": CommandInfo(
            name="/db",
            short_desc="Database operations",
            description="List databases or show tables. For queries, use natural language.",
            syntax="/db [tables <database>]",
            examples=["/db", "/db tables wordpress", "/db tables myapp"],
            category="database",
            related=["tables"],
        ),
        "tables": CommandInfo(
            name="/tables",
            short_desc="List database tables",
            description="Shortcut to list tables in a specific database.",
            syntax="/tables <database>",
            examples=["/tables wordpress", "/tables production_db"],
            category="database",
            related=["db"],
        ),
        # Tools
        "tunnel": CommandInfo(
            name="/tunnel",
            short_desc="Manage SSH tunnels",
            description="View, start, or stop SSH tunnels for secure database access.",
            syntax="/tunnel [start|stop <name>]",
            examples=["/tunnel", "/tunnel start db-prod", "/tunnel stop mysql-tunnel"],
            category="tools",
        ),
        "backup": CommandInfo(
            name="/backup",
            short_desc="Manage backups",
            description="List recent backups or create a new backup. Creation requires confirmation.",
            syntax="/backup [create [target]]",
            examples=["/backup", "/backup create", "/backup create database"],
            category="tools",
        ),
        "hestia": CommandInfo(
            name="/hestia",
            short_desc="HestiaCP management",
            description="Manage HestiaCP panel - list users, domains, and web configurations.",
            syntax="/hestia [domains|web] [user]",
            examples=["/hestia", "/hestia domains admin", "/hestia web admin"],
            category="tools",
        ),
        "run": CommandInfo(
            name="/run",
            short_desc="Execute remote command",
            description="Run a shell command on the current host. Dangerous commands are blocked.",
            syntax="/run <command>",
            examples=[
                "/run ls -la",
                "/run cat /etc/nginx/nginx.conf",
                "/run systemctl status nginx",
            ],
            category="tools",
        ),
        # Utilities & Tools
        "about": CommandInfo(
            name="/about",
            short_desc="Bot information",
            description="Learn about NAVIG - your personal Kraken from the SCHEMA community.",
            syntax="/about",
            examples=["/about"],
            category="utilities",
        ),
        "whois": CommandInfo(
            name="/whois",
            short_desc="Domain WHOIS lookup",
            description="Look up domain registration information using WHOIS.",
            syntax="/whois <domain>",
            examples=["/whois example.com", "/whois google.com"],
            category="utilities",
        ),
        "time": CommandInfo(
            name="/time",
            short_desc="Timezone utility",
            description="Show current time in different timezones. Useful for coordinating across server locations.",
            syntax="/time [timezone]",
            examples=["/time", "/time utc", "/time pst", "/time jst"],
            category="utilities",
        ),
        "ip": CommandInfo(
            name="/ip",
            short_desc="Server IP addresses",
            description="Show both internal and external IP addresses of the server.",
            syntax="/ip",
            examples=["/ip"],
            category="utilities",
            related=["env"],
        ),
        "env": CommandInfo(
            name="/env",
            short_desc="Server environment info",
            description="Show server OS, kernel version, hostname, and system details.",
            syntax="/env",
            examples=["/env"],
            category="utilities",
            related=["ip"],
        ),
        "df": CommandInfo(
            name="/df",
            short_desc="Detailed disk usage",
            description="Show detailed disk usage with filesystem types (excludes tmpfs).",
            syntax="/df",
            examples=["/df"],
            category="monitoring",
            related=["disk", "memory"],
        ),
        "top": CommandInfo(
            name="/top",
            short_desc="Top processes",
            description="Show top 10 processes sorted by CPU usage.",
            syntax="/top",
            examples=["/top"],
            category="monitoring",
            related=["cpu", "memory"],
        ),
        "netstat": CommandInfo(
            name="/netstat",
            short_desc="Network connections",
            description="Show active network connections (ESTABLISHED and LISTEN).",
            syntax="/netstat",
            examples=["/netstat"],
            category="monitoring",
            related=["ports"],
        ),
        "cron": CommandInfo(
            name="/cron",
            short_desc="List cron jobs",
            description="Show scheduled cron jobs for the current user.",
            syntax="/cron",
            examples=["/cron"],
            category="monitoring",
        ),
        "ssl": CommandInfo(
            name="/ssl",
            short_desc="Check SSL certificate",
            description="Check SSL certificate expiry date and issuer for a domain.",
            syntax="/ssl <domain>",
            examples=["/ssl example.com", "/ssl yourdomain.dev"],
            category="monitoring",
        ),
        "uptime": CommandInfo(
            name="/uptime",
            short_desc="Server uptime",
            description="Show how long the server has been running since last reboot.",
            syntax="/uptime",
            examples=["/uptime"],
            category="monitoring",
            related=["disk", "memory", "cpu"],
        ),
        "services": CommandInfo(
            name="/services",
            short_desc="List running services",
            description="Show systemd services currently running on the server.",
            syntax="/services",
            examples=["/services"],
            category="monitoring",
        ),
        "ports": CommandInfo(
            name="/ports",
            short_desc="Show open ports",
            description="List TCP ports currently listening on the server.",
            syntax="/ports",
            examples=["/ports"],
            category="monitoring",
        ),
        "pick": CommandInfo(
            name="/pick",
            short_desc="Random choice",
            description="Let the Kraken make a random choice between options.",
            syntax="/pick <option1> <option2> [option3...]",
            examples=[
                "/pick yes no",
                "/pick deploy wait rollback",
                "/pick staging production",
            ],
            category="utilities",
        ),
        "note": CommandInfo(
            name="/note",
            short_desc="Save a note",
            description="Save important information. Reply to a message with /note to save it.",
            syntax="/note <text>",
            examples=["/note Server restart at 3am", "/note Migration completed"],
            category="utilities",
            related=["notes"],
        ),
        "notes": CommandInfo(
            name="/notes",
            short_desc="List saved notes",
            description="View your saved notes, most recent first.",
            syntax="/notes",
            examples=["/notes"],
            category="utilities",
            related=["note"],
        ),
        "remind": CommandInfo(
            name="/remind",
            short_desc="Set a reminder",
            description="Create a reminder that will be sent at the specified time.",
            syntax="/remind <time> <message>",
            examples=[
                "/remind 30m check backup status",
                "/remind 2h review deployment",
                "/remind 1d renew SSL certificate",
            ],
            category="utilities",
            related=["reminders"],
        ),
        "reminders": CommandInfo(
            name="/reminders",
            short_desc="List your reminders",
            description="Show all active reminders with their scheduled times.",
            syntax="/reminders",
            examples=["/reminders"],
            category="utilities",
            related=["remind"],
        ),
        # AI Status
        "ai_persona": CommandInfo(
            name="/ai_persona",
            short_desc="View/change AI persona",
            description="View available personas or switch AI personality style.",
            syntax="/ai_persona [name]",
            examples=["/ai_persona", "/ai_persona devops", "/ai_persona kraken"],
            category="utilities",
            related=["ai_status"],
        ),
        "ai_status": CommandInfo(
            name="/ai_status",
            short_desc="AI mode status",
            description="Check AI status - always active with Kraken persona.",
            syntax="/ai_status",
            examples=["/ai_status"],
            category="utilities",
            related=["ai_persona"],
        ),
        # Extra Utilities
        "crypto": CommandInfo(
            name="/crypto",
            short_desc="Cryptocurrency price",
            description="Get current price and 24h change for a cryptocurrency.",
            syntax="/crypto [symbol]",
            examples=["/crypto", "/crypto BTC", "/crypto ETH", "/crypto SOL"],
            category="utilities",
        ),
        "weather": CommandInfo(
            name="/weather",
            short_desc="Weather information",
            description="Get current weather for a location.",
            syntax="/weather [location]",
            examples=["/weather", "/weather London", '/weather "New York"'],
            category="utilities",
        ),
        "calc": CommandInfo(
            name="/calc",
            short_desc="Calculator",
            description="Evaluate a mathematical expression.",
            syntax="/calc <expression>",
            examples=["/calc 2 + 2", "/calc 100 * 1.5", "/calc 2 ** 10"],
            category="utilities",
        ),
        "hash": CommandInfo(
            name="/hash",
            short_desc="Hash text",
            description="Generate MD5, SHA1, and SHA256 hashes of text.",
            syntax="/hash <text>",
            examples=["/hash password123", '/hash "my secret"'],
            category="utilities",
        ),
        "dns": CommandInfo(
            name="/dns",
            short_desc="DNS lookup",
            description="Look up DNS records (A, MX, NS) for a domain.",
            syntax="/dns <domain>",
            examples=["/dns google.com", "/dns example.com"],
            category="utilities",
            related=["whois", "ssl"],
        ),
        "encode": CommandInfo(
            name="/encode",
            short_desc="Base64 encode",
            description="Encode text to Base64.",
            syntax="/encode <text>",
            examples=["/encode Hello World", '/encode "secret message"'],
            category="utilities",
            related=["decode"],
        ),
        "decode": CommandInfo(
            name="/decode",
            short_desc="Base64 decode",
            description="Decode Base64 to text.",
            syntax="/decode <base64>",
            examples=["/decode SGVsbG8gV29ybGQ="],
            category="utilities",
            related=["encode"],
        ),
        "curl": CommandInfo(
            name="/curl",
            short_desc="HTTP request",
            description="Make a simple HTTP GET request to a URL.",
            syntax="/curl <url>",
            examples=["/curl https://httpbin.org/ip", "/curl api.example.com/health"],
            category="utilities",
        ),
        "convert": CommandInfo(
            name="/convert",
            short_desc="Currency conversion",
            description='Convert between currencies. Also works in natural language: "100 USD to EUR".',
            syntax="/convert <amount> <from> <to>",
            examples=["/convert 100 USD EUR", "/convert 50 EUR GBP", "100 USD to EUR"],
            category="utilities",
        ),
        "respect": CommandInfo(
            name="/respect",
            short_desc="Give respect",
            description="Reply to a message with /respect to give respect points.",
            syntax="/respect (reply)",
            examples=["/respect"],
            category="utilities",
        ),
        "profile": CommandInfo(
            name="/profile",
            short_desc="User profile",
            description="View user profile and stats. Reply to see someone else's profile.",
            syntax="/profile",
            examples=["/profile"],
            category="utilities",
        ),
        "quote": CommandInfo(
            name="/quote",
            short_desc="Random quote",
            description="Get a random saved quote/note.",
            syntax="/quote",
            examples=["/quote"],
            category="utilities",
            related=["note", "notes"],
        ),
        "uid": CommandInfo(
            name="/uid",
            short_desc="Get user ID",
            description="Get Telegram user ID and chat ID. Useful for authorization.",
            syntax="/uid",
            examples=["/uid"],
            category="utilities",
        ),
        "joke": CommandInfo(
            name="/joke",
            short_desc="Programming joke",
            description="Get a random programming joke.",
            syntax="/joke",
            examples=["/joke"],
            category="utilities",
        ),
        "flip": CommandInfo(
            name="/flip",
            short_desc="Flip a coin",
            description='Flip a coin. Also works: "flip a coin" or "heads or tails".',
            syntax="/flip",
            examples=["/flip", "flip a coin"],
            category="utilities",
        ),
        "roll": CommandInfo(
            name="/roll",
            short_desc="Roll dice",
            description='Roll a dice (default d6). Also works: "roll d20".',
            syntax="/roll [sides]",
            examples=["/roll", "/roll 20", "/roll d20", "roll a dice"],
            category="utilities",
        ),
        "crypto_list": CommandInfo(
            name="/crypto_list",
            short_desc="List cryptocurrencies",
            description="Show all supported cryptocurrencies for price lookup.",
            syntax="/crypto_list",
            examples=["/crypto_list"],
            category="utilities",
            related=["crypto"],
        ),
        "explain": CommandInfo(
            name="/explain",
            short_desc="AI explanation",
            description='Ask the Kraken to explain anything. Also: "explain X" or "what is X".',
            syntax="/explain <question>",
            examples=[
                "/explain Docker",
                "/explain how does SSH work",
                "what is kubernetes",
            ],
            category="utilities",
            aliases=["ask"],
        ),
        "imagegen": CommandInfo(
            name="/imagegen",
            short_desc="AI image generation",
            description="Generate an AI image from text description (requires API setup).",
            syntax="/imagegen <description>",
            examples=[
                "/imagegen a kraken managing servers",
                "/imagegen cyberpunk data center",
            ],
            category="utilities",
        ),
        "music": CommandInfo(
            name="/music",
            short_desc="Music link conversion",
            description="Convert music links between platforms. Also auto-detects Spotify/YouTube Music URLs.",
            syntax="/music <URL>",
            examples=["/music https://open.spotify.com/track/..."],
            category="utilities",
        ),
        "video": CommandInfo(
            name="/video",
            short_desc="Video download info",
            description="Get download info for social media videos. Auto-detects YouTube, TikTok, Instagram, Twitter URLs.",
            syntax="/video <URL>",
            examples=[
                "/video https://youtube.com/watch?v=...",
                "/download https://tiktok.com/...",
            ],
            category="utilities",
            aliases=["download"],
        ),
        "cancelreminder": CommandInfo(
            name="/cancelreminder",
            short_desc="Cancel a reminder",
            description="Cancel a pending reminder by its ID.",
            syntax="/cancelreminder <id>",
            examples=["/cancelreminder 5"],
            category="utilities",
            related=["remind", "reminders"],
        ),
    }


def get_commands_by_category() -> Dict[str, Dict[str, Any]]:
    """
    Get commands grouped by category.

    Returns:
        Dict with category ID as key, containing 'info' and 'commands' dicts
    """
    all_commands = get_all_commands()
    grouped = {}

    for cat_id, cat_info in CATEGORIES.items():
        grouped[cat_id] = {
            "info": cat_info,
            "commands": {},
        }

    for cmd_id, cmd in all_commands.items():
        category = cmd.category
        if category in grouped:
            grouped[category]["commands"][cmd_id] = cmd

    # Remove empty categories
    grouped = {k: v for k, v in grouped.items() if v["commands"]}

    return grouped


def get_command(command_id: str) -> Optional[CommandInfo]:
    """Get a single command's documentation."""
    commands = get_all_commands()
    return commands.get(command_id)


def search_commands(query: str) -> List[CommandInfo]:
    """Search commands by text in name, description, or examples."""
    query_lower = query.lower()
    results = []

    for cmd in get_all_commands().values():
        if (
            query_lower in cmd.name.lower()
            or query_lower in cmd.short_desc.lower()
            or query_lower in cmd.description.lower()
            or any(query_lower in ex.lower() for ex in cmd.examples)
        ):
            results.append(cmd)

    return results


def format_command_help(cmd: CommandInfo, detailed: bool = False) -> str:
    """Format a command's help text."""
    text = f"**{cmd.name}** - {cmd.short_desc}\n\n"

    if detailed:
        text += f"{cmd.description}\n\n"
        text += f"**Syntax:** `{cmd.syntax}`\n\n"

        if cmd.examples:
            text += "**Examples:**\n"
            for ex in cmd.examples:
                text += f"  `{ex}`\n"

        if cmd.related:
            text += f"\n**Related:** {', '.join(f'/{r}' for r in cmd.related)}"

    return text


def format_category_help(category_id: str) -> str:
    """Format help text for a category."""
    grouped = get_commands_by_category()

    if category_id not in grouped:
        return f"Unknown category: {category_id}"

    cat = grouped[category_id]
    info = cat["info"]

    text = f"{info['emoji']} **{info['name']}**\n\n"
    text += f"{info['description']}\n\n"
    text += "**Commands:**\n\n"

    for cmd_id, cmd in cat["commands"].items():
        text += f"• `{cmd.name}` - {cmd.short_desc}\n"

    return text


def format_main_help() -> str:
    """Format the main help menu text."""
    text = "**NAVIG Bot** 🦑\n\n"
    text += "I'm not just for servers—I'm your personal assistant!\n\n"
    text += "**📚 Command Categories:**\n"

    grouped = get_commands_by_category()
    for cat_id, cat in grouped.items():
        info = cat["info"]
        cmd_count = len(cat["commands"])
        text += f"{info['emoji']} **{info['name']}** ({cmd_count} commands)\n"

    text += "\n**💬 Or just ask naturally:**\n"
    text += '• "Search the web for Docker tips"\n'
    text += '• "What\'s the price of Bitcoin?"\n'
    text += '• "Remind me in 30 min to check logs"\n'
    text += '• "Explain this error message"\n'

    return text
