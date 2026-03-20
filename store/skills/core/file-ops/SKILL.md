---
name: "file-operations"
description: "Essential file system operations."
user-invocable: true
version: "1.0.0"
category: "system"
risk-level: "moderate"

navig-commands:
  - name: "list-files"
    syntax: "navig fs list <path>"
    description: "List files in a directory."
    risk: "safe"

  - name: "read-file"
    syntax: "navig fs read <path>"
    description: "Read file contents."
    risk: "safe"

  - name: "write-file"
    syntax: "navig fs write <path> <content>"
    description: "Write content to a file."
    risk: "destructive"
    confirmation_msg: "Overwrite file at {path}?"

examples:
  - user: "List documents"
    thought: "User wants to see files in current dir."
    command: "navig fs list ."

  - user: "Read the readme"
    thought: "User wants to read README.md."
    command: "navig fs read README.md"
---

# File Operations

Standard filesystem manipulation capabilities.


