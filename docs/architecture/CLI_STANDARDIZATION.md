# NAVIG CLI Command Standardization

## Status: ✅ IMPLEMENTED

This document describes the canonical CLI pattern for NAVIG commands.
All commands follow the pattern below, with legacy commands deprecated but functional.

## Canonical Pattern
```
navig <resource> <action> [options] [arguments]
```

## Valid Resources (Frozen)
| Resource | Description | Status |
|----------|-------------|--------|
| `host` | Remote host configurations | ✅ Implemented |
| `server` | Server operations (web, services) | ✅ Implemented |
| `app` | Application configurations | ✅ Implemented |
| `db` | Database operations | ✅ Implemented |
| `file` | File system operations | ✅ Implemented |
| `task` | Workflows and scheduled tasks | ✅ Implemented (alias for workflow) |
| `backup` | Backup operations | ✅ Implemented |
| `tunnel` | SSH tunnel management | ✅ Implemented |
| `deploy` | Deployment operations | ⏳ Future |
| `log` | Log viewing and management | ✅ Implemented |
| `monitor` | Monitoring and health checks | ✅ Implemented |
| `security` | Security operations | ✅ Implemented |
| `config` | Configuration management | ✅ Implemented |
| `env` | Environment variables | ⏳ Future |
| `plugin` | Plugin management | ✅ Implemented |
| `workflow` | Workflow management | ✅ Implemented |

## Valid Actions (Frozen)
| Action | Description |
|--------|-------------|
| `add` | Create new resource |
| `list` | List resources |
| `show` | Show detailed information |
| `edit` | Modify existing resource |
| `update` | Update/sync resource |
| `remove` | Delete resource |
| `run` | Execute/start operation |
| `test` | Test/validate resource |
| `use` | Set active/default resource |

## Implemented Command Mapping

### Host Commands (`navig host`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig host list` | `navig host list` | ✅ Canonical |
| `navig host use <name>` | `navig host use <name>` | ✅ Canonical |
| `navig host use <name> --default` | `navig host use <name> --default` | ✅ NEW - sets default |
| `navig host current` | `navig host show --current` | ✅ Deprecated → show |
| `navig host default <name>` | `navig host use <name> --default` | ✅ Deprecated → use |
| `navig host add` | `navig host add` | ✅ Canonical |
| `navig host add <name> --from <source>` | `navig host add --from` | ✅ NEW - clone from |
| `navig host remove` | `navig host remove` | ✅ Canonical |
| `navig host inspect` | `navig host show --inspect` | ✅ Deprecated → show |
| `navig host edit` | `navig host edit` | ✅ Canonical |
| `navig host clone` | `navig host add --from` | ✅ Deprecated → add |
| `navig host test` | `navig host test` | ✅ Canonical |
| `navig host info` | `navig host show` | ✅ Deprecated → show |
| `navig host show` | `navig host show` | ✅ NEW Canonical |

### App Commands (`navig app`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig app list` | `navig app list` | ✅ Canonical |
| `navig app use` | `navig app use` | ✅ Canonical |
| `navig app current` | `navig app show --current` | ✅ Deprecated → show |
| `navig app add` | `navig app add` | ✅ Canonical |
| `navig app add --from <source>` | `navig app add --from` | ✅ NEW - clone from |
| `navig app remove` | `navig app remove` | ✅ Canonical |
| `navig app show` | `navig app show` | ✅ Canonical |
| `navig app edit` | `navig app edit` | ✅ Canonical |
| `navig app clone` | `navig app add --from` | ✅ Deprecated → add |
| `navig app info` | `navig app show` | ✅ Deprecated → show |

### Database Commands (`navig db`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig db show` | `navig db show` | ✅ NEW Canonical |
| `navig db show --tables` | `navig db show --tables` | ✅ NEW - show tables |
| `navig db show --containers` | `navig db show --containers` | ✅ NEW - show containers |
| `navig db show --users` | `navig db show --users` | ✅ NEW - show users |
| `navig db run <sql>` | `navig db run` | ✅ NEW Canonical |
| `navig db run --file` | `navig db run --file` | ✅ NEW - run SQL file |
| `navig db run --shell` | `navig db run --shell` | ✅ NEW - open shell |
| `navig db query` | `navig db run` | ⚠️ Keep for compatibility |
| `navig db shell` | `navig db run --shell` | ✅ Deprecated → run |
| `navig db containers` | `navig db show --containers` | ✅ Deprecated → show |
| `navig db users` | `navig db show --users` | ✅ Deprecated → show |
| `navig db list` | `navig db list` | ✅ Canonical |
| `navig db tables` | `navig db show --tables` | ⚠️ Keep for compatibility |
| `navig db dump` | `navig db dump` | ✅ Keep (specific operation) |
| `navig db restore` | `navig db restore` | ✅ Keep (specific operation) |
| `navig db shell` | `navig db run --shell` | ⚠️ Merge with `run` |
| `navig db containers` | `navig db list --containers` | ⚠️ Merge with `list` |
| `navig db users` | `navig db list --users` | ⚠️ Merge with `list` |
| `navig db optimize` | `navig db run --optimize` | ⚠️ Merge with `run` |
| `navig db repair` | `navig db run --repair` | ⚠️ Merge with `run` |

### Legacy DB Commands (Top-level, hidden)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig sql` | `navig db run` | 🔄 Already hidden |
| `navig sqlfile` | `navig db run --file` | 🔄 Already hidden |
| `navig db-list` | `navig db list` | 🔄 Deprecate |
| `navig db-tables` | `navig db list --tables` | 🔄 Deprecate |
| `navig db-databases` | `navig db list` | 🔄 Deprecate |
| `navig db-show-tables` | `navig db list --tables` | 🔄 Deprecate |
| `navig db-query` | `navig db run` | 🔄 Deprecate |
| `navig db-dump` | `navig backup add --type db` | 🔄 Deprecate |
| `navig db-shell` | `navig db run --shell` | 🔄 Deprecate |
| `navig db-optimize` | `navig db run --optimize` | 🔄 Deprecate |
| `navig db-repair` | `navig db run --repair` | 🔄 Deprecate |
| `navig db-users` | `navig db list --users` | 🔄 Deprecate |
| `navig db-containers` | `navig db list --containers` | 🔄 Deprecate |

### File Commands (`navig file`)
Currently scattered as top-level commands. Need to group under `file`:

| Current | Canonical | Status |
|---------|-----------|--------|
| `navig upload` | `navig file add <local> <remote>` | 🔄 Reorganize |
| `navig download` | `navig file show <remote> --download <local>` | 🔄 Reorganize |
| `navig list <path>` | `navig file list <path>` | 🔄 Reorganize |
| `navig ls <path>` | `navig file list <path>` | 🔄 Reorganize |
| `navig delete` | `navig file remove <path>` | 🔄 Reorganize |
| `navig mkdir` | `navig file add --dir <path>` | 🔄 Reorganize |
| `navig chmod` | `navig file edit <path> --mode <mode>` | 🔄 Reorganize |
| `navig chown` | `navig file edit <path> --owner <owner>` | 🔄 Reorganize |
| `navig cat` | `navig file show <path>` | 🔄 Reorganize |
| `navig write-file` | `navig file edit <path> --content` | 🔄 Reorganize |
| `navig tree` | `navig file list <path> --tree` | 🔄 Reorganize |

### Tunnel Commands (`navig tunnel`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig tunnel start` | `navig tunnel run` | ✅ Deprecated → run |
| `navig tunnel stop` | `navig tunnel remove` | ✅ Deprecated → remove |
| `navig tunnel restart` | `navig tunnel update` | ✅ Deprecated → update |
| `navig tunnel status` | `navig tunnel show` | ✅ Deprecated → show |
| `navig tunnel auto` | `navig tunnel auto` | ✅ Keep as-is |
| `navig tunnel run` | `navig tunnel run` | ✅ NEW Canonical |
| `navig tunnel remove` | `navig tunnel remove` | ✅ NEW Canonical |
| `navig tunnel update` | `navig tunnel update` | ✅ NEW Canonical |
| `navig tunnel show` | `navig tunnel show` | ✅ NEW Canonical |

### Monitor Commands (`navig monitor`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig monitor resources` | `navig monitor show --resources` | ✅ Deprecated → show |
| `navig monitor disk` | `navig monitor show --disk` | ✅ Deprecated → show |
| `navig monitor services` | `navig monitor show --services` | ✅ Deprecated → show |
| `navig monitor network` | `navig monitor show --network` | ✅ Deprecated → show |
| `navig monitor health` | `navig monitor show` | ✅ Deprecated → show |
| `navig monitor report` | `navig monitor run --report` | ✅ Deprecated → run |
| `navig monitor show` | `navig monitor show` | ✅ NEW Canonical |
| `navig monitor run` | `navig monitor run` | ✅ NEW Canonical |

### Security Commands (`navig security`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig security firewall` | `navig security show --firewall` | ✅ Hidden |
| `navig security fail2ban` | `navig security show --fail2ban` | ✅ Deprecated → show |
| `navig security unban` | `navig security edit --unban <ip>` | ✅ Deprecated → edit |
| `navig security ssh` | `navig security show --ssh` | ✅ Deprecated → show |
| `navig security updates` | `navig security show --updates` | ⚠️ Keep for now |
| `navig security connections` | `navig security show --connections` | ⚠️ Keep for now |
| `navig security scan` | `navig security run` | ⚠️ Keep for now |
| `navig security show` | `navig security show` | ✅ NEW Canonical |
| `navig security run` | `navig security run` | ✅ NEW Canonical |
| `navig security edit` | `navig security edit` | ✅ NEW Canonical |

### File Commands (`navig file`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig file add` | `navig file add` | ✅ NEW Canonical (upload/mkdir) |
| `navig file list` | `navig file list` | ✅ NEW Canonical |
| `navig file show` | `navig file show` | ✅ NEW Canonical (cat/download) |
| `navig file edit` | `navig file edit` | ✅ NEW Canonical (chmod/chown/write) |
| `navig file remove` | `navig file remove` | ✅ NEW Canonical |

### Log Commands (`navig log`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig log show` | `navig log show` | ✅ NEW Canonical |

### Server Commands (`navig server`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig server list` | `navig server list` | ✅ NEW Canonical (docker ps) |
| `navig server show` | `navig server show` | ✅ NEW Canonical (inspect/stats) |
| `navig server run` | `navig server run` | ✅ NEW Canonical (docker exec) |
| `navig server add` | `navig server add` | ✅ NEW Canonical (start container) |
| `navig server remove` | `navig server remove` | ✅ NEW Canonical (stop container) |
| `navig server update` | `navig server update` | ✅ NEW Canonical (restart) |

### Task Commands (`navig task`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig task list` | `navig workflow list` | ✅ Alias for workflow |
| `navig task show` | `navig workflow show` | ✅ Alias for workflow |
| `navig task run` | `navig workflow run` | ✅ Alias for workflow |
| `navig task test` | `navig workflow test` | ✅ Alias for workflow |
| `navig task add` | `navig workflow add` | ✅ Alias for workflow |
| `navig task remove` | `navig workflow remove` | ✅ Alias for workflow |
| `navig task edit` | `navig workflow edit` | ✅ Alias for workflow |

### Workflow Commands (`navig workflow`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig workflow validate` | `navig workflow test` | ✅ Deprecated → test |
| `navig workflow create` | `navig workflow add` | ✅ Deprecated → add |
| `navig workflow delete` | `navig workflow remove` | ✅ Deprecated → remove |
| `navig workflow test` | `navig workflow test` | ✅ NEW Canonical |
| `navig workflow add` | `navig workflow add` | ✅ NEW Canonical |
| `navig workflow remove` | `navig workflow remove` | ✅ NEW Canonical |

### Backup Commands (`navig backup`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig backup export` | `navig backup export` | ✅ Keep (specific operation) |
| `navig backup import` | `navig backup import` | ✅ Keep (specific operation) |
| `navig backup list` | `navig backup show` | ✅ Deprecated → show |
| `navig backup inspect` | `navig backup show <file>` | ✅ Deprecated → show |
| `navig backup delete` | `navig backup remove` | ✅ Deprecated → remove |
| `navig backup show` | `navig backup show` | ✅ NEW Canonical |
| `navig backup remove` | `navig backup remove` | ✅ NEW Canonical |

### Config Commands (`navig config`)
| Current | Canonical | Status |
|---------|-----------|--------|
| `navig config migrate` | `navig config migrate` | ✅ Keep (specific operation) |
| `navig config validate` | `navig config test` | ✅ Deprecated → test |
| `navig config show` | `navig config show` | ✅ Canonical |
| `navig config settings` | `navig config settings` | ✅ Keep |
| `navig config set` | `navig config set` | ✅ Keep |
| `navig config get` | `navig config get` | ✅ Keep |
| `navig config test` | `navig config test` | ✅ NEW Canonical |
| `navig config edit` | `navig config edit` | ✅ NEW Canonical |

## Implementation Summary

### Completed ✅
- All canonical actions (`add`, `list`, `show`, `edit`, `update`, `remove`, `run`, `test`, `use`) implemented
- New resource groups: `file`, `log`, `server`, `task` (alias for workflow)
- Deprecated commands hidden with warning messages
- Backward compatibility maintained - all old commands still work

### Deprecation Pattern
All deprecated commands:
1. Are marked `hidden=True` (not shown in --help)
2. Print deprecation warning with canonical command suggestion
3. Still execute the original functionality

### Legacy Behavior
Old commands like `navig tunnel start` work as before but print:
```
⚠️  DEPRECATED: 'navig tunnel start' → Use 'navig tunnel run' instead
```


