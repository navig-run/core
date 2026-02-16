# Help Text Management

## Overview

All NAVIG CLI help text is centralized for consistency and maintainability:

1. **`navig/cli.py` → `HELP_REGISTRY`**: Quick reference dictionary for the custom compact help display
2. **`navig/help_texts.py`**: Comprehensive module with dataclasses for full documentation

## Quick Reference: HELP_REGISTRY

The `HELP_REGISTRY` dictionary in `cli.py` provides the data for the compact help display shown when running commands without arguments (e.g., `navig db`).

```python
HELP_REGISTRY = {
    "db": {
        "desc": "Database operations (MySQL, PostgreSQL, SQLite)",
        "commands": {
            "list": "list databases",
            "show": "show database info or tables",
            "run": "run SQL query or open shell",
            # ...
        }
    },
    # ...
}
```

### Adding a New Command Group

1. Add entry to `HELP_REGISTRY` in `cli.py`:
```python
"mygroup": {
    "desc": "Short description of what this group does",
    "commands": {
        "list": "list all items",
        "add": "add new item",
        "remove": "remove an item",
    }
}
```

2. Create the Typer app with callback:
```python
mygroup_app = typer.Typer(
    help="Full description here",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(mygroup_app, name="mygroup")

@mygroup_app.callback()
def mygroup_callback(ctx: typer.Context):
    """Mygroup management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("mygroup", ctx)
        raise typer.Exit()
```

## Comprehensive Documentation: help_texts.py

For full documentation including epilogs (examples), use the `help_texts.py` module:

```python
from navig import help_texts as ht

# Access help text
print(ht.DB.description)      # "Database operations (MySQL, PostgreSQL, SQLite)."
print(ht.DB.epilog)           # Examples section
print(ht.DB_LIST.short_help)  # "list databases"
```

### Adding Help Text for New Commands

1. Add to `navig/help_texts.py`:
```python
MY_COMMAND = CommandHelp(
    short_help="brief description",
    description="Detailed description with period.",
    epilog="""
Examples:
  navig mygroup command arg1    # Description of example
  navig mygroup command --flag  # Another example
"""
)
```

2. Reference in command decorator (optional, for `--help` output):
```python
from navig import help_texts as ht

@mygroup_app.command("mycommand")
def mycommand():
    """Docstring for code documentation."""
    pass
```

## Standardization Rules

### Command Group Descriptions
- Format: `"Verb noun phrase"` (e.g., "Manage remote hosts")
- Start with: Manage, Execute, Control, View
- Use sentence case (capitalize first word only, except proper nouns)

**Examples:**
- ✅ `"Manage remote server connections"`
- ✅ `"Execute commands on remote hosts"`
- ❌ `"Host management"` (noun phrase)
- ❌ `"Manage Remote Hosts"` (Title Case)

### Subcommand Help Text
- Format: Verb phrase, no period, lowercase after first word
- **Examples:**
  - ✅ `"list configured hosts"`
  - ✅ `"add new host interactively"`
  - ❌ `"List Configured Hosts"` (Title Case)
  - ❌ `"list configured hosts."` (has period)

### Verb Consistency

Use these standard verbs across all commands:

| Action | Standard Verb | ❌ Avoid |
|--------|---------------|----------|
| Create new resource | `add` | create, register, new |
| Delete resource | `remove` | delete, destroy, drop |
| Show resources | `list` | show (for single), display |
| Modify resource | `edit` | modify, update, change |
| Verify functionality | `test` | check, verify, validate |
| Activate resource | `use` | switch, select, activate |
| Display details | `show` | display, view, get |
| Execute action | `run` | execute, start |

### Capitalization
- Sentence case everywhere
- Capitalize proper nouns: SSH, Docker, MySQL, PostgreSQL, HestiaCP, Nginx, Apache

## File Locations

| File | Purpose |
|------|---------|
| `navig/cli.py` | CLI definitions, `HELP_REGISTRY` dictionary |
| `navig/help_texts.py` | Comprehensive help text with dataclasses |
| `docs/development/help-text-management.md` | This documentation |

## Testing Help Output

```bash
# Test main help
navig --help

# Test each group
navig host
navig db
navig app
# etc.

# Test subcommand help
navig db list --help
navig host add --help
```

## Troubleshooting

### Help text doesn't appear in custom format
- Ensure the group name is in `HELP_REGISTRY`
- Check the callback calls `show_subcommand_help("groupname", ctx)`

### Command not showing in group help
- Add the command to `HELP_REGISTRY["groupname"]["commands"]`
- Make sure the command is not marked as `hidden=True`

### Typer's default help shows instead of custom
- The custom help shows when running `navig <group>` without `--help`
- `navig <group> --help` shows Typer's default format (expected behavior)


