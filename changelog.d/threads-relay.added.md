- **Lighthouse 1.7.0 — the sign-in relay.** `GET /oauth/callback` on the self-hosted edge
  302s the browser to this machine's local callback port (the same one the Cloudflare login
  uses), which is how a provider that refuses `http://` redirects — Threads — completes a
  sign-in with **no pasted URL**: the browser doing the sign-in is this machine, so no tunnel
  and no uplink hop is needed. It addresses nothing (no Bearer, no tenant, no Durable Object),
  carries `?code=…&state=…` untouched, and the state token is checked by the waiting flow.
  The shipped worker bundle ships the same version; `navig lighthouse status` reports an edge
  that lacks the route, so redeploy after upgrading.
