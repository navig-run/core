"""
NAVIG Bot Command Tools Registry

Defines all bot commands as function schemas for AI function calling.
Used by the Intent Parser for natural language → command mapping.

Each command is defined with:
- name: The command function identifier
- description: What the command does (used by AI to understand intent)
- parameters: JSON schema for command arguments
"""

from collections.abc import Callable
from typing import Any

# ============================================================================
# COMMAND FUNCTION SCHEMAS
# These are used by AI function calling to understand user intent
# ============================================================================

COMMAND_TOOLS: list[dict[str, Any]] = [
    # -------------- Core Commands --------------
    {
        "type": "function",
        "function": {
            "name": "start",
            "description": "Start the bot and show welcome message with overview of capabilities",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "help",
            "description": "Show help for bot commands, optionally filtered by category or specific command",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Category name (core, hosts, monitoring, docker, database, tools, utilities) or specific command name",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "status",
            "description": "Show detailed bot status including AI model, loaded skills, and conversation stats",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ping",
            "description": "Quick health check showing bot latency and active host",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stats",
            "description": "View command usage statistics, error counts, and performance metrics",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reset",
            "description": "Clear conversation history and reset AI context",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "about",
            "description": "Show information about NAVIG bot and the SCHEMA community",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # -------------- Host Management --------------
    {
        "type": "function",
        "function": {
            "name": "hosts",
            "description": "List all configured remote servers and hosts",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "use_host",
            "description": "Switch to a different server/host by name or alias. All subsequent commands will run on this host.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host_name": {
                        "type": "string",
                        "description": "The server name, alias, IP address, or partial name to switch to",
                    }
                },
                "required": ["host_name"],
            },
        },
    },
    # -------------- System Monitoring --------------
    {
        "type": "function",
        "function": {
            "name": "disk",
            "description": "Check disk space usage on the current server",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory",
            "description": "Check memory (RAM) usage on the current server",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cpu",
            "description": "Check CPU load average and uptime on the current server",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "df",
            "description": "Show detailed disk usage with filesystem types",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top",
            "description": "Show top processes sorted by CPU usage",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "uptime",
            "description": "Show how long the server has been running since last reboot",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ip",
            "description": "Show server internal and external IP addresses",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "env",
            "description": "Show server environment info: OS, kernel version, hostname",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "netstat",
            "description": "Show active network connections (ESTABLISHED and LISTEN)",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ports",
            "description": "List TCP ports currently listening on the server",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "services",
            "description": "List systemd services currently running on the server",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cron",
            "description": "List scheduled cron jobs for the current user",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssl",
            "description": "Check SSL certificate expiry date and issuer for a domain",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Domain name to check SSL certificate for",
                    }
                },
                "required": ["domain"],
            },
        },
    },
    # -------------- Docker --------------
    {
        "type": "function",
        "function": {
            "name": "docker_ps",
            "description": "List all Docker containers on the current server with their status",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_logs",
            "description": "View recent logs from a Docker container",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": "Container name or ID to get logs from",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Number of log lines to fetch (default 50, max 200)",
                    },
                },
                "required": ["container"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_restart",
            "description": "Restart a Docker container (requires confirmation)",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": "Container name or ID to restart",
                    }
                },
                "required": ["container"],
            },
        },
    },
    # -------------- Database --------------
    {
        "type": "function",
        "function": {
            "name": "db_list",
            "description": "List all databases on the current server",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "db_tables",
            "description": "List tables in a specific database",
            "parameters": {
                "type": "object",
                "properties": {
                    "database": {
                        "type": "string",
                        "description": "Database name to list tables from",
                    }
                },
                "required": ["database"],
            },
        },
    },
    # -------------- Tools --------------
    {
        "type": "function",
        "function": {
            "name": "tunnel",
            "description": "View, start, or stop SSH tunnels",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "start", "stop"],
                        "description": "Action to perform: list tunnels, start a tunnel, or stop a tunnel",
                    },
                    "tunnel_name": {
                        "type": "string",
                        "description": "Name of the tunnel (required for start/stop)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backup",
            "description": "List recent backups or create a new backup",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create"],
                        "description": "Action: list backups or create a new backup",
                    },
                    "target": {
                        "type": "string",
                        "description": "What to backup (optional, for create action)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hestia",
            "description": "Manage HestiaCP panel - list users, domains, and web configurations",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource": {
                        "type": "string",
                        "enum": ["users", "domains", "web"],
                        "description": "Resource type to manage",
                    },
                    "user": {
                        "type": "string",
                        "description": "HestiaCP username (optional)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command on the current remote server",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute (dangerous commands blocked)",
                    }
                },
                "required": ["command"],
            },
        },
    },
    # -------------- Utilities --------------
    {
        "type": "function",
        "function": {
            "name": "whois",
            "description": "Look up domain registration information using WHOIS",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Domain name to look up",
                    }
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "time",
            "description": "Show current time in different timezones",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Timezone name like UTC, PST, EST, JST (optional)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pick",
            "description": "Make a random choice between multiple options",
            "parameters": {
                "type": "object",
                "properties": {
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of options to choose from",
                    }
                },
                "required": ["options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flip",
            "description": "Flip a coin - heads or tails",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "roll",
            "description": "Roll a dice",
            "parameters": {
                "type": "object",
                "properties": {
                    "sides": {
                        "type": "integer",
                        "description": "Number of sides on the dice (default 6)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "note",
            "description": "Save a note for later reference",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Note text to save"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notes",
            "description": "List all saved notes",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remind",
            "description": "Set a reminder for a specific time in the future",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "What to remind about",
                    },
                    "duration": {
                        "type": "integer",
                        "description": "Time amount (e.g., 5)",
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["minutes", "hours", "days", "weeks"],
                        "description": "Time unit",
                    },
                },
                "required": ["message", "duration", "unit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reminders",
            "description": "List all active reminders",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancelreminder",
            "description": "Cancel a reminder by its ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "integer",
                        "description": "ID of the reminder to cancel",
                    }
                },
                "required": ["reminder_id"],
            },
        },
    },
    # -------------- Crypto & Finance --------------
    {
        "type": "function",
        "function": {
            "name": "crypto",
            "description": "Get current price and 24h change for a cryptocurrency",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Cryptocurrency symbol like BTC, ETH, SOL, XRP, DOGE",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crypto_list",
            "description": "List supported cryptocurrencies",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert",
            "description": "Convert currency from one to another",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Amount to convert"},
                    "from_currency": {
                        "type": "string",
                        "description": "Source currency code (e.g., USD, EUR, GBP)",
                    },
                    "to_currency": {
                        "type": "string",
                        "description": "Target currency code",
                    },
                },
                "required": ["amount", "from_currency", "to_currency"],
            },
        },
    },
    # -------------- Weather --------------
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": "Get current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name or location",
                    }
                },
                "required": [],
            },
        },
    },
    # -------------- Developer Tools --------------
    {
        "type": "function",
        "function": {
            "name": "calc",
            "description": "Evaluate a mathematical expression",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Mathematical expression to evaluate",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hash",
            "description": "Hash text using various algorithms (MD5, SHA1, SHA256)",
            "parameters": {
                "type": "object",
                "properties": {
                    "algorithm": {
                        "type": "string",
                        "enum": ["md5", "sha1", "sha256"],
                        "description": "Hash algorithm to use",
                    },
                    "text": {"type": "string", "description": "Text to hash"},
                },
                "required": ["algorithm", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dns",
            "description": "Perform DNS lookup for a domain",
            "parameters": {
                "type": "object",
                "properties": {"domain": {"type": "string", "description": "Domain to look up"}},
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "encode",
            "description": "Base64 encode text",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Text to encode"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "decode",
            "description": "Base64 decode text",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Base64 text to decode"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "curl",
            "description": "Make an HTTP request to a URL",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to request"}},
                "required": ["url"],
            },
        },
    },
    # -------------- Social & Fun --------------
    {
        "type": "function",
        "function": {
            "name": "profile",
            "description": "Show user profile information",
            "parameters": {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": "Username to look up (optional, defaults to self)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quote",
            "description": "Get an inspirational or random quote",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "joke",
            "description": "Tell a random joke",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "uid",
            "description": "Get user's Telegram ID",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "respect",
            "description": "Give respect or pay respects",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Who or what to pay respects to (optional)",
                    }
                },
                "required": [],
            },
        },
    },
    # -------------- Media --------------
    {
        "type": "function",
        "function": {
            "name": "music",
            "description": "Convert music link to other platforms (Spotify, YouTube Music, Apple Music)",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Music URL from Spotify, YouTube Music, Apple Music, etc.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "video",
            "description": "Get video download information for social media videos",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Video URL from YouTube, TikTok, Instagram, Twitter, etc.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "imagegen",
            "description": "Generate an image using AI (requires API setup)",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Description of the image to generate",
                    }
                },
                "required": ["prompt"],
            },
        },
    },
    # -------------- AI Commands --------------
    {
        "type": "function",
        "function": {
            "name": "explain",
            "description": "Get an AI explanation for a topic, concept, or question",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Topic, concept, or question to explain",
                    }
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ai_persona",
            "description": "View available AI personas or switch personality style",
            "parameters": {
                "type": "object",
                "properties": {
                    "persona": {
                        "type": "string",
                        "description": "Persona name to switch to (optional)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ai_status",
            "description": "Check AI status and current persona",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ============================================================================
# COMMAND HANDLER MAPPING
# Maps AI function calls to actual slash commands
# ============================================================================


def _build_command_string(cmd: str, args: dict[str, Any]) -> str:
    """Build a command string from function name and arguments."""
    return cmd


COMMAND_HANDLER_MAP: dict[str, str | Callable[[dict[str, Any]], str]] = {
    # Core
    "start": "/start",
    "help": lambda args: f"/help {args.get('topic', '')}".strip(),
    "status": "/status",
    "ping": "/ping",
    "stats": "/stats",
    "reset": "/reset",
    "about": "/about",
    # Host Management
    "hosts": "/hosts",
    "use_host": lambda args: f"/use {args.get('host_name', '')}",
    # System Monitoring
    "disk": "/disk",
    "memory": "/memory",
    "cpu": "/cpu",
    "df": "/df",
    "top": "/top",
    "uptime": "/uptime",
    "ip": "/ip",
    "env": "/env",
    "netstat": "/netstat",
    "ports": "/ports",
    "services": "/services",
    "cron": "/cron",
    "ssl": lambda args: f"/ssl {args.get('domain', '')}",
    # Docker
    "docker_ps": "/docker",
    "docker_logs": lambda args: (
        f"/logs {args.get('container', '')} {args.get('lines', '')}".strip()
    ),
    "docker_restart": lambda args: f"/restart {args.get('container', '')}",
    # Database
    "db_list": "/db",
    "db_tables": lambda args: f"/tables {args.get('database', '')}",
    # Tools
    "tunnel": lambda args: _build_tunnel_cmd(args),
    "backup": lambda args: _build_backup_cmd(args),
    "hestia": lambda args: _build_hestia_cmd(args),
    "run_command": lambda args: f"/run {args.get('command', '')}",
    # Utilities
    "whois": lambda args: f"/whois {args.get('domain', '')}",
    "time": lambda args: f"/time {args.get('timezone', '')}".strip(),
    "pick": lambda args: f"/pick {' '.join(args.get('options', []))}",
    "flip": "/flip",
    "roll": lambda args: f"/roll {args.get('sides', '')}".strip() if args.get("sides") else "/roll",
    "note": lambda args: f"/note {args.get('text', '')}",
    "notes": "/notes",
    "remind": lambda args: _build_remind_cmd(args),
    "reminders": "/reminders",
    "cancelreminder": lambda args: f"/cancelreminder {args.get('reminder_id', '')}",
    # Crypto & Finance
    "crypto": lambda args: f"/crypto {args.get('symbol', '')}".strip(),
    "crypto_list": "/crypto_list",
    "convert": lambda args: (
        f"/convert {args.get('amount', '')} {args.get('from_currency', '')} {args.get('to_currency', '')}"
    ),
    # Weather
    "weather": lambda args: f"/weather {args.get('location', '')}".strip(),
    # Developer Tools
    "calc": lambda args: f"/calc {args.get('expression', '')}",
    "hash": lambda args: f"/hash {args.get('algorithm', 'sha256')} {args.get('text', '')}",
    "dns": lambda args: f"/dns {args.get('domain', '')}",
    "encode": lambda args: f"/encode {args.get('text', '')}",
    "decode": lambda args: f"/decode {args.get('text', '')}",
    "curl": lambda args: f"/curl {args.get('url', '')}",
    # Social & Fun
    "profile": lambda args: f"/profile {args.get('user', '')}".strip(),
    "quote": "/quote",
    "joke": "/joke",
    "uid": "/uid",
    "respect": lambda args: f"/respect {args.get('target', '')}".strip(),
    # Media
    "music": lambda args: f"/music {args.get('url', '')}",
    "video": lambda args: f"/video {args.get('url', '')}",
    "imagegen": lambda args: f"/imagegen {args.get('prompt', '')}",
    # AI Commands
    "explain": lambda args: f"/explain {args.get('question', '')}",
    "ai_persona": lambda args: f"/ai_persona {args.get('persona', '')}".strip(),
    "ai_status": "/ai_status",
}


def _build_tunnel_cmd(args: dict[str, Any]) -> str:
    """Build tunnel command string."""
    action = args.get("action", "list")
    name = args.get("tunnel_name", "")
    if action == "list" or not action:
        return "/tunnel"
    return f"/tunnel {action} {name}".strip()


def _build_backup_cmd(args: dict[str, Any]) -> str:
    """Build backup command string."""
    action = args.get("action", "list")
    target = args.get("target", "")
    if action == "list" or not action:
        return "/backup"
    return f"/backup {action} {target}".strip()


def _build_hestia_cmd(args: dict[str, Any]) -> str:
    """Build hestia command string."""
    resource = args.get("resource", "")
    user = args.get("user", "")
    if not resource:
        return "/hestia"
    return f"/hestia {resource} {user}".strip()


def _build_remind_cmd(args: dict[str, Any]) -> str:
    """Build remind command string."""
    message = args.get("message", "")
    duration = args.get("duration", 30)
    unit = args.get("unit", "minutes")

    # Convert unit to short form
    unit_map = {"minutes": "m", "hours": "h", "days": "d", "weeks": "w"}
    short_unit = unit_map.get(unit, "m")

    return f"/remind {duration}{short_unit} {message}"


def get_command_string(function_name: str, args: dict[str, Any]) -> str | None:
    """
    Convert a function call to a command string.

    Args:
        function_name: Name of the function from AI response
        args: Arguments dictionary

    Returns:
        Command string like "/docker" or "/use production"
    """
    handler = COMMAND_HANDLER_MAP.get(function_name)
    if handler is None:
        return None

    if callable(handler):
        return handler(args)
    return handler


def get_tool_by_name(name: str) -> dict[str, Any] | None:
    """Get a tool definition by its function name."""
    for tool in COMMAND_TOOLS:
        if tool.get("function", {}).get("name") == name:
            return tool
    return None


def get_all_tool_names() -> list[str]:
    """Get list of all available tool/function names."""
    return [tool["function"]["name"] for tool in COMMAND_TOOLS]


# ============================================================================
# INTENT KEYWORDS
# Keywords that help identify user intent for pattern-based matching
# ============================================================================

INTENT_KEYWORDS: dict[str, list[str]] = {
    # Monitoring
    "disk": ["disk", "space", "storage", "filesystem", "drive", "hdd", "ssd"],
    "memory": ["memory", "ram", "mem", "free memory", "available memory"],
    "cpu": ["cpu", "processor", "load", "load average"],
    "uptime": ["uptime", "running", "since", "last boot", "reboot"],
    # Docker
    "docker_ps": [
        "docker",
        "containers",
        "running containers",
        "docker ps",
        "container list",
    ],
    "docker_logs": ["logs", "container logs", "docker logs"],
    "docker_restart": ["restart container", "restart docker", "reboot container"],
    # Host
    "hosts": ["hosts", "servers", "machines", "list servers", "show servers"],
    "use_host": ["switch", "use", "connect to", "change server", "select server"],
    # Database
    "db_list": ["databases", "list databases", "show databases", "dbs"],
    "db_tables": ["tables", "database tables", "show tables"],
    # Utilities
    "weather": ["weather", "temperature", "forecast", "climate"],
    "crypto": [
        "bitcoin",
        "btc",
        "eth",
        "ethereum",
        "crypto",
        "cryptocurrency",
        "price of",
    ],
    "convert": ["convert", "exchange", "currency", "usd", "eur", "gbp"],
    "time": ["time", "timezone", "current time", "what time"],
    # Fun
    "flip": ["flip", "coin", "heads", "tails"],
    "roll": ["roll", "dice", "d6", "d20"],
    "pick": ["pick", "choose", "select", "random"],
    "joke": ["joke", "funny", "laugh"],
    "quote": ["quote", "inspiration", "motivational"],
}
