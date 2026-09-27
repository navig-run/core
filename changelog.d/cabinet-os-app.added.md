- **A Cabinet app in NAVIG OS, plus `is_local_request()` in deck auth.** The desktop gets
  an app for the encrypted document cabinet: search inside documents, see what expires,
  add files, open them. Its routes answer only this computer. The check is the same one
  the desktop auth bypass uses, now exposed publicly as
  `navig.gateway.deck.auth.is_local_request`, so traffic through the tunnel, Lighthouse
  or the Mini App gets a 403. The routes return metadata only.
