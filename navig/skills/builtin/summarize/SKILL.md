---
id: summarize
name: Content Summarizer
version: 1.0.0
category: text
tags: [summary, text, nlp, extraction, digest]
platforms: [linux, macos, windows]
tools: [web_fetch, memory_store, memory_fetch]
safety: safe
user_invocable: true
description: >
  Fetch text from a URL or a memory key and produce a concise structured summary —
  a one-line headline, 3–7 key bullets, an approximate word count, and the source —
  then store it back in session memory. Use when the user says "summarize this",
  "give me the key points", "tl;dr", "summarize <url>", or "summarize the text stored
  as <key>". Read-only aside from writing the summary into memory.
---

# Summarize Skill

## Description

Given either a URL or a memory key pointing to text content, produce a structured
summary containing:

- **headline** — one sentence (≤ 20 words)
- **bullets** — 3–7 key points
- **word_count** — approximate word count of the source
- **source** — origin URL or memory key

The summary is stored in session memory under `"summary.<slug>"` where `<slug>`
is derived from the source.

---

## System Prompt / Behavior

You are a precise summarization agent.  Extract only what matters.  No filler.

Steps:
1. If `url` is provided: `web_fetch(url=<url>)` → capture text.
   If `memory_key` is provided: `memory_fetch(key=<key>)` → capture text.
2. Analyse the text and produce the summary JSON:
   ```json
   {
     "headline": "...",
     "bullets": ["...", "..."],
     "word_count": 0,
     "source": "..."
   }
   ```
3. Store: `memory_store(key="summary.<slug>", value=<summary>, tags=["summary"])`.
4. Return the summary to the user as Markdown.

Output format (Markdown):

```
**<headline>**

- <bullet 1>
- <bullet 2>
- <bullet 3>

*Source: <source> | ~<word_count> words*
```

---

## Examples

**User:** Summarize https://example.com/article
**Agent:**
1. `web_fetch(url="https://example.com/article")`
2. Summarise → produce headline + bullets
3. `memory_store(key="summary.example-com-article", value=<summary>, tags=["summary"])`
4. Return Markdown summary

**User:** Summarize the text stored in memory as "scraped.blog.post"
**Agent:**
1. `memory_fetch(key="scraped.blog.post")` → retrieve text
2. Summarise → produce headline + bullets
3. `memory_store(key="summary.scraped-blog-post", value=<summary>, tags=["summary"])`
4. Return Markdown summary
