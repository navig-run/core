- **`navig audio regenerate` — make NEW audio from a recording with AI, labelled as new.**
  The counterpart to Audio Enhance (which filters and proves it is the same recording):
  `--op voice` re-speaks speech in another ElevenLabs voice with the same words and timing;
  `--op variations` makes 1–4 new takes of a sound or cue at the source's length from a
  description (measured locally; `--describe-with-ai` also lets Gemini listen — it uploads the
  clip) plus your `--direction`, sending only text to ElevenLabs; `--op replace --stem …`
  splits the mix and either re-voices the vocals or generates a new drums/bass/other stem at
  the measured tempo, loudness-matched and remixed. Every run that uploads or bills needs
  `--allow-cloud`; `--dry-run` shows exactly what would be sent and billed. Outputs are new
  `*.regen-*.wav` files with a provenance sidecar and a "how close to the original" reading
  (informative, never a pass). NAVIG Desktop: Studio → Enhance → **New versions with AI**, with a
  "What is sent?" preview and a per-run consent box; `/api/deck/media/{id}/regenerate` runs it
  as a job whose takes become new variants beside the source.
