---
name: nlp_aliases
plugin: telegram-bot-navig
version: 1.0.0
triggers:
  - language: en
    keywords: [explain, correct, improve, simplify, translate, brainstorm, proofread, creative, summary, context]
  - language: fr
    keywords: [explique, corrige, ameliore, simplifie, traduis, relis, creatif, resume, contexte]
  - language: ru
    keywords: ["объясни", "исправь", "улучши", "упрости", "переведи", "идеи", "проверь", "креатив", "резюме", "контекст"]
---

# NLP Aliases Skill

Multilingual natural-language triggers for AI text-action commands.
Reply to any message with a trigger word, or write: `trigger <text>`.

## Full Alias Table

| Action    | EN            | FR                | RU          |
|-----------|---------------|-------------------|-------------|
| Explain   | explain       | explique          | объясни   |
| Correct   | correct       | corrige           | исправь   |
| Improve   | improve       | ameliore          | улучши    |
| Simplify  | simplify      | simplifie         | упрости   |
| Translate | translate     | traduis           | переведи  |
| Brainstorm| brainstorm    | brainstorm        | идеи       |
| Proofread | proofread     | relis             | проверь    |
| Creative  | creative      | creatif           | креатив  |
| Summarize | summary       | resume            | резюме     |
| Analyze   | context       | contexte          | контекст  |

## Examples

```
explain quantum entanglement
objectsбъясни это
translate Hello, world
переведи Bonjour le monde
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
