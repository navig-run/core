---
name: summarize-url
description: Summarize web pages, articles, YouTube videos, and documents using AI
user-invocable: true
navig-commands:
  - navig ai ask "summarize {url}"
requires:
  - curl (for fetching web content)
  - navig ai (for AI-powered summarization)
examples:
  - "Summarize this article: https://..."
  - "What's this video about? https://youtu.be/..."
  - "TLDR of this page"
  - "Summarize the error in this log file"
---

# Summarize URLs & Content

Summarize web pages, articles, YouTube videos, and text content using NAVIG's AI capabilities.

## How It Works

NAVIG uses its built-in AI to process and summarize content. This works on your local machine — no remote server needed.

## Common Tasks

### Summarize a Web Page

**User says:** "Summarize this: https://example.com/article"

```bash
navig ai ask "Summarize this article: {url}"
```

**Response format:**
```
📄 Summary of "{title}":

{2-3 paragraph summary}

Key points:
• Point 1
• Point 2
• Point 3
```

### Summarize a YouTube Video

**User says:** "What's this video about? https://youtu.be/..."

```bash
navig ai ask "Summarize this YouTube video: {url}"
```

### Summarize Server Logs

**User says:** "Summarize the errors in the nginx log"

```bash
navig host use {host}
navig run "tail -200 /var/log/nginx/error.log"
# Then feed output to AI
navig ai ask "Summarize these error patterns: {log_output}"
```

**Response format:**
```
📋 Log Summary for nginx on {host}:

Found 3 error patterns in last 200 lines:

🔴 502 Bad Gateway (45 occurrences)
   → PHP-FPM seems to be crashing. Check process limits.

🟡 404 Not Found (23 occurrences)
   → Mostly bots scanning for /wp-admin, /phpmyadmin

🟢 Connection reset (5 occurrences)
   → Normal client disconnects, no action needed.
```

### Summarize Local Files

**User says:** "TLDR of this document"

```bash
navig ai ask "Summarize this document: $(cat {file})"
```

## Tips

- For long content, AI will produce a concise summary with key points
- Works with articles, docs, README files, changelogs, error logs
- Can summarize in different languages if asked
- Combine with other skills: "Summarize the last 100 lines of nginx errors on production"

## Limitations

- Cannot access paywalled content
- YouTube transcripts depend on availability
- Very long documents may be truncated
- Requires AI provider to be configured (`navig ai providers`)

## Error Handling

- **URL not accessible**: "Can't reach that URL. Check if it's publicly accessible."
- **No AI provider**: "Set up an AI provider first: `navig ai providers`"
- **Content too long**: "Content is very long. Here's a summary of the first section..."


