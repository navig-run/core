# NAVIG Workspace Ownership

This document defines where NAVIG files belong and which location wins when duplicates exist.

## Scope

NAVIG uses two `.navig/` scopes:

- User scope: `~/.navig/`
- Project scope: `<project>/.navig/`

## Ownership Rules

| File Category | Authoritative Location | Project `.navig/` Allowed |
|---|---|---|
| Personal/state workspace files (`SOUL.md`, `HEARTBEAT.md`, `IDENTITY.md`, `USER.md`, `AGENTS.md`, `TOOLS.md`, `BOOTSTRAP.md`, related personal state json) | `~/.navig/workspace/` | No (legacy copies may exist, but are not authoritative) |
| Project config (`hosts/`, `apps/`, project `config.yaml`, project cache, plans, project memory/index artifacts) | `<project>/.navig/` | Yes |

## Resolution Order

When both user and project workspace files exist with the same personal filename:

1. Use `~/.navig/workspace/<file>` (source of truth)
2. Use `<project>/.navig/workspace/<file>` only as legacy fallback

NAVIG never auto-deletes either copy.

## Init and Migration Behavior

- During workspace template initialization, personal/state files are created only in `~/.navig/workspace/`.
- If duplicate personal files are detected in `<project>/.navig/workspace/`, NAVIG prints a warning and keeps both files.
- Duplicate cleanup is manual by design to avoid accidental data loss.

## Why This Separation Exists

- User-level files represent identity, behavior, and long-lived personal state.
- Project-level files represent project-local configuration and runtime artifacts.
- Explicit separation removes ambiguity and prevents stale duplicate edits.
