- **Every GPT-4.1 turn was billed at classic GPT-4 prices — about 15x too high.** Both cost
  trackers matched a model to a price with a bare `startswith`, and `"gpt-4.1".startswith("gpt-4")`
  is True — so `gpt-4.1` (OpenAI's catalog head), `gpt-4.1-mini` and even `gpt-4.1-nano` were all
  priced at $30/$60 per M. A price now belongs to the model it names and to that model's
  *variants* only — a key followed by `-`, `@` or `:` (a dated snapshot, a preview or `-fast`
  suffix, a Vertex version, an ollama tag), never `.` (which continues a version) — and the
  longest priced key wins regardless of declaration order. The per-turn tracker also stopped
  REVERSE-matching (`claude-opus` priced as whichever `claude-opus-*` came first). The GPT-4.1
  family gets its own entries at OpenAI's published launch prices; `navig cost`'s operator-written
  table follows the same rule. An unknown model still reports $0 and says so at debug level.
