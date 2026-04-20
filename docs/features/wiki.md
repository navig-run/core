# Wiki Module (`navig wiki`)

The NAVIG wiki module manages project documentation in `.navig/wiki/`.
It provides structured storage, full-text search, and AI-powered inbox processing
to keep project knowledge organised and discoverable.

---

## Quick Start

```bash
# 1. Initialise wiki in your project
navig wiki init

# 2. Add notes to the inbox
navig wiki add my-notes.md

# 3. Let AI categorise inbox items
navig wiki inbox process

# 4. Search your knowledge base
navig wiki search "database schema"
```

---

## Directory Structure

```
.navig/wiki/
├── inbox/           # Staging area — drop files here
├── .meta/           # Config and index (hidden)
│   ├── config.yaml
│   ├── index.md
│   └── glossary.md
├── knowledge/       # Encyclopedia and concepts
│   ├── concepts/
│   ├── domain/
│   ├── guides/
│   └── resources/
├── technical/       # Technical documentation
│   ├── architecture/
│   ├── api/
│   ├── database/
│   ├── decisions/   # ADRs
│   └── troubleshooting/
├── hub/             # Project command centre
│   ├── roadmap/
│   ├── planning/
│   ├── tasks/
│   ├── changelog/
│   └── retrospectives/
├── external/        # External-facing content
│   ├── business/
│   ├── marketing/
│   └── press/
└── archive/         # Archived content, auto-organised by year
```

---

## Commands

### Initialisation

```bash
navig wiki init                    # Create project wiki
navig wiki init --global           # Create global wiki (~/.navig/wiki/)
navig wiki init --force            # Reinitialise
```

### Content Management

```bash
navig wiki list                    # List all pages
navig wiki list knowledge          # List pages in a folder
navig wiki show <page>             # View page
navig wiki show <page> --raw       # View raw Markdown

navig wiki add <file>              # Add file to inbox
navig wiki add <file> --folder hub/tasks   # Add to a specific folder

navig wiki edit <page>             # Open page in editor
navig wiki remove <page>           # Archive page
navig wiki remove <page> --delete  # Permanently delete

navig wiki search <query>          # Full-text search
```

### Inbox Processing

Drop files into `.navig/wiki/inbox/` then process them:

```bash
navig wiki inbox                   # List pending items
navig wiki inbox process           # AI-categorise all items
navig wiki inbox process --auto    # Auto-move to suggested folders
navig wiki inbox process <file>    # Process one file
```

AI categorisation uses keywords to route content:

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
| guide, tutorial, how to | `knowledge/guides/` |
| concept, definition | `knowledge/concepts/` |

### Publishing

```bash
navig wiki publish                 # Export public content
navig wiki publish --preview       # Preview what would be exported
navig wiki publish --all           # Include private content
navig wiki publish --output <dir>  # Custom output directory
```

Visibility is controlled by `.navig/wiki/knowledge/.visibility`:

```yaml
visibility: public    # or "private"
```

### Sync

```bash
navig wiki sync                    # Sync project wiki with global wiki
```

---

## Wiki Links

Use `[[wiki-links]]` to cross-reference pages:

```markdown
See [[architecture/overview]] for system design.
Related: [[domain/user-personas|Our Users]]
This implements [[decisions/adr-001]].

# Reference the global wiki
See [[global:tools/docker]] for Docker setup.
```

**Resolution rules:**
1. Exact path: `[[folder/page]]` → `folder/page.md`
2. Fuzzy search: `[[page]]` → searches all folders for `page.md`
3. Display text: `[[path|Display Text]]`

---

## Global vs Project Wiki

```
~/.navig/wiki/           # Global — shared knowledge across all projects
.navig/wiki/             # Project — project-specific docs
```

Project wiki is checked first; falls back to global wiki for cross-wiki references.

---

## Configuration

`.navig/wiki/.meta/config.yaml`:

```yaml
wiki:
  version: "1.0"
  default_language: en
  link_style: wiki           # [[wiki-links]]

  ai:
    auto_process_inbox: true
    auto_categorise: true
    auto_move: false         # Require confirmation before moving

  cleanup:
    archive_after_days: 365

  publish:
    default_visibility: private
    exclude_patterns:
      - "*.draft.md"
      - "_*"
      - ".meta/*"
```

---

## Best Practices

- **Drop files in inbox** and let AI categorise — avoid manually placing files.
- **Use wiki-links** (`[[page]]`) for cross-referencing instead of relative paths.
- **Keep `knowledge/` non-technical** — pure concepts, domain language, guides.
- **Use `hub/` for planning** — roadmap, tasks, retrospectives.
- **Use `technical/` for devs** — APIs, architecture decisions (ADRs).
- **Set visibility** on `knowledge/` to control what gets published.

---

> See also: [HANDBOOK.md](../user/HANDBOOK.md) | [commands.md](../user/commands.md)
