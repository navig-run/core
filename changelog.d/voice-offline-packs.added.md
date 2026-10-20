- **Offline neural voices route through the whole voice stack.** `TTSProvider.LOCAL`
  (sherpa-onnx + an installed Piper pack, the navig-audio `local-tts` extra) is
  catalogued honestly — available only when the extra AND at least one pack are present,
  with the two states named apart — and the `offline` policy resolves to it or refuses
  with what is missing; it never falls back to a network provider. Speech profiles may
  name `provider: local`, and the deck speak route sets its `Content-Type` from the real
  format (`audio/wav` for the on-device engine, `audio/mpeg` for the cloud ones) instead
  of declaring mp3 over every byte. Packs are installed, checksum-verified and removed
  with `navig voice packs list|install|remove` (details in navig-audio 0.6.0).
