# 🗼 Lighthouse — your own always-on Cloudflare edge (no tunnel)

> **Status:** shipped (Phase 0 + Phase 1). Self-host only — navig operates no
> relay or data-plane for users. See [CLOUD.md](./CLOUD.md) for the other
> connectivity modes (cloudflared, Tailscale, direct).

## What it is

The navig brain (the Python daemon) runs on your machine behind NAT. Anything
that must *reach in* — the Telegram webhook, Twilio inbound SMS, the remote
Deck — normally needs a public URL (a tunnel + broker).

**Lighthouse** replaces that with a tiny **Cloudflare Worker + Durable Object**
that *you* deploy to *your own* Cloudflare account. The brain dials **OUT** to
it over a single persistent WebSocket and stays connected; Lighthouse pushes
inbound work down that pipe and reads the replies back:

```
   your bot / Twilio / Stripe ─┐   (inbound hits YOUR edge, never your laptop)
   deck (browser / Mini App) ──┤
                                ▼
        ┌─────────────────────────────────────┐  YOUR Cloudflare account
        │  LIGHTHOUSE (Worker)                 │  https://navig-lighthouse.<sub>.workers.dev
        │  • Hono routes (tenant = sha256(key))│
        │  • BrainSocket Durable Object        │◄── WS Hibernation; holds the uplink;
        │      req↔reply · SSE fanout          │     correlates inbound → brain
        │      DO-SQLite: offline snapshot+queue│
        └───────────────────┬──────────────────┘
              persistent OUTBOUND WebSocket (the brain dials out)
                            ▼
                navig brain (Python) — local data + agent + bot token
```

- **No tunnels, no port-forwarding, ever.** The brain only makes an outbound
  connection.
- **Outbound replies stay direct.** Bot messages and SMS sends go straight from
  the brain to Telegram/Twilio, so the bot token never transits the edge.
- **Self-host only / free.** navig hosts nothing per user → zero per-user cost,
  privacy by default. Lighthouse is free for everyone; subscriptions sell host
  scale + modules + support + advanced Lighthouse features.

## Deploy it (no Node, no domain)

**Easiest — one-click browser login (no token to create):**

```bash
navig lighthouse login     # opens Cloudflare → click Authorize → it deploys for you
navig gateway start        # bring the outbound uplink online
```

`login` runs Cloudflare's browser OAuth (the same flow `wrangler login` uses),
stores an access **+ refresh** token in your vault (so future redeploys are
unattended), uploads the edge, and sets your Telegram webhook.

**Or with a scoped API token** (good for CI / headless, or if you prefer):

```bash
navig lighthouse deploy    # prompts for a token (see below), or pass --token
navig gateway start
```

With no token configured, `navig lighthouse deploy` opens the Cloudflare token
page, prints the exact permissions, and reads a hidden paste — then vaults it.
`navig init` also offers all of this as an **optional first-run step** ("Make
navig reachable anywhere with no tunnel?").

### Create your Cloudflare API token (~30s, free plan is fine)

You only do this once. The token is stored in your vault and is **only ever
sent to `api.cloudflare.com`** — never to navig.

1. Open **https://dash.cloudflare.com/profile/api-tokens**
2. **Create Token** → use the **“Edit Cloudflare Workers”** template *(easiest — it
   already includes everything below)*. Or **Create Custom Token** with just these
   two **Account** permissions:
   - **Workers Scripts → Edit**  *(upload the Worker + its Durable Object)*
   - **Account Settings → Read**  *(discover your account id + `workers.dev` subdomain)*
3. **Account Resources** → include the account you want to host on → **Continue → Create** → copy the token.

> A `*.workers.dev` subdomain is auto-registered on first deploy if you don't
> have one. No custom domain is required. Don't use the **Global API Key** — it
> grants full account access; a scoped token is safer.

You can also provide it non-interactively:

```bash
export CLOUDFLARE_API_TOKEN=…        # or
navig vault add cloudflare           # or
navig lighthouse deploy --token <token> [--account-id <id-if-multiple>]
```

`navig lighthouse deploy` ships a **prebuilt Worker bundle** inside the navig
package and uploads it via the **Cloudflare REST API** — no Node, no wrangler,
no custom domain. It then flips `cloud.mode=lighthouse`, stores the URL, and
points your Telegram webhook at `…/tg/<hash>`.

Other commands: `navig lighthouse status` · `url` · `redeploy` · `disable [--delete]`.

## Your own deck UI (separate Cloudflare deployment)

The deck (the web UI / Telegram Mini App) deploys **separately** to your own
Cloudflare account, one command:

```bash
navig miniapp deploy        # build the deck → upload to your Cloudflare,
                            # bake in your Lighthouse URL, set the bot's Mini App button
```

What it does:
1. `next build` (static export → `out/`) with `NEXT_PUBLIC_LIGHTHOUSE_URL=<your edge>`
   baked in, so the deployed deck **auto-targets your brain** (no manual config).
2. Uploads `out/` to **Workers Static Assets** via the Cloudflare REST API —
   *pure-Python, same as the edge deploy* (`navig/cloud/deck_deploy.py`). It **reuses
   the same Cloudflare credential as Lighthouse** (API token or the OAuth token from
   `lighthouse login`), so there's **no wrangler, no `wrangler login`, and no Pages
   scope** — Workers scope covers it. Result: a stable `https://navig-deck.<sub>.workers.dev`.
3. `setChatMenuButton` → your bot's "Open" button launches **your** deck.

Only requirement: **Node 18+** for the *build* (the deck is a Next.js app); the
*upload* needs neither Node nor wrangler. Prefer Cloudflare Pages instead? Pass
`--wrangler` (needs `npx wrangler login` once). The deck talks to your edge
cross-origin (Lighthouse reflects CORS); the Mini App authenticates via Telegram
initData, a browser visit via the api_key.

## How routing works

Tenancy is keyed by `sha256(deck.api_key)` — the brain authenticates the uplink
with its api_key, the Deck sends the same key as a Bearer, and inbound webhooks
address an opaque per-brain path the brain set itself.

| Public path | Auth | Goes to |
|---|---|---|
| `GET /uplink` | `Bearer <api_key>` | the brain's outbound WebSocket |
| `GET /api/events` | `Bearer` | Deck SSE (held open by the DO) |
| `ALL /api/*`, `/runtime/*`, `/mesh/*` | `Bearer` | Deck API → uplink (kind=deck) |
| `POST /tg/<hash>` | Telegram secret header | the brain's Telegram handler |
| `POST /sms/<hash>` | provider signature (brain-checked) | the brain's inbound-SMS handler |

`deck` requests are dispatched in-process by the brain as a loopback HTTP call
to the local gateway (so auth + middleware run unchanged); `telegram` goes to
`TelegramChannel.handle_webhook_update` (validates the secret token); `sms`
replays as a loopback POST to `/sms/webhook`.

## Offline behaviour

While the brain is asleep, the Durable Object:

- serves the **last status snapshot** (cached in DO SQLite) with a `503 +
  X-Navig-Brain: offline` so the Deck keeps its last-known view and shows a
  "brain offline" banner (`isBrainOffline()` / `onBrainOfflineChange()` in
  `navig-deck/lib/api.ts`); and
- **queues** inbound Telegram/SMS (bounded + 24h TTL) and **replays** them when
  the brain reconnects — nothing is lost.

DO-SQLite buffering keeps the free Cloudflare plan viable. Cloudflare Queues +
DLQ are an optional Workers-Paid hardening (commented in
`navig-lighthouse/wrangler.jsonc`).

## Where the code lives

| Piece | Path |
|---|---|
| Edge Worker (Hono + `BrainSocket` DO) | `navig-lighthouse/src/{index,brain_socket,protocol,env}.ts` |
| Prebuilt bundle (shipped, uploaded by deploy) | `navig-core/navig/cloud/lighthouse_worker/worker.js` |
| Brain uplink client | `navig-core/navig/cloud/uplink.py` |
| CloudManager `mode=lighthouse` | `navig-core/navig/cloud/manager.py` |
| Pure-Python CF deploy | `navig-core/navig/cloud/lighthouse_deploy.py` |
| CLI | `navig-core/navig/commands/lighthouse.py` |
| First-run wizard step | `navig-core/navig/onboarding/steps.py` (`_step_lighthouse`) |
| Deck integration | `navig-deck/lib/api.ts` (`setLighthouseUrl`, offline banner) |

To regenerate the bundle after changing the Worker:
`cd navig-lighthouse && npm install && npm run ship:bundle`.

## Config keys

```yaml
cloud:
  enabled: true
  mode: lighthouse              # selects the uplink (vs tunnel/direct/local)
  lighthouse_url: https://navig-lighthouse.<sub>.workers.dev
  lighthouse_account_id: <cf account id>
  lighthouse_worker: navig-lighthouse
telegram:
  webhook_url: https://navig-lighthouse.<sub>.workers.dev/tg/<hash>
  webhook_secret: <generated>   # the brain validates uplink-delivered updates
```

## Roadmap (future)

- **Phase 2 — public apps:** host `navig-www` + a locked read-only Deck + client
  portals on the edge; optional Cloudflare Queues+DLQ for durability.
- **Phase 3 — multi-brain + leases + per-space sync:** `role` on `FluxPeer`; a
  lease registry (single owner per integration → no double-processing +
  failover); per-space op-log sync. **Vault + bizops never sync** (single-owner).
