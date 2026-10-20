- **Translate a selection in place (`POST /api/deck/translate`).** The Dock's new Translate
  feature sends the selected passage and a target language code (`en`, `fr`, `pt-BR` —
  validated, prompt-safe) and gets the translation back to write over the selection: the
  LLM seam runs the new builtin `translate/selection` prompt (faithful translation that
  preserves line breaks, numbers, units, proper nouns and code, returns only the
  translation, and treats the passage as delimited data — never as instructions), with a
  5,000-character limit stated in the error instead of a silent truncation, the client's
  `lang` hint passed on as a hint to verify (best-effort `navig_audio` detection when no
  hint arrives, never a plugin dependency), `{text, source, target, chars}` back, and
  honest failures: 400 `bad_target` / `bad_request` / `too_long`, 502
  `translate_failed` with the engine's reason, 503 `llm_required` when no AI connection
  answers.
