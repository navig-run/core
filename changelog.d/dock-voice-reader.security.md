- **`/api/voice/*` no longer answers websites.** `synthesize` had no auth check, and
  `transcribe` computed one then ignored it. Any web page could fire a `text/plain` POST at
  `127.0.0.1` (no CORS preflight) and spend the operator's speech credits, and a relayed or
  tunneled request reached them too. They now follow the deck's local-desktop rule: loopback
  with no Origin, this machine's own UI or an installed extension, or the gateway token. Echo's
  bridge is local and sends no Origin, so it is unaffected.
