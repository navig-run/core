- **The web UI could not connect through a tunnel or reverse proxy.** `/api/config` handed the
  browser a WebSocket URL that was wrong twice over: it substituted the server's *internal*
  port for the public one (a tunnel published on 443 got `wss://host:9100`, where nothing
  listens), and it trusted the server's *internal* TLS state (cloudflared terminates TLS and
  reaches us over plain http, so the browser got `ws://` for an https page and blocked it as
  mixed content). The UI and the WebSocket share one port, so the forwarded authority is now
  used verbatim and the scheme comes from the forwarded protocol; `NAVIG_WEBUI_WS_URL` is
  needed only for a proxy that publishes a non-default port without reporting it. A
  pre-existing test had asserted the defect — it forwarded public port 3100 and expected
  `:9100` back, under the name *"derives a browser-facing websocket URL"* — which is how this
  shipped green. Also fixed on the direct path, found by the new tests: WHATWG `URL` keeps the
  brackets on an IPv6 hostname, so `[::1]:9100` became `ws://[[::1]]:9100` and IPv6 loopback
  could never connect, while a bare `::1` was mangled to `ws://::9100` by a port-stripping
  regex. A forwarded host is now refused unless it is a bare authority, so it cannot inject a
  path or an origin into the URL the browser is given.
- **The web UI is no longer dropped silently when it cannot start.** `NAVIG_WEBUI_DIR` set
  without `NAVIG_SERVER_TOKEN` (which signs the login session) produced no handler, no warning
  and a browser opening onto a port that serves nothing. Both that and a missing build
  directory now say so, with the command that fixes them.
