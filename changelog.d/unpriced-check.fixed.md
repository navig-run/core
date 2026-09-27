- **A size-suffixed model borrowed the bigger model's price.** Under the variant rule a `-lite`,
  `-mini` or `-nano` after a priced name read as a variant, so `gemini-2.5-flash-lite` ($0.10 /
  $0.40) would have billed at Flash's $0.30 / $2.50, and `gpt-5-nano` at `gpt-5`. A size token
  anywhere in the remainder now means a different model — anywhere, because otherwise a shorter key
  reclaims what the longer one refused (`claude-opus-4-8-mini` fell through to `claude-opus-4`).
  `gpt-5` and `gpt-5-mini` — live in the openai catalog and unpriced — added from OpenAI's page.
