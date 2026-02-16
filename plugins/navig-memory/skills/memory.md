---
name: "navig-memory"
description: "Long-term semantic memory and session context management."
user-invocable: true
version: "2.0.0"
category: "core"
risk-level: "safe"

navig-commands:
  - name: "recall"
    syntax: "navig memory recall <query>"
    description: "Search the knowledge graph for related facts, decisions, or files."
  
  - name: "memorize"
    syntax: "navig memory remember <content> --type <type>"
    description: "Store a new memory into the vault."
    parameters:
      type:
        type: "enum"
        description: "The category of memory to store."
        options: ["fact", "decision", "lesson", "task"]
        default: "fact"

  - name: "session-checkpoint"
    syntax: "navig memory checkpoint"
    description: "Snapshot the current workspace state and conversation context."

examples:
  - user: "What did we decide about the database?"
    thought: "User is asking for a past decision. I will search the memory vault."
    command: "navig memory recall 'database decision'"

  - user: "Save this: Always use UTC for timestamps"
    thought: "User explicitly wants to save a lesson."
    command: "navig memory remember 'Always use UTC for timestamps' --type lesson"
---

# NAVIG Memory 🧠

A superior, graph-based memory system that integrates tightly with the NAVIG workspace.

## Features vs ClawVault
- **Graph-Based**: Uses vector embeddings AND graph correlations, not just file search.
- **Zero-Config**: Uses the `.navig/memory` folder automatically.
- **Auto-Tagging**: analyzing the content to automatically apply `tags` and `entities`.


