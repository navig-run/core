---
name: discord-bot
description: Send notifications, alerts, and deployment status updates to Discord channels.
metadata:
  navig:
    emoji: 📢
    requires:
      config: [channels.discord]
---

# Discord Bot Skill

Send messages and notifications to Discord. Ideal for deployment alerts, system status reports, and agent-to-human communication.

## Core Actions

### Send Message
Send a simple text message to a channel.

**Payload:**
```json
{
  "action": "sendMessage",
  "to": "channel:<channel_id>",
  "content": "🚀 **Deployment Successful**\nProject: `navig-core`\nEnvironment: `production`"
}
```

### Send Embed / Rich Content
Use this for structured status reports.

**Payload:**
```json
{
  "action": "sendMessage",
  "to": "channel:<channel_id>",
  "content": "",
  "embeds": [{
    "title": "System Alert",
    "description": "High CPU usage detected on host `web-01`.",
    "color": 15158332,
    "fields": [
      { "name": "CPU Load", "value": "95%", "inline": true },
      { "name": "Memory", "value": "45%", "inline": true }
    ]
  }]
}
```

### React to Message
Acknowledge commands or mark items as done.

**Payload:**
```json
{
  "action": "react",
  "channelId": "<channel_id>",
  "messageId": "<message_id>",
  "emoji": "✅"
}
```

## Integration Workflows

### Deployment Notification
1. **Start**: Send "🚀 Deploying..." message.
2. **Execute**: Run `navig docker compose up`.
3. **Finish**:
   - If success: React with ✅ and edit message to "Deployed successfully".
   - If fail: React with ❌ and reply with error log snippet.

## Configuration
Requires `channels.discord` to be configured in the agent settings with a valid bot token.



