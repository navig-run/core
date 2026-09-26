- **The README shows navig running.** Eleven real terminal recordings — hero, dashboard,
  hosts & safety, spaces, blocks, skills, ledger & undo, store & doctor, whoami, gateway boot,
  schema for agents — live in `docs/showcase/` with a gallery page and a provenance manifest;
  six are embedded in the README and a 90-second trailer plus per-scene MP4/WebM are attached
  to the `showcase-<date>` release. Every frame is a real `navig` session in an isolated home
  against a real local `sshd` — nothing typed into the output, nothing edited out. The tapes
  (`tools/showcase/tapes/`), the sandbox and the WSL environment script are committed:
  `npm run showcase:record` regenerates the lot and `npm run showcase:check` verifies the
  committed GIFs against `MANIFEST.json` (sha256 + size per asset, plus the navig version and
  the VHS/ffmpeg/Chrome builds that produced them). Recording them is how eleven CLI defects
  were found and fixed.
