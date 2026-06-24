# `navig agent install`

Initializes the NAVIG agent runtime on the local machine. Creates the configuration
directory structure under `~/.navig/agent/` and writes `config.yaml` with the
specified personality, operating mode, and optional Telegram integration flag.

Does **not** start the agent process. After install, use `navig agent start` for
a foreground interactive session, or `navig service start` for the background
Telegram/gateway daemon.

---

## Usage

```
navig agent install [OPTIONS]
```

---

## Options

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--personality` | `-p` | `TEXT` | `friendly` | Default personality profile. Choices: `friendly`, `professional`, `witty`, `paranoid`, `minimal` |
| `--mode` | `-m` | `TEXT` | `supervised` | Operating mode. Choices: `autonomous`, `supervised`, `observe-only` |
| `--telegram` | | flag | `false` | Enable Telegram integration in the generated config |
| `--force` | `-f` | flag | `false` | Overwrite an existing `config.yaml` without prompting |
| `--help` | | flag | | Print usage and exit |

---

## Examples

**Minimal install with defaults (supervised mode, friendly personality):**
```bash
navig agent install
```

**Install with a specific personality and mode:**
```bash
navig agent install --personality professional --mode autonomous
```

**Install with Telegram integration enabled:**
```bash
navig agent install --mode supervised --telegram
```

**Re-install over an existing configuration:**
```bash
navig agent install --force
```

---

## Expected Output

A successful run prints:

```
✓ Agent mode installed!
  Config: /home/user/.navig/agent/config.yaml
  Personality: friendly
  Mode: supervised

ℹ Next steps:
  1. Edit config: navig agent config
  2. Start agent: navig agent start
```

The following directory structure is created:

```
~/.navig/agent/
├── config.yaml          # Primary agent configuration
├── workspace/           # Agent working directory
├── personalities/       # Custom personality overrides
└── logs/                # Agent-specific log files
```

---

## Error Handling

| Condition | Message | Resolution |
|-----------|---------|------------|
| Config already exists | `Agent already installed. Use --force to overwrite.` | Add `--force` to overwrite, or edit the existing config with `navig agent config` |
| Import error (`AgentConfig`) | `Installation failed: <traceback>` | Run `pip install -e .` to ensure the package is installed in development mode |
| Permission denied on `~/.navig/` | `Installation failed: [Errno 13] Permission denied` | Check write permissions on the home directory; on Windows ensure the process is not blocked by AV |

---

## Related Commands

| Command | Purpose |
|---------|---------|
| `navig agent config` | Edit the installed configuration interactively |
| `navig agent start` | Start the foreground agent process (requires prior install) |
| `navig agent status` | Show foreground agent state plus daemon-backed Telegram/gateway state |
| `navig agent personality` | Manage and switch personality profiles |
| `navig service install` | Write daemon runtime files under `~/.navig/` |
| `navig service start` | Launch the background daemon for Telegram/gateway workers |
