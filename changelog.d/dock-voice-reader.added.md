- **NAVIG Voice for the Dock — read the selection aloud.** `GET /api/deck/voice/catalog` says
  which speech engines are usable here, what they cost, which voices suit French, Russian and
  English, and which controls each really supports. `POST /api/deck/voice/speak` turns one
  sentence-bounded chunk (≤ 1,200 characters, never silently truncated) into mp3. Auto routes
  to a free engine only. A paid provider runs only when it is named in the request; it is
  never a fallback. Being deck routes, they serve the Dock over loopback and a remote client
  through Lighthouse with no new pairing.
