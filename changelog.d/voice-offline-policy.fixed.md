- **`/api/voice/synthesize` honours `voice.policy`.** The desktop TTS route ignored the
  policy entirely and always sent text to the default cloud engine — under `offline`
  the operator's text still left the machine through Edge. It now routes server-side
  like every other client surface: offline resolves only to the on-device engine or
  answers 422 naming what is missing, the chosen provider is pinned into synthesis with
  no fallback chain behind it, and the response carries `format` (`wav`/`mp3`) so a
  caller never has to guess the container of `audio_b64`.
