- **The embedded gateway no longer grants CORS to every origin.** The telegram worker's
  copy of the gateway answered *every* route with `Access-Control-Allow-Origin: *`, which
  is harmless-looking only while every route carries a Bearer — but
  `/api/memory/facts/*`, `/install/config` (the `mesh_token`), `/llm/providers` and
  `/router/*` carry none, so any page open in any browser on the machine could read them
  cross-origin (measured with `Origin: https://evil.example`, loopback and LAN). It now
  delegates to the daemon's own `cors_headers_for` allowlist, so the two listeners cannot
  drift; Telegram WebView, the hosted Relay and any localhost dev port keep their grant.
  Regression tests: `tests/gateway/test_embedded_gateway_cors.py`.
- **A release build can no longer pick up a DevTools port from its launch environment.**
  `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=…` opened a live CDP
  endpoint on the packaged `navig-os.exe` (verified: `msedgewebview2.exe` bound 9332 and
  served `/json/version`), while a debug flag on the app's own command line opened
  nothing. All three shells now drop that variable before the first webview exists
  (`debug_env::strip_remote_debugging_env()`), `apps/anchor` and `apps/echo` gained the
  `build.rs` refusal `apps/os` has always had, and
  `tests/quality/test_release_debug_surface_guards.py` pins all of it — configs clean,
  every shell wired, `tauri-dev.ts` still the only `TAURI_CONFIG` setter.
