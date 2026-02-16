# Council Deliberation

Run multi-agent deliberation sessions using agents from the active formation.
Agents discuss a question over multiple rounds, building on each other's
insights, and the default agent synthesizes a final decision.

## Commands

### `navig council run <question>`
Run a council deliberation on a question or topic.

Options:
- `--rounds, -r`: Number of deliberation rounds (default: 3)
- `--timeout, -t`: Per-agent timeout in seconds (default: 30)
- `--plain`: Plain text output
- `--json`: JSON structured output

## Examples

```bash
# Basic deliberation
navig council run "Should we adopt Kubernetes?"

# Quick 1-round deliberation
navig council run "Best database for our use case?" --rounds 1

# Extended deliberation with longer timeout
navig council run "5-year technology roadmap" --rounds 5 --timeout 60

# JSON output for scripting
navig council run "Budget priorities for Q3" --json
```

## How It Works

1. The active formation's agents are loaded from `.navig/profile.json`
2. Each agent receives the question with their specialized system prompt
3. Agents respond with analysis and a confidence score (0.0-1.0)
4. In subsequent rounds, agents see previous responses for context
5. The formation's default agent synthesizes all inputs into a final decision

## Environment Variables

- `NAVIG_COUNCIL_TIMEOUT`: Override default per-agent timeout (seconds)

## Notes

- Requires an active formation (run `navig formation init <name>` first)
- Each agent's `council_weight` influences the final decision
- Agents report confidence scores that are displayed as progress bars
- Council works best with 2-3 rounds for most decisions
