"""
Console Helper - Centralized Rich formatting utilities

Provides consistent, beautiful terminal output across all NAVIG commands.
The Schema's visual language for encrypted operations.

Performance note: ALL Rich imports are deferred until actually needed
to improve CLI startup time (~120 ms saved).
"""

import sys

from typing import Optional, List, Dict, Any, Union
from pathlib import Path


# ---------------------------------------------------------------------------
# Lazy Rich class accessors  (loaded on first use, then cached)
# ---------------------------------------------------------------------------
_Console = None
_Panel = None
_Table = None
_Tree = None
_Progress = None
_SpinnerColumn = None
_TextColumn = None
_BarColumn = None
_TaskProgressColumn = None
_Syntax = None
_Markdown = None


def _ensure_rich():
    """Import core Rich classes on first call, then cache globally."""
    global _Console, _Panel, _Table, _Tree
    global _Progress, _SpinnerColumn, _TextColumn, _BarColumn, _TaskProgressColumn
    if _Console is not None:
        return
    from rich.console import Console as _C
    from rich.panel import Panel as _P
    from rich.table import Table as _T
    from rich.tree import Tree as _Tr
    from rich.progress import (
        Progress as _Pr,
        SpinnerColumn as _SC,
        TextColumn as _TC,
        BarColumn as _BC,
        TaskProgressColumn as _TPC,
    )
    _Console = _C
    _Panel = _P
    _Table = _T
    _Tree = _Tr
    _Progress = _Pr
    _SpinnerColumn = _SC
    _TextColumn = _TC
    _BarColumn = _BC
    _TaskProgressColumn = _TPC


def _get_syntax():
    """Lazy load rich.syntax.Syntax."""
    global _Syntax
    if _Syntax is None:
        from rich.syntax import Syntax
        _Syntax = Syntax
    return _Syntax


def _get_markdown():
    """Lazy load rich.markdown.Markdown."""
    global _Markdown
    if _Markdown is None:
        from rich.markdown import Markdown
        _Markdown = Markdown
    return _Markdown


# ---------------------------------------------------------------------------
# Lazy console singleton — proxy that imports Rich on first real access.
# ---------------------------------------------------------------------------

class _LazyConsole:
    """Lightweight proxy that defers ``Console()`` creation."""

    __slots__ = ("_real",)

    def __init__(self):
        object.__setattr__(self, "_real", None)

    def _load(self):
        _ensure_rich()
        real = _Console()
        object.__setattr__(self, "_real", real)
        return real

    def __getattr__(self, name):
        real = object.__getattribute__(self, "_real")
        if real is None:
            real = self._load()
        return getattr(real, name)

    def __setattr__(self, name, value):
        real = object.__getattribute__(self, "_real")
        if real is None:
            real = self._load()
        setattr(real, name, value)


# ============================================================================
# GLOBAL CONSOLE INSTANCE
# ============================================================================

console = _LazyConsole()


def _safe_symbol(preferred: str, fallback: str) -> str:
    """Return preferred symbol if it can be encoded, else fallback.

    Windows terminals can default to legacy encodings (e.g. cp1252) that cannot
    encode some Unicode symbols. This keeps output robust for scripting and logs.
    """
    encoding = getattr(console.file, "encoding", None) or sys.stdout.encoding or "utf-8"
    try:
        preferred.encode(encoding)
        return preferred
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# Module __getattr__: allow ``from navig.console_helper import Table`` etc.
# ---------------------------------------------------------------------------
_RICH_CLASS_MAP = {
    "Console": lambda: (_ensure_rich(), _Console)[1],
    "Panel": lambda: (_ensure_rich(), _Panel)[1],
    "Table": lambda: (_ensure_rich(), _Table)[1],
    "Tree": lambda: (_ensure_rich(), _Tree)[1],
    "Progress": lambda: (_ensure_rich(), _Progress)[1],
    "SpinnerColumn": lambda: (_ensure_rich(), _SpinnerColumn)[1],
    "TextColumn": lambda: (_ensure_rich(), _TextColumn)[1],
    "BarColumn": lambda: (_ensure_rich(), _BarColumn)[1],
    "TaskProgressColumn": lambda: (_ensure_rich(), _TaskProgressColumn)[1],
}


def __getattr__(name: str):
    factory = _RICH_CLASS_MAP.get(name)
    if factory is not None:
        cls = factory()
        globals()[name] = cls  # cache for subsequent accesses
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ============================================================================
# COLOR SCHEME (Schema Standard)
# ============================================================================

class Colors:
    """Centralized color definitions for consistent theming."""
    SUCCESS = "green"
    ERROR = "red"
    WARNING = "yellow"
    INFO = "blue"
    PROMPT = "cyan"
    PATH = "magenta"
    HIGHLIGHT = "bright_cyan"
    ACCENT = "bright_cyan"
    DIM = "dim"
    SERVER = "cyan"
    SERVICE = "yellow"
    STATUS_GOOD = "green"
    STATUS_BAD = "red"


# ============================================================================
# MESSAGE HELPERS
# ============================================================================

def success(message: str, details: Optional[str] = None):
    """Print success message in green with checkmark."""
    mark = _safe_symbol("✓", "+")
    console.print(f"[{Colors.SUCCESS}]{mark}[/{Colors.SUCCESS}] {message}")
    if details:
        console.print(f"  [{Colors.DIM}]{details}[/{Colors.DIM}]")


def error(message: str, details: Optional[str] = None):
    """Print error message in red with X mark."""
    mark = _safe_symbol("✗", "x")
    console.print(f"[{Colors.ERROR}]{mark}[/{Colors.ERROR}] {message}")
    if details:
        console.print(f"  [{Colors.DIM}]{details}[/{Colors.DIM}]")


def warning(message: str, details: Optional[str] = None):
    """Print warning message in yellow with warning symbol."""
    mark = _safe_symbol("⚠", "!")
    console.print(f"[{Colors.WARNING}]{mark}[/{Colors.WARNING}] {message}")
    if details:
        console.print(f"  [{Colors.DIM}]{details}[/{Colors.DIM}]")


def info(message: str, details: Optional[str] = None, no_wrap: bool = False):
    """Print info message in blue with info symbol.

    Args:
        message: The message to display
        details: Optional additional details to display below
        no_wrap: If True, truncates instead of wrapping long lines
    """
    mark = _safe_symbol("ℹ", "i")
    if no_wrap:
        console.print(f"[{Colors.INFO}]{mark}[/{Colors.INFO}] {message}", overflow="ellipsis", no_wrap=True)
    else:
        console.print(f"[{Colors.INFO}]{mark}[/{Colors.INFO}] {message}")
    if details:
        console.print(f"  [{Colors.DIM}]{details}[/{Colors.DIM}]")


def step(message: str):
    """Print step indicator for multi-step operations."""
    mark = _safe_symbol("→", ">")
    console.print(f"[{Colors.PROMPT}]{mark}[/{Colors.PROMPT}] {message}")


def header(title: str, subtitle: Optional[str] = None):
    """Print section header with optional subtitle."""
    console.print(f"\n[{Colors.HIGHLIGHT}]=== {title} ===[/{Colors.HIGHLIGHT}]")
    if subtitle:
        console.print(f"[{Colors.DIM}]{subtitle}[/{Colors.DIM}]")
    console.print()


def subheader(title: str):
    """Print subsection header."""
    console.print(f"[{Colors.ACCENT}]{title}[/{Colors.ACCENT}]")


def dim(message: str):
    """Print dimmed/muted message."""
    console.print(f"[{Colors.DIM}]{message}[/{Colors.DIM}]")


def raw_print(message: str):
    """Print raw text without formatting (for --raw flag)."""
    print(message)


# ============================================================================
# PANELS
# ============================================================================

def panel(
    content: str,
    title: Optional[str] = None,
    style: str = "cyan",
    border_style: str = "cyan"
):
    """Display content in a bordered panel."""
    _ensure_rich()
    console.print(_Panel(
        content,
        title=title,
        style=style,
        border_style=border_style
    ))


def success_panel(message: str, title: str = "Success"):
    """Display success message in green panel."""
    panel(message, title=title, style=Colors.SUCCESS, border_style=Colors.SUCCESS)


def error_panel(message: str, title: str = "Error"):
    """Display error message in red panel."""
    panel(message, title=title, style=Colors.ERROR, border_style=Colors.ERROR)


def warning_panel(message: str, title: str = "Warning"):
    """Display warning message in yellow panel."""
    panel(message, title=title, style=Colors.WARNING, border_style=Colors.WARNING)


# ============================================================================
# TABLES
# ============================================================================

def create_table(
    title: Optional[str] = None,
    columns: Optional[List[Dict[str, str]]] = None,
    show_header: bool = True,
    show_lines: bool = False
):
    """
    Create a Rich table with standard styling.
    
    Args:
        title: Optional table title
        columns: List of dicts with 'name', 'style', 'justify' keys
        show_header: Whether to show column headers
        show_lines: Whether to show row lines
        
    Returns:
        Configured Rich Table instance
        
    Example:
        table = create_table("Servers", [
            {"name": "Name", "style": "cyan"},
            {"name": "Host", "style": "green"},
        ])
        table.add_row("prod", "10.0.0.1")
        print_table(table)
    """
    _ensure_rich()
    table = _Table(
        title=title,
        show_header=show_header,
        show_lines=show_lines,
        header_style="bold cyan"
    )
    
    if columns:
        for col in columns:
            table.add_column(
                col.get('name', ''),
                style=col.get('style', 'white'),
                justify=col.get('justify', 'left')
            )
    
    return table


def print_table(table):
    """Print a Rich table to console."""
    console.print(table)


def format_db_output(stdout: str, query_type: Optional[str] = None) -> None:
    """
    Format database query output with minimal colors for token efficiency.
    
    Only highlights critical schema elements:
    - PRI keys (bold)
    - auto_increment (dim)
    
    Args:
        stdout: Raw tab-separated output from database
        query_type: Optional hint ('describe', 'select', 'show')
    """
    if not stdout or not stdout.strip():
        return
    
    lines = stdout.strip().split('\n')
    if not lines:
        return
    
    # Parse header and rows
    header = lines[0].split('\t')
    rows = [line.split('\t') for line in lines[1:] if line.strip()]
    
    if not rows:
        # Just header, no data
        raw_print(stdout)
        return
    
    # Detect query type from headers if not provided
    if not query_type:
        header_lower = [h.lower() for h in header]
        if 'field' in header_lower and 'type' in header_lower:
            query_type = 'describe'
        else:
            query_type = 'select'
    
    # Create minimal table - no box, no colors on columns
    _ensure_rich()
    table = _Table(
        show_header=True,
        show_lines=False,
        header_style="bold",  # Just bold headers, no color
        box=None,
        padding=(0, 1),
    )
    
    # Add columns - no styling (saves tokens)
    for col_name in header:
        table.add_column(col_name)
    
    # Add rows - only highlight critical info
    for row in rows:
        formatted_row = []
        for i, val in enumerate(row):
            if i >= len(header):
                continue
                
            col_lower = header[i].lower() if i < len(header) else ''
            
            # Only color truly critical things (saves tokens)
            if query_type == 'describe':
                if col_lower == 'key' and val == 'PRI':
                    val = "[bold]PRI[/]"  # Primary keys are important
                elif col_lower == 'extra' and 'auto_increment' in val.lower():
                    val = "[dim]AI[/]"  # Shorten to save tokens
            
            formatted_row.append(val)
        
        # Ensure row has same number of columns as header
        while len(formatted_row) < len(header):
            formatted_row.append('')
        
        table.add_row(*formatted_row[:len(header)])
    
    console.print(table)


def format_db_output_plain(stdout: str) -> None:
    """
    Print database output as-is for scripting/piping.
    No colors, no formatting, just raw output.
    """
    if stdout:
        raw_print(stdout)


# ============================================================================
# PROGRESS INDICATORS
# ============================================================================

def create_progress():
    """Create a progress bar with standard styling."""
    _ensure_rich()
    return _Progress(
        _SpinnerColumn(),
        _TextColumn("[progress.description]{task.description}"),
        _BarColumn(),
        _TaskProgressColumn(),
        console=console
    )


def create_spinner(message: str = "Working...") -> "SpinnerContext":
    """Create a spinner for indeterminate operations.
    
    Args:
        message: The message to display next to the spinner
        
    Returns:
        A context manager that shows a spinner while active
    """
    return SpinnerContext(message)


class SpinnerContext:
    """Context manager for showing a spinner with a message."""
    
    def __init__(self, message: str):
        self.message = message
        self.status = None
    
    def __enter__(self):
        self.status = console.status(f"[{Colors.INFO}]{self.message}[/{Colors.INFO}]", spinner="dots")
        self.status.__enter__()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.status:
            self.status.__exit__(exc_type, exc_val, exc_tb)
        return False


# ============================================================================
# SYNTAX HIGHLIGHTING
# ============================================================================

def print_code(
    code: str,
    language: str = "python",
    theme: str = "monokai",
    line_numbers: bool = False
):
    """Print code with syntax highlighting."""
    Syntax = _get_syntax()
    syntax = Syntax(
        code,
        language,
        theme=theme,
        line_numbers=line_numbers,
        word_wrap=True
    )
    console.print(syntax)


def print_json(data: Union[dict, list], indent: int = 2):
    """Print JSON with syntax highlighting."""
    import json
    json_str = json.dumps(data, indent=indent)
    print_code(json_str, language="json")


def print_yaml(yaml_content: str):
    """Print YAML with syntax highlighting."""
    print_code(yaml_content, language="yaml")


def print_sql(sql: str):
    """Print SQL with syntax highlighting."""
    print_code(sql, language="sql")


def print_path(path: Union[str, Path]):
    """Print file path with highlighting."""
    console.print(f"[{Colors.PATH}]{path}[/{Colors.PATH}]")


# ============================================================================
# TREE VIEWS
# ============================================================================

def create_tree(label: str, guide_style: str = "cyan"):
    """Create a tree structure for hierarchical data."""
    _ensure_rich()
    return _Tree(label, guide_style=guide_style)


# ============================================================================
# MARKDOWN
# ============================================================================

def print_markdown(markdown_text: str):
    """Print markdown with rich formatting."""
    Markdown = _get_markdown()
    md = Markdown(markdown_text)
    console.print(md)


# ============================================================================
# STATUS INDICATORS
# ============================================================================

def status_icon(is_good: bool) -> str:
    """Return colored status icon."""
    if is_good:
        mark = _safe_symbol("✓", "+")
        return f"[{Colors.STATUS_GOOD}]{mark}[/{Colors.STATUS_GOOD}]"
    else:
        mark = _safe_symbol("✗", "x")
        return f"[{Colors.STATUS_BAD}]{mark}[/{Colors.STATUS_BAD}]"


def status_text(text: str, is_good: bool) -> str:
    """Return colored status text."""
    color = Colors.STATUS_GOOD if is_good else Colors.STATUS_BAD
    return f"[{color}]{text}[/{color}]"


# ============================================================================
# SERVER-SPECIFIC HELPERS
# ============================================================================

def print_server_info(name: str, config: Dict[str, Any]):
    """Print formatted server information."""
    table = create_table(
        title=f"Server: {name}",
        columns=[
            {"name": "Property", "style": "cyan"},
            {"name": "Value", "style": "green"}
        ]
    )
    
    table.add_row("Host", config.get('host', 'N/A'))
    table.add_row("User", config.get('user', 'N/A'))
    table.add_row("Port", str(config.get('port', 22)))
    
    if 'database' in config:
        db = config['database']
        table.add_row("Database", f"{db.get('type', 'N/A')} ({db.get('name', 'N/A')})")
    
    if 'metadata' in config:
        meta = config['metadata']
        if meta.get('os'):
            table.add_row("OS", meta['os'])
        if meta.get('php_version'):
            table.add_row("PHP", meta['php_version'])
    
    print_table(table)


def print_tunnel_status(tunnel_info: Dict[str, Any], server_name: str):
    """Print formatted tunnel status."""
    table = create_table(
        title=f"Tunnel Status: {server_name}",
        columns=[
            {"name": "Property", "style": "cyan"},
            {"name": "Value", "style": "green"}
        ]
    )
    
    table.add_row("Status", status_text("RUNNING", True))
    table.add_row("Local Endpoint", f"127.0.0.1:{tunnel_info['local_port']}")
    table.add_row("Process ID", str(tunnel_info['pid']))
    table.add_row("Started At", tunnel_info.get('started_at', 'Unknown'))
    
    print_table(table)


# ============================================================================
# CONFIRMATION HELPERS
# ============================================================================
# OPERATION CONFIRMATION SYSTEM
# ============================================================================

# Operation type levels (lower number = more dangerous)
OPERATION_LEVELS = {
    'critical': 1,   # Most dangerous: DROP, DELETE, rm, service stop
    'standard': 2,   # State-changing: CREATE, UPDATE, uploads, config changes
    'verbose': 3,    # All operations including reads
}

# Confirmation level thresholds
# 'critical' level: only confirm critical operations (level <= 1)
# 'standard' level: confirm standard and critical (level <= 2)
# 'verbose' level: confirm all operations (level <= 3)
CONFIRMATION_THRESHOLDS = {
    'critical': 1,
    'standard': 2,
    'verbose': 3,
}


def requires_confirmation(
    operation_type: str,
    confirmation_level: str,
    execution_mode: str,
    auto_confirm: bool = False
) -> bool:
    """
    Determine if an operation requires confirmation.
    
    Args:
        operation_type: Type of operation ('critical', 'standard', or 'verbose')
        confirmation_level: Configured confirmation level
        execution_mode: Configured execution mode ('interactive' or 'auto')
        auto_confirm: If True (--yes flag), bypass confirmation
        
    Returns:
        True if confirmation is required, False otherwise
    """
    # --yes flag bypasses all confirmation
    if auto_confirm:
        return False
    
    # Auto mode bypasses confirmation
    if execution_mode == 'auto':
        return False
    
    # Check if operation level meets the confirmation threshold
    op_level = OPERATION_LEVELS.get(operation_type, 2)  # Default to standard
    threshold = CONFIRMATION_THRESHOLDS.get(confirmation_level, 2)  # Default to standard
    
    return op_level <= threshold


def confirm_operation(
    operation_name: str,
    operation_type: str = 'standard',
    details: Optional[str] = None,
    host: Optional[str] = None,
    app: Optional[str] = None,
    auto_confirm: bool = False,
    force_confirm: bool = False,
) -> bool:
    """
    Prompt user for confirmation based on configured level.
    
    This function checks the global execution mode and confirmation level
    settings, then prompts the user if confirmation is required.
    
    Args:
        operation_name: Name/description of the operation (e.g., "DROP DATABASE production_db")
        operation_type: Type of operation - 'critical', 'standard', or 'verbose'
        details: Optional additional details to display
        host: Optional host name for context
        app: Optional app name for context
        auto_confirm: If True (--yes flag), bypass confirmation
        force_confirm: If True (--confirm flag), force confirmation even in auto mode
        
    Returns:
        True if operation should proceed, False otherwise
        
    Operation Types:
        - 'critical': Destructive operations (DROP, DELETE, rm, service stop)
        - 'standard': State-changing operations (CREATE, UPDATE, uploads)
        - 'verbose': All operations including read-only (SELECT, downloads)
    """
    from navig.config import get_config_manager
    
    config_manager = get_config_manager()
    execution_mode = config_manager.get_execution_mode()
    confirmation_level = config_manager.get_confirmation_level()
    
    # --confirm flag forces interactive mode for this command
    if force_confirm:
        execution_mode = 'interactive'
        auto_confirm = False
    
    # Check if confirmation is needed
    if not requires_confirmation(operation_type, confirmation_level, execution_mode, auto_confirm):
        return True
    
    # Build confirmation message
    console.print()
    
    # Choose icon and color based on operation type
    if operation_type == 'critical':
        icon = "⚠️"
        title_color = Colors.ERROR
        title_text = "Confirm DESTRUCTIVE operation"
    elif operation_type == 'standard':
        icon = "📝"
        title_color = Colors.WARNING
        title_text = "Confirm operation"
    else:
        icon = "ℹ️"
        title_color = Colors.INFO
        title_text = "Confirm action"
    
    console.print(f"[{title_color}]{icon} {title_text}:[/{title_color}]")
    console.print(f"  [bold]{operation_name}[/bold]")
    
    if host:
        console.print(f"  [{Colors.DIM}]Host:[/{Colors.DIM}] [{Colors.SERVER}]{host}[/{Colors.SERVER}]")
    if app:
        console.print(f"  [{Colors.DIM}]App:[/{Colors.DIM}] [{Colors.ACCENT}]{app}[/{Colors.ACCENT}]")
    if details:
        console.print(f"  [{Colors.DIM}]{details}[/{Colors.DIM}]")
    
    console.print()
    
    # Default to Yes, except for critical operations (keep No for destructive ops)
    default = False if operation_type == 'critical' else True
    
    return confirm_action("Are you sure you want to proceed?", default=default)


def classify_command(command: str) -> str:
    """
    Classify a shell command by its danger level.
    
    Args:
        command: Shell command to classify
        
    Returns:
        'critical', 'standard', or 'verbose'
    """
    command_lower = command.lower().strip()
    
    # Critical patterns (destructive operations)
    critical_patterns = [
        'rm ', 'rm\t', 'rmdir', 'rm -rf', 'rm -r',
        'drop database', 'drop table', 'truncate',
        'delete from', 'delete ',
        'systemctl stop', 'systemctl disable',
        'service stop', 'service disable',
        'shutdown', 'reboot', 'halt', 'poweroff',
        'dd if=', 'mkfs', 'fdisk', 'parted',
        'userdel', 'groupdel',
        ':(){:|:&};:',  # Fork bomb
    ]
    
    for pattern in critical_patterns:
        if pattern in command_lower:
            return 'critical'
    
    # Standard patterns (state-changing operations)
    standard_patterns = [
        'create database', 'create table', 'alter table',
        'insert into', 'update ', 'replace into',
        'chmod', 'chown', 'chgrp',
        'mv ', 'cp ', 'ln ',
        'systemctl start', 'systemctl restart', 'systemctl enable',
        'service start', 'service restart',
        'apt install', 'apt remove', 'apt upgrade',
        'yum install', 'yum remove', 'dnf install',
        'pip install', 'npm install', 'composer install',
        'git push', 'git reset', 'git checkout',
        'mysql ', 'psql ', 'redis-cli',
        'certbot', 'ufw ', 'iptables',
        'crontab', 'at ',
    ]
    
    for pattern in standard_patterns:
        if pattern in command_lower:
            return 'standard'
    
    # Default to verbose (read-only or unknown)
    return 'verbose'


def classify_sql(query: str) -> str:
    """
    Classify an SQL query by its danger level.
    
    Args:
        query: SQL query to classify
        
    Returns:
        'critical', 'standard', or 'verbose'
    """
    query_upper = query.upper().strip()
    
    # Critical SQL operations
    if any(kw in query_upper for kw in ['DROP', 'TRUNCATE', 'DELETE']):
        return 'critical'
    
    # Standard SQL operations
    if any(kw in query_upper for kw in ['CREATE', 'ALTER', 'INSERT', 'UPDATE', 'REPLACE', 'GRANT', 'REVOKE']):
        return 'standard'
    
    # Verbose (read-only)
    return 'verbose'


def confirm_action(message: str, default: bool = True) -> bool:
    """
    Ask user for confirmation with rich formatting.
    
    Args:
        message: Question to ask
        default: Default answer if user presses Enter (default: True)
        
    Returns:
        True if user confirms, False otherwise
    """
    from rich.prompt import Confirm
    return Confirm.ask(f"[{Colors.PROMPT}]{message}[/{Colors.PROMPT}]", default=default)


def prompt_input(message: str, default: Optional[str] = None, password: bool = False) -> str:
    """
    Prompt user for input with rich formatting.
    
    Args:
        message: Prompt message
        default: Default value
        password: Whether to hide input
        
    Returns:
        User input string
    """
    from rich.prompt import Prompt
    return Prompt.ask(f"[{Colors.PROMPT}]{message}[/{Colors.PROMPT}]", default=default, password=password)


def prompt_choice(message: str, choices: List[str], default: Optional[str] = None) -> str:
    """
    Prompt user to choose from list of options.
    
    Args:
        message: Prompt message
        choices: List of valid choices
        default: Default choice
        
    Returns:
        Selected choice
    """
    from rich.prompt import Prompt
    return Prompt.ask(f"[{Colors.PROMPT}]{message}[/{Colors.PROMPT}]", choices=choices, default=default)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def clear():
    """Clear the console screen."""
    console.clear()


def rule(title: Optional[str] = None, style: str = "cyan"):
    """Print a horizontal rule with optional title."""
    console.rule(title, style=style)


def newline(count: int = 1):
    """Print one or more newlines."""
    for _ in range(count):
        console.print()


def get_console():
    """Get the global console instance for advanced usage."""
    return console
