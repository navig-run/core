- **Sign-in success pages stay centred and honest.** The Cloudflare and Codex local OAuth
  callback pages used a 100vh flex `<body>`, which an injected element (a browser
  extension's bar) squeezed into a one-word-wide column off the edge of the screen. They
  now use a fixed full-viewport overlay with a viewport meta. The Codex page is served
  even when the callback carries no code or a mismatched state, so its heading now reads
  "Sign-in received" instead of claiming success.
