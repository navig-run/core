---
applyTo: '**'
---

# NAVIG Wiki Module - AI Instructions

## Overview

The wiki module manages project documentation in `.navig/wiki/` with AI-powered inbox processing.

## Structure

```
.navig/wiki/
├── inbox/           # Staging area for unprocessed content
├── .meta/           # Wiki configuration & indexes (hidden)
│   ├── config.yaml  # Wiki settings
│   ├── index.md     # Auto-generated wiki index
│   └── glossary.md  # Project terminology
├── knowledge/       # 📚 Encyclopedia & Knowledge Base
│   ├── .visibility  # "public" or "private" (controls publishing)
│   ├── concepts/    # Core concepts, terminology
│   ├── domain/      # Business domain, entities, rules
│   ├── guides/      # User guides, tutorials
│   └── resources/   # Links, references, research
├── technical/       # 🔧 Technical Documentation
│   ├── architecture/    # System design, diagrams
│   ├── api/             # API docs, endpoints
│   ├── database/        # Schema, migrations
│   ├── decisions/       # ADRs (Architectural Decision Records)
│   └── troubleshooting/ # Known issues, solutions
├── hub/             # 🎯 Project Command Center (PIM)
│   ├── roadmap/         # Vision, milestones, releases
│   ├── planning/        # Sprint notes, backlog, priorities
│   ├── tasks/           # Active tasks, in-progress work
│   ├── changelog/       # Version history, release notes
│   └── retrospectives/  # Lessons learned, improvements
├── external/        # 🌐 External-Facing Content
│   ├── business/        # Investor materials, strategy
│   ├── marketing/       # Campaigns, copy, social
│   └── press/           # Press kit, announcements
└── archive/         # 📦 Archived content
    └── {year}/          # Auto-organized by year
```

## Commands

```bash
# Initialize
navig wiki init                    # Create project wiki
navig wiki init --global           # Create global wiki (~/.navig/wiki/)
navig wiki init --force            # Reinitialize

# Content Management
navig wiki list                    # List all pages
navig wiki list knowledge          # List pages in folder
navig wiki show <page>             # View page
navig wiki show <page> --raw       # View raw markdown
navig wiki add <file>              # Add to inbox
navig wiki add <file> --folder hub/tasks  # Add to specific folder
navig wiki edit <page>             # Open in editor
navig wiki remove <page>           # Archive page
navig wiki remove <page> --delete  # Permanently delete
navig wiki search <query>          # Full-text search

# Inbox Processing
navig wiki inbox                   # List pending items
navig wiki inbox process           # AI-categorize items
navig wiki inbox process --auto    # Auto-move to suggested folders
navig wiki inbox process <file>    # Process specific file

# Wiki Links
navig wiki links                   # Show link statistics
navig wiki links broken            # Find broken links

# Publishing
navig wiki publish                 # Export public content
navig wiki publish --preview       # Preview what would be published
navig wiki publish --all           # Include private content
navig wiki publish --output <dir>  # Custom output directory

# Sync
navig wiki sync                    # Sync with global wiki
```

## Wiki Link Syntax

Use `[[wiki-links]]` to reference other pages:

```markdown
See [[architecture/overview]] for system design.
Related: [[domain/user-personas|Our Users]]
This implements [[decisions/adr-001]].

# Global wiki reference
See [[global:tools/docker]] for Docker setup.
```

### Resolution Rules

1. **Exact path**: `[[folder/page]]` → `folder/page.md`
2. **Fuzzy search**: `[[page]]` → Searches all folders for `page.md`
3. **Display text**: `[[path|Display Text]]` → Shows "Display Text"

## Content Categorization

When AI processes inbox items, it categorizes by keywords:

| Keywords | Destination |
|----------|-------------|
| api, endpoint, code, function | `technical/api/` |
| database, schema, migration | `technical/database/` |
| architecture, design, diagram | `technical/architecture/` |
| decision, adr, why | `technical/decisions/` |
| roadmap, milestone, version | `hub/roadmap/` |
| task, todo, sprint | `hub/tasks/` |
| changelog, release | `hub/changelog/` |
| investor, pitch, roi | `external/business/` |
| marketing, campaign, social | `external/marketing/` |
| guide, tutorial, how to | `knowledge/guides/` |
| concept, definition | `knowledge/concepts/` |

## Visibility & Publishing

The `knowledge/.visibility` file controls publishing:

```yaml
visibility: public    # or "private"
```

- **public**: Content can be exported via `navig wiki publish`
- **private**: Content stays internal

## Global vs Project Wiki

```
~/.navig/wiki/           # Global wiki (shared knowledge)
/project/.navig/wiki/    # Project wiki (project-specific)
```

### Resolution Priority

1. Project wiki checked first
2. Falls back to global wiki

### Cross-wiki References

```markdown
[[global:tools/docker]]      # Reference global wiki
[[project:other/page]]       # Reference another project (future)
```

## Configuration

Wiki config is in `.navig/wiki/.meta/config.yaml`:

```yaml
wiki:
  version: "1.0"
  default_language: en
  link_style: wiki           # Use [[wiki-links]]

  ai:
    auto_process_inbox: true
    auto_translate: true
    auto_categorize: true
    auto_move: false         # Require confirmation
    rewrite_style: preserve  # preserve | standardize | summarize

  cleanup:
    archive_after_days: 365

  publish:
    default_visibility: private
    exclude_patterns:
      - "*.draft.md"
      - "_*"
      - ".meta/*"
```

## Best Practices

1. **Drop files in inbox** → Let AI categorize
2. **Use wiki-links** → Easy cross-referencing
3. **Keep knowledge non-technical** → Pure concepts, no code
4. **Hub for planning** → Roadmap, tasks, retrospectives
5. **Technical for devs** → APIs, architecture, decisions
6. **Set visibility** → Control what gets published
