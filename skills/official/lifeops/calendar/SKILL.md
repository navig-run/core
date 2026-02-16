---
name: calendar-cli
description: Command-line calendar management using 'khal'.
metadata:
  navig:
    emoji: 📅
    requires:
      bins: [khal]
      config: [khal.conf]
---

# Calendar CLI Skill

Manage your schedule, appointments, and agenda from the terminal using `khal`.

## Core Commands

### View Schedule
```bash
# Show agenda for today and tomorrow
khal list

# Show agenda for next 7 days
khal list today 7d

# Show specific date
khal list 2024-03-25
```

### Add Event
Natural language support for quick entry.

```bash
# Quick add
khal new "Lunch with Team tomorrow 12:00 to 13:00"

# Specific calendar
khal new -a work "Meeting with Client 14:00"

# Interactive add (if supported by agent/terminal)
khal new
```

### Edit & Delete
```bash
# Search for an event to get details
khal search "Lunch"

# Delete an event (interactive confirmation usually required, use --force if available in config or script wrapper)
# Note: khal does not have a direct 'delete by ID' aimed at automation easily without interactive selection, 
# so 'search' + manual intervention is often safest for agents unless using a specific wrapper.
```

## Integration with LifeOS
Combine with `task-warrior` for a complete daily plan:
1. Run `khal list today` to see hard landscape (meetings).
2. Run `task next` to fill gaps with soft landscape (todos).

## Configuration
Requires configured `~/.config/khal/config` pointing to local vdirsyncer directories or local ics files.



