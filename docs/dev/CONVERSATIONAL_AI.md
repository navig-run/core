# NAVIG Conversational AI

> **Talk to NAVIG like a friend - it understands natural language and executes tasks autonomously!**

## Overview

NAVIG's conversational AI is designed to feel like chatting with a helpful, tech-savvy friend. Just tell it what you want in plain English, and it will:

1. **Understand** your goal
2. **Plan** the necessary steps
3. **Execute** each step automatically
4. **Report** the results back to you
5. **Retry** if something doesn't work

## Quick Examples

### Opening Apps
```
You: open calculator
NAVIG: Sure! I'll open Calculator for you 🚀

🎉 Completed!
✅ Opening Calculator

Anything else I can help with? 😊
```

### Window Management
```
You: snap VS Code to the left
NAVIG: Snapping VS Code to the left side! 📐

🎉 Completed!
✅ Snapping VS Code to left

Anything else I can help with? 😊
```

### Multi-Step Tasks
```
You: open notepad and type hello world
NAVIG: I'll open Notepad and type "hello world" for you!

⚙️ Step 1/2: Opening Notepad
⚙️ Step 2/2: Typing text

🎉 Completed!
✅ Opening Notepad
✅ Typing text

Anything else I can help with? 😊
```

### Asking for Confirmation
```
You: create a workflow to backup my documents
NAVIG: I'll create a workflow to backup your documents. Want me to proceed? 🛠️

Plan:
  1. Creating workflow: backup documents

Reply 'yes' or 'go' to proceed, or 'no' to cancel.

You: yes
NAVIG: 🚀 Starting task execution!
...
```

## Natural Language Patterns

NAVIG understands many ways of expressing the same intent:

### Opening Applications
- "open calculator"
- "launch notepad"
- "start VS Code"
- "run chrome"

### Clicking
- "click at 100, 200"
- "click on 500 300"
- "tap at 100,100"

### Typing
- "type hello world"
- "write this is a test"
- "enter my password"

### Window Management
- "snap chrome to the left"
- "move VS Code to the right"
- "arrange windows"
- "show me all windows"

### Clipboard
- "show clipboard"
- "what's copied?"
- "paste this"

### Workflows
- "list workflows"
- "create a workflow to..."
- "make an automation that..."

## Confirmation

For sensitive operations, NAVIG will ask for confirmation:

**Confirmation words (proceed):**
- `yes`, `go`, `proceed`, `do it`
- `ok`, `sure`, `yep`, `yeah`

**Cancellation words (cancel):**
- `no`, `cancel`, `stop`, `nevermind`
- `nope`, `nah`

## Personality

NAVIG is designed to be:

- 🤗 **Friendly** - Warm and conversational
- 💪 **Helpful** - Proactively assists with tasks
- 🎯 **Capable** - Gets things done automatically
- 😊 **Positive** - Celebrates successes
- 🔄 **Resilient** - Tries alternatives when something fails
- 💬 **Clear** - Explains what it's doing

## Capabilities

### Desktop Automation
- Open applications
- Click at screen coordinates
- Type text
- Manage windows (snap, minimize, maximize)
- Work with clipboard

### AI Evolution
- Generate new workflows
- Create Python scripts
- Fix code automatically

### DevOps
- Run remote commands
- Manage hosts
- Execute workflows

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     User Message                        │
├─────────────────────────────────────────────────────────┤
│                   Channel Router                        │
│  ┌─────────────────────────────────────────────────┐   │
│  │           Conversational Agent                   │   │
│  │  ┌───────────┐  ┌──────────┐  ┌─────────────┐  │   │
│  │  │   NLP     │→│  Planner │→│   Executor  │  │   │
│  │  │ (AI/Rule) │  │          │  │             │  │   │
│  │  └───────────┘  └──────────┘  └─────────────┘  │   │
│  └─────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────┤
│               Platform Adapters                         │
│    [Windows/AHK]  [Linux/xdotool]  [macOS/AppleScript] │
└─────────────────────────────────────────────────────────┘
```

## Configuration

### Enable AI for Smarter Responses

Set your API key in `.env`:

```ini
OPENROUTER_API_KEY=sk-or-...
AI_MODEL=anthropic/claude-3.5-sonnet
```

With an AI client, NAVIG can:
- Understand complex requests
- Generate multi-step plans
- Handle ambiguous instructions
- Learn from conversation context

### Without AI Client

NAVIG still works using pattern matching for common intents:
- Opening apps
- Clicking
- Typing
- Window management
- Clipboard operations

## Telegram Integration

### Setup
1. Create a bot via @BotFather
2. Add token to config
3. Start the gateway

### Usage via Telegram
```
You: hey
NAVIG: Hey! I'm here to help. You can ask me to:
• Open apps ("open calculator")
• Click on screen ("click at 100, 200")
• Type text ("type hello world")
• Manage windows ("snap VS Code to the left")
• Create automations ("create a workflow to...")

What would you like me to do? 😊
```

## API Reference

### ConversationalAgent

```python
from navig.agent.conversational import ConversationalAgent

agent = ConversationalAgent(
    ai_client=None,  # Optional AI client
    on_status_update=None,  # Callback for step updates
)

# Chat
response = await agent.chat("open calculator")

# Confirm pending task
response = await agent.confirm(True)

# Get status
status = agent.get_status()
```

### Task Status

```python
class TaskStatus(Enum):
    PENDING = auto()       # Task created
    PLANNING = auto()      # Waiting for confirmation
    EXECUTING = auto()     # Running steps
    WAITING_INPUT = auto() # Needs user input
    SUCCESS = auto()       # Completed successfully
    FAILED = auto()        # Failed after retries
    CANCELLED = auto()     # User cancelled
```

### Available Actions

| Action | Description | Example |
|--------|-------------|---------|
| `auto.open_app` | Open application | `{"target": "Calculator"}` |
| `auto.click` | Click coordinates | `{"x": 100, "y": 200}` |
| `auto.type` | Type text | `{"text": "hello"}` |
| `auto.snap_window` | Snap window | `{"selector": "App", "position": "left"}` |
| `auto.windows` | List windows | `{}` |
| `auto.get_clipboard` | Get clipboard | `{}` |
| `auto.set_clipboard` | Set clipboard | `{"text": "copied"}` |
| `command` | Run shell command | `{"cmd": "ls -la"}` |
| `workflow.run` | Run workflow | `{"name": "my_workflow"}` |
| `evolve.workflow` | Generate workflow | `{"goal": "description"}` |

## Best Practices

1. **Be Natural** - Talk like you would to a friend
2. **Be Specific** - Include details when needed
3. **Confirm Carefully** - Review plans before confirming
4. **Start Simple** - Build up to complex tasks
5. **Provide Feedback** - Tell NAVIG if something didn't work

## Troubleshooting

### NAVIG Doesn't Understand

Try rephrasing your request:
- "open calc" → "open the calculator app"
- "click there" → "click at 500, 300"

### Task Failed

NAVIG will try alternatives automatically. If it still fails:
1. Check the error message
2. Ensure the app/target exists
3. Try manual approach first to test

### Slow Responses

- Check internet connection (for AI mode)
- Reduce complexity of requests
- Pattern matching mode is faster

## Future Enhancements

- [ ] Voice input support
- [ ] Visual feedback with screenshots
- [ ] Learning from user corrections
- [ ] Custom personality profiles
- [ ] Multi-language support

---

**Talk to NAVIG like a friend - it's here to help!** 🤖💬


