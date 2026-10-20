- **The gateway's bearer-less routes now require the admin token.** `GET /router/status`,
  `GET /router/traces`, `POST /router/detect`, `GET /llm/providers`,
  `POST /llm/providers/register|unregister`, `GET /sync`, `POST|DELETE /mesh/target` and
  `POST /mesh/discovery/scan` answered anyone — provider inventory, route traces, a
  classifier over the operator's router, an SSRF-shaped "register an LLM URL the daemon
  will call", and the two mesh routes that move where commands run. A caller survey found
  no live in-repo client sending credentials to any of them (the archived VS Code
  extension aside), so gating them breaks nothing: `navig flux` now sends the bearer via
  `gateway_request_headers()` — previously it posted without one — and the route tests
  assert the 401/503 as well as the behaviour behind it. Routes that *cannot* take header
  auth yet (the memory-review page's inline `fetch`, `/api/events`' `EventSource`) keep
  their current design and stay documented in SECURITY_CDP_AUDIT §4 F1.
- **`/install/*` refuses tunneled requests, not just non-LAN ones.** The join flow hands
  out the fleet `mesh_token` to any private/loopback peer — and a cloudflared tunnel
  terminates on this machine, so a request from the public internet presents as
  `127.0.0.1` and passed. It now also refuses on the two markers the deck's
  local-desktop bypass already trusts (`CF-Ray`, `X-Navig-Via`), which a caller cannot
  strip on the way in.
