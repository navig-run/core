---
name: "git-basics"
description: "Common Git version control operations."
user-invocable: true
version: "1.0.0"
category: "devops"
risk-level: "safe"

navig-commands:
  - name: "git-status"
    syntax: "navig git status"
    description: "Show working tree status."

  - name: "git-log"
    syntax: "navig git log"
    description: "Show commit logs."

  - name: "git-commit"
    syntax: "navig git commit -m <message>"
    description: "Commit changes."
    risk: "moderate"

examples:
  - user: "What changed?"
    thought: "User wants to see status."
    command: "navig git status"

  - user: "Show me history"
    thought: "User wants to see logs."
    command: "navig git log"
---

# Git Basics

Simple wrap around git CLI.
