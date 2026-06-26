# NAVIG Cloud Routing — Operator Guide

This doc covers `relay.navig.run` (the hosted Deck + broker), the two daemon connection modes (cloudflared / direct), and the full reverse-proxy + systemd setup for VPS deployments.

The headline: **your daemon and its data stay on your machine.** The broker is a routing lookup table — it knows your current public URL and your Telegram user_id, nothing else. No vault contents, no command history, no LLM provider data.

---

## Architecture

```
┌────────────────────────────────┐
│ Your machine (laptop OR VPS)   │
│                                │
│  navig daemon  127.0.0.1:8765  │ ← loopback bind, never public
│        │                       │
│        ▼                       │
│  Mode A: cloudflared tunnel    │ ← outbound 443 only, no inbound
│  Mode B: nginx/Caddy on :443   │ ← inbound 443 from internet
│        │                       │
└────────┼───────────────────────┘
         │
         ▼
   Mode A: https://<random>.trycloudflare.com
   Mode B: https://navig.example.com  (your domain)
         ▲
         │ "current daemon URL" registered + heartbeat every 60s
         │
┌────────┼─────────────────────────────────────────┐
│  relay.navig.run    (Cloudflare Pages + D1)      │
│                                                  │
│  ─ static Next.js Deck     /, /connect, …       │
│  ─ Pages Functions         /api/cloud/*         │
│        ├─ register (Bearer api_key)             │
│        ├─ heartbeat                             │
│        ├─ bind-telegram                         │
│        ├─ resolve?api_key=…                     │
│        └─ resolve?telegram_id=…                 │
│  ─ D1 stores: sha256(api_key) → tunnel_url      │
│              telegram_user_id → user_id         │
└──────────────────────────────────────────────────┘
         ▲                          ▲
         │ TMA initData              │ Browser:
         │ (validated by daemon)     │ Bearer api_key
         │                           │
        Telegram Mini App      Browser at relay.navig.run
```

The Deck never hits a hardcoded daemon URL — on boot it asks the broker "where's my daemon?" and caches the answer in `localStorage`. Every subsequent request goes straight to your machine.

---

## Choosing a mode (decision tree)

0. **Want always-on access (Telegram / SMS / remote Deck) with no tunnel and no
   domain — the recommended default?** → use **Lighthouse**
   (`navig lighthouse deploy`). It deploys a tiny edge to *your own* Cloudflare
   account and the brain dials out to it. Free, self-hosted, one command. See
   [LIGHTHOUSE.md](./LIGHTHOUSE.md). Otherwise continue:
1. **Do you have a public domain pointing at this machine?**
   - **No** → continue to step 2.
   - **Yes** → continue to step 4.
2. **Do you have a Tailscale account (or are willing to make a free one)?**
   - **Yes** → use **Tailscale Funnel** (`navig cloud tailscale --enable`).
     Free, stable `*.ts.net` URL, no subscription needed. **This is the
     canonical self-host Mini App path.**
   - **No** → continue to step 3.
3. **Do you have an active NAVIG Deck subscription?**
   - **Yes** → use **cloudflared mode** (default). Free hosted relay
     via `relay.navig.run`.
   - **No, free Solo / never paid** → still use cloudflared. The hosted
     relay is allowed for new free users (broker rate-limits abuse).
   - **No, perpetual-pack buyer** → cloudflared mode is NOT available
     to you (the hosted relay is a subscription feature). Use
     Tailscale Funnel or direct mode instead.
4. **Do you have nginx, Caddy, or Traefik already terminating TLS on that domain?**
   - **No** → install Caddy (5 minutes, auto-TLS) or use Tailscale Funnel.
   - **Yes** → use **direct mode**.
5. **Do you want the daemon reachable only from this same machine?**
   - **Yes** → **off mode** (`navig cloud disconnect`). The Deck won't see it.

> **The relay gate**: the cloudflared / hosted-broker path is the one
> feature that costs us money per active user (bandwidth, broker D1
> reads, relay uptime). It's available to active subscribers + new
> free Solo users + lapsed subscribers within a 30-day grace.
> Perpetual-pack buyers get the local app forever and self-host the
> Mini App via Tailscale Funnel — free for them, costs us nothing.
> Direct mode (your own URL) is always available regardless of license.
> **Lighthouse** (self-hosted on your own Cloudflare) is also always available
> and free — navig hosts nothing for it, so it's outside the relay gate.

---

## Mode L — Lighthouse (recommended: your own Cloudflare edge, no tunnel)

The brain dials **out** over one WebSocket to a Worker you deploy to your own
Cloudflare account, so there's no tunnel, no broker, no open port, and no
domain. Outbound replies (bot, SMS) stay direct from the brain.

```bash
navig vault add cloudflare        # token: Workers Scripts: Edit + Account Settings: Read
navig lighthouse deploy           # → https://navig-lighthouse.<sub>.workers.dev (no Node needed)
navig gateway start               # brings the uplink online
navig lighthouse status           # config + live uplink state
```

Sets `cloud.mode=lighthouse` + `cloud.lighthouse_url`, and points the Telegram
webhook at `…/tg/<hash>`. While the brain is asleep the edge serves a cached
snapshot + queues inbound for replay. Full design + offline behaviour +
file map: **[LIGHTHOUSE.md](./LIGHTHOUSE.md)**.

---

## Mode A — cloudflared (default, laptop / NAT)

What happens on `navig gateway start`:

1. The daemon checks `cloud.enabled` (default `true`) and `cloud.public_url` (empty by default).
2. With `public_url` empty, it spawns `cloudflared tunnel --url http://127.0.0.1:<port>` as a managed child process.
3. cloudflared opens an outbound WebSocket to Cloudflare's edge, gets back a fresh `https://<random>.trycloudflare.com` hostname.
4. The daemon scrapes the URL from cloudflared's stdout and POSTs it to `https://relay.navig.run/api/cloud/register` with `Authorization: Bearer <api_key>`.
5. A heartbeat loop refreshes the URL every 60s; if cloudflared dies, the daemon's watchdog restarts it, scrapes the new URL, re-registers.

### Quick start

```bash
navig init                  # mint api_key, configure bot, etc.
navig gateway start         # cloud is on by default
```

Look for the boot banner:

```
   Mode:     cloudflared tunnel  (https://abc-def-ghi.trycloudflare.com)
   Browser:  open https://relay.navig.run/connect?key=navig_xyz...
   Telegram: open @yourbot -> /start -> tap the Mini App button
```

Click the browser link. The Deck stashes your key, asks the broker for the tunnel URL, loads the dashboard.

### Ports

- **Inbound**: none. cloudflared doesn't listen on a public port.
- **Outbound**: 443 to `*.cloudflare.com` (the tunnel WebSocket) and 443 to `relay.navig.run` (heartbeat every 60s).
- **Daemon bind**: `127.0.0.1:8765` — loopback only.

### When cloudflared mode is NOT enough

- You need a stable URL (cloudflared rotates on every restart). The broker hides this from the Deck — the Deck always uses `relay.navig.run/connect?key=...` which is stable. But if you embed the daemon URL in another tool, the rotation will break it.
- You want lower latency (cloudflared adds an extra hop via Cloudflare's PoP).
- Your VPS provider already terminates TLS for you.

In those cases → direct mode (your domain) OR Tailscale Funnel.

---

## Mode A2 — Tailscale Funnel (canonical self-host Mini App path)

For free, stable, subscription-free public HTTPS access on any machine
(laptop, desktop, NAS, VPS without a domain). The recommended path for:

- Perpetual-pack buyers who don't have a subscription for the hosted relay
- Anyone who wants a stable URL without buying a domain
- Home labs

Prerequisites:
- A free Tailscale account (https://tailscale.com)
- `tailscale` CLI installed: https://tailscale.com/download
- `tailscale up` done once on this machine

```bash
navig cloud tailscale --status
# Expect: not enabled

navig cloud tailscale --enable
# Brings up Tailscale Funnel for port 8765
# Captures the *.ts.net URL and writes it to ~/.navig/config.yaml under cloud.public_url
# CloudManager picks up direct mode automatically (no broker involved)

navig miniapp register
# Pushes the *.ts.net URL to BotFather as your bot's Mini App menu URL
# The Mini App button appears in Telegram within ~30 seconds

navig cloud tailscale --status
# Expect: enabled with URL
```

To turn off:
```bash
navig miniapp unregister
navig cloud tailscale --disable
```

### Ports
- **Inbound**: 443 to your machine, terminated by Tailscale Funnel
  (Tailscale handles TLS certs automatically; you don't need certbot)
- **Outbound**: nothing extra beyond the Tailscale connection
- **Daemon bind**: still `127.0.0.1:8765`. Tailscale Funnel proxies
  Internet:443 → 127.0.0.1:8765 on your behalf.

### Why this is the canonical self-host Mini App path
- Free Tailscale tier covers Funnel for personal use
- URL is stable across machine reboots
- Auto-TLS — no certbot, no Let's Encrypt rate limit dance
- Works behind NAT, on a laptop, on a residential ISP
- The broker is NOT involved — costs us $0 → costs you nothing → no
  subscription needed → works for perpetual-pack buyers

---

## Mode B — direct (VPS with reverse proxy)

What happens on `navig gateway start` when `cloud.public_url` is set:

1. The daemon validates `public_url` starts with `https://`.
2. It **skips** `cloudflared` entirely — no subprocess, no binary download, no URL scraping.
3. It registers `public_url` with the broker via `/api/cloud/register`.
4. Heartbeat loop runs as before. No URL-rotation watchdog (the URL is static — your domain doesn't change).
5. The daemon's HTTP server still binds to `127.0.0.1:8765` (or whatever `gateway.host` / `gateway.port` are). Your reverse proxy forwards from `0.0.0.0:443` → `127.0.0.1:8765`.

### Prerequisites

- A domain pointing at your VPS (e.g., A record `navig.example.com` → your VPS IP).
- TLS for that hostname. Easiest: Let's Encrypt via Caddy (auto) or Certbot + nginx.

### Setup — nginx

```nginx
# /etc/nginx/sites-enabled/navig.example.com
server {
    listen 443 ssl http2;
    server_name navig.example.com;

    ssl_certificate     /etc/letsencrypt/live/navig.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/navig.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;

        # SSE and WebSocket forwarding (NAVIG uses /api/events SSE and /voice/* SSE)
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $http_connection;

        # Don't cut SSE streams short
        proxy_read_timeout 1d;
        proxy_send_timeout 1d;

        # Pass-through headers the daemon needs to see
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Origin            $http_origin;
    }
}

server {
    listen 80;
    server_name navig.example.com;
    return 301 https://$host$request_uri;
}
```

```bash
sudo certbot --nginx -d navig.example.com
sudo nginx -t && sudo systemctl reload nginx
```

### Setup — Caddy 2 (one-liner, auto-TLS)

```caddy
# /etc/caddy/Caddyfile
navig.example.com {
    reverse_proxy 127.0.0.1:8765 {
        flush_interval -1   # required for SSE streams
    }
}
```

```bash
sudo systemctl reload caddy
```

That's it — Caddy auto-acquires the Let's Encrypt cert and handles renewal.

### Tell NAVIG about your URL

Pick one of three ways:

**1. CLI (writes to `~/.navig/config.yaml`):**
```bash
navig cloud direct https://navig.example.com
# then restart the gateway to apply
```

**2. Environment variable (no config edit — best for systemd units):**
```bash
export NAVIG_PUBLIC_URL=https://navig.example.com
navig gateway start
```

**3. Edit config manually:**
```yaml
# ~/.navig/config.yaml
cloud:
  enabled: true
  public_url: https://navig.example.com
```

### Systemd unit (recommended for VPS)

```ini
# ~/.config/systemd/user/navig.service
[Unit]
Description=NAVIG operator gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=NAVIG_PUBLIC_URL=https://navig.example.com
ExecStart=/usr/local/bin/navig gateway start
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now navig
journalctl --user -u navig -f
```

User-scope unit — no `sudo` required. Survives logout if you have linger enabled: `sudo loginctl enable-linger $USER`.

### Verify direct mode is live

```bash
# 1. No cloudflared process
pgrep -a cloudflared          # expect nothing

# 2. Broker has your URL
curl https://relay.navig.run/api/cloud/resolve?api_key=$(navig cloud key --reveal | grep -o 'navig_[A-Za-z0-9_-]*')
# {"tunnel_url":"https://navig.example.com"}

# 3. Public URL responds
curl -H "Authorization: Bearer $(navig cloud key --reveal | grep -o 'navig_[A-Za-z0-9_-]*')" \
     https://navig.example.com/api/deck/status

# 4. CORS preflight (browser sanity check)
curl -i -H "Origin: https://relay.navig.run" -H "Access-Control-Request-Method: GET" \
     -X OPTIONS https://navig.example.com/api/deck/status
# expect Access-Control-Allow-Origin: https://relay.navig.run

# 5. SSE keeps headers (fixed in Phase 2)
curl -i -N -H "Origin: https://relay.navig.run" https://navig.example.com/api/events
# first ~200 bytes include Access-Control-Allow-Origin
```

### Switching back to cloudflared

```bash
navig cloud direct --clear
# then restart the gateway
```

---

## Mode C — off (fully local)

```bash
navig cloud disconnect
```

Sets `cloud.enabled: false`. The daemon doesn't spawn cloudflared, doesn't talk to the broker. The Deck on `relay.navig.run` won't be able to find your daemon — you can only reach it from the same machine via `http://127.0.0.1:8765`. Use the bundled local Deck (served by the daemon itself) at the same address.

---

## CLI reference

```
navig cloud connect                    Turn on cloudflared mode (default).
navig cloud direct <https://url>       Switch to VPS direct mode.
navig cloud direct --clear             Revert direct mode to cloudflared.
navig cloud status                     Show mode, public URL, last heartbeat.
navig cloud disconnect                 Off. Stops broker registration.
navig cloud key                        Show api_key (truncated).
navig cloud key --reveal               Show api_key in full (sensitive!).
navig cloud key --rotate               Mint a fresh api_key; re-register with broker.
```

---

## Security model

The broker is intentionally minimal — what's stored there cannot compromise your daemon. Everything sensitive stays local.

| Stored on broker | Stored on daemon |
|---|---|
| `sha256(api_key)` (one-way hash) | the api_key itself, vault contents, bot token, command history, LLM keys |
| Your current public URL | everything that matters |
| `telegram_user_id → user_id` mapping | the bot token that validates initData |
| Timestamps (created, last heartbeat) | … |

### Auth

- **`/register`, `/heartbeat`, `/bind-telegram`, `/unregister`** — require `Authorization: Bearer <api_key>`. Broker compares `sha256(key)` against stored hashes.
- **`/resolve`** — no auth. The key IS the secret in the api_key path; the tunnel URL alone is useless without the api_key Bearer (or a valid initData from your bot's Mini App).
- **Daemon endpoints** (`/api/deck/*`, `/api/events`, `/mesh/*`, `/runtime/*`) — Bearer api_key OR HMAC-validated Telegram initData (validated locally by the daemon using its own bot token).

### Rate limiting

Per-IP, per-endpoint, per minute:

| Endpoint | Limit |
|---|---|
| `/api/cloud/register` | 20 / min |
| `/api/cloud/heartbeat` | (no broker-side limit; daemon throttles to 1/min) |
| `/api/cloud/bind-telegram` | 10 / min |
| `/api/cloud/resolve` | 120 / min |
| `/api/cloud/unregister` | 5 / min |

On limit hit the broker returns a generic 404 (same shape as "unknown key") so it can't be used to enumerate valid keys.

### CORS allowlist

Daemon and broker both restrict CORS to:
- `https://relay.navig.run`, `https://navig.run`, `https://www.navig.run`
- `https://web.telegram.org`, `https://webk.telegram.org`, `https://webz.telegram.org`, `https://oauth.telegram.org`
- `*.navig-deck.pages.dev` (Pages preview deploys)
- `*.navig.run` (future subdomains)
- `http://localhost:3000`, `http://127.0.0.1:3000` (local dev)

Everything else is rejected — no `Access-Control-Allow-Origin` header set, browser blocks the response.

### Telegram bind allowlist

The daemon only binds a Telegram `user_id` to the broker if that user appears in `telegram.allowed_users` (set during `navig init`). Empty list = bind anyone (so first `/start` on a fresh install still works); once you've allowlisted yourself, strangers' `/start` messages stop being broker-bound.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Browser says `530` or `522` from tunnel | cloudflared process died, broker still points at the old URL | restart daemon; CloudManager watchdog should auto-restart cloudflared and re-register |
| Deck stuck on "Connecting…" | no daemon registered for your api_key | `navig cloud status` — if Status is not `online`, restart gateway |
| Broker `/resolve` returns 404 for a valid key | first heartbeat hasn't fired since boot | wait 60s, or restart daemon |
| SSE drops after ~5s | (legacy) StreamResponse missing CORS headers — **fixed in Phase 2** | restart daemon |
| `navig cloud direct` says "URL must be https" | http:// is rejected — Cloudflare won't proxy unencrypted | set up TLS first (Caddy is the fastest path) |
| Telegram Mini App says "Daemon offline" | broker has no `telegram_user_id` binding for you | send `/start` to your bot — daemon binds you on receive |
| Restart loops in systemd | check `journalctl --user -u navig -n 200` for the exact error |

---

## Migration notes

If you previously used `scripts/navig-tunnel.sh` / `scripts/navig-tunnel.service`, they're deprecated. The in-daemon `CloudManager` does the same thing and integrates with the broker. To migrate:

```bash
# Stop the legacy systemd unit
systemctl --user stop navig-tunnel
systemctl --user disable navig-tunnel

# Let the daemon manage cloudflared itself
navig cloud connect
navig gateway start
```

The legacy script is still in the repo as a reference but is no longer auto-installed.
