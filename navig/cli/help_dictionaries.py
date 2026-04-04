from typing import Any

HELP_REGISTRY: dict[str, dict[str, Any]] = {
    # =========================================================================
    # INFRASTRUCTURE
    # =========================================================================
    "host": {
        "desc": "Manage remote server connections",
        "commands": {
            "list": "list configured hosts",
            "add": "add new host interactively",
            "use": "switch active host",
            "show": "show host configuration",
            "test": "test SSH connection",
            "discover-local": "detect local environment",
            "monitor": "monitoring subcommands",
            "security": "security subcommands",
            "maintenance": "maintenance subcommands",
        },
    },
    "context": {
        "desc": "Manage host/app context for current project",
        "commands": {
            "show": "show current context resolution",
            "set": "set project-local context",
            "clear": "clear project-local context",
            "init": "initialize .navig directory",
        },
    },
    "history": {
        "desc": "Command history, replay, and audit trail",
        "commands": {
            "list": "list recent operations with filtering",
            "show": "show operation details",
            "replay": "re-run a previous operation",
            "undo": "undo a reversible operation",
            "export": "export history to file (json/csv)",
            "clear": "clear all history",
            "stats": "show history statistics",
        },
    },
    "dashboard": {
        "desc": "Real-time TUI for infrastructure monitoring",
        "commands": {
            "(default)": "launch live dashboard with auto-refresh",
            "--no-live": "single snapshot mode",
            "--refresh": "set refresh interval (seconds)",
        },
    },
    "suggest": {
        "desc": "Intelligent command suggestions based on history",
        "commands": {
            "(default)": "show suggested commands",
            "--context": "filter by context (docker, database, etc.)",
            "--run <n>": "run suggestion by number",
            "--dry-run": "preview without executing",
        },
    },
    "quick": {
        "desc": "Quick action shortcuts for frequent operations",
        "commands": {
            "(default)": "list or run quick actions",
            "list": "list all quick actions",
            "add": "add a new quick action",
            "remove": "remove a quick action",
            "<name>": "run a quick action by name",
        },
    },
    "tunnel": {
        "desc": "Manage SSH tunnels for secure connections",
        "commands": {
            "run": "start tunnel for active host",
            "show": "show tunnel status",
            "remove": "stop and remove tunnel",
            "update": "restart tunnel",
            "auto": "auto-detect and create tunnel",
        },
    },
    "local": {
        "desc": "Local machine operations and diagnostics",
        "commands": {
            "show": "show system information",
            "audit": "security audit of local machine",
            "ports": "list open ports",
            "firewall": "show firewall status",
            "ping": "ping remote host",
            "dns": "DNS lookup",
            "interfaces": "show network interfaces",
        },
    },
    "hosts": {
        "desc": "Manage /etc/hosts file entries",
        "commands": {
            "view": "view hosts file",
            "edit": "edit hosts file",
            "add": "add hosts entry",
        },
    },
    # =========================================================================
    # SERVICES
    # =========================================================================
    "app": {
        "desc": "Manage applications on remote hosts",
        "commands": {
            "list": "list configured apps",
            "add": "add new app interactively",
            "use": "switch active app",
            "show": "show app configuration",
            "edit": "edit app settings",
            "remove": "remove app configuration",
            "search": "search apps by name/domain",
            "migrate": "migrate app to another host",
        },
    },
    "docker": {
        "desc": "Manage Docker containers on remote hosts",
        "commands": {
            "ps": "list containers",
            "logs": "view container logs",
            "exec": "execute command in container",
            "compose": "docker-compose operations",
            "restart": "restart container",
            "stop": "stop container",
            "start": "start container",
            "stats": "show resource usage",
            "inspect": "inspect container details",
        },
    },
    "web": {
        "desc": "Manage web servers (Nginx/Apache)",
        "commands": {
            "vhosts": "list virtual hosts",
            "test": "test server configuration",
            "enable": "enable a site",
            "disable": "disable a site",
            "reload": "reload server configuration",
            "module-enable": "enable web server module",
            "module-disable": "disable web server module",
            "recommend": "get optimization recommendations",
            "hestia": "HestiaCP subcommands",
        },
    },
    # =========================================================================
    # DATA
    # =========================================================================
    "db": {
        "desc": "Database operations (MySQL, PostgreSQL, SQLite)",
        "commands": {
            "list": "list databases",
            "show": "show database info or tables",
            "run": "run SQL query or open shell",
            "query": "execute SQL query",
            "file": "execute SQL file",
            "tables": "show database tables",
            "dump": "export database backup",
            "restore": "restore database from backup",
            "optimize": "optimize database tables",
            "repair": "repair database tables",
        },
    },
    "file": {
        "desc": "File operations (upload, download, edit)",
        "commands": {
            "list": "list remote directory",
            "add": "upload file or create directory",
            "show": "view file contents",
            "edit": "edit remote file",
            "get": "download file",
            "remove": "delete remote file",
        },
    },
    "log": {
        "desc": "View and manage remote log files",
        "commands": {
            "show": "view log file contents",
            "run": "tail log in real-time",
        },
    },
    "backup": {
        "desc": "Backup and restore NAVIG configuration",
        "commands": {
            "export": "export config to backup file",
            "import": "import config from backup",
            "show": "show backup details",
            "remove": "delete backup file",
        },
    },
    # =========================================================================
    # AUTOMATION
    # =========================================================================
    "flow": {
        "desc": "Manage and execute reusable workflows (canonical workflow command)",
        "commands": {
            "list": "list available flows",
            "show": "show flow definition",
            "run": "execute a flow",
            "test": "validate flow syntax",
            "add": "create new flow",
            "remove": "delete a flow",
            "edit": "edit flow definition",
        },
    },
    "skills": {
        "desc": "Manage AI skill definitions",
        "commands": {
            "list": "list available skills",
            "tree": "show skills by category",
            "show": "show skill details, commands, and examples",
            "run": "run a skill command (skill:command [args])",
        },
    },
    "scaffold": {
        "desc": "Generate project structures from templates",
        "commands": {
            "apply": "generate files from template",
            "validate": "check template syntax",
            "list": "list available templates",
        },
    },
    "ai": {
        "desc": "AI assistant for server management",
        "commands": {
            "ask": "ask a question",
            "explain": "explain a log file or shell command",
            "diagnose": "diagnose server issues",
            "suggest": "get optimisation suggestions",
            "show": "show AI context or history",
            "run": "run AI system analysis",
            "edit": "configure AI assistant settings",
            "models": "list available AI models from all providers",
            "providers": "manage AI providers and API keys",
            "airllm": "configure and manage AirLLM local inference",
            "login": "OAuth login (e.g., OpenAI Codex)",
            "logout": "remove OAuth credentials",
            "memory": "manage AI memory — what NAVIG knows about you",
            "memory show": "display stored user profile",
            "memory edit": "open profile in $EDITOR",
            "memory add": "add a note to memory",
            "memory search": "search memory",
            "memory clear": "clear all memory (requires --confirm)",
            "memory set": "set a specific profile field",
        },
    },
    "config": {
        "desc": "Manage NAVIG settings and configuration",
        "commands": {
            "show": "show host/app configuration",
            "edit": "edit configuration file",
            "test": "validate configuration",
            "settings": "show NAVIG settings",
            "set-mode": "set execution mode",
            "set-confirmation-level": "set confirmation level",
            "set": "set configuration value",
            "get": "get configuration value",
            "migrate": "migrate legacy config",
        },
    },
    "wiki": {
        "desc": "Wiki & knowledge base management",
        "commands": {
            "init": "initialize wiki structure",
            "list": "list wiki pages",
            "show": "view wiki page",
            "add": "add file to wiki",
            "edit": "edit wiki page",
            "remove": "archive/delete wiki page",
            "search": "full-text search",
            "publish": "publish public wiki content",
            "sync": "sync with global wiki",
            "inbox": "inbox processing commands",
            "links": "wiki link management",
            "rag": "RAG knowledge base for AI",
        },
    },
    "mcp": {
        "desc": "MCP server management for AI assistants",
        "commands": {
            "search": "search MCP directory",
            "install": "install MCP server",
            "uninstall": "uninstall MCP server",
            "list": "list installed servers",
            "enable": "enable MCP server",
            "disable": "disable MCP server",
            "start": "start MCP server",
            "stop": "stop MCP server",
            "restart": "restart MCP server",
            "status": "show server status",
            "serve": "start NAVIG as MCP server",
            "config": "generate MCP config for AI tools",
        },
    },
    # =========================================================================
    # AUTONOMOUS AGENT
    # =========================================================================
    "agent": {
        "desc": "Manage autonomous agent mode",
        "commands": {
            "install": "install and configure agent mode",
            "start": "start the autonomous agent",
            "stop": "stop the running agent",
            "status": "show agent status",
            "config": "manage agent configuration",
            "logs": "view agent logs",
            "personality": "manage personality profiles",
            "service": "install agent as system service (systemd/launchd/Windows)",
            "remediation": "view and manage auto-remediation actions",
            "learn": "analyze logs and learn from error patterns",
            "goal": "autonomous goal planning and execution tracking",
        },
    },
    "tray": {
        "desc": "Windows system tray launcher for NAVIG services",
        "commands": {
            "start": "launch the tray app (system tray icon with service controls)",
            "install": "install tray app (desktop shortcut + optional auto-start)",
            "status": "check if tray app is running",
        },
    },
    "gateway": {
        "desc": "Autonomous agent gateway server (24/7 control plane)",
        "commands": {
            "start": "start the gateway server",
            "stop": "stop the gateway server",
            "status": "show gateway status",
            "session": "manage sessions",
            "test": "run channel smoke-tests (use --strict/--json for automation)",
        },
    },
    "heartbeat": {
        "desc": "Periodic health check system",
        "commands": {
            "status": "show heartbeat status",
            "trigger": "trigger immediate heartbeat",
            "history": "show heartbeat history",
            "configure": "configure heartbeat settings",
        },
    },
    "cron": {
        "desc": "Persistent job scheduling",
        "commands": {
            "list": "list scheduled jobs",
            "add": "add new scheduled job",
            "remove": "remove a job",
            "run": "run a job immediately",
            "enable": "enable a job",
            "disable": "disable a job",
            "status": "show cron service status",
        },
    },
    "trigger": {
        "desc": "Event-driven automation triggers",
        "commands": {
            "list": "list configured triggers",
            "add": "create a new trigger",
            "show": "show trigger details",
            "remove": "delete a trigger",
            "enable": "enable a trigger",
            "disable": "disable a trigger",
            "test": "test trigger (dry run)",
            "fire": "manually fire a trigger",
            "history": "show trigger execution history",
            "stats": "show trigger statistics",
        },
    },
    "insights": {
        "desc": "Operations analytics and insights",
        "commands": {
            "(default)": "show insights summary",
            "hosts": "host health scores and trends",
            "commands": "top commands analysis",
            "time": "time-based usage patterns",
            "anomalies": "detect unusual patterns",
            "recommend": "personalized recommendations",
            "report": "generate full analytics report",
        },
    },
    "pack": {
        "desc": "Shareable operations bundles (runbooks, checklists, workflows)",
        "commands": {
            "(default)": "list available packs",
            "list": "list packs with filters",
            "show": "show pack details",
            "install": "install a pack",
            "uninstall": "remove an installed pack",
            "run": "execute a pack",
            "create": "create a new pack",
            "search": "search for packs",
        },
    },
    "approve": {
        "desc": "Human approval system for agent actions",
        "commands": {
            "list": "list pending approval requests",
            "yes": "approve a pending request",
            "no": "deny a pending request",
            "policy": "show/edit approval policy",
        },
    },
    "browser": {
        "desc": "Browser automation for web tasks",
        "commands": {
            "open": "navigate to URL",
            "click": "click element on page",
            "fill": "fill form field",
            "screenshot": "capture page screenshot",
            "stop": "stop browser",
            "status": "show browser status",
        },
    },
    "ahk": {
        "desc": "AutoHotkey v2 automation (Windows)",
        "commands": {
            "install": "detect or install AHKv2",
            "status": "show AHK status",
            "doctor": "diagnose integration issues",
            "run": "execute AHK script file",
            "exec": "execute inline AHK code",
            "click": "click at screen coordinates",
            "type": "type text with keyboard",
            "send": "send key sequence",
            "open": "open application or URL",
            "close": "close window by selector",
            "move": "move/resize window",
            "windows": "list all visible windows",
            "clipboard": "get/set clipboard content",
            "automate": "AI-powered automation",
        },
    },
    "task": {
        "desc": "Alias for 'flow' — run 'navig flow' for the canonical workflow command",
        "commands": {
            "list": "list available flows (same as: navig flow list)",
            "run": "execute a flow (same as: navig flow run <name>)",
            "show": "show flow definition (same as: navig flow show <name>)",
            "add": "create new flow (same as: navig flow add)",
            "test": "validate flow syntax (same as: navig flow test <name>)",
        },
    },
    "memory": {
        "desc": "Conversation memory and knowledge base",
        "commands": {
            "sessions": "list conversation sessions",
            "history": "show session messages",
            "clear": "clear session or all memory",
            "knowledge": "manage knowledge entries",
            "stats": "show memory statistics",
        },
    },
    "calendar": {
        "desc": "Calendar operations and event management",
        "commands": {
            "list": "list upcoming events",
            "auth": "authenticate with calendar provider",
            "add": "add new calendar event",
            "sync": "sync calendar data from remote",
        },
    },
    "email": {
        "desc": "Email operations and inbox management",
        "commands": {
            "list": "list unread emails",
            "setup": "configure email provider",
            "search": "search emails by query",
            "send": "send an email",
            "sync": "sync email data from remote",
        },
    },
    # =========================================================================
    # DOCUMENTATION & HELP
    # =========================================================================
    "docs": {
        "desc": "Search NAVIG documentation",
        "commands": {
            "(no args)": "list all documentation topics",
            "<query>": "search docs for relevant content",
        },
    },
    "fetch": {
        "desc": "Fetch and extract content from URLs",
        "commands": {
            "<url>": "fetch content from URL",
            "--mode": "extraction mode: markdown, text, raw",
            "--json": "output in JSON format",
        },
    },
    "search": {
        "desc": "Search the web for information",
        "commands": {
            "<query>": "search the web",
            "--limit": "max number of results",
            "--provider": "brave or duckduckgo",
        },
    },
    "formation": {
        "desc": "Manage profile-based agent formations",
        "commands": {
            "list": "list available formations",
            "show": "show formation details",
            "init": "initialize profile for workspace",
            "agents": "list agents in active formation",
        },
    },
    "council": {
        "desc": "Multi-agent council deliberation",
        "commands": {
            "run": "run deliberation across all agents",
        },
    },
    "version": {
        "desc": "Show NAVIG version and system info",
        "commands": {
            "(no args)": "show version with random quote",
            "--json": "output version in JSON format",
        },
    },
    "start": {
        "desc": "Quick launcher for gateway + Telegram bot",
        "commands": {
            "(no args)": "start gateway + bot in background",
            "--foreground": "start with visible logs",
            "--no-bot": "start gateway only",
            "--no-gateway": "start bot only (standalone)",
        },
    },
    "init": {
        "desc": "Interactive setup wizard for new installations",
        "commands": {
            "(no args)": "run setup wizard",
            "--reconfigure": "re-run setup for existing installation",
            "--install-daemon": "install NAVIG as system service",
        },
    },
    "telegram": {
        "desc": "Telegram bot management",
        "commands": {
            "status": "show bot status",
            "send": "send message to chat_id/@username",
            "sessions list": "list active sessions",
            "sessions show": "show session details",
            "sessions clear": "clear session history",
            "sessions delete": "delete session",
            "sessions prune": "remove inactive sessions",
        },
    },
    "crash": {
        "desc": "Manage crash reports and logs",
        "commands": {
            "export": "export latest crash report for GitHub",
        },
    },
    # ── Phase 2: Links ──────────────────────────────────────────────────────
    "links": {
        "desc": "Browser bookmark manager with vault auto-login",
        "commands": {
            "add": "add a bookmark (optionally attach vault credential)",
            "list": "list all bookmarks",
            "search": "full-text search (FTS5)",
            "show": "show bookmark details",
            "open": "open bookmark in browser (auto-login if cred attached)",
            "edit": "edit bookmark metadata",
            "tag": "add a tag to a bookmark",
            "delete": "delete a bookmark",
            "import": "import bookmarks from JSON or native browser files",
        },
    },
    "import": {
        "desc": "Universal import engine for bookmarks, contacts, and servers",
        "commands": {
            "--source all": "import every supported source",
            "--source <name>": "import one source",
            "--path <file>": "use explicit existing path (not with --source all)",
            "--output <file>": "write normalized JSON output",
            "--json": "print normalized JSON output to stdout",
            "list-sources": "show available import sources",
        },
    },
    # ── Phase 3: Knowledge Graph ────────────────────────────────────────────
    "kg": {
        "desc": "Knowledge graph — remember facts, routines, and habits",
        "commands": {
            "remember": "store a fact triple (subject predicate object)",
            "recall": "recall all facts about a subject",
            "search": "full-text search across all facts",
            "forget": "delete a fact by ID",
            "routines": "list all registered routines",
            "status": "show knowledge graph statistics",
        },
    },
    # ── Phase 4: Webhooks ───────────────────────────────────────────────────
    "webhook": {
        "desc": "Manage inbound/outbound webhooks",
        "commands": {
            "list": "list all registered webhooks",
            "add-inbound": "create inbound trigger endpoint (HMAC-secured)",
            "add-outbound": "register outbound notification URL",
            "disable": "disable a webhook",
            "delete": "permanently delete a webhook",
            "test": "send test event to an outbound webhook",
        },
    },
}
