---
name: task-warrior
description: Local task management using the powerful TaskWarrior CLI.
metadata:
  navig:
    emoji: ✅
    requires:
      bins: [task]
---

# Task Warrior Skill

Manage tasks entirely from the command line with TaskWarrior. Allows filtering, prioritizing, and tagging tasks without leaving the terminal.

## Core Commands

### Adding Tasks
```bash
# Basic task
task add Buy milk

# With project and tag
task add Update documentation project:navig +docs

# With due date and priority
task add Submit report due:friday priority:H
```
*Note: `task add` returns the ID of the created task.*

### Listing & Filtering
```bash
# List pending tasks (default view)
task list

# Filter by project
task list project:navig

# Filter by status
task completed
```

### Task Management
```bash
# Mark as done
task <id> done

# Start working on a task (tracks time)
task <id> start

# Modify existing task
task <id> modify due:tomorrow
```

## Advanced

### Reports
```bash
# Burndown chart
task burndown.daily

# History
task history
```

### Contexts
You can define contexts in `.taskrc` to filter tasks automatically.
```bash
# Switch to 'work' context
task context work

# Clear context
task context none
```

## Best Practices
1. **Use IDs**: Always list tasks first to get the ID, then operate on that ID. IDs change as tasks are completed!
2. **Projects**: Organize tasks into projects (`project:name`) for better filtering.
3. **Tags**: Use `+tag` to add tags and `-tag` to remove them.
