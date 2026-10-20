- **Lenses can send to your NAVIG inbox — safely (`@grant navig.capture`).** A Lens that
  declares the grant gets `navig.capture(text | {content, title?, kind?, note?})`, which files a
  Capture v1 tagged with the Lens. In Chrome such a Lens runs in its own isolated user-script
  world with a per-Lens token, so the site's own JavaScript can never use the permission, and
  the capture is addressed with the page's real URL. Elsewhere (NAVIG Desktop, `navig cdp`)
  `navig.capture` is absent for now. Settings → Lenses marks these Lenses **Can send to NAVIG**.
