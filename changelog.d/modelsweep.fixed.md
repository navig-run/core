- **A provider can retire its ENTIRE catalog, and nothing looked.** `navig mode doctor` probes the
  models an install *routes* to; nothing probed the two hand-maintained lists those routes are
  substituted *from* — the registry manifest and `BUILTIN_PROVIDERS`, whose `models[0]` is both the
  credential probe and the routing substitution default. `navig ai models --check` is that sweep:
  it calls every catalog id once (1 token), marks which list named it, skips ids already denylisted
  and providers with no credential (reported as *not judged*, never as a tick), and prints a
  paste-ready `RETIRED_MODELS` entry per finding — the sweep needs live keys and cannot gate a
  build, the denylist is offline and already does. Exits 1 only for a retired id: not for a slow
  model, a busy free tier, or a missing key.
- **groq shipped eleven model ids and ALL ELEVEN were gone.** Audited by calling each one on
  2026-09-26: four answered 404 and seven answered `400 has been decommissioned`, so
  `models[0]` — the credential probe and the substitution default — was dead, and
  `navig ai providers --test groq` could only report "every known model id is retired". Replaced
  with the five ids that answered a real call (`openai/gpt-oss-120b`, `gpt-oss-20b`,
  `gpt-oss-safeguard-20b`, `qwen/qwen3.8-27b`, `allam-2-7b`); `whisper-large-v3` is in groq's own
  listing and is NOT included because it answered "does not support chat" — the listing is not the
  truth, the call is. All eleven are denylisted.
- **A retirement served as HTTP 400 was filed as an unclassified error.** `classify_probe_error`
  knew 410 and 404; groq's `400 The model X has been decommissioned and is no longer supported`
  matched neither ("no longer *supported*" is not "no longer *available*"), so all seven landed in
  the catch-all `error` — which `dead_modes()` does not report, so the heartbeat raised nothing for
  a provider whose whole catalog was gone. Now `dead`, requiring BOTH the retirement phrase and the
  word "model" (400 is also a malformed request), and confirmed by a second call like the 404.
- **A cold model paged the operator.** A read timeout was classified `unreachable`, which
  `dead_modes()` turns into a `[HIGH]` heartbeat issue and the gateway turns into an approval
  prompt. Measured: two NVIDIA ids reported `unreachable` twice each at a 40 s cap and then answered
  `live` in 107 s and 75 s cold. A read timeout means the connection SUCCEEDED and the model ran
  long, so it is now its own verdict, `slow` — retried like `unreachable`, but a defect to nobody.
  A *connect* timeout stays `unreachable` even though its name carries the word "timeout". The
  distinction is only legible because #1503 kept the exception type in the message.
  Per-consumer, deliberately: the plans drafter and skill distiller no longer answer a timeout with
  "No AI backend is configured or reachable — connect a provider" (they have one; it was slow), and
  the council still retries a `slow` route on the default provider.
- **The Telegram `/providers` small-talk tier offered mistral's LARGE VISION model.** The tier
  picker's generic branch matched ("mini", "8b", "7b", …) against the model list; not one matches
  any mistral id, so it fell through to `models[-1]` — `pixtral-large-latest` — while
  `mistral-small-latest` sat in the same list. `"small"` now sits next to `"mini"`; measured by
  calling the real function for every provider before and after, it changes mistral and nothing
  else. A new floor also asserts every provider's tier picks land on an id that provider still
  lists and that is not denylisted — 0 offenders today, and the mechanism that would create one
  has fired twice in eight days.
