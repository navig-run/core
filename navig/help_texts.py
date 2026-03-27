"""
Centralized help text for all NAVIG CLI commands.

This module is the SINGLE SOURCE OF TRUTH for all help text shown in
`navig --help` and subcommand help menus. When adding new commands,
define help text here first, then reference it in navig/cli.py.

=============================================================================
STANDARDIZATION RULES
=============================================================================

1. GROUP DESCRIPTIONS:
   - Start with verb: "Manage", "Execute", "Control", "View"
   - Use sentence case (capitalize first word only, except proper nouns)
   - Examples:
     ✅ "Manage remote server connections"
     ✅ "Execute commands on remote hosts"
     ❌ "Host Management" (noun phrase, Title Case)
     ❌ "Manage Remote Server Connections" (Title Case)

2. SUBCOMMAND SHORT_HELP:
   - Verb phrase, no period, lowercase after first word
   - Examples:
     ✅ "list configured hosts"
     ✅ "add new host interactively"
     ❌ "List Configured Hosts" (Title Case)
     ❌ "list configured hosts." (has period)

3. SUBCOMMAND DESCRIPTION:
   - Complete sentence(s) with period
   - Provide more detail than short_help
   - Examples:
     ✅ "Add a new remote host configuration through an interactive wizard."
     ❌ "Add new host" (too brief, same as short_help)

4. CAPITALIZATION:
   - Sentence case everywhere (except proper nouns: SSH, Docker, MySQL, etc.)

5. VERB CONSISTENCY (use these across all commands):
   | Action              | Standard Verb | ❌ Avoid                    |
   |---------------------|---------------|----------------------------|
   | Create new resource | add           | create, register, new      |
   | Delete resource     | remove        | delete, destroy, drop      |
   | Show resources      | list          | show (for single), display |
   | Modify resource     | edit          | modify, update, change     |
   | Verify functionality| test          | check, verify, validate    |
   | Activate resource   | use           | switch, select, activate   |
   | Display details     | show          | display, view, get (for single) |
   | Execute action      | run           | execute, start             |

6. OPTION HELP:
   - Brief, lowercase, no period
   - Format: "description of what option does"
   - Examples:
     ✅ "show all containers including stopped"
     ✅ "output format: table, json, yaml"
     ❌ "Show all containers." (Title Case, has period)

=============================================================================
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandHelp:
    """Help text for a command or command group."""

    short_help: str  # One-line summary (shown in parent --help)
    description: str  # Full description (shown in command --help header)
    epilog: str | None = None  # Examples/notes (shown at bottom after options)


@dataclass(frozen=True)
class OptionHelp:
    """Help text for a command option/flag."""

    text: str  # Description shown next to the option


# =============================================================================
# COMMAND GROUPS
# =============================================================================

# -----------------------------------------------------------------------------
# INFRASTRUCTURE
# -----------------------------------------------------------------------------

HOST = CommandHelp(
    short_help="Manage remote hosts",
    description="Manage remote server connections and configurations.",
    epilog="""
Examples:
  navig host add myserver          Add a new host interactively
  navig host list                  Show all configured hosts
  navig host use production        Switch to production host
  navig host test myserver         Test SSH connection
  navig host discover-local        Detect local development environment
""",
)

HOST_LIST = CommandHelp(
    short_help="list configured hosts",
    description="List all configured remote hosts with connection status.",
)

HOST_ADD = CommandHelp(
    short_help="add new host interactively",
    description="Add a new remote host configuration through an interactive wizard.",
)

HOST_USE = CommandHelp(
    short_help="switch active host",
    description="Set the active host for subsequent commands.",
)

HOST_SHOW = CommandHelp(
    short_help="show host configuration",
    description="Display detailed configuration for a specific host.",
)

HOST_TEST = CommandHelp(
    short_help="test SSH connection",
    description="Test SSH connectivity to the specified or active host.",
)

HOST_DISCOVER_LOCAL = CommandHelp(
    short_help="detect local environment",
    description="Auto-detect local development environment (databases, web servers, Docker, etc.) and create a localhost configuration.",
)

TUNNEL = CommandHelp(
    short_help="Manage SSH tunnels",
    description="Manage SSH tunnels for secure database and service connections.",
    epilog="""
Examples:
  navig tunnel run                 Start tunnel for active host
  navig tunnel show                Show tunnel status
  navig tunnel remove              Stop and remove tunnel
""",
)

TUNNEL_RUN = CommandHelp(
    short_help="start tunnel for active host",
    description="Start an SSH tunnel for the currently active server.",
)

TUNNEL_SHOW = CommandHelp(
    short_help="show tunnel status",
    description="Display the status of active SSH tunnels.",
)

TUNNEL_REMOVE = CommandHelp(
    short_help="stop and remove tunnel",
    description="Stop and remove the SSH tunnel for the active server.",
)

TUNNEL_UPDATE = CommandHelp(
    short_help="restart tunnel",
    description="Restart the SSH tunnel (stop and start again).",
)

TUNNEL_AUTO = CommandHelp(
    short_help="auto-detect and create tunnel",
    description="Automatically detect tunnel configuration and create a tunnel.",
)

LOCAL = CommandHelp(
    short_help="Local operations",
    description="Local machine operations and diagnostics.",
    epilog="""
Examples:
  navig local show                 Show system information
  navig local ports                List open ports
  navig local audit                Security audit of local machine
""",
)

LOCAL_SHOW = CommandHelp(
    short_help="show system information",
    description="Display local system information including OS, CPU, memory, and disk.",
)

LOCAL_AUDIT = CommandHelp(
    short_help="security audit of local machine",
    description="Perform a security audit of the local machine.",
)

LOCAL_PORTS = CommandHelp(
    short_help="list open ports",
    description="List all open ports on the local machine.",
)

LOCAL_FIREWALL = CommandHelp(
    short_help="show firewall status",
    description="Display the local firewall status and rules.",
)

LOCAL_PING = CommandHelp(
    short_help="ping remote host",
    description="Ping a remote host to check connectivity.",
)

LOCAL_DNS = CommandHelp(
    short_help="DNS lookup",
    description="Perform DNS lookup for a hostname.",
)

LOCAL_INTERFACES = CommandHelp(
    short_help="show network interfaces",
    description="Display network interfaces and their configurations.",
)

HOSTS = CommandHelp(
    short_help="Manage /etc/hosts",
    description="Manage /etc/hosts file entries.",
    epilog="""
Examples:
  navig hosts view                 View hosts file
  navig hosts add 10.0.0.1 myhost  Add hosts entry
  navig hosts edit                 Edit hosts file
""",
)

HOSTS_VIEW = CommandHelp(
    short_help="view hosts file",
    description="Display the contents of the hosts file.",
)

HOSTS_EDIT = CommandHelp(
    short_help="edit hosts file",
    description="Open the hosts file in the default editor.",
)

HOSTS_ADD = CommandHelp(
    short_help="add hosts entry",
    description="Add a new entry to the hosts file.",
)

# -----------------------------------------------------------------------------
# SERVICES
# -----------------------------------------------------------------------------

APP = CommandHelp(
    short_help="Manage applications",
    description="Manage applications on remote hosts.",
    epilog="""
Examples:
  navig app list                   List all configured apps
  navig app add myapp              Add a new app interactively
  navig app use myapp              Switch to myapp as active
  navig app show myapp             Show app configuration
""",
)

APP_LIST = CommandHelp(
    short_help="list configured apps",
    description="List all configured applications with their host associations.",
)

APP_ADD = CommandHelp(
    short_help="add new app interactively",
    description="Add a new application configuration through an interactive wizard.",
)

APP_USE = CommandHelp(
    short_help="switch active app",
    description="Set the active application for subsequent commands.",
)

APP_SHOW = CommandHelp(
    short_help="show app configuration",
    description="Display detailed configuration for a specific application.",
)

APP_EDIT = CommandHelp(
    short_help="edit app settings",
    description="Edit application configuration in the default editor.",
)

APP_REMOVE = CommandHelp(
    short_help="remove app configuration",
    description="Remove an application configuration from NAVIG.",
)

APP_SEARCH = CommandHelp(
    short_help="search apps by name/domain",
    description="Search for applications by name or domain pattern.",
)

APP_MIGRATE = CommandHelp(
    short_help="migrate app to another host",
    description="Migrate an application configuration to a different host.",
)

DOCKER = CommandHelp(
    short_help="Docker management",
    description="Manage Docker containers on remote hosts.",
    epilog="""
Examples:
  navig docker ps                  List running containers
  navig docker ps --all            List all containers
  navig docker logs nginx          View nginx container logs
  navig docker exec nginx "nginx -t"  Execute command in container
""",
)

DOCKER_PS = CommandHelp(
    short_help="list containers",
    description="List Docker containers on the remote host.",
)

DOCKER_LOGS = CommandHelp(
    short_help="view container logs",
    description="View logs from a Docker container.",
)

DOCKER_EXEC = CommandHelp(
    short_help="execute command in container",
    description="Execute a command inside a Docker container.",
)

DOCKER_COMPOSE = CommandHelp(
    short_help="docker-compose operations",
    description="Run docker-compose commands on the remote host.",
)

DOCKER_RESTART = CommandHelp(
    short_help="restart container",
    description="Restart a Docker container.",
)

DOCKER_STOP = CommandHelp(
    short_help="stop container",
    description="Stop a running Docker container.",
)

DOCKER_START = CommandHelp(
    short_help="start container",
    description="Start a stopped Docker container.",
)

DOCKER_STATS = CommandHelp(
    short_help="show resource usage",
    description="Display resource usage statistics for containers.",
)

DOCKER_INSPECT = CommandHelp(
    short_help="inspect container details",
    description="Display detailed information about a container.",
)

WEB = CommandHelp(
    short_help="Web server management",
    description="Manage web servers (Nginx/Apache) on remote hosts.",
    epilog="""
Examples:
  navig web vhosts                 List virtual hosts
  navig web test                   Test server configuration
  navig web enable mysite          Enable a site
  navig web reload                 Reload server configuration
""",
)

WEB_VHOSTS = CommandHelp(
    short_help="list virtual hosts",
    description="List all virtual hosts (enabled and available).",
)

WEB_TEST = CommandHelp(
    short_help="test server configuration",
    description="Test web server configuration syntax.",
)

WEB_ENABLE = CommandHelp(
    short_help="enable a site",
    description="Enable a web server site.",
)

WEB_DISABLE = CommandHelp(
    short_help="disable a site",
    description="Disable a web server site.",
)

WEB_RELOAD = CommandHelp(
    short_help="reload server configuration",
    description="Reload the web server configuration.",
)

WEB_MODULE_ENABLE = CommandHelp(
    short_help="enable web server module",
    description="Enable a web server module (Apache only).",
)

WEB_MODULE_DISABLE = CommandHelp(
    short_help="disable web server module",
    description="Disable a web server module (Apache only).",
)

WEB_RECOMMEND = CommandHelp(
    short_help="get optimization recommendations",
    description="Get web server optimization recommendations.",
)

# -----------------------------------------------------------------------------
# DATA
# -----------------------------------------------------------------------------

DB = CommandHelp(
    short_help="Database operations",
    description="Database operations (MySQL, PostgreSQL, SQLite).",
    epilog="""
Examples:
  navig db list                    List all databases
  navig db tables mydb             Show tables in mydb
  navig db query "SELECT 1"        Execute SQL query
  navig db dump mydb               Export database backup
  navig db restore mydb backup.sql Restore from backup
""",
)

DB_LIST = CommandHelp(
    short_help="list databases",
    description="List all databases on the remote server.",
)

DB_SHOW = CommandHelp(
    short_help="show database info or tables",
    description="Show database information or tables.",
)

DB_RUN = CommandHelp(
    short_help="run SQL query or open shell",
    description="Run an SQL query or open an interactive database shell.",
)

DB_QUERY = CommandHelp(
    short_help="execute SQL query",
    description="Execute an SQL query on the remote database.",
)

DB_FILE = CommandHelp(
    short_help="execute SQL file",
    description="Execute an SQL file through the tunnel.",
)

DB_TABLES = CommandHelp(
    short_help="show database tables",
    description="Show tables in a specific database.",
)

DB_DUMP = CommandHelp(
    short_help="export database backup",
    description="Export a database to a backup file.",
)

DB_RESTORE = CommandHelp(
    short_help="restore database from backup",
    description="Restore a database from a backup file.",
)

DB_OPTIMIZE = CommandHelp(
    short_help="optimize database tables",
    description="Optimize database tables to improve performance.",
)

DB_REPAIR = CommandHelp(
    short_help="repair database tables",
    description="Repair corrupted database tables.",
)

FILE = CommandHelp(
    short_help="File operations",
    description="File operations (upload, download, edit) on remote hosts.",
    epilog="""
Examples:
  navig file list /var/log         List files in /var/log
  navig file add local.txt /tmp/   Upload file to remote
  navig file get /var/log/syslog   Download file
  navig file edit /etc/nginx/nginx.conf  Edit remote file
""",
)

FILE_LIST = CommandHelp(
    short_help="list remote directory",
    description="List contents of a remote directory.",
)

FILE_ADD = CommandHelp(
    short_help="upload file or create directory",
    description="Upload a file to the remote server or create a directory.",
)

FILE_SHOW = CommandHelp(
    short_help="view file contents",
    description="View the contents of a remote file.",
)

FILE_EDIT = CommandHelp(
    short_help="edit remote file",
    description="Edit a remote file in the default editor.",
)

FILE_GET = CommandHelp(
    short_help="download file",
    description="Download a file from the remote server.",
)

FILE_REMOVE = CommandHelp(
    short_help="delete remote file",
    description="Delete a file or directory on the remote server.",
)

LOG = CommandHelp(
    short_help="Log management",
    description="View and manage remote log files.",
    epilog="""
Examples:
  navig log show /var/log/syslog   View log file
  navig log run /var/log/nginx/access.log  Tail log in real-time
""",
)

LOG_SHOW = CommandHelp(
    short_help="view log file contents",
    description="View the contents of a remote log file.",
)

LOG_RUN = CommandHelp(
    short_help="tail log in real-time",
    description="Tail a log file in real-time (like tail -f).",
)

BACKUP = CommandHelp(
    short_help="Backup management",
    description="Backup and restore NAVIG configuration.",
    epilog="""
Examples:
  navig backup export              Export config to backup file
  navig backup import backup.tar.gz  Import config from backup
  navig backup show                List available backups
""",
)

BACKUP_EXPORT = CommandHelp(
    short_help="export config to backup file",
    description="Export NAVIG configuration to a portable backup file.",
)

BACKUP_IMPORT = CommandHelp(
    short_help="import config from backup",
    description="Import NAVIG configuration from a backup file.",
)

BACKUP_SHOW = CommandHelp(
    short_help="show backup details",
    description="Show details of a backup file or list available backups.",
)

BACKUP_REMOVE = CommandHelp(
    short_help="delete backup file",
    description="Delete a backup file.",
)

# -----------------------------------------------------------------------------
# AUTOMATION
# -----------------------------------------------------------------------------

FLOW = CommandHelp(
    short_help="Workflow automation",
    description="Manage and execute reusable command workflows.",
    epilog="""
Examples:
  navig flow list                  List available flows
  navig flow run deploy            Execute the deploy flow
  navig flow add myflow            Create a new flow
  navig flow test myflow           Validate flow syntax
""",
)

FLOW_LIST = CommandHelp(
    short_help="list available flows",
    description="List all available workflow definitions.",
)

FLOW_SHOW = CommandHelp(
    short_help="show flow definition",
    description="Display the definition and steps of a workflow.",
)

FLOW_RUN = CommandHelp(
    short_help="execute a flow",
    description="Execute a workflow with optional parameters.",
)

FLOW_TEST = CommandHelp(
    short_help="validate flow syntax",
    description="Validate the syntax and structure of a workflow.",
)

FLOW_ADD = CommandHelp(
    short_help="create new flow",
    description="Create a new workflow definition.",
)

BROWSER = CommandHelp(
    short_help="Browser automation",
    description="Control headless browser for web automation tasks.",
    epilog="""
Examples:
  navig browser open https://example.com    Navigate to URL
  navig browser click "#submit"             Click element by CSS selector
  navig browser fill "#email" "test@ex.com" Fill form field
  navig browser screenshot output.png       Capture screenshot
  navig browser status                      Check browser status
  navig browser stop                        Stop browser session
""",
)

BROWSER_OPEN = CommandHelp(
    short_help="navigate to URL",
    description="Navigate the browser to a specified URL.",
)

BROWSER_CLICK = CommandHelp(
    short_help="click element on page",
    description="Click an element using CSS selector or XPath.",
)

BROWSER_FILL = CommandHelp(
    short_help="fill form field",
    description="Fill a form field with a value using CSS selector.",
)

BROWSER_SCREENSHOT = CommandHelp(
    short_help="capture page screenshot",
    description="Capture a screenshot of the current page.",
)

BROWSER_STOP = CommandHelp(
    short_help="stop browser",
    description="Stop the headless browser session.",
)

BROWSER_STATUS = CommandHelp(
    short_help="show browser status",
    description="Display the status of the headless browser session.",
)

# -----------------------------------------------------------------------------
# AUTONOMOUS AGENT SYSTEM
# -----------------------------------------------------------------------------

START = CommandHelp(
    short_help="Quick launcher for gateway + bot",
    description="Start the NAVIG gateway and Telegram bot for 24/7 autonomous operation.",
    epilog="""
Examples:
  navig start                      Start in background (recommended)
  navig start --foreground         Start with visible logs
  navig start --no-bot             Start gateway only
  navig start --no-gateway         Start bot only (standalone)
""",
)

GATEWAY = CommandHelp(
    short_help="Autonomous agent gateway",
    description="Manage the autonomous agent gateway server for 24/7 operation.",
    epilog="""
Examples:
  navig gateway start              Start gateway server
  navig gateway start --port 9000  Start on custom port
  navig gateway stop               Stop gateway gracefully
  navig gateway status             Check if gateway is running
  navig gateway session list       List active sessions
""",
)

GATEWAY_START = CommandHelp(
    short_help="start gateway server",
    description="Start the NAVIG gateway server for persistent sessions and scheduling.",
)

GATEWAY_STOP = CommandHelp(
    short_help="stop gateway server",
    description="Send graceful shutdown signal to the running gateway.",
)

GATEWAY_STATUS = CommandHelp(
    short_help="show gateway status",
    description="Check if the gateway is running and display health information.",
)

GATEWAY_SESSION = CommandHelp(
    short_help="manage sessions",
    description="List, show, or clear gateway sessions.",
)

HEARTBEAT = CommandHelp(
    short_help="Periodic health monitoring",
    description="Manage the heartbeat system for periodic server health checks.",
    epilog="""
Examples:
  navig heartbeat status           Show heartbeat status
  navig heartbeat trigger          Run immediate health check
  navig heartbeat history          View heartbeat history
  navig heartbeat configure --interval 15  Set 15-minute interval
""",
)

HEARTBEAT_STATUS = CommandHelp(
    short_help="show heartbeat status",
    description="Display heartbeat service status including next scheduled check.",
)

HEARTBEAT_TRIGGER = CommandHelp(
    short_help="trigger immediate check",
    description="Run an immediate health check on all configured hosts.",
)

HEARTBEAT_HISTORY = CommandHelp(
    short_help="show heartbeat history",
    description="Display history of past heartbeat checks and results.",
)

HEARTBEAT_CONFIGURE = CommandHelp(
    short_help="configure heartbeat settings",
    description="Configure heartbeat interval, enable/disable, and notification settings.",
)

CRON = CommandHelp(
    short_help="Scheduled job management",
    description="Manage scheduled jobs for automated task execution.",
    epilog="""
Examples:
  navig cron list                  List all scheduled jobs
  navig cron add "Backup" "daily" "navig backup export"
  navig cron run job_1             Run job immediately
  navig cron enable job_1          Enable a disabled job
  navig cron remove job_1          Remove a job
""",
)

CRON_LIST = CommandHelp(
    short_help="list scheduled jobs",
    description="Display all scheduled cron jobs and their status.",
)

CRON_ADD = CommandHelp(
    short_help="add scheduled job",
    description="Add a new scheduled job with name, schedule, and command.",
)

CRON_REMOVE = CommandHelp(
    short_help="remove scheduled job",
    description="Remove a scheduled job by ID.",
)

CRON_RUN = CommandHelp(
    short_help="run job immediately",
    description="Execute a scheduled job immediately without waiting for its schedule.",
)

CRON_ENABLE = CommandHelp(
    short_help="enable a job",
    description="Enable a previously disabled scheduled job.",
)

CRON_DISABLE = CommandHelp(
    short_help="disable a job",
    description="Disable a scheduled job without removing it.",
)

CRON_STATUS = CommandHelp(
    short_help="show cron service status",
    description="Display the status of the cron scheduling service.",
)

# -----------------------------------------------------------------------------
# AGENT MODE & APPROVAL SYSTEM
# -----------------------------------------------------------------------------

AGENT = CommandHelp(
    short_help="Autonomous agent mode",
    description="Manage the autonomous agent for 24/7 server monitoring and management.",
    epilog="""
Examples:
  navig agent install              Install agent with default personality
  navig agent install --personality professional  Install with specific personality
  navig agent start                Start the autonomous agent
  navig agent status               Check agent health and component status
  navig agent stop                 Stop the running agent
  navig agent config --show        View current configuration
  navig agent logs --follow        Follow agent log output
  navig agent personality list     List available personalities
  navig agent goal add "Deploy app" Execute autonomous goal
""",
)

AGENT_INSTALL = CommandHelp(
    short_help="install agent mode",
    description="Install and configure the autonomous agent with workspace files.",
)

AGENT_START = CommandHelp(
    short_help="start the agent",
    description="Start the autonomous agent for 24/7 monitoring.",
)

AGENT_STOP = CommandHelp(
    short_help="stop the agent",
    description="Stop the running autonomous agent gracefully.",
)

AGENT_STATUS = CommandHelp(
    short_help="show agent status",
    description="Display agent health, component status, and configuration.",
)

AGENT_CONFIG = CommandHelp(
    short_help="manage configuration",
    description="View, edit, or modify agent configuration.",
)

AGENT_LOGS = CommandHelp(
    short_help="view agent logs",
    description="View or follow agent log output with filtering.",
)

AGENT_PERSONALITY = CommandHelp(
    short_help="manage personalities",
    description="List, set, or create personality profiles for the agent.",
)

AGENT_SERVICE = CommandHelp(
    short_help="install as system service",
    description="Install agent as systemd, launchd, or Windows service.",
)

AGENT_REMEDIATION = CommandHelp(
    short_help="view auto-remediation actions",
    description="View and manage automatic recovery actions taken by the agent.",
)

AGENT_LEARN = CommandHelp(
    short_help="analyze logs for patterns",
    description="Analyze logs to detect error patterns and provide recommendations.",
)

AGENT_GOAL = CommandHelp(
    short_help="autonomous goal execution",
    description="Add, track, and manage autonomous goals for the agent.",
)

MEMORY = CommandHelp(
    short_help="Conversation memory and knowledge",
    description="Manage conversation memory and knowledge base for AI context.",
    epilog="""
Examples:
  navig memory sessions            List conversation sessions
  navig memory history my-task     Show messages for a session
  navig memory clear --session x   Clear specific session
  navig memory stats               Show memory statistics
  navig memory knowledge list      List knowledge entries
  navig memory knowledge add       Add knowledge entry
""",
)

MEMORY_SESSIONS = CommandHelp(
    short_help="list conversation sessions",
    description="List all stored conversation sessions with metadata.",
)

MEMORY_HISTORY = CommandHelp(
    short_help="show session messages",
    description="Display conversation history for a specific session.",
)

MEMORY_CLEAR = CommandHelp(
    short_help="clear memory",
    description="Clear a specific session or all conversation memory.",
)

MEMORY_STATS = CommandHelp(
    short_help="show memory statistics",
    description="Display memory usage statistics and session counts.",
)

MEMORY_KNOWLEDGE = CommandHelp(
    short_help="manage knowledge entries",
    description="Add, list, search, or clear knowledge base entries.",
)

TASK = CommandHelp(
    short_help="Async task queue",
    description="Manage the task queue for asynchronous operations.",
    epilog="""
Examples:
  navig task list                  List queued tasks
  navig task add "backup db"       Add a task to the queue
  navig task show task_123         Show task details
  navig task cancel task_123       Cancel a pending task
  navig task stats                 Show queue statistics
""",
)

TASK_LIST = CommandHelp(
    short_help="list queued tasks",
    description="Display all tasks in the queue with their status.",
)

TASK_ADD = CommandHelp(
    short_help="add task to queue",
    description="Add a new task to the asynchronous task queue.",
)

TASK_SHOW = CommandHelp(
    short_help="show task details",
    description="Display detailed information about a specific task.",
)

TASK_CANCEL = CommandHelp(
    short_help="cancel pending task",
    description="Cancel a task that is pending or waiting in the queue.",
)

TASK_STATS = CommandHelp(
    short_help="show queue statistics",
    description="Display task queue statistics and performance metrics.",
)

APPROVE = CommandHelp(
    short_help="Human approval system",
    description="Manage human approval requests for agent actions.",
    epilog="""
Examples:
  navig approve list               List pending approval requests
  navig approve yes req_123        Approve a request
  navig approve no req_123         Deny a request
  navig approve policy             Show approval policy
  navig approve policy --edit      Edit approval policy
""",
)

APPROVE_LIST = CommandHelp(
    short_help="list pending requests",
    description="List all pending approval requests from the agent.",
)

APPROVE_YES = CommandHelp(
    short_help="approve a request",
    description="Approve a pending request, allowing the agent to proceed.",
)

APPROVE_NO = CommandHelp(
    short_help="deny a request",
    description="Deny a pending request, blocking the agent action.",
)

APPROVE_POLICY = CommandHelp(
    short_help="show/edit approval policy",
    description="View or edit the approval policy that determines what needs human approval.",
)

AI = CommandHelp(
    short_help="AI assistant",
    description="AI assistant for server management and troubleshooting.",
    epilog="""
Examples:
  navig ai ask "How do I restart nginx?"
  navig ai explain "iptables -L"
  navig ai diagnose                Diagnose server issues
""",
)

AI_ASK = CommandHelp(
    short_help="ask a question",
    description="Ask the AI assistant a question about server management.",
)

AI_EXPLAIN = CommandHelp(
    short_help="explain a command or concept",
    description="Get an explanation of a command or technical concept.",
)

AI_DIAGNOSE = CommandHelp(
    short_help="diagnose server issues",
    description="Use AI to diagnose server issues based on logs and metrics.",
)

AI_SUGGEST = CommandHelp(
    short_help="get suggestions",
    description="Get AI suggestions for the current context.",
)

AI_SHOW = CommandHelp(
    short_help="show AI context or history",
    description="Show the current AI context or conversation history.",
)

AI_RUN = CommandHelp(
    short_help="run AI-generated command",
    description="Run a command generated by the AI assistant.",
)

AI_EDIT = CommandHelp(
    short_help="edit AI system prompt",
    description="Edit the AI assistant's system prompt.",
)

CONFIG = CommandHelp(
    short_help="Configuration management",
    description="Manage NAVIG settings and configuration.",
    epilog="""
Examples:
  navig config show myhost         Show host configuration
  navig config edit myhost         Edit host configuration
  navig config settings            Show NAVIG settings
  navig config test                Validate all configurations
""",
)

CONFIG_SHOW = CommandHelp(
    short_help="show host/app configuration",
    description="Display configuration for a host or application.",
)

CONFIG_EDIT = CommandHelp(
    short_help="edit configuration file",
    description="Open a configuration file in the default editor.",
)

CONFIG_TEST = CommandHelp(
    short_help="validate configuration",
    description="Validate configuration files for errors.",
)

CONFIG_SETTINGS = CommandHelp(
    short_help="show NAVIG settings",
    description="Display current NAVIG settings including execution mode.",
)

CONFIG_SET_MODE = CommandHelp(
    short_help="set execution mode",
    description="Set the default execution mode (interactive or auto).",
)

CONFIG_SET_CONFIRMATION_LEVEL = CommandHelp(
    short_help="set confirmation level",
    description="Set the confirmation level for destructive operations.",
)

CONFIG_SET = CommandHelp(
    short_help="set configuration value",
    description="Set a configuration value.",
)

CONFIG_GET = CommandHelp(
    short_help="get configuration value",
    description="Get a configuration value.",
)

CONFIG_MIGRATE = CommandHelp(
    short_help="migrate legacy config",
    description="Migrate legacy configuration files to the new format.",
)

# -----------------------------------------------------------------------------
# STANDALONE COMMANDS
# -----------------------------------------------------------------------------

RUN = CommandHelp(
    short_help="Execute remote command",
    description="Execute arbitrary shell command on remote server.",
    epilog="""
Examples:
  navig run "ls -la"                              Simple command
  navig run --b64 "curl -d '{\\"k\\":\\"v\\"}' api"  JSON (escape-proof)
  navig run @script.sh                            Read from file
  cat script.sh | navig run @-                    Read from stdin
  navig run -i                                    Open editor for input

Input methods:
  "command"     Direct command string
  @filename     Read command from file
  @-            Read command from stdin
  -i            Open editor for multi-line input

Flags:
  --b64, -b     Base64 transport mode (escape-proof for JSON, special chars)
  --stdin, -s  Read command from stdin
  --file, -f   Read command from file
  -i           Open interactive editor
""",
)

INIT = CommandHelp(
    short_help="Initialize NAVIG",
    description="Initialize NAVIG configuration in the current directory or globally.",
)

MENU = CommandHelp(
    short_help="Interactive menu",
    description="Launch the interactive menu for NAVIG operations.",
)

# =============================================================================
# COMMON OPTIONS
# =============================================================================

OPT_HOST = OptionHelp(text="override active host for this command")
OPT_APP = OptionHelp(text="override active app for this command")
OPT_VERBOSE = OptionHelp(text="detailed logging output")
OPT_QUIET = OptionHelp(text="minimal output")
OPT_YES = OptionHelp(text="skip confirmation prompts")
OPT_CONFIRM = OptionHelp(text="force confirmation prompt")
OPT_DRY_RUN = OptionHelp(text="show what would be done without making changes")
OPT_JSON = OptionHelp(text="output as JSON")
OPT_PLAIN = OptionHelp(text="plain text output for scripting")
OPT_FORMAT = OptionHelp(text="output format: table, json, yaml")
OPT_ALL = OptionHelp(text="show all items including hidden/inactive")
OPT_FORCE = OptionHelp(text="force operation without confirmation")


# =============================================================================
# HELP TEXT LOOKUP (for HELP_REGISTRY compatibility)
# =============================================================================


def get_group_help(group_name: str) -> dict[str, str]:
    """Get help text for a command group in HELP_REGISTRY format."""
    groups = {
        "host": HOST,
        "tunnel": TUNNEL,
        "local": LOCAL,
        "hosts": HOSTS,
        "app": APP,
        "docker": DOCKER,
        "web": WEB,
        "db": DB,
        "file": FILE,
        "log": LOG,
        "backup": BACKUP,
        "flow": FLOW,
        "ai": AI,
        "config": CONFIG,
        # Autonomous agent system
        "start": START,
        "gateway": GATEWAY,
        "heartbeat": HEARTBEAT,
        "cron": CRON,
        # Agent mode & approval
        "agent": AGENT,
        "memory": MEMORY,
        "task": TASK,
        "approve": APPROVE,
        # Browser automation
        "browser": BROWSER,
    }

    if group_name in groups:
        return {
            "desc": groups[group_name].description,
            "short_help": groups[group_name].short_help,
            "epilog": groups[group_name].epilog,
        }
    return None
