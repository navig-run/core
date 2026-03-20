---
name: "disk-space-basic"
description: "Monitor system disk space and storage usage safely."
user-invocable: true
version: "1.0.0"
author: "NAVIG Core Team"
category: "system"
risk-level: "safe"
os: ["all"]
tags: ["storage", "disk", "monitoring", "system"]

navig-commands:
  - name: "check-disk"
    syntax: "navig system disk"
    description: "Show disk usage for all mounted filesystems."
    output-format: "table"
    
  - name: "check-directory"
    syntax: "navig system storage --path <path>"
    description: "Analyze storage usage for a specific directory."
    parameters:
      path:
        type: "string"
        description: "The absolute path to analyze"
        required: true

examples:
  - user: "How much space is left on the C drive?"
    thought: "User wants to check global disk usage."
    command: "navig system disk"
  
  - user: "Why is my projects folder so big?"
    thought: "User wants to analyze a specific directory. I should check the current working directory or ask, but here I'll check the projects folder."
    command: "navig system storage --path k:\\_PROJECTS_SMALL"

  - user: "Run a disk check"
    thought: "Ambiguous, but implies checking general disk status."
    command: "navig system disk"
---

# Disk Space Basic

This skill provides essential tools for monitoring disk usage. It is designed to be completely read-only and safe to run without confirmation.

## Commands

### `navig system disk`
Displays a summary of all mounted drives, their total size, used space, and available space.

### `navig system storage --path <path>`
Calculates the size of a specific directory and lists the largest subdirectories. This is useful for drilling down into what is consuming space.


