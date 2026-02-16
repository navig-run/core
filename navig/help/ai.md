# ai

AI assistant for server management and automation.

Common commands:
- `navig ai ask "How do I..."` — ask a question
- `navig ai explain "iptables -L"` — explain a command
- `navig ai diagnose` — diagnose server issues
- `navig ai suggest` — get suggestions
- `navig ai show` — show AI context/history
- `navig ai providers` — manage AI providers and API keys

Examples:
- `navig ai ask "optimize nginx for 10k concurrent"`
- `navig ai explain "find / -name '*.log' -mtime +30 -delete"`
- `navig ai diagnose --host production`

Tip:
- Use `navig ai providers` to configure API keys for OpenAI, Anthropic, etc.
