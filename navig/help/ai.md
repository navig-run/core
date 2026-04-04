# ai

AI assistant for server management and automation.

Common commands:
- `navig ask "How do I..."` — ask a question (canonical)
- `navig ai ask "How do I..."` — deprecated alias to `navig ask`
- `navig ai explain` / `navig ai diagnose` / `navig ai suggest` — deprecated aliases to `navig ask`
- `navig ai show` — show AI context/history
- `navig ai providers` — manage AI providers and API keys

Examples:
- `navig ask "optimize nginx for 10k concurrent"`
- `navig ask "explain find / -name '*.log' -mtime +30 -delete"`
- `navig ask "diagnose production host health"`

Tip:
- Use `navig ai providers` to configure API keys for OpenAI, Anthropic, etc.
