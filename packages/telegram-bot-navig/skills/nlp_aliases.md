---
name: nlp_aliases
plugin: telegram-bot-navig
version: 1.0.0
triggers:
  - language: en
    keywords: [explain, correct, improve, simplify, translate, brainstorm, proofread, creative, summary, context]
---

# NLP Aliases Skill

Natural-language triggers for AI text-action commands.
Reply to any message with a trigger word, or write: `trigger <text>`.

## Alias Table

| Action     | Keyword    |
|------------|------------|
| Explain    | explain    |
| Correct    | correct    |
| Improve    | improve    |
| Simplify   | simplify   |
| Translate  | translate  |
| Brainstorm | brainstorm |
| Proofread  | proofread  |
| Creative   | creative   |
| Summarize  | summary    |
| Analyze    | context    |

## Examples

```
explain quantum entanglement
correct this sentence
translate Hello, world
summary [reply to a long message]
```

## Configuration

Add to `~/.navig/config.yaml`:
```yaml
openrouter_api_key: your-key   # preferred
# or
openai_api_key: your-key       # fallback
```

Without an AI key, the plugin acknowledges triggers but cannot generate AI responses.
