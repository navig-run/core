---
name: check-disk-space
description: Check disk space on remote servers via NAVIG
user-invocable: true
navig-commands:
  - navig host use {host}
  - navig run "df -h"
examples:
  - "How much space on my Hetzner server?"
  - "Check disk usage on production"
  - "Is my server running out of space?"
---

# Check Disk Space

When user asks about disk space or storage on a server, use this skill.

## Steps

1. **Identify the host**: Extract server name from user's query (e.g., "example-vps", "production", "web-server")
2. **Switch host**: `navig host use {host}`
3. **Check disk space**: `navig run "df -h"`
4. **Parse and format**: Convert output to conversational response

## Parsing df -h Output

```
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1       100G   55G   45G  55% /
/dev/sdb1       200G  140G   60G  70% /home
```

Extract key information:
- Total space
- Used space
- Available space
- Percentage used
- Mount point (focus on / and /home)

## Response Format

**Good responses:**
- "Your Hetzner server has 45GB free on / (55% used) and 60GB free on /home (70% used)"
- "The production server has 45GB available (55% used)"

**Proactive suggestions:**
- If usage > 80%: "⚠️ Disk space is running low. Want me to find what's using the most space?"
- If usage > 90%: "🚨 Critical! Only 10% space left. I can help clean up Docker images or old logs."

## Related Commands

If user asks "what's using space?":
- `navig run "du -sh /*"` - Check directory sizes
- `navig run "docker system df"` - Check Docker space usage
- `navig run "find /var/log -type f -size +100M"` - Find large log files

## Examples

**Example 1:**
- **User:** "How much space on my Hetzner server?"
- **Action:** `navig host use example-vps && navig run "df -h"`
- **Response:** "Your Hetzner server has 45GB free on / (55% used) and 60GB free on /home (70% used). Looks healthy! 👍"

**Example 2:**
- **User:** "Is production server full?"
- **Action:** `navig host use production && navig run "df -h"`
- **Response:** "⚠️ Production server is at 87% capacity (13GB free). Want me to check what's using the space?"

**Example 3:**
- **User:** "Check disk on all servers"
- **Action:** Loop through configured hosts
- **Response:** 
  ```
  📊 Disk Space Summary:
  • example-vps: 45GB free (55% used) ✅
  • production: 13GB free (87% used) ⚠️
  • staging: 120GB free (40% used) ✅
  ```

## Error Handling

- **Host not found**: "I don't have a server configured with that name. Available hosts: example-vps, production, staging"
- **Connection failed**: "Can't connect to {host}. Check if the server is online and SSH keys are configured."
- **Command failed**: "Error running disk check: {error_message}"


