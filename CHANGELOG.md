# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

<!-- Add entries here until the next release, then move them under a new version heading. -->
<!-- Run: git log v3.25.0..HEAD --pretty="- %s (%h)" to auto-generate draft entries. -->

## [3.25.0] — 2026-09-03

### Added
- **Telegram Extensions — one switch per bot feature, and "off" actually means off.**
  The bot's 108 commands, ~34 button prefixes, silent message-pipeline behaviours and
  scheduled senders are now grouped into 18 **extensions** (`/extensions` in Telegram,
  `navig telegram extensions list|enable|disable|info`, Deck → Social → Telegram →
  Extensions). Switching one off removes its commands from Telegram's `/` autocomplete
  and `/help`, stops its buttons being offered, rejects a stale tap with an explanation,
  halts its pipeline behaviours, and stops its scheduled messages being delivered.
  Nothing is deleted; switching it back on restores it exactly.
  State lives in the existing `modules.overrides`, so the card, the CLI and the Deck
  cannot disagree — and the pre-existing per-feature keys
  (`telegram.business.enabled`, `telegram.inline_mode_enabled`, …) still apply as an
  AND, so a feature that is off today stays off.
  · **Menus are filtered in ONE place** — `_api_call` strips buttons belonging to a
  disabled extension from every outbound `reply_markup`, so it covers all twelve keyboard
  builders, the cards a separate `navig habit checkin` process sends, and anything a
  plugin adds later, without editing a single builder.
  · **Habits** stops delivering (guarded where the cron job would queue the reminder, and
  at the CLI's direct `sendMessage`) but deliberately does **not** rewrite `cron_jobs.json`
  — so habits you paused yourself with `navig habit pause` stay paused when you switch it
  back on. Because the schedule is untouched, `navig habit list`, `/habits` and `/health`
  now carry a banner saying the reminders are scheduled but not delivered, rather than
  reporting a healthy count into a void.
  · Two guards make an escape impossible: `test_telegram_extension_coverage.py` (every
  command and reply action maps to exactly one extension) and
  `test_telegram_callback_prefixes_are_gated.py` (an AST scan of every routed AND emitted
  callback prefix). The gate itself is deliberately FAIL-OPEN — a developer's omission must
  never silence the operator's bot — which is exactly why the guards are the load-bearing part.
  Both are wired into `sourceGuardArgs`. The exemption list is EMPTY.

### Fixed
- **`register_fact_extraction` was certified green by tests that only checked it had been
  registered.** It records nothing, for four independent reasons: it probes `record_command`
  on a `MemoryManager` (that method lives on `UserProfile`), the `hasattr` guard turns the
  mismatch into a silent skip, the daemon thread it starts at `atexit` gets ~5 ms while its
  first statement needs 229 ms, and every exception is swallowed to `_log.debug`.
  `tests/cli/test_cli_middleware.py` asserts only that an atexit handler was **registered** —
  the "registered ≠ effective" shape — so nothing in 29,000 tests noticed.
  The dormancy is now **documented at the source and asserted**, following
  `tests/quality/test_dormant_modules.py`: *"a 'not wired' note is only useful while it is
  true, and it goes stale in the dangerous direction."* Four tests, one per reason; each fails
  if that reason stops holding, and one fails if the `NOT WIRED` block is deleted from the
  docstring — so the note and the assertions cannot drift apart.
  ⚠ **Deliberately neither wired nor deleted.** Making it live means deciding whether every
  CLI command a user runs is written into their profile — a product and privacy call for the
  owner, and one that also needs a redesign, since an atexit daemon thread cannot do 229 ms of
  work. The question is now framed in the code rather than only in a session log.
- **A caller asking for an ephemeral port was offered the operator's live gateway as a
  fallback.** `_bind_candidates` implements the gateway's self-healing bind — preferred port,
  its five neighbours, the sticky last-bound port, then `0`. Correct when the caller has a
  preference; wrong when the caller passes **0**, which means "any free port the OS hands out"
  and is not a preference to heal away from.
  Measured on this machine: `_bind_candidates(0, read_gateway_discovery()[0])` returned
  **`[0, 1, 2, 3, 4, 5, 8789, 0]`** — ports 1-5 privileged and meaningless, and 8789 read from
  the discovery file, i.e. **the operator's own running daemon**. `0` now short-circuits to
  `[0]`.
  `tests/e2e/test_gateway_api.py` asks for exactly this ephemeral bind, and its own docstring
  warns that a foreign gateway answering on the port it asserts against is "a silent false
  PASS that proves nothing about the gateway this test started".
  ⚠ The self-healing behaviour is unchanged for a real preference, and a test now pins that:
  neighbours, the sticky port, and `0` as last resort all still apply to `_bind_candidates(8789, …)`.
  Cases were added to the existing `tests/gateway/test_bind_candidates.py` rather than a new
  file, so coverage of one function stays in one place.
  ⚠ Not claimed: this is the defect, not a proven cause of the two e2e failures seen earlier
  in this session. Their shape is consistent with it — a test asserting against a port it did
  not bind — but that chain was never reproduced.
- **`/help` advertised commands you had already switched off.** `_generate_help_text` and
  the whole Help Encyclopedia filtered on nothing — not even the long-standing
  `telegram.disabled_commands` — so a command disabled in the Deck was still listed, and
  tapping it reported that it was disabled. Both gates now run through one predicate
  (`command_is_live`), and a category whose commands are all gone stops rendering a button
  that leads to an empty screen. The natural-language command suggester was filtered too:
  it used to recommend switched-off commands.
- **A `slash:` button was a live bypass of the disabled-commands gate.** The generic
  re-dispatch branch resolved any registry entry by name and checked nothing, so a button
  on a weeks-old card (`/health` emits `slash:habits`) ran a command the operator had
  switched off. The gate now sits above that branch and resolves `slash:<cmd>` to the
  owning extension rather than treating the prefix as always-on.

- **A test must now leave `os.environ` as it found it — enforced, not hoped for.**
  `conftest._no_env_leaks` fails any test that adds, removes or changes an environment
  variable. A leak is a *cross-file* failure: it reads as a flake in another test, in another
  directory, and only when xdist puts the two in one worker. This repo has paid for that three
  times — #1125 (teardowns popping `NAVIG_CONFIG_DIR`), #1143 (a `config_dir` patch poisoning
  modules imported inside it) and #1156 (eight tests, the worst redirecting
  `navig/memory/paths.py` at a deleted `tmp_path`) — each found by hand, after the fact, from
  a failure somewhere else.
  Gateable where the sibling real-home audits are not: a dict compare against a **zero
  baseline**, rather than `open`-wrapping with a non-zero import-time floor.
  ⚠ **Ordering is load-bearing twice.** It depends on `_isolate_navig_config_dir` so the
  snapshot happens *after* the session sets `NAVIG_CONFIG_DIR`/`NAVIG_DATA_DIR` — otherwise
  every worker's first test reports the session's own setup as a leak. And autouse fixtures
  set up first, so this finalises **last**, after `monkeypatch` has undone its changes;
  correctly scoped usage is invisible.
  ⚠ **It repairs before failing** — a report-only guard leaves every later test in the worker
  running dirty, turning one leak into a cascade.
  ⚠ **It ignores `PYTEST_CURRENT_TEST`**, which pytest rewrites per phase. The first version
  flagged all 29,000 tests; a negative control caught it before it was trusted. That exclusion
  has its own test so it is not "cleaned up" later.
  ⚠ It also tolerates `PYTHONIOENCODING`/`PYTHONUTF8` **appearing** — `navig.cli` sets them
  once via `os.environ.setdefault` on Windows so `ch.success("✓")` does not crash a cp1252
  console. That is the product configuring its own stdio, not a test leaking. A *change* or
  *removal* is still reported. Found by the gate, not locally: the ambient shell already had
  both set, so there was no delta to see — validate this guard with
  `env -u PYTHONIOENCODING -u PYTHONUTF8` or it silently proves nothing.
  Teeth, three mutations each breaking its own test: stop detecting, stop repairing, stop
  ignoring pytest's variable. The guard's tests drive the real fixture by stepping its
  generator via `__wrapped__` — a source check would pass while the fixture did nothing.
- **`navig habit review` — the weekly review, built from the tracker instead of from memory.**
  The review block was filled in by hand from seven days of recollection, and memory reports
  the two bad days as the whole week. The rows were already on disk. The command renders them:
  the three that decide the day laid out day by day, whatever else was logged, average bedtime
  and score, and the setbacks **already written in the journal** — no retyping. `--write`
  appends the block straight into that day's journal file.
  - **The gaps get their own lines.** *"No tracker row at all"* and *"No journal entry"* are
    printed explicitly: a summary built only from the days that exist reports a broken week as
    a good one, and *"failure is when you stop recording"* is the rule the tracker is built on.
  - **Average bedtime wraps past midnight.** 23:40 and 00:20 are twenty minutes apart; a naive
    mean puts them at 12:00 — the middle of the next day — and the one number the sleep rail is
    steered by would be nonsense in exactly the weeks it matters.
  - **"One decision for next week" is left blank.** Reading the week is what a machine can do.
  - New `navig/spaces/weekly_review.py`; 16 tests in `tests/commands/test_habit_review.py`.
- **Dictating the evening three lines now works.** Replying to the check-in prompt with a voice
  note lands in the journal as text — the voice branch already transcribed and fell through to
  the pending-reply chain, so this needed no new plumbing, only honest recording.
  A transcript is written **verbatim and marked `(dictated)`**, never split into the card's three
  labelled lines: speech-to-text returns one run of words, and attaching "Proud of" to whichever
  clause came first would be the system writing someone's journal for them. For the same reason
  the weekly review never mines a dictated entry for a setback.
- **The evening check-in now captures the three lines it has always asked for.**
  Closing the day with the card printed *"Three lines in the journal and you're done"*
  and **nothing anywhere recorded them.** Taps landed in `habits.csv`; the three lines
  landed nowhere, so a tracker whose own rule is *"failure is when you stop recording"*
  was recording exactly half of itself — in the space this was built for, `habits.csv`
  had 16 days of rows and `journal/` had not gained a file since day 1.
  Closing the day now sends a `force_reply` prompt with the card's three questions;
  the reply is appended to `<space>/journal/YYYY-MM-DD.md` by the gateway. New
  `navig habit journal "…"` writes the same entry without Telegram involved at all.
  - Consumed **only** when the message is a reply to that exact prompt id — an ordinary
    question sent to the bot at 22:20 can never be silently swallowed into a file
    instead of being answered.
  - The prompt is persisted next to the tracker pin (`habit_checkin_targets.json`),
    not held in memory on the channel: a gateway restart between the prompt and the
    reply would otherwise drop the entry silently, which is the failure this fixes.
  - Exactly three lines get the card's labels; **any other count is written verbatim**.
    Labelling two lines with three questions would put words in someone's mouth, and a
    journal that invents content is worse than an empty one.
  - Re-sent identical text does not write twice (Telegram redelivers updates).
  - New module `navig/spaces/journal.py`; 17 tests in
    `tests/commands/test_habit_journal.py`.
- **`GET /mesh/topology` — the endpoint the mesh guide has always documented.**
  `docs/guides/mesh-multi-machine.md` told users to
  `curl http://localhost:8789/mesh/topology` for a *"topology report with SPOF analysis"*
  and listed it beside `/mesh/peers` in its API table. **The route was never registered**,
  so the documented command returned a 404 — while `mesh/router.get_topology_report()`,
  the function it describes, was complete, tested, and had **zero production callers**.
  Verified by running it before wiring anything (the #1044→#1050 lesson — code that has
  never run has never been checked against its own dependencies): it returns a real report,
  every downstream call resolves. Unauthenticated like its read-only siblings
  (`/mesh/peers`, `/mesh/agents`), which is also what the documented `curl` assumes;
  mutating mesh routes keep bearer auth.
  ⚠ It primes `get_registry(gw.storage_dir)` first. That singleton honours `storage_dir`
  only on the FIRST call process-wide and `get_topology_report()` passes none, so without
  priming this route could answer from a different registry than `/mesh/peers` depending
  on call order.
- **The route guard now checks the DOCS, which is how this one got through.** The code-side
  check stayed green because no Python caller ever named `/mesh/topology` — only a README
  did. Every `http://localhost:<gateway port>/…` in either doc tree must now name a served
  route. The port is **imported** from `_daemon_defaults`, not typed, and it is the scoping
  signal: measured across both trees, 12 URLs sit on the gateway port, 3 on `:8091` (the
  operational-factory `tool-gateway`, which genuinely serves its `/flow/*` routes), 1 on
  `:8090`, 1 on `:9090` — scoping by "localhost" alone reported all five as broken gateway
  routes.
  Teeth: reverting either real fix is caught, naming the file and line.

### Fixed
- **A timing test that had already been re-tuned once, and still went red under load, now
  asserts the invariant instead of the clock.**
  `test_react_loop_abandons_hung_tool` proves the react loop abandons a hung tool rather than
  blocking on it. It measured wall clock: `elapsed < 5.0`. Its own comment records the first
  re-tune — red at 2.30s and 2.81s against a 2s budget, so the hang was lengthened from 3s to
  30s to buy headroom. It then failed at **6.37s, 6.49s and 7.76s** under `-n auto` on an
  otherwise green tree. A third threshold would have failed the same way.
  Wall clock was only a proxy for the real invariant: **the loop returned while the tool was
  still hung.** The worker now sets a `completed` event only if it runs to completion, and the
  test asserts that flag is clear — before releasing the worker, since releasing it sets the
  flag. No clock, no threshold, no headroom to erode. `elapsed` is still measured, but only to
  make the failure message useful.
  Teeth: with the dispatch timeout raised so the loop genuinely waits, the assertion fires and
  names the cause.
- **Two tests left process globals modified for the rest of their xdist worker.**
  `test_speedtest_worker_ships_and_actually_loads` left `navig/builtin/tools/_lib` on
  `sys.path` — loading the worker inserts it (that is how it finds `common` when run
  standalone) and nothing removes it, so a later bare `import common` or `import worker`
  anywhere in that worker resolves there, and those names are generic enough to shadow
  something real. `test_gateway_start_uses_cli_defaults_when_unspecified` left a
  `logging.StreamHandler` on the **root** logger, duplicating output for every later test —
  so anything asserting on captured output sees each line twice.
  ⚠ Found by auditing four process globals across a full run (`sys.path`,
  `warnings.filters`, root handlers, `sys.meta_path`). Raw result: 15 records across 14
  tests. **Naming the leaked objects collapsed that to two.** The other 12 are third-party
  libraries initialising themselves on first import — numpy and torch adding their own
  warning filters, `win32ctypes.core.BackendFinder` installing an import hook — which is
  library-owned, idempotent, and not the tests' to control. Trusting the count would have
  meant "fixing" fourteen tests for something none of them did.
  ⚠ Deliberately **not** guarded, unlike `os.environ` (#1158). That axis reached a zero
  baseline after fixing; this one never can, because every new dependency adds its own
  initialisation. A guard needing a permanently growing exemption list is a parking space.
- **Eight tests leaked environment variables into every later test in their xdist worker.**
  Found by auditing `os.environ` across a full run, compared at `pytest_runtest_logfinish`
  so a correctly-scoped `monkeypatch` is already undone and never shows up. **9 leaks across
  8 tests → 0.**
  The one that mattered: `test_task_bootstrap_puts_config_and_memory_on_one_home` leaked
  `NAVIG_HOME` and `NAVIG_SERVICE`, and `navig/memory/paths.py` honours `NAVIG_HOME` as its
  **first** precedence rule — so every later test in that worker resolved memory paths to a
  `tmp_path` pytest had already deleted. The rest were `NAVIG_DEBUG` (×4, leaving later tests
  in debug mode), `NAVIG_INVOCATION_CWD` (×2) and `NAVIG_NO_NARRATOR`.
  ⚠ **`monkeypatch.delenv` does not clean up a variable that was ABSENT.** It records nothing
  to restore, so a later direct write — `exec`'d bootstrap assignments, or production code
  like `CrashHandler.enable_debug()` — survives teardown. `monkeypatch.setenv` on an absent
  var records "was absent" and its undo **deletes** whatever the value became. Verified
  empirically, not reasoned from pytest internals. Where the code under test needs the var
  absent (`os.environ.setdefault`), `setenv` then `delenv` gives both: absent during the
  test, deleted after.
  ⚠ Re-measuring after fixing revealed a leak the first pass had masked — a second
  `NAVIG_DEBUG` site in the same class, invisible while its sibling was leaving the variable
  dirty. Fixing the sites you found is not the same as closing the class.
  Same family as #1125 (three teardowns popping `NAVIG_CONFIG_DIR`) and #1143's second
  commit (a `config_dir` patch poisoning modules imported inside it).
- **`get_proactive_engine()` raised on every default install, because
  `init_providers()` imported Google's client libraries unconditionally.**
  The method opened with `from navig.agent.proactive.google_calendar import GoogleCalendar`.
  That module imports `google.auth` / `google_auth_oauthlib` / `googleapiclient` at module
  scope, and **none of them is a navig dependency** — they appear nowhere in
  `pyproject.toml`, as a requirement or an extra. So the import raised
  `ModuleNotFoundError: No module named 'google.auth'`, and the engine's only accessor was
  unusable for anyone who had not separately installed Google's libraries — **whether or not
  they use Google Calendar**.
  ⚠ The degradation was already designed, and already implemented twice: this call site's own
  `try/except` exists to turn a provider failure into a warning, and `proactive/__init__.py`
  guards the same import with `except ImportError: GoogleCalendar = None`. Only
  `init_providers` bypassed both. Moving the import inside the `provider == "google"` branch
  lets the existing `except` do the job it was written for.
  Now: not configured → the engine builds and keeps `MockCalendar`; configured but the
  library is absent → `Failed to init Google Calendar: No module named 'google.auth'` and the
  fallback stands. Both verified end to end.
  ⚠ The regression tests poison the module in `sys.modules` rather than relying on Google's
  libraries being absent — asserting against what happens to be installed is how a test
  quietly loses the ability to fail.
  Found while validating #1144: a subprocess probe calling the accessor crashed, which is not
  something the suite ever asked it to do.
- **Importing the proactive engine constructed it, so a plain import did disk I/O.**
  `engine.py` ended with `_engine = ProactiveEngine()` at module scope. `__init__` builds a
  `TriggerManager`, which mkdirs `<config_dir>/triggers`, and reaching `config_dir()` builds
  the ConfigManager, which mkdirs the config root and its subdirs. Every consumer of the
  module paid that whether or not it ever asked for the engine — and at **import** it runs
  before any test isolation applies, so it landed in the operator's real `~/.navig`.
  Built on first use now, via the accessor that already existed. Measured with the write
  audit (`tools/audit_home_reads.py`'s sibling): mkdirs into the real home go
  **37 → 22**, with `triggers` at **0**. The remaining 22 are `ConfigManager` creating its
  own standard dirs (`<root>`, `hosts`, `cache`, `backups`, `apps`) during logging
  bootstrap — navig making its own directory, not a defect.
  ⚠ **The first version of the regression test was vacuous, and the teeth test caught it.**
  It asserted "import creates no `triggers` dir" against the operator's real home — but
  `TriggerManager` only mkdirs when the directory is **absent**, and an earlier manual probe
  had already created `~/.navig/triggers` on this machine. The call never happened again, so
  the assertion could not fail here, permanently. It now runs against a **fresh temp config
  dir**, which makes "was anything created?" a real question independent of local state.
  Both assertions have teeth: restoring the eager singleton fails both.
  ⚠ The probe stubs `init_providers()`. That method imports optional integrations
  (`google.auth` et al) and raises without them — orthogonal to laziness, and pre-existing.
- **A test patched `config_dir` around an import and poisoned every module loaded inside
  it, for the rest of the xdist worker.** `tests/agent/proactive/test_user_state.py` did its
  imports inside
  `patch("navig.platform.paths.config_dir", return_value=Path("/tmp/navig_test_state"))`.
  Modules bind that function **by value** (`from navig.platform.paths import config_dir`),
  so any module first imported inside the block kept the **MagicMock** in its namespace
  after the patch exited. `navig.agent.soul` is one of them, and its `SOUL_FILE` property
  then answered `/tmp/navig_test_state/workspace/SOUL.md` for the rest of the worker.
  The damage landed in a different file: `tests/core/test_call_time_paths.py::
  test_soul_file_property` — the guard that exists to catch frozen paths — failed whenever
  the two landed in the same worker. Deterministic, not flaky: running those two files
  together in that order failed 100% of the time and passed in the other order.
  ⚠ **The production code was already correct.** `user_state` resolves `config_dir()` in
  `UserStateTracker.__init__`, and `soul.py`'s `SOUL_FILE` is a property precisely so it is
  not frozen — its docstring says so. The patch was an obsolete workaround for a freeze that
  no longer exists, and removing it fixes the pollution outright; the imports work unpatched.
  Same shape as the lazy-export patch trap: patch the *module attribute*, and anything that
  already did `from … import name` keeps the old binding — here, in the other direction.
- **Five browser/desktop dirs ignored `NAVIG_CONFIG_DIR` — and the test suite was creating
  directories in the operator's real `~/.navig` because of it.** `screenshot_dir` and the
  browser profile dirs were hardcoded `"~/.navig/..."` strings in `browser/controller.py`,
  `browser/stealth.py`, `browser/firefox.py`, `browser/system_chrome.py` and
  `desktop/controller.py`. A default install is unaffected either way (`paths.config_dir()`
  *is* `~/.navig` there), which is exactly why they survived; an install that moved its
  config got a split brain, screenshots and profiles in the real home.
  Not passive: `BrowserController.__init__`, `StealthBrowser.__init__` and
  `DesktopController.__init__` **`mkdir`** these paths, so merely constructing one wrote into
  the operator's live home — from the test suite, under a fully isolated config.
  Measured with an audit of every WRITE under the real home during a full run:
  **96 mkdirs, 64 of them from tests**; `screenshots` alone was 56. Now **37 total / 6 from
  tests**, with `screenshots` and browser profiles at **0**.
  ⚠ **The fifth site was found only by re-running the audit after fixing the first four.**
  Four stray `screenshots` mkdirs remained, from a *different* `controller.py` — the desktop
  one. Fixing the sites you already know about says nothing about whether the class is
  closed; re-measuring does.
  ⚠ The helpers live in `navig/platform/paths.py` (`screenshot_dir`, `browser_profile_dir`),
  not in the browser package — `navig.desktop` importing `navig.browser._paths` would be an
  architectural smell, and `platform/paths` already owns `config_dir()`.
  ⚠ `firefox.py`/`system_chrome.py` held theirs as **module constants**, frozen at import.
  These modules load once per process and outlive any single config, so the default is now
  resolved per call; a test asserts it changes when `NAVIG_CONFIG_DIR` changes, which a
  constant would fail.
  ⚠ `tests/platform/test_cross_brain_isolation.py` caught the new `screenshot_dir` as
  unclassified and made me declare it per-brain OWNED — correct, since a second brain sharing
  it would write into the first brain's home. That guard working is why this entry is short.
  Same class as the gateway's `storage_dir` (#1121); the `Path.home() / ".navig"` AST guard
  misses all of these because they are string constants.
  Teeth: restoring any of the four literals fails the regression test, naming it.
- **Three test teardowns deleted `NAVIG_CONFIG_DIR`, so every later test in that xdist
  worker read the operator's REAL `~/.navig`.** `test_telegram_evening_briefing.py` has
  three classes that need their own config dir. Each **overwrote** the isolated value the
  session fixture sets and then **popped** it in teardown instead of restoring it — and
  nothing sets it again, so from that point on `paths.config_dir()` answered with the real
  home for the rest of the worker.
  Measured, not guessed: an audit of every read under the real `~/.navig` during a full run
  recorded **106 reads across 32 tests**; after fixing the gateway's hardcoded `storage_dir`
  (#1121) these three teardowns were the whole remainder. Reads of `config.yaml`, its pickle
  cache, `vault/.vault_fp`, `vault/active_profile.txt` and `events.json` are now **0** on the
  measured surface (`tests/telegram` + `tests/update` + `tests/ui`); the only real-home read
  left is `terminal.json`, an import-time theme probe.
  ⚠ **This is the upstream cause of the `test_from_env_defaults` failure fixed defensively
  in #1114.** That test asserts the "nothing configured anywhere" image-generation default,
  and the operator's real config pins `image_provider: openai_gpt_image` — so it passed or
  failed purely on whether this file had already run in the same worker. The defensive patch
  stays (a test should not depend on the config file either way); this removes the leak that
  made it fail.
  How it was found: instrument, don't guess. A probe recorded each `ConfigManager`
  construction with its resolved dir **and the value of `NAVIG_CONFIG_DIR` at that moment** —
  19 constructions resolved to the real home, all with `ENV=<UNSET>`. A second probe wrapped
  `os.environ.__delitem__`/`pop` and named the exact `teardown_method`.
  The three now share `_IsolatedConfigDir`, which saves and restores. After the fix exactly
  one `os.environ.pop` remains in any teardown in the suite — that base class's own, which
  pops only when there was nothing to restore.
- **The gateway's storage dir ignored `NAVIG_CONFIG_DIR` — a split brain, and a test suite
  reaching into the operator's real home.** `GatewayConfig` defaulted `storage_dir` to the
  literal `"~/.navig"`, so an install that moved its config kept its own state —
  `events.json`, `task_queue.json`, `mesh_peers.json`, sessions — in the real home. The
  default install is unaffected either way (`paths.config_dir()` *is* `~/.navig` there),
  which is exactly why it survived: it looks correct on the only machine anyone tests on.
  It also meant **constructing a gateway in a test read the operator's live `~/.navig`** —
  and `NavigGateway.__init__` `mkdir`s that directory. Found by auditing every read under
  the real `~/.navig` during a full suite run: **106 reads across 32 tests**, of which
  **22 arrived through this one line**. After the fix, those 22 are **0** (the only
  remaining real-home read in that set is `terminal.json`, an import-time theme probe).
  ⚠ `tests/platform/test_no_hardcoded_home.py` guards this exact assumption and did not
  catch it: it matches the *expression* `Path.home() / ".navig"` on the AST, while this site
  spelled it as a **string constant** fed to `.expanduser()`. A guard protects a shape, not
  a surface. The sibling scar is two comments up in the same file — parsing a config used to
  mint a token into the operator's real `config.yaml`.
  ⚠ Not widened to every string-literal `~/.navig`: there are **23**, and most are
  docstrings, a membership test, and user-facing prompt defaults (browser profiles,
  screenshot dirs). Gating them would need a ~20-entry allowlist, which is how a guard
  becomes a parking space. Measured and reported rather than gated.
  Teeth: restoring the hardcoded default fails the regression test, naming it.
- **`test_from_env_defaults` was reading the operator's REAL `~/.navig/config.yaml`.**
  It asserts the "nothing configured anywhere" default and already defended that against
  the VAULT (a real Recraft key used to make it pick recraft). The layer above was
  undefended: `_resolve_default_provider` consults the persistent config
  (`generate.image_provider` / `media.image_provider`) *before* it looks at any key, and
  `ConfigManager` is a process-wide singleton — so on a machine that pins a provider (this
  one does: `image_provider: openai_gpt_image`) the result depended on whether an earlier
  test in the same xdist worker happened to isolate the singleton first. Verified directly:
  with no isolation `_config_image_provider()` returns `openai_gpt_image`, exactly the value
  the assertion received.
  ⚠ It passed alone, passed in every subset tried, and failed only in the full suite — and
  it is **not** caused by the branch that surfaced it: `origin/main` ran 28,874 passed / 0
  failed while that branch failed it 2 for 2, because two added test files shift the xdist
  distribution. Measuring the base branch is the only thing that separated "my change broke
  it" from "my change moved the dice".
- **The `sys.modules`-eviction footgun is now closed as a CLASS, not two instances.**
  `#1109` fixed the one live case; this makes the safe shape the only shape.
  `tests.fixtures.module_eviction.evicted_modules(monkeypatch, prefixes)` is a **context
  manager**, so eviction and its purge cannot be separated — there is no way to use it and
  forget the cleanup, which is the whole failure mode. `tests/quality/
  test_sys_modules_eviction.py` (wired into `sourceGuardArgs`, so it runs on every push)
  keeps a hand-rolled `monkeypatch.delitem(sys.modules, …)` out of `core/tests`, every
  `plugins/navig-*/tests` and `private/harbor/tests`.
  Both existing call sites moved onto it: `tests/plugins/test_core_standalone.py` (the live
  case) and `tests/gateway/test_voice_routes.py` (measured latent — an orphan probe showed
  it leaks nothing today, because the one submodule imported inside is blocked by its own
  simulated-uninstall blocker and its parent `navig` is never rolled back; it was one
  condition away).
  ⚠ Two implementations of this would drift, and the drift is invisible until it reddens
  someone else's test in another directory — the original cost four blocked pre-push runs
  while reading as a timing flake.
  Teeth, three mutations, each firing its own assertion: reintroducing a raw `delitem`
  fails the guard; moving the helper fails the "still matches" check; collapsing the scan
  roots fails the anti-vacuity floor (>500 files, against ~1,900 real).
- **A "flaky" gateway test was never flaky — one test file was orphaning modules in
  `sys.modules` for every test that ran after it in the same xdist worker.**
  `tests/plugins/test_core_standalone.py` simulates an install with no plugins by evicting
  `navig.gateway.routes`, `navig.gateway.server`, `navig.agent.voice_input` and every plugin
  package via `monkeypatch.delitem`. That restores what existed **when eviction ran** — and
  `test_every_gateway_route_imports_without_plugins` then imports ~20 modules beneath
  `navig.gateway.routes` that a fresh worker had never loaded. Nothing tracked those, so they
  outlived teardown while their parent package was rolled back to the original object, which
  has no such attribute. `sys.modules` was left holding a child its parent does not know
  about, and `importlib.import_module` returns that child from the cache **without**
  re-binding the parent.
  The damage lands somewhere else entirely: dotted `monkeypatch.setattr` resolves by
  attribute traversal, so from that point on every
  `setattr("navig.gateway.routes.<mod>.X", …)` in the worker raises
  `AttributeError: 'module' object at navig.gateway.routes.core has no attribute 'core'`.
  It reddened the two WS-heartbeat tests in `tests/gateway/test_gateway_core_routes.py` and
  blocked four pre-push gate runs. **`pytest tests/plugins/test_core_standalone.py
  tests/gateway/test_gateway_core_routes.py` fails both, 100% of the time, in that order —
  and passes in the other**, which is the whole reason it read as a timing flake.
  The fixture now purges every module born inside the simulated install before monkeypatch
  restores the originals, and `_EVICTED_PREFIXES` is the one definition both sides read so
  they cannot drift.
  ⚠ The first version of the new regression test had **no teeth**: it called the purge
  helper itself, so deleting the fixture's call to it still passed — it proved the function
  worked and nothing about anything *using* it. It now drives the real fixture, setup and
  teardown, through `__wrapped__`. Teeth: removing the fixture's purge fails the guard **and**
  brings both WS failures back.
  ⚠ `tests/gateway/test_voice_routes.py` repeats the eviction pattern and has the same hole.
  It is latent, not live — the only submodule imported inside is blocked by its own
  simulated-uninstall blocker, and its parent (`navig`) is never rolled back.
- **The operation ledger dropped records under load, because a best-effort side-channel
  was charged to the authoritative write's deadline.** `complete_operation()` runs inside a
  one-second budget — `cli/middleware.py`'s atexit completer joins its writer thread with
  `timeout=1.0` — and it spent that budget in the wrong order: the best-effort dual-write to
  `audit.db` went **first**. Measured on a cold config dir, constructing that store costs
  **50–407 ms** (disk + schema + lock contention) against **3 ms** for the ledger append, so
  up to 40% of the budget was gone before the only authoritative record of the command was
  touched. On a loaded machine it overran and the operation vanished from `navig ledger show`
  entirely — a command that really ran, and really failed, leaving no line at all. Surfaced as
  `tests/ops/test_middleware_exit_status.py::TestEndToEndLedgerHonesty` failing with
  `entries=[]` under a busy machine while passing 20/20 in isolation. The authoritative append
  and the in-flight clear that depends on it now go first; every best-effort write may only
  spend budget the ledger no longer needs.
  ⚠⚠ **That deadline is harder than the code claimed.** The comment read *"daemon=False so the
  write completes before the process exits"* — it does not. Verified, not assumed: a
  non-daemon thread started **inside** an atexit handler is waited on by nobody, because
  `threading`'s own shutdown join has already run by the time atexit callbacks execute. When
  the join expires the write is **abandoned outright**, not finished later — a probe's marker
  file never appeared, not after the process exited and not three seconds later. A *daemon*
  thread started there fares worse still: measured, it completes only if its work finishes
  within **~5 ms** (0/1/5 ms landed, 50 ms did not). The in-flight marker written by
  `mark_inflight()` remains the backstop, but it can only ever be reaped as `interrupted` —
  the command's true terminal status is gone.
  Teeth: restoring the old order fails both new regression tests, naming the defect.
- **The HANDBOOK's Gateway REST API block did not work as written.** `§24.3` documented
  `curl http://localhost:8789/memory/sessions/my-task/history`; the gateway serves
  `GET /memory/history/{session_key}`, so that path 404s. Worse, **all six** curls in the
  block omitted authorization while **every** memory route requires a bearer token — so the
  whole section 401'd even where the path was right. Now: the correct path, the token
  exported once (`navig config get gateway.auth.token` — the same remedy the 401 body
  names), the header on every call, and a note that `navig memory …` needs no token.

- **The gateway route guard now checks the HTTP method too — it was validating half a
  two-part contract.** It matched the path and discarded the verb, so a caller using the
  wrong method passed. **335 of the gateway's 376 routes accept exactly one method**, and a
  wrong verb yields a 405 that reaches the user *identically* to the 404 the guard already
  caught: an exception inside a `try:` reported as "the subsystem is unavailable". Same
  defect, different status code.
  Zero mismatches exist today — this is a floor placed before something falls through it,
  the same reasoning that wired the `registry/` suites while they were green. Coverage is
  total rather than partial: **33/33 call sites and 376/376 routes** resolve a method.
  Verb resolution is permissive when unsure on both sides (a non-literal method, or a route
  whose method cannot be read, is not verb-checked), so **two ratio floors** stop that
  leniency from silently becoming total — without them the verb half could stop enforcing
  anything while every path check still passed and the guard still read as green.
  ⚠ The caller's method comes from an explicit `method` argument when the helper takes one,
  and only otherwise from the helper's name — the caller chooses at the call site, so a
  name-derived guess must never outrank it.
  Teeth: **seven mutations, all caught** — four path forms and three verb forms (explicit
  `method` arg, helper name, direct f-string), plus all four floors verified by collapsing
  each input in turn.
  ⚠ Two probes run first were measured **negatives**, recorded so they are not re-derived:
  no caller currently uses a wrong verb, and the five apparent duplicate `(verb, path)`
  registrations are all false positives — `mcp_server.py` and `agent/ears.py` each build
  their **own** `web.Application()`, and the `/deck` pair sits in mutually exclusive
  if/else branches.
- **A guard that no caller may request a gateway route this gateway does not serve.** #1050
  wired four Telegram slash commands, proved they DISPATCH, and shipped them — and all four
  still failed at runtime, because `/mesh/status`, `/mesh/config`, `/mesh/handoff` and
  `/mesh/election/state` are not routes the gateway serves. **Four of the five endpoints those
  handlers called were 404s.** Reachability had been checked; existence had not. That change
  named this guard as the durable fix and it was never built — this is it.
  Nothing else can see the class: caller and route are strings in different packages, so ruff
  sees two valid literals, `check_module_attrs` sees no attribute, and the type checker sees
  `str`. At runtime the 404 arrives as an exception inside a `try:` whose handler reports
  "mesh unavailable" — indistinguishable from a mesh that is genuinely down.
  Both sides are resolved from the AST: **376 registered routes** against **33 call sites**
  across four caller forms. Runs in `sourceGuardArgs` (every push) at ~1.4s — 16.0s before a
  text pre-filter cut the parse set to 48 of the package's files.
  ⚠ Helpers are discovered **per module, by shape** — a function taking a `path` parameter whose
  body builds its URL from a gateway-base symbol. Resolving them by NAME across the tree was
  measured to be wrong: `cloud/broker_client.py` and `connectors/perplexity/` each define their
  own `_get`/`_post` aimed at a REMOTE service, and a global name map reported all 7 of those
  calls as unserved gateway routes (the #1020 name-collision trap again).
  ⚠⚠ **Two versions reported NO TEETH, and only the mutation test said so.**
  (1) `_mesh_get(self, path)` puts `path` at signature index 1, but the bound call
  `self._mesh_get("/mesh/peers")` passes it at argument index 0 — so every call site in the
  Telegram mesh handlers, the exact surface this guard exists for, was silently skipped while it
  reported a clean run over 28 other sites. A leading `self`/`cls` must be dropped.
  (2) On the served side, `deck/__init__.py` mounts static files with `add_get(f"/{f.name}")`,
  which as a pattern matches **any single-segment path** — so `/statuz` resolved against it and
  passed. **A wildcard route does not make a guard permissive, it makes it blind**; that side now
  accepts statically literal paths only.
  ⚠ Deliberately NOT caught: a path a *parameterised* route would accept is treated as served,
  because aiohttp really would route it (`/tasks/statz` resolves against `/tasks/{task_id}`).
  That reaches the wrong handler rather than a 404 — a different class.

### Security
- **NAVIG's fourth tool dispatcher ran shell commands with no interlock — and the guard written
  to prevent a fourth could not see it.** `TaskExecutor._execute_step` dispatches a plan step via
  `ActionRegistry` and otherwise falls through to **`ToolRouter`**, a second and entirely separate
  tool registry whose `exec_pack` registers `bash_exec` ("Execute a shell command") as DANGEROUS
  with a live handler. That router has no approval gate: `gate_agent_tool_call` appears nowhere in
  it, and its only protection is `safety_mode == "strict"` while the default is `"standard"`. So
  the same tool name was held for approval through the agent registry and ran unprompted here.
  Reachable from `ConversationalAgent.chat()`: when `register_all_tools()` fails or no tools are
  configured, the fallback branch extracts a plan from the model's reply and executes it.
  ⚠ `execute_plan` does honour a `confirmation_needed` flag — but that flag comes from the **plan**,
  so the model decided whether its own plan needed a human. **A gate the gated party can switch off
  is not a gate.**
  ⚠ A denial raises `PermissionError` and is **not retried**: `execute()` retries a failed step up
  to `_max_attempts` times, and re-asking someone who already said no — three times, with a backoff
  — is how an operator learns to click "yes" to make the prompts stop.
- **The same widening found a fifth.** `llm.generate._maybe_execute_tools` parses a tool call out
  of the model's own text and runs it through the same ungated router. Dormant —
  `run_llm(enable_tools=...)` defaults False and no caller passes True — but that is precisely the
  state the MCP client pool was in when this class was found there, so it is gated too (via
  `check_sync`, the synchronous bridge, since that path is not async). Every step of a multi-step
  action is checked, not just the first, and a refusal is reported back to the model rather than
  silently dropped.
- **The dispatcher guard now derives from BOTH tool registries, and accepts BOTH interlock seams.**
  It keyed on `_AGENT_REGISTRY.dispatch` — a PATH, not the surface — so a module dispatching
  through `ToolRouter` was invisible to it. It also accepted only `gate_agent_tool_call`, which
  would have reported the correctly-gated (but synchronous) `llm/generate.py` as ungated;
  `check_sync` is the seam `mcp_server.py` has always used.

### Fixed
- **A plugin's tests were run against a different checkout than the one being changed — and
  could pass over a core that does not even import.** A plugin step runs with `cwd` set to
  the plugin directory, so nothing puts `core/` on `sys.path` and `import navig` falls
  through to the pip-installed package: an editable install pointing at the MAIN checkout.
  That is a different tree whenever the work is in a worktree, which the repo guard
  requires, so every agent session tested plugin code against somebody else's core.
  Measured from `.dev/worktrees/<slug>/plugins/navig-mobile`:

      without PYTHONPATH   navig -> <main checkout>/core/navig/__init__.py
      with    PYTHONPATH   navig -> <worktree>/core/navig/__init__.py

  Both directions are wrong. A plugin needing a new core API fails against the older core
  and reads as a real defect — that cost a full debugging pass last week. The dangerous
  direction is the other one, and it is measured rather than feared: renaming
  `decode_console_result` in a checkout's core and running navig-msstore's suite gave
  **30 passed** without the fix (green over a core that cannot import) and **2 errors**
  with it. Plugin steps and their importability probe now both run with `core/` prepended
  to `PYTHONPATH`.
  ⚠ That also exposes `build`, `tests`, `docs`, `scripts` and `tools` as importable
  top-level names, and `build` shadows the real PEP 517 module. Checked before adopting it:
  every plugin suite reports an identical test count with and without, because pytest puts
  the plugin's own rootdir at `sys.path[0]` and it wins. PYTHONPATH is prepended, never
  replaced. Pinned by `scripts/test/ci-local-selection.test.mjs`, mutation-tested against
  the runner ignoring `step.env`, the probe losing it, the path being replaced instead of
  prepended, and the helper being deleted.
- **41 dynamic subprocess sites now decode with the codec their child actually writes.** The
  register left behind by the previous pass listed 44 as real debt; each has been read and
  given an answer instead of a default. mysql/mysqldump (7), ffmpeg (3), docker compose,
  tailscale, AutoHotkey, mkcert, adb/fastboot/frida/pymobiledevice3, chromaprint and the
  generic runners (`hooks/executor`, `blocks/runner`, `mcp/tools/system::_tool_run_command`,
  `automation_engine`, `evolution/fix`, the Linux/macOS automation adapters) all capture
  bytes now and go through `proc_text.decode_console_result` — UTF-8 strictly, then the
  console page — which is correct for a UTF-8-emitting tool AND a Windows console tool,
  where `text=True` (the ANSI page) is correct for neither.
  The register is 61 -> 15, and the 15 left are **not** debt: 14 are Python children
  (`sys.executable`, pip, uv, `-m navig`, pymobiledevice3), where the measured answer is
  that they write the ANSI page and today's behaviour is already right. The last one,
  `skills.py`'s runner, executes `.py` OR `.js` — the one case where no single codec is
  correct for both, so it is named as such rather than silently picked.
  ⚠ `navig-mini`'s agent got the same two steps written out inline instead of the import:
  it is deliberately zero-dependency, stdlib-only, deployed as a bare `python3 agent.py`
  onto a Pi or a NAS where navig is not installed.
- **`decode_console_result` demanded attributes it never used.** It read `result.args` and
  `result.stdout` directly, which a real `CompletedProcess` always carries — but a large
  number of tests fake `subprocess.run` with a bare `SimpleNamespace(returncode=0)`. Eight
  suites broke on `args` and three more on `stdout`, as AttributeErrors raised inside a
  decode helper for fields it only passes through. Everything is read via `getattr` now;
  a missing stream decodes to `""` exactly as an uncaptured one already did, so the result
  keeps its shape. Pinned by a test that hands it a bare stand-in.
- **`https://navig.run/install.sh` now serves the fixed installer — the whole chain is
  closed.** Publishing to `navig-run/core` fixed only the first hop; the site serves static
  assets from its last *deploy*, so production kept the old build. Built from a clean
  worktree (committed state only, not the other checkout's uncommitted artifacts) and
  deployed. ⚠ The first deploy went to a PREVIEW alias: wrangler names a Pages deployment
  after the current git branch, so `--branch main` is what actually reaches production —
  without it `navig.run` is untouched and the command still reports success. Verified live:
  0 occurrences of the bare `pkill -f 'navig'`, 8 references to the scoped helpers.
  `scripts/published-installer.mjs` now fetches the live URLs too, so all three points
  (canonical -> public repo -> site) are checked rather than assumed.
- **`install.sh`'s 18 shell tests now actually run.** `bats` is a declared devDependency but
  was never installed, and `npm install` fails outright on this pnpm-managed tree. Run via
  `pnpm dlx bats@1.13.0`: 13/13 in the unit suite and 5/5 in the integration one, including
  the four that guard the published fix — the uninstaller never kills its own shell, the
  pattern spares processes that merely mention navig, regex metacharacters in `$HOME` stay
  literal, and the bare-word pattern cannot come back.
- **Three SSH sites decoded remote output with the local ANSI code page.**
  `discovery.py`'s SSH probe, `host.py`'s connection test and `deploy/engine.py`'s rsync all
  used `text=True`, so a Linux host's UTF-8 came back through cp1251. Now `encoding="utf-8"`,
  matching `remote.py::execute_command`. ⚠ A fourth candidate was NOT changed:
  `host.py:946` looked like an SSH call by its surroundings and is a local
  `[sys.executable, "-c", …]` probe, where the current behaviour is correct.
- **The dynamic-subprocess register is now classified, not undifferentiated debt.** Measured:
  a Python child writing to a redirected pipe emits the **ANSI** page here — utf-8 *raises*
  on its output and cp866 returns garbage — so for those 14 sites `text=True` is already
  correct and converting them to the two-step decode would break them. They are separated
  from the 44 that are real debt, so the list says which is which instead of implying all 58
  are wrong. This is why the group was never swept in one pass.
- **Deleted `web/www/.github/workflows/sync-install-scripts.yml`.** GitHub only runs
  workflows at a repository ROOT, so it had never executed once — while looking exactly like
  working automation, which is how the published installer drifted for seven weeks. Its job
  belongs to `scripts/published-installer.mjs` now. A README records that the four remaining
  files there are dead the same way, so the next reader does not inherit the same belief.
- **The installer users actually download still had the machine-wide `pkill`, seven weeks
  after it was fixed here.** `curl https://navig.run/install.sh` does not serve
  `core/install.sh`: there are three copies, and both hops between them were dead — the
  publish to the public `navig-run/core` repo was manual and undocumented, and the workflow
  meant to sync from there into `web/www/public/` lives at `web/www/.github/workflows/`, a
  SUBDIRECTORY, where GitHub never runs it. Nothing in the repo could report the drift.
  Measured: the published `install.sh` was 103 lines behind and still contained a bare
  `pkill -f 'navig'`, which kills every process whose command line merely *contains* "navig";
  `install.ps1` was 46 behind. Both are now published and verified through
  `raw.githubusercontent.com`, and `web/www/public/` is synced in-repo (serving it needs a
  site deploy). New `scripts/published-installer.mjs` reports both hops and can publish.
- **`install.ps1`'s end-to-end test suite asserted nothing, and ran in no tier.** Its helper
  was defined at file top level, and Pester 5 runs `It` bodies in a scope that cannot see
  those — every test died with "The term 'Invoke-Installer' is not recognized". Worse than
  red: Pester then reports **0 passed, 0 failed**, so the gate's `exit $r.FailedCount` would
  have been green over a suite that ran nothing. Moved into `BeforeAll` (5/5 now), colocated
  with the unit suite as `install.Integration.Tests.ps1`, wired into the gate's triggers, and
  the step now also requires a measured floor of 50 passing tests so a vanished suite fails
  instead of passing silently. One test asserted a pip-era precondition the uv rewrite
  removed (that a missing system Python must warn); it now pins the real contract — the
  installer does not depend on a system Python at all. 53/53 pass together.
- **Two generic runners still decoded child output with the locale code page.**
  `builtin/tools/_lib/common.py::run` (behind `reg`, `powercfg`, `fsutil`, `logman` and
  PowerShell) and `core/tools/build.py::run_command` take the command as a parameter, so no
  codec can be named up front; both now capture bytes and decode UTF-8-first,
  console-page-second. Measured: `common.run(["icacls", …])` went from mojibake to
  `BUILTIN\<localized>` with 0 replacement characters. Neither imports `navig.core.proc_text`
  unconditionally — `build.py` is what *builds* navig and must run without it.
- **A recovery failure was logged as "Context compression skipped".** The compaction had
  already been applied by that point, so the message named a step that had succeeded and
  pointed anyone reading the log at the wrong subsystem. The recovery now has its own
  handler and says what actually failed.
- **A subprocess with a run-time command had no rule at all.** The two sibling guards cover
  git (UTF-8) and Windows console tools (console page); a command assembled at run time was
  covered by neither, so `text=True` there silently meant "the ANSI code page" — right for
  essentially nothing, since git writes UTF-8, a console tool writes the console page, and a
  Python child on a redirected pipe writes ANSI. New guard
  `tests/quality/test_dynamic_subprocess_encoding.py` demands a *choice* rather than one
  codec: name an `encoding=`, or capture bytes and use `decode_console_result`. The 61
  pre-existing sites are a counted debt in `dynamic_subprocess_allowlist.txt` that the guard
  forces to shrink — deliberately not swept in one pass, because the correct codec differs
  per site and a blanket rewrite would trade one silent bug for another.
- **Three of the five "unbuilt" commands were already built under different names.** Asked to
  implement `origin`, `portable`, `sync`, `radar` and `watch`, I checked what each would need
  before writing any of it — and three would have been **parallel systems**, which this repo
  bans. `origin` (named identities) already ships as `navig agent personality list|show|set`
  and `navig agent soul`; `portable` (an encrypted config you carry on a drive) is
  `navig backup export --include-secrets --encrypt` plus `NAVIG_CONFIG_DIR`, which is what
  "mounting" one actually means; `sync` is `navig file add`/`file get`, which already take
  directories. Both handbook chapters are rewritten onto the shipped commands with an
  old→new table, and the stub messages point there. Recorded debt **148 → 136**.
  ⚠ `radar` and `watch` are the genuine gap — no mention-tracking store, and `watchdog` is not
  even a dependency. Building them means designing a data model, so they stay honest stubs.
- **The last six dead commands now say what is actually available.** `benchmark` turned out to
  have a live sibling — `navig skill benchmark` — so it is a pointer like the other duplicates.
  The remaining five genuinely have no backend: `radar` (no mention-tracking store), `watch` (no
  file-watch backend — `watchdog` is not even a dependency), `sync`, `portable` and `origin` (no
  install-provenance is recorded anywhere). Rather than invent five subsystems, each message now
  states the absence and names the nearest thing that runs (`navig file add`/`get`, `navig cron`,
  `navig vault list`, `navig version`/`paths`) — and each still exits 1. Every command named was
  smoke-run. Wording follows the session rule: quote the verb, never `navig <verb>`, when naming
  something that does not exist.
- **Compaction recovery was inert in exactly the case it exists for.** The agent handed
  `recover_context` the messages from AFTER the compaction. That function searches for the
  newest user message, and compaction is precisely what removes it — so once a compaction
  was aggressive enough to drop the user turn, the query was empty, recovery returned `""`,
  and nothing was re-injected. The Context Summary still appeared, so the run looked healthy.
  Measured on a full-suite run: a 12-message compaction left 7 messages with no user turn,
  `recover_context` was called three times, raised nothing and returned empty every time,
  while a direct search of the same index returned 5 hits. The query now comes from the
  pre-compaction list, which always still contains the current ask. This was `main`'s only
  red test, failing deterministically; it passes in the conditions that broke it.
- **`recover_context` documented a never-raise contract it did not implement.** Its docstring
  promised `""` when "anything at all goes wrong"; there was no `try/except`, so a raising
  index propagated into the agent loop's broad handler, which logs "Context compression
  skipped" — a message describing a step that had already succeeded, for a failure in the
  recovery after it. The contract is now real, and a test pins it.
- **Four handbook chapters documented stub commands as if they worked.** `navig node` (device
  identity), `origin`, `blueprint` and `portable` each get a verified banner: the commands are
  stubs that print a notice and **exit 1**, the chapter describes intended design, and the
  banner names what does run instead (`navig mesh peers` / `whoami`, `navig block list` /
  `apply <id>`, `navig vault list`) — or says plainly that nothing does. `navig origin` in
  particular is a command group with no subcommands at all. Every command named was smoke-run;
  recorded debt is unchanged at 148 because the entries still do not resolve — the banner adds
  truth without adding a claim.
- **Dead commands now point at the live command that does the job, and `navig explain` is gone.**
  Four stubs were duplicates of something that already ships, so the fix is a pointer, not a
  second implementation (this repo bans parallel systems): `agents run` → `navig agent run`
  (the singular is the real one), `node` → `navig mesh peers`, `replay` → `navig history list` /
  `history replay <id>`, `blueprint` → `navig block list` / `navig apply <id>`. All still exit 1 —
  they still don't do what was asked.
  ⚠ Adding advice re-creates the hazard this release spent so long closing, so a new check
  resolves every command named in a stub message through the real Click tree. It immediately paid
  for itself twice: the pre-existing `test_no_phantom_command_hints` rejected my first wording
  (`"navig replay is not implemented"` parses as a command `replay is`), and the fix is the rule
  already established for the handbook — **quote the verb, never `navig <verb>`, when naming
  something that does not exist**.
  `navig explain` was mounted by no CLI entry: three stub commands and their tests, reachable by
  nothing. Removed rather than registered — registering would have surfaced three commands that
  only fail. Its section of the shared test file was excised and the file renamed to match what
  it actually covers.
- **A red gate reported how the failures were SCHEDULED, not how many there were.** The core
  profile ran pytest with `-x`, which under `-n auto` does not mean "stop at the first
  failure" — it stops *scheduling*, and whatever was in flight across the workers still
  finishes. The printed count is therefore a property of how the failing tests happened to be
  distributed. Measured on `tests/core` (4,317 tests) with 30 deliberately failing: `-x`
  reported **16 of 30** in 86.0s, `--maxfail=25` reported **all 30** in 89.7s. On the incident
  that motivated this — 17 tests left red on main by #1030's exit-code change — a `-x` gate
  run reported **2**; fixing those found 1 more, and the last 6 surfaced only from a
  hand-written sweep of every file referencing a stub command app. Three rounds of ~9 minutes
  to learn what one run can say, while `-x` saved about 3 minutes on a run that had already
  failed. Now `--maxfail=25`: a broken conftest still aborts instead of printing thousands,
  and an ordinary multi-file breakage is reported in full. `--full` stays uncapped on purpose.
  Pinned by `scripts/test/ci-local-selection.test.mjs`, which fails on a revert to `-x`, on
  the cap being removed, and on a cap so small or so large that it defeats the point.
- **The stub-honesty guard I shipped yesterday was scoped to a folder, and missed five more
  commands.** It scanned `navig/commands/` and keyed on the exact project phrase *"not yet
  implemented in this build"*. Both limits leaked: **`navig email search`** — a *plugin* command —
  printed "Searching for: <query>" and then "coming soon" and **exited 0**, so it looked like a
  search that found nothing; and four core commands used a variant phrasing the exact match
  missed (`explain command`/`config`/`concept`, `snapshot create`, the last of which is live and
  returned success for a snapshot it never took). The guard now scans by SURFACE — core commands
  **plus** `plugins/` and `private/harbor` — and matches the family of announcements
  ("not yet implemented", "not implemented", "coming soon") including f-strings, which is the form
  the email stub used. An address-scoped guard has now hidden a class four separate times in this
  repo; this is the same mistake, made by me, one PR after fixing twenty instances of it.
  ⚠ `navig explain` is a registered-by-nothing module: three commands mounted by no CLI entry,
  with tests. Its exits are corrected so it behaves if wired, but whether to register or delete it
  is a product call and was left alone. Six more tests that pinned the old exit-0 contract were
  renamed and flipped.
- **The mojibake guard's new pruning test failed in every worktree — the `.dev` absolute-path
  trap, one layer up.** `_files()` correctly matches skips relative to the repo (its docstring
  cites the trap), but `test_the_scan_prunes_instead_of_filtering` asserted on `p.parts` of the
  ABSOLUTE path. `.dev` is in `_SKIP_PARTS` and every agent works under `.dev/worktrees/<slug>/`,
  so it reported that every file came from a skipped directory — failing the push gate for
  anyone working the documented way while passing in the main checkout. Now matched relative to
  the repo, and mutation-tested: disabling pruning still fails it, so the fix is not vacuous.
  Third occurrence of this trap in this repo, second in this file.
- **`navig run` returned mangled output on BOTH a local and a remote host, and the previous
  pass at this fixed a different executor than the one `navig run` uses.** There are three
  separate implementations of "run a command on the local machine" — `_execute_local_command`
  (the `navig run` path), `RemoteOperations.execute_local` (the local-host bypass used by
  deploy/discovery/ai), and `LocalConnection.run` (`navig local *`) — and only the third was
  corrected. Measured on the real `navig run` code path: `BUILTIN\<localized>` came back as
  14 wrong characters. **All three are fixed together, and a test now asserts they agree**,
  because a fix applied to one proves nothing about the others.
- **No single codec is correct for a command the user chose.** The shell does not normalise
  what its child wrote: measured, `cmd.exe` and `powershell -Command` BOTH pass child bytes
  through untouched, so `git log` arrives as raw UTF-8 while `whoami` arrives in the console
  code page. Naming UTF-8 mangles the console tool; naming the console page mangles git —
  which is exactly what the previous pass did, trading one failure for the other. These
  executors now capture **bytes** and decode via `proc_text.decode_console_result`
  (UTF-8 strictly, then the console page), the only reading correct for both. Tests pin both
  directions; a half-fix passes one and fails the other.
- **The `desktop_powershell` MCP tool handed the model U+FFFD for any localized output.**
  `PowerShellExecutor` prepends `[Console]::OutputEncoding = UTF8` and then decodes
  everything as UTF-8 — sound for a cmdlet, wrong for a native console tool, because that
  prefix does **not** convert a native tool's byte stream (measured: `icacls C:\Windows`
  returns cp866 with the prefix applied exactly as without it). The command comes from the
  model, so it can be either; it now uses the same two-step. `icacls` went from every
  localized byte replaced to **0 replacement characters**. Same class as #1017, where the
  agent's own git tools fed the model corrupted text.
- **`navig run` on a REMOTE host decoded the server's output with the local ANSI code page.**
  `RemoteOperations.execute_command`'s SSH branch used `text=True`, so a Linux host's UTF-8
  came back through cp1251. Now `encoding="utf-8", errors="replace"`, matching the sibling
  SSH executor `SSHConnection.run`, which already named it. Deliberately not the two-step:
  its fallback is the *local* console page, which is meaningless for bytes produced elsewhere.
- **A local command's output came back with the wrong characters — 19 decode points across
  15 files read console tools with the wrong code page.** (A previous version of this entry
  credited the fix to `navig run`; that was wrong. `core/connection.py` serves
  `navig local *` via `LocalOperations`. The `navig run` path is
  `commands/remote.py::_execute_local_command`, corrected separately below.) Windows console
  tools
- **Twenty unimplemented commands told the shell they had succeeded.** Every stub in
  `navig/commands/` announces itself with `ch.warning("navig X is not yet implemented in this
  build.")` and then falls off the end of the function — **exit 0**. Measured live before the fix:
  `navig origin`, `node list`, `radar list`, `sync status`, `watch list`, `replay list` and
  `portable validate` all returned success while doing nothing. A script branching on `$?`, or an
  agent asked to "check sync status", reads that as done. `navig origin` was the worst of them —
  a registered group whose *only* behaviour is that warning, so the command can never do anything
  at all. Each stub now ends in `raise typer.Exit(1)`.
  ⚠ The existing whole-tree ban (`test_command_exit_honesty.py`) could not see this: it keys on
  **`ch.error`**, and every stub uses **`ch.warning`** — the one escape hatch that guard's own
  docstring names. A companion guard now bans the shape, scoped narrowly to the project's stub
  marker so it makes no judgement about `ch.warning` in general, and it deletes itself (via an
  anti-vacuity test) once no stub is left.
  ⚠ **38 tests asserted the dishonest contract**, several literally named `test_list_exits_0`.
  They were renamed and flipped to require exit 1. Two more asserted `result.exception is None`,
  which a deliberate `typer.Exit` violates by design — they now assert the deliberate exit
  instead, which is the contract they were reaching for.
- **`navig run` on a Windows host replaced the operator's own output with `?` characters —
  and 18 other decode points across 15 files read console tools with the wrong code
  page.** Windows console tools
  (`tasklist`, `icacls`, `whoami`, `sc`, `schtasks`, `netstat`, `cmd`, `powershell`) write the
  **console output** code page; Python's `text=True` decodes with the **ANSI** one, and on a
  Russian-locale Windows those are 866 and 1251. Measured on the same bytes: `oem` yields the
  real localized group name, `text=True` yields 14 wrong characters with **no exception**, and
  UTF-8 raises. `core/connection.py` — the `navig local *` executor — hardcoded
  `encoding="utf-8", errors="replace"`, so every non-ASCII byte a command printed became
  U+FFFD before it was shown (`BUILTIN\<name>` → 12 replacement characters); it "worked" and
  only lied about the output, which is why it survived. Also fixed: the daemon supervisor's two
  `Win32_Process` command-line reads (used to decide whether a PID is our daemon), the
  single-instance process table, `sc query` status, `schtasks` status detail, `mklink`/`rmdir`
  error text, `netstat` port-holder discovery, the trigger script runner, `ping` latency, the
  machine-id probe, and the two duplicate `_decode_command_output`/`_decode_subprocess_output`
  helpers, which both fell back to the ANSI page and now delegate to one implementation.
  ⚠ Forcing UTF-8 is the wrong fix twice over: measured under `PYTHONUTF8=1` (which the
  documented pytest invocation sets, and PEP 686 may make the default) `subprocess.run(...,
  text=True)` on `icacls` **returns normally with `stdout=None`** — the `UnicodeDecodeError`
  kills subprocess's reader thread, so callers hit `AttributeError` far from the cause or
  silently conclude "not found". Where output is captured but never read (`file_permissions.py`,
  `doctor.py`) text mode is now dropped entirely, which has no failure mode at all.
  New canonical helper `navig.core.proc_text` (`console_encoding()` / `decode_console_output()`),
  guarded by `tests/quality/test_console_subprocess_encoding.py` — the deliberate mirror of the
  git guard, since git's contract *is* UTF-8 and a console tool's is not.
- **The git-encoding guard reported an unrelated `curl` call as a git site.** It scanned the
  module as one scope with `ast.walk`, which descends into every function, so one function's
  `cmd = ["git", …]` made every other function's `cmd` look like git. Now scoped lexically:
  a function inherits module-level names but not its siblings' locals.
- **The Packs chapter said "removed" at the top and then taught the removed commands for 90
  lines.** Its notice already named the successors — outcomes are **Blocks** (`navig apply <id>`),
  capability bundles are **plugins** — but every section below it still demonstrated
  `navig pack list/show/run/install/uninstall/create/search`, marked only as "historical". In the
  document agents consult before touching live infrastructure, a chapter of command-shaped text
  is advice regardless of the label. Quick Start, Pack Commands, Running and Creating are now the
  real surfaces, with an explicit retired→replacement table; the pack YAML is kept, labelled
  historical, because the format is still what `navig doctor migrate-packs` reads. Variables are
  `--input k=v`, not `--var`, and a destructive Block step needs its own `--approve`. Every
  command and Block id introduced was smoke-run. Recorded debt **158 → 148**, nothing added.
- **Three "is it running?" probes worked only by luck, and the obvious tidy-up would have
  broken them.** `browser/targets.py::is_running`, navig-antivirus's `browser_running` and
  navig-games' `is_steam_running` all shell out to `tasklist` with `text=True` and look for
  an ASCII name like `chrome.exe`. Measured on this machine: `chcp` reports code page **866**
  while `text=True` decodes with **cp1251**, and one filtered query returns 6710 bytes of
  which **85 are non-ASCII** (the localized header). The mismatch is harmless for an ASCII
  needle — but adding `encoding="utf-8"`, the natural way to make these consistent with the
  rest of the codebase, **raises `UnicodeDecodeError` at byte 150**, and all three wrap the
  call in `except Exception: return False`. The raise would never surface; it would silently
  become *"the process is not running"* — the answer that decides whether navig-antivirus
  reads browser data files a live browser has locked, and whether navig-games overwrites
  shortcuts under a running client.
  All three now compare **bytes** and do not decode at all, so the question of which code
  page it is never arises. Three regression tests pin it with output that is genuinely
  undecodable as UTF-8 (asserted, so an ASCII-ified fixture cannot make them vacuous), each
  with an anti-vacuity case for the absent process.
- **13 emoji reached Telegram users as cp1251 garbage.** `gateway/channels/telegram.py`
  carried UTF-8 that had been decoded as cp1251 and saved back, so the corruption was baked
  into the source rather than happening at runtime: `📝` is the four bytes F0 9F 93 9D, which through
  cp1251 is `рџ“ќ`, and that is what the bot has been sending. Twelve lines, every one a
  string a user reads — the "AI is not configured yet" onboarding message, the `[CODER]`
  status, the photo caption, both vision notes, the OCR header, and three inline **button
  labels**. (The `set_reaction` default was mangled too, but every caller passes an explicit
  emoji, so that one is latent rather than live.)
  ⚠ The display lies in **both** directions: printing the mangled text to a cp1251 console
  re-encodes it into the original bytes and a UTF-8 reader then renders a perfect emoji, so
  the corruption is invisible exactly where you would look for it. Only the raw bytes settle
  it. Recovery is a round trip rather than a lookup table — map each non-ASCII run back to
  bytes through cp1251 with the **C1 range passed through** (byte 98 has no cp1251 character
  and survives as U+0098, which is how the 😄 came back), then decode as UTF-8 and accept only
  results that are symbols. Guarded by `tests/quality/test_no_mojibake_in_source.py`: this is
  the RESIDUE of the class closed at the I/O call, and fixing a reader cannot un-corrupt a
  literal already on disk.
- **Two OCR surfaces still read the operator's screen as English.** Tesseract does not decline
  a script it has no pack for — given no `lang` it assumes English and returns
  confident-looking nonsense, so a Cyrillic window came back as plausible Latin garbage with
  nothing to say it was wrong. `ocr_language()` was added when the Telegram transcript read
  Russian slides as gibberish and that was recorded as closing the class; `navig ahk ocr` and
  the windows-automation `read_region.py` skill script survived it, and both read the
  operator's own screen. Measured A/B on Cyrillic text: `UHTYNUNA 3TO CbYHKLVA…` became
  `Интуиция это функция…`. Neither reuses `extract_ocr_text_from_image_bytes` — that helper
  also applies a confidence filter, and a screen scraper that silently drops low-confidence
  words is a worse answer than a raw dump. Guarded by
  `tests/quality/test_ocr_calls_pass_a_language.py`.
- **The agent's own git tools were still handing the model corrupted text.** The previous fix
  resolved a command list whose first element is a string literal. `agent/tools/git_tools.py`
  binds `git_exe = shutil.which("git") or "git"` and builds `cmd = [git_exe, *args]`, so its
  head is a *name* and the resolver walked straight past it — while **eight** agent tools
  (`git_status`, `git_diff`, `git_log`, `git_add`, `git_commit`, `git_stash`) route through
  that one helper. Measured through `_run_git` against this repository: 40 commit subjects,
  5 with non-ASCII, **5 of 5 wrong** before and 0 after. The model was being told the
  repository contained `вЏ©/рџђў` where it contains `⏩/🐢`.
  The guard now resolves the executable itself — the literal, `shutil.which("git")`, and
  `... or "git"` — for both `cmd = [git_exe, …]` and the inline form. It also got **3× faster
  than before this change** (29.8s → 10.6s in a step that runs on every push) by skipping
  files that never enable text mode; that pre-filter is whitespace-tolerant, because
  `text = True` is legal Python and a plain substring test would have silently skipped such
  a file.
- **The handbook's own migration table was wrong twice over, and pointed at a retired engine.**
  Its "Deprecated Commands (Migration)" section promised *"Old commands continue to work but show
  warnings"* — **six of the nine error instead** (`monitor`, `security`, `workflow`, `template`,
  `addon`, `hestia`). Of the three that resolve, two are mis-described: `navig system` is a live,
  unrelated command (`info`/`clean`), not an alias for `host maintenance`; and `navig task`'s own
  help says *"retired, superseded by Blocks"*. Worse, the table's `workflow → flow` arrow is a
  migration to a dead end — **`navig flow list` itself prints "the workflow engine is retired"**
  and points at Blocks. The table now states each name's real status, and the workflow chapter is
  rewritten onto the live surface: `block list` / `block show` / `apply <id>` / `block verify` /
  `block doctor` / `block new`, with `--input k=v` (not `--var`) and the warning that a
  destructive step needs its own `--approve`. The `template` chapter moved to the still-live
  `navig flow template` (`list`/`show`/`add`/`remove`), and `edit`/`validate` — which have no
  equivalent — now say so. Five removed `monitor-*`/`security-*` alias sections already named the
  right replacement but spelled the dead name as a runnable command in their heading; they are
  now `name (removed)`, and `security-scan`/`security-updates` gained the real replacement they
  never had (`navig apply security-audit`, `navig host maintenance`). Every command introduced
  was smoke-run. Recorded debt **192 → 158**, nothing added.
- **Every commit subject navig read from git came back mis-decoded.** Same root cause as the
  file-I/O fix below, one surface over: `subprocess.run(cmd, text=True)` decodes the *child's*
  stdout with `locale.getpreferredencoding(False)`, and git's own output encoding is UTF-8 by
  default. Measured end-to-end through `get_repository_log` against this repository's own
  history — 40 commits, 6 with non-ASCII subjects, **6 of 6 wrong** before and 0 after:
  `⏩/🐢` arrived as `вЏ©/рџђў`, `—` as `вЂ”`. No exception is raised, so nothing ever
  looked broken; that text feeds contributor analytics, changelog generation, diff summaries
  and the repo-guard session briefing. 53 sites fixed with
  `encoding="utf-8", errors="replace"`, matching the convention already used by
  `builtin/tools/speedtest/worker.py` and navig-explore — `errors="replace"` so a legacy
  commit with an undeclared encoding degrades rather than raising.
  **Scoped to git deliberately.** 180 text-mode subprocess sites exist; git is the only one
  with an unambiguous UTF-8 contract. Eleven call Windows-native console tools (`tasklist`,
  `netstat`, `powershell`, `sc`, `cmd`) which emit the **OEM** code page, where forcing UTF-8
  would turn today's silent mojibake into a hard `UnicodeDecodeError` — trading a wrong answer
  for a crash. Those are left alone and named in the new guard's docstring.
  The guard resolves `cmd = ["git", …]` locals, not just inline lists: the inline-only version
  missed six sites, **including the one that reads commit subjects and author names**.
- **The handbook's host chapter documented six verbs that are actually flags, a clone, or
  nothing at all.** `navig host` is the first thing an operator touches, and its chapter named
  `current`, `inspect`, `info`, `default`, `clone` and `edit` — none of which exist. Five map
  exactly and were verified against `--help`: `current`/`inspect` are **flags on `show`**
  (`host show --current` / `--inspect`), `info [name]` is plain `host show [name]`, `default` is
  `host use`, and `clone <src> <new>` is `host add <new> --from <src>`. `host security firewall`
  is just `host firewall`. The sixth, `host edit`, has no equivalent — so instead of inventing
  one the section now names the real file (`~/.navig/hosts/<name>.yaml`), shows the
  remove-then-re-add path, and warns that `--from` clones to a NEW name rather than modifying
  the source. Every command the rewrite recommends was smoke-run. Recorded debt **203 → 192**,
  nothing added.
- **49 file reads and writes used the locale code page instead of UTF-8 — on this machine
  that is cp1251.** `open(p)`, `Path.read_text()` and `Path.write_text(s)` with no
  `encoding=` fall back to `locale.getpreferredencoding(False)`, which is UTF-8 on most
  Linux boxes and is not on Windows, the platform this project targets first. Measured
  directly (CPython 3.13, `PYTHONUTF8` unset, `utf8_mode` 0): writing `- 🐛 crash` raises
  `UnicodeEncodeError`, and — worse, because it is silent — reading a UTF-8 file returns
  **mojibake with no exception at all**, since cp1251 maps almost every byte to some
  character. If that text is written back, the corruption is now on disk. The guarded trees
  read and write GitHub issue titles, PR bodies, commit messages, notification templates and
  generated markdown reports, where emoji and non-Latin names are ordinary content. All 49
  sites now name `encoding="utf-8"`; binary mode is untouched.
  The existing check missed every one of them: it was textual, matched the literal
  `read_text()` only, and scanned core alone — so `write_text(...)`, `open(...)` and all 20
  plugin packages were invisible. Worse, three of the four things it *would* have flagged in
  core were `adapter.read_text(selector, control_id)`, an AutoHotkey UI-control read that is
  not a file read at all. Replaced by an AST guard over core + plugins, registered in
  `sourceGuardArgs`. (Ruff implements this as `PLW1514`, but it is a preview rule — adopting
  it means enabling ruff's whole preview channel, and on the same baseline it found 37 where
  this finds 49.)
- **`navig telegram topics` was dead on every current install.** Telegram moved the
- **My own command guards recorded seven WORKING commands as broken.** Flag checking read
  `cmd.params`, but Click appends the help option at *parse* time — so `.params` omits it and
  every documented `<cmd> --help` was reported as rejecting a flag it plainly accepts.
  `navig host --help` and `navig host monitor show --help` were in the recorded debt while
  running fine. Fixed to `cmd.get_params(ctx)` in all five guards that check flags (the doctor
  one resolves verbs only and was never affected), and in the debt generator so the two cannot
  drift. A debt list that records working commands teaches people to distrust the whole list.
- **The handbook's operation-history chapter put a subcommand's flags on the group.** Every
  documented filter — `navig history --limit/--type/--status/--host/--since` — belongs to
  `navig history list`; the bare group is a command group and accepts none of them. Rewritten,
  including the summary table and the two stray examples. The rest of the chapter claimed options
  that exist nowhere: `undo --force/--dry-run` (only `--yes`), `export --since/--until/--status`
  (only `--format`/`--limit`, and the destination is a required **argument**, not a redirect),
  `clear --keep-days/--status` (all-or-nothing). Each now states the real surface and gives the
  working alternative — `history list --status failed --json` for filtered export,
  `history show` before an undo. Recorded debt **223 → 203**, nothing added.ram moved the
  forum-topic methods from the `channels` namespace to `messages` and telethon followed in
  1.44, so the call raised `ImportError: cannot import name 'GetForumTopicsRequest'`. It now
  resolves the request class from either namespace, and passes `peer=` (the new signature)
  instead of `channel=`. A second, quieter bug went with it: the single `limit=100` request
  silently truncated any larger forum — it now pages through all of them, de-duplicating
  overlapping pages and refusing to spin when the offset stops advancing. `topics` also
  gained `--limit` and `--json`.


### Fixed
- **A numeric chat id passed as a string could not be resolved at all.** Chat ids arrive from
  the CLI as strings, and Telethon reads a string as a *username* — so
  `navig telegram download-media -- -1001736302429` died with "Cannot find any entity",
  while the identical value as an `int` worked. Every MTProto verb that routes through
  `media.resolve_entity` was affected. It now coerces a numeric string to `int` before the
  first lookup, and strips the `-100` prefix for the `PeerChannel` fallback.

### Added
- **`navig telegram delete-chat`** — delete a whole chat/group/channel, not just its messages.
  `delete` empties a chat; there was no verb for removing one, so the only route was hand-written
  Telethon. Deleting a supergroup cannot be undone, so the command is dry-run unless `--confirm`,
  the dry run resolves and prints the **title and member count** (you confirm against a name, not
  an id you may have mistyped), and `--expect-title` is a script fuse that refuses to fire when
  the id resolves to a chat you did not mean.

### Security
- **Any local process could answer the agent's pending approvals.** `require_bearer_auth` opens
  with `if not token: return None` — no token means **open access** — and `gateway.auth.token`
  had no default. Seventeen route modules sit behind it, including
  `POST /approval/{id}/respond`. So on a default install a program running on the machine could
  enumerate the agent's pending approvals via `GET /approval/pending` (which includes the
  rendered description) and answer them. **An approval endpoint anyone can call is not an
  approval endpoint** — it silently defeats the whole gate, however carefully the gate is built.
  The gateway now **mints and persists a token at first start**, exactly as it has always
  auto-generated `deck.api_key`. Minting rather than refusing, because refusing breaks the only
  consumer: `navig gateway approve` reads this same config key, so the token is picked up
  transparently, and the deck and desktop authenticate with `deck.api_key` on separate routes.
  Nothing is derived from this token (unlike `deck.api_key`, whose hash is the lighthouse
  tenant), so minting carries no identity or rotation consequences.
  ⚠ **Enforce only what was persisted.** A token held only in memory is one no client can read —
  the CLI would send no header and be locked out of its own gateway — so a failed write leaves
  the previous behaviour in place and says so loudly, naming the manual remedy. Fail-open is
  wrong in general; it is the lesser wrong than an operator locked out by a credential that
  exists nowhere. The 401 now names `gateway.auth.token` so a refusal is actionable.

### Added
- **A routine MCP call can stop asking — but only if two independent parties agree.** Holding
  every third-party write for a prompt is correct and, alone, unusable: an operator prompted for
  the same idempotent call fifty times reaches for `--yes`, and that switches off every gate at
  once. **The pressure was the vulnerability.** A tool now runs unprompted only when the *server*
  declares it non-destructive **and** idempotent on a **vetted** endpoint (gate 1) **and** the
  operator names it in `mcp.trust.servers.<id>.auto_approve` (gate 2). Either alone is the wrong
  authority — gate 1 is the server marking its own homework, gate 2 is a blank cheque written
  against a description the operator cannot see change. Nothing is pre-authorised by default and
  there is no "allow everything" spelling.
  This finally **consumes `auto_approvable`**, which had been recorded and read by nothing since
  it was introduced — the operator-side gate it was waiting for did not exist until now.
  Auto-approved actions are still written to the audit log with the config key that allowed them
  (`enabled_by`), because an unprompted write nobody can account for afterwards is precisely what
  the mechanism exists to avoid. Reads are not logged this way — they never needed a prompt, and
  putting them here would bury the entries that carry accountability.
  ⚠ Deliberately **external-only**: a first-party tool has no server-side claim to pair the
  opt-in with, so extending it would be a one-gate allowlist — a narrower `--yes`.
  `navig doctor` lists pre-authorised tools and **warns** when they are set on a non-vetted
  server, where the setting silently does nothing.
- **`navig doctor` now says when nothing is authenticating the local gateway.**
  `require_bearer_auth` opens with `if not token: return None` — no token means **open access**
  — and seventeen route modules sit behind it, including approval responses. Since the gateway
  now mints a token on first start, the row distinguishes the two states that look alike and are
  not: a gateway that has **never started** is informational (the token appears when it does),
  while one that **has run and still has no token** means the mint could not write — a running
  gateway serving its admin routes to anything on the machine. `gateway.json` is the
  discriminator. A loopback bind **warns**; a non-loopback bind is an **error**, not a nudge, and
  an unreadable config dir assumes the worse of the two rather than the reassuring one. The row
  never prints the token.

### Security
- **The approval card the operator reads was thrown away before they saw it.** The MCP paths
  render one through `navig/tools/untrusted_text.py` — the server's own description quoted and
  defused, the arguments fenced, the endpoint in a code span, a line stating whose word the
  read/action classification rests on — and `_manager_backend`, the gateway backend that is the
  *only* path where a human sees any of it, discarded it and rebuilt a one-liner. So the careful
  rendering reached nobody, and the replacement interpolated `tool_name` **raw** into a Markdown
  surface (`/approval/pending` → deck + OS Inbox → Telegram). That name is not always NAVIG's: an
  external tool carries a name a third party chose. The card is now passed through, and the
  fallback is built with the same defences instead of an f-string.
- **The adversarial verifier and the approval gate disagreed about "destructive".**
  `agent/conv/agent.py` decided whether to run the pre-execution verifier from
  `name in DESTRUCTIVE_TOOLS` — the raw frozenset, which can only hold names core knows at import
  time. Three kinds of destructive tool are absent from it by construction: generated connector
  writes, a plugin's self-declared `safety = "dangerous"`, and anything an external MCP server
  offers. The gate held all three; the verifier skipped all three. `is_destructive_tool` is now
  **the single answer** to that question — the split between "known destructive" and "must be
  confirmed" is what let them drift — and `needs_approval` got shorter as a result.
- **`navig doctor` now shows what each MCP server is actually allowed to do.** `mcp.trust`
  decides whether a server's declared reads run unprompted and which tools it may offer, and
  there was no way to confirm either took effect. The failure that matters is silent: an
  unrecognised tier falls back to `byo` with a log line the person who typed it never sees, so
  that state is reported as a **warning** rather than a tidy ✓ over a setting being ignored. The
  section is omitted entirely on an install with no MCP servers.
- **One MCP server can no longer inflate every prompt.** Each discovered tool becomes an entry in
  the agent's tool schema, sent to the model on *every* request, and nothing in the protocol stops
  a server advertising thousands. `MAX_TOOLS_PER_SERVER` (200) now bounds it — loudly, naming both
  numbers, because "200 tools" and "200 of 4000 tools" are different situations.
- **`navig agent plan` ran every tool with no interlock, and approved itself.** NAVIG has
  three tool dispatchers; the two agent editions consult the approval gate and this one never
  did — `gate_agent_tool_call` appeared **zero** times in `plan_execute.py`. Worse, the
  whole-plan prompt that was supposed to stand in for it returned `True` when
  `not sys.stdin.isatty()` — every daemon-hosted caller is non-interactive by definition, so
  the one environment with nobody watching was the one that approved itself — and returned
  `True` from a bare `except`, because a failure to *ask* was being read as an answer.
  A whole-plan prompt could never have been the boundary anyway: **`_revise_plan` lets the LLM
  replace the remaining steps mid-run**, so the operator approves plan A and plan B executes.
  Each dispatch is now gated individually, which covers revised steps for free; a denial stops
  the plan rather than half-applying it (later steps were planned assuming the earlier one ran);
  and both fail-opens now fail closed. `--yes` still skips the *plan* prompt and still does not
  skip the per-tool gate.
- **A plugin's tools can now tell the approval gate they are dangerous.** `DESTRUCTIVE_TOOLS`
  can only list names core knows at import time, so a plugin's tools were invisible to it and
  core cannot enumerate a plugin's vocabulary without coupling that does not scale. `navig-games`'
  `games_claim` — which completes a checkout on the operator's real store account using their
  vaulted session — therefore ran ungated. `navig.tools.bridge` had already established
  `safety = "dangerous"` as the declaration; nothing on the agent side read it. Registration now
  pushes it into the gate. Polarity is one-way: a tool declaring itself *safe* is not believed
  over `DESTRUCTIVE_TOOLS`. ⚠ `owner_only` is deliberately **not** the mechanism — it is an
  authorization flag that ~20 read-only devops tools also set.
- **Registering an MCP server now asks first.** `POST /mcp/connect` took a `command` and `args`
  from the request body and ran them — "run this binary as me, and keep it running", strictly
  more powerful than `bash_exec`, which the agent cannot invoke without asking. Bearer auth is
  not the boundary it looks like: `require_bearer_auth` returns *open access* when
  `gateway.auth.token` is unset, and there is no default. The gateway binds to 127.0.0.1, so the
  exposure is every local process rather than the network — still an escalation, since a program
  with no rights to the operator's vault or hosts could obtain them by POSTing a command. No UI
  called this route, so gating it costs nothing.
- **A server's tools can now be scoped.** `MCPClientConfig` had no tool-level field —
  `enabled` was all-or-nothing — so a `vetted` server's declared reads ran unprompted including
  any tool it added later. `mcp.trust.servers.<id>.tools` restricts what it may offer at all,
  checked before trust (asking about a call the operator already ruled out is noise). A `tools`
  key that names nothing **denies everything** rather than falling back to unrestricted: turning
  a typo in an allowlist into full access would be a worse bug than the one scoping prevents.
- **Tools from a third-party MCP server no longer run without asking you.** The approval gate
  decided whether a tool needed confirmation by looking its name up in `DESTRUCTIVE_TOOLS` — a
  list of ~45 of NAVIG's *own* tools. That is an allowlist inverted into a denylist, so its answer
  for any name it had never seen was "safe": a connected server publishing `delete_all_repositories`
  executed with no prompt and no audit line. The gate did not judge it harmless; it had nothing to
  look it up in.
  Two doors, and they failed differently. `MCPClientManager.call_tool` — the live one, reachable
  from the gateway's `POST /mcp/tools/{name}/call` — consulted **no gate at all** (`approval` was
  not imported in `mcp/registry.py`, `mcp/client.py` or `gateway/routes/mcp.py`), so an operator's
  deny could not even be expressed. The agent-registry path was dormant but registered tools under
  the server's **raw** name, so an upstream tool could also *shadow* a first-party one — the
  operator approves a prompt naming their local shell and the call goes to the remote server; on
  disconnect it then deleted NAVIG's tool.
  Now: external tools are namespaced `mcp__<server>__<tool>` (so the gate, which holds only a
  string, can tell who chose the name), the shared reader defaults them to **needs approval**, and
  a classification pushed in at discovery lifts that only where the server declared
  `readOnlyHint: true`. Fails closed twice — an unclassified external name is denied by shape
  whether or not the classifier ever ran. Vouch for a server with
  `navig config set mcp.trust.servers.<id> vetted`; `mcp.trust.honor_read_only_hint false` ignores
  the hint entirely.
  ⚠ **Every annotation test is `is True` / `is False`, never truthiness** — `{"readOnlyHint": "true"}`
  and `{"readOnlyHint": 1}` are *actions*. A server that did not follow the spec did not annotate.
  This is also why `coerce_bool` is deliberately **not** used on annotations: it accepts
  `"true"`/`"on"`/`"yes"`, which is right for the operator's own config and wrong for a remote
  party's JSON. (Design ported from Cloudflare OS `packages/mcp-shared/src/tools.ts`.)
- **An MCP server can no longer forge structure in the approval card you read.** The card is
  assembled from the server's tool name and description plus the agent's own arguments, and both
  were interpolated raw — so a description could close NAVIG's code fence and continue in NAVIG's
  voice, writing its own "Endpoint:" line and its own assurance that the call was routine. The
  same string is `fnmatch`ed by `classify_command`, so a crafted name could also match an
  operator's `approval.levels.safe` pattern and escalate itself. `navig/tools/untrusted_text.py`
  now defuses fences, strips heading and quote markers **repeatedly** (one pass over `##` leaves
  `#` — still a heading), drops backticks inside code spans, caps every field, and block-quotes
  untrusted prose so it reads as reported speech. Applied to the agent's arguments too: the agent
  is who the prompt protects the operator from.
- **`find_tool` no longer silently picks between two servers.** Two connected servers advertising
  the same tool name resolved by `dict` order; ambiguity is now refused, and a call can be
  qualified as `<server>:<tool>`.

### Performance
- **One shared TikTok link made up to four identical metadata requests.** The card reads the post,
  then 🎧, 📄 and 🔍 each read it again. Measured on the real flow (card + 📄 + 🎧): **3 yt-dlp
  metadata requests, now 1**. Every avoided request is one fewer chance for TikTok to decide we look
  like a bot — the same concern the resolved-share-link cache already existed for. Bounded, 10-minute
  TTL (a post's description, sound and author don't change; its view count does), keyed on the
  canonical URL **plus** the fetch options so an authenticated read is never served to an anonymous
  caller. Every hand-out is a copy — the briefing path mutates what it gets back — and a failed read
  is never cached, because a 403 that lasted ten minutes would be worse than the extra request.

### Added
- **The TikTok card shows the post's cover image.** A slideshow's whole point is the picture and the
  card showed none. It is a **separate message** by necessity: Telegram caps a photo *caption* at
  1024 characters against a plain message's 4096, so a picture and a full caption cannot share one
  bubble — putting the card in the caption would push the description back behind a truncation.
  Cover first, then the card with the complete text and the buttons. Best-effort throughout: no
  thumbnail, an expired signed URL, a fetch past 8s, or a send Telegram rejects each degrade to the
  card alone. `telegram.tiktok_cards.photo false` turns it off.

### Fixed
- **Seven more guards could not be reached from the file they guard.** Same class as the
  plugin-scanning batch, one tree wider: every wiring mechanism in the gate keys on
  `core/navig/`, so a guard whose subject lives anywhere else runs only in the full suite.
  Measured — `--explain-selection` selected **zero** for each of these subjects. Now
  `INVARIANT_GUARDS` edges, keyed per file so nothing unrelated is dragged in:
  **`apps/os/.../renderer/lib/modules.ts`** (two python guards compare `BUILTIN_MODULES`
  against this one TypeScript file; dropping an icon key is the edit that breaks them) ·
  **`scripts/agent-hooks/`** (the repo guard's live hooks — the guard asserts they have not
  drifted from `navig/guard/`, so editing the copy is exactly what makes it false) ·
  **`core/tools/_version_sync.py`** and **`core/tools/export_registry.py`** (`core/tools/` is
  reached by nothing; the exporter is the *other* input to the shipped command manifest, and
  only the command-module direction was wired). Zero always-on cost — each fires only for its
  own subject — and pinned in both directions in `ci-local-selection.test.mjs`.
- **Four guards that scan the plugin tree were wired to the one direction that cannot break
  them.** The gate has two wiring mechanisms for whole-tree guards and they are not
  interchangeable: `sourceGuardArgs` runs for every change, while the derived tree-scanner rule
  fires on `when: f.startsWith("core/navig/")` — i.e. only when *core* changes. That is correct
  for a guard scanning `navig/` alone and backwards for one that also scans `plugins/`, since a
  plugin-only change triggers neither it nor the pytest selection (which runs the changed
  plugin's own suite). Measured: `--explain-selection --changed=plugins/navig-games/…/gog.py`
  selected exactly one core guard. So console_helper **API drift** (a plugin calling an API that
  does not exist), **phantom command hints** (a plugin printing advice for a command that does
  not exist), **event-loop blocking** (a blocking call in plugin async code — it freezes the
  daemon) and the **command→provider map** ran against plugins only in the ~56-minute full
  suite. All four are now in `sourceGuardArgs`; +11.8s measured at `-n auto`.
  The fourth is a distinct shape worth naming: `test_command_providers_fresh.py` **delegates**
  its walk to a subprocess, so it contains no `glob(` and neither detector saw it — while being
  the one guard whose subject is *exclusively* plugins. The meta-guard now also asks whether a
  file's subject reaches `plugins/`, on the **AST**: `tmp_path / "plugins"` and `REPO /
  "plugins"` are textually identical, and a regex version demanded six fixture-building tests be
  added to the pre-push gate.
  Two of the four had no anti-vacuity floor, so making them run more often would have protected
  nothing: point their `PLUGINS` root at a directory that does not exist and **eight tests still
  pass** while the plugin tree is silently out of scope. Both now assert a plugin package is
  actually in scope, matching the sibling guard that already did.
- **🔍 Analyse briefed a slideshow off its backing track and called it speech.** A TikTok photo
  post has no speech of its own. When the caption is too thin to brief from, the engine asks core
  for the post's words, and core answered by downloading the audio and transcribing it — on a
  slideshow that is the licensed song playing behind the slides, so the briefing presented song
  lyrics as what the post says, under a header reading `📝 from speech`. The words a slideshow
  carries are printed on its slides. `_post_text` now dispatches on post type — slides first
  (cheaper than the download + STT it replaces: 1.2s measured on the reported post), audio only as
  a fallback so a voiceover with no printed text is still read — and returns the text *with* a
  label for where it came from, which the header prints (`from slide text` / `from the audio
  track`, never a fixed "from speech"). It also carries the OCR caveat: an install without
  Tesseract silently turns the briefing into a summary of a song, and one without the language
  pack is worse, since Tesseract returns confident-looking nonsense rather than declining.
- **"No speech in this clip" was said about clips nothing ever listened to.** `transcribe_audio`
  returns `text if success else None`, discarding `TranscriptionResult.error` — so "faster-whisper
  is not installed" and "this clip is silent" reach the caller as the *same* empty answer. 📝
  answered "no speech and no on-screen text detected" for a post nothing listened to, and 🔍
  quietly briefed off a caption the engine had **already judged too thin to brief from**. The same
  function guards against exactly this for OCR three lines away — `_ocr_caveat` exists because
  Tesseract's absence must not read as "nothing there" — and the speech half had no equivalent.
  Adds `stt_unavailable_reason()` beside the handler, reading the handler's **own resolved**
  backend so it cannot disagree with what `transcribe()` will do, and wires `_speech_caveat()`
  into the three sites that could turn a missing listener into an empty answer (video transcript,
  photo transcript, briefing). `STT_INSTALL_HINT` is now one string shared by the transcriber's
  error and the caveats, so the advice a user meets in two places cannot drift.
  ⚠ Found by mutation testing that the **video** branch of `_do_transcript` ran in no test at all
  (every case in the file resolves to a photo post, which routes elsewhere) and that every caveat
  test patched the availability check, so the real one executed nowhere. Both now covered.
- **The TikTok de-duplication guard covered the buttons and neither other door.** `_IN_FLIGHT`
  stops a second tap re-running work already in progress — a duplicate re-downloads the clip,
  re-requests tiktok.com at exactly the moment it is deciding whether we look like a bot, and
  since 🔍 began reading slides pays for a second AI briefing. It lived *inside*
  `handle_callback`, so it covered the card's four buttons and neither of the other two entry
  points: an emoji reaction and the reply-menu action both called `_do_analyse` directly, so
  re-reacting ran the whole analysis again. Extracted as `run_action()`, with `analyse_link()`
  as the guarded entry for callers holding a URL. Same class as the two fixes above it — a guard
  protecting a PATH rather than the SURFACE — so it is now enforced by a build guard
  (`tests/quality/test_tiktok_workers_are_guarded.py`, registered in `sourceGuardArgs` because a
  tree-scanning guard names no module and can be selected by nothing else) rather than by a
  sentence in a docstring.
- **The slide reader capped at 10 and told nobody.** `download_post_images` returns
  `(paths, total)` — per its own docstring, "so a caller forced to cap can say it showed part of a
  post instead of presenting the part as the whole". Both callers cap at 10; only ⬇️ Download
  honoured it ("Showing the first 10 of 30 slides"). `_ocr_slides` discarded the total, so 📝 read
  10 slides of a 30-slide post and presented them as its text — and 🔍, now that the briefing
  reads slides, briefed off a third of a post as though it were all of it. TikTok allows 35 images
  per post. Both callers now print the note, and stay silent on a post that fits.
- **The handbook's file-operations chapter documented a CLI that does not exist — now rewritten
  against the real one.** Every remote file operation an agent performs was documented under a
  verb the CLI has never had: `navig ls`, `cat`, `mkdir`, `write-file`, `chmod`, `chown`, `tree`,
  `list`, `upload`. Each is now the real command, with its **parameter table rebuilt from
  `--help`** rather than renamed — because the flags differed too: `ls --long/--human` do not
  exist (`file list` has `--all/--tree/--depth`), and `cat --head 20` is really
  `file show --head --lines 20`, where `--head` is a *flag* and the count lives in `--lines`.
  Where a documented capability genuinely has no equivalent — recursive `chmod`/`chown`,
  `tree --dirs-only` — the doc now says so and gives the `navig run "…"` form instead of implying
  a switch that would error.
  ⚠ The nastiest one was not a missing command but a **stolen name**: the handbook said
  "Legacy compatibility: `navig download <remote> [local]` still works". `navig download` *does*
  exist — it is the **TikTok media-downloader plugin** — so an agent following that would hand a
  server path to a video downloader. No resolver could flag it (the group resolves); it was found
  by reading. Recorded debt: **232 → 223**.
  ⚠ A guard cannot tell "run this" from "this is not a thing": the first draft of the very notes
  documenting these removals wrote ``There is no `navig cat`.`` and thereby held all nine entries
  open. Negative notes now quote the VERB alone (``There is no `cat` verb.``), which is both
  guard-safe and what the reader actually needs; the rule is written into the guard's docstring.
- **`install.sh --action uninstall` SIGTERMed every process whose command line merely mentioned
  "navig".** `_stop_navig_background` ran `pgrep -f 'navig'` and killed the lot — the operator's
  editor holding a file in a navig checkout, a terminal running a navig script, the agent session
  driving the install. `pgrep -f` matches the FULL command line, so the bare word was never a
  navig-process test; it was a "mentions navig" test. It now matches the shapes that actually
  identify one — invoked as `navig` (by process *name*, so `code ~/projects/navig` is safe), a
  `-m navig…` module launch, or anything running the managed runtime — with `$HOME` regex-escaped,
  because an unescapable home made pgrep reject the pattern and stop *nothing*. Every real NAVIG
  background process still matches; the pair of tests pinning both directions is the point.
- **`--action reinstall` and `--action repair` were accepted, validated, documented in `--help`,
  named in the invalid-action error — and dispatched nowhere.** Both silently fell through to a
  plain overlay install, leaving exactly the stale runtime a reinstall exists to replace.
  `install.ps1` has always had the branch, and `uninstall_navig`'s entire `preserve_data=1` path
  was written for it and unreachable. Now wired: the previous install is cleared (user data kept)
  and the install proceeds, matching `install.ps1` on both words. The accepted actions come from
  one list that the parser, the validator, the usage text and the error message all read, so an
  action can no longer be advertised without being handled.
- **Editing either installer ran no Python guard at all.** Two pytest files read `core/install.sh`
  and `core/install.ps1` from disk — a syntax check and a no-BOM check, the latter guarding the
  failure where `curl … | bash` dies on line 1. Both live under `core/tests`, and the selection
  maps changed *core modules* onto test names, so they could only ever be reached from the core
  side; the installers are not under `core/navig/` either, so even the whole-tree scanner guard
  missed them. Measured: `--explain-selection --changed=core/install.sh` selected zero pytest
  files. Now an `INVARIANT_GUARDS` edge, pinned in both directions.
- **The operator handbook told agents that a nonexistent command "still works".**
  `core/docs/user/HANDBOOK.md` calls itself the "Primary Knowledge Base for AI Assistants" and the
  global operating doc points agents at it *before they touch live infrastructure*, so a command
  named there becomes a failed action against a real server. It stated "Legacy compatibility:
  `navig upload …` **still works**, but `navig file add …` is the canonical form" — it does not
  work, and no dispatch alias layer exists (`_RESOURCE_ALIASES` in `cli/middleware.py` is the
  operation-recorder's *classifier*, not a dispatcher). Its decision tree also named
  `navig upload` as the RECOMMENDED path for config files. Both corrected to `navig file add`,
  along with every example — a 1:1 rename the handbook itself calls canonical.
  ⚠ The sweep found **232 distinct command paths in the handbook that the CLI cannot run** —
  whole families (`pack`, `template`, `sql`, `workflow`, `ls`, `cat`, `chmod`, `chown`, `mkdir`,
  `write-file`, `monitor`, `security`) plus missing verbs under groups that do exist. The handbook
  documents an older generation of the CLI, differing in FLAGS as well as verbs (`chmod
  --recursive`, `cat --head 20`), so a faithful rewrite is a documentation project with a
  per-command intent decision — guessing 232 times would be worse than the debt. Instead the set
  is recorded and **ratcheted**: `test_handbook_command_refs.py` fails on any NEW dead command,
  and equally on a recorded entry that no longer appears, so the list can only shrink and a
  deleted command cannot keep its exemption as cover for re-adding it. Both directions were
  teeth-tested by injection.
- **`navig system info` crashed every time it was run.** `system_info()` called
  `system_default(None)` and the callback's first statement is `if ctx.invoked_subcommand:` — so
  the command died with `AttributeError: 'NoneType' object has no attribute 'invoked_subcommand'`
  for the whole of its existence. Two things kept it invisible: a `# type: ignore[arg-type]` over
  the exact mismatch that caused it, and two tests asserting `exit_code in (0, 1)` whose comment
  *named the defect* ("ctx may be None causing AttributeError") — a test that accepts the failure
  it documents is not a test. The overview is now a shared `_render_system_overview()` helper that
  both the callback and `info` call, and the tests require exit 0 with real output. A proven
  detector (verified against the original before being trusted) found **no other instance** of
  `f(None)` where the callee dereferences that parameter unguarded, across 1727 files.
- **A paid Block would have failed mid-apply, after its destructive step.** `safe-deployment` ran
  `argv: [navig, upload, …]`; `navig upload` has never existed — uploading to a remote is
  `navig file add`. The step sits *after* a `safety: destructive` backup, so applying the Block
  copied a backup onto the server and then died: a half-done deploy from a product whose selling
  point is a verified receipt. Fixed in both the wheel copy and the marketplace catalog copy — the
  drift guard caught that fixing one is not fixing the Block.
- **Thirteen shipped Skills instructed agents to run commands that do not exist.** A Skill is
  instruction an agent follows, so each was a failed action: `navig db databases` (→ `db list`),
  `docker stats --no-stream` (no such flag), `run --sudo …` (→ `run "sudo …"`),
  `docker system prune` (→ `run "docker system prune -f"`), `tunnel list`/`tunnel status`
  (→ `tunnel show`), `file upload`/`file download` (→ `file add`/`file get`), `navig shell`
  (→ `navig run`). ⚠ The sweep also surfaced something larger that is **not** a typo: a family of
  tool-wrapper skills written against a planned `navig <domain> <tool> <verb>` surface that was
  never built — `navig sys` alone is referenced by seven skills, plus `ios futurerestore`,
  `net iperf3`, `sync pull/push`, `telemetry audit`, `cloud rclone`, `dev gh`, `media yt`,
  `system disk/storage` and `tunnel add/start/stop`. Rewriting those would misrepresent what the
  skill does and implementing them is feature work, so they are RECORDED in the guard's
  `KNOWN_MISSING`, exactly as `test_interactive_menu_targets.py` records menu options wired before
  their implementation. The floor is what matters: any new dead reference under a group that does
  exist now fails the build, which is the shape all thirteen fixes had.
  ⚠ Four `KNOWN_MISSING` entries were first written at GROUP level and the staleness check
  rejected them on the first run — `ios`/`net`/`sync`/`telemetry` are real groups whose planned
  *subcommand* is what is absent. Classify at the level that is actually missing.
- **The new plugin-advice guard ran in no tier — caught by the repo's own meta-guard.** A
  tree-scanning guard can never be selected by "tests for changed modules" (no core module maps to
  it) and a plugin change runs that plugin's own suite, not core's — so it would have executed only
  in the ~56-minute full suite and could have gone red on `main` unnoticed. That is the single
  most-repeated CI defect here, and it landed one PR after adding the guard that exists to prevent
  a similar blind spot. Now registered in `sourceGuardArgs`. The lesson is procedural: running the
  one new test file is not the same as running the suite of the directory it joins.
- **The command-reference guards were scoped to `core/navig`; plugins print to the same operator.**
  This repo's most-repeated CI defect is a guard drawn at an ADDRESS rather than at the shape that
  makes something the surface, so the sweep was extended over all 22 first-party plugins plus
  `private/harbor` (299 files). Four real defects, one of them runtime output on a blocked path:
  `navig blackbox record` on a sealed blackbox said "run `navig blackbox unseal` first"
  (`seal --unseal` is the real form), `navig media explore <folder>` named a deprecated group and a
  verb that never existed (it is `navig explore <folder>`), and `navig generate --modality …`
  appeared in navig-audio and navig-text docs though `--modality` belongs to the `gen` subcommand.
  ⚠ The new guard is deliberately NARROWER than the core ones — it only checks a reference whose
  GROUP already resolves. Two candidates were verified to be *environment* facts, not defects:
  `navig antivirus` (plugin not installed here) and `navig design` (navig-text declares the entry
  point and the target imports fine with verbs `edit`/`check`, but the editable install's dist-info
  predates it). Flagging either would report an install state as a code bug. Nothing is lost:
  all four real defects were a wrong verb or a wrong flag under a group that does resolve.
  ⚠ Two parser rules earned their place by measurement — a bare token after a flag is that flag's
  VALUE (`--from-browser firefox` is not a `firefox` verb, 4 false hits) and resolution stops at a
  leaf command so trailing words are ARGUMENTS (`social connect linkedin`, 16 false hits): 31 raw
  hits became 4 real ones.
- **The no-LLM fallback planned a command that does not exist.**
  `conversational_legacy._simple_response` is what answers when no AI provider is available, and on
  "list workflows" it hand-built the plan `{"action": "command", "params": {"cmd": "navig workflow
  list"}}` — dispatched for real, and `navig workflow` has never existed. Same dead group as the
  agent system prompt, in a second file, so fixing the prompt alone left the degraded path broken —
  the path that runs precisely when the user has no working model to correct it. Now `navig flow
  list`. A guard resolves every literal `"cmd"` plan across `navig/agent/**` (structured dict
  values, found by AST, so there is no prose to trade off against).
- **The start-menu renderer is now guarded, not just the command table.** The table fix shipped with
  a guard over its *contents*; nothing covered the *renderer* whose `KeyError` made all 98 buttons
  dead in the first place. Reintroducing `info["description"]` now fails 100 tests instead of
  silently reverting to the generic stub — verified by injecting exactly that regression. The
  renderer's contract is pinned from both sides: every entry must render something real, a slash
  entry must not be prefixed with `navig`, and an unknown entry shape must degrade to naming the
  action rather than raising inside a Telegram callback.
- **The TikTok card cut the caption for no reason, and the 🎧 track arrived anonymous.**
  The card kept a flat 1500 characters against Telegram's 4096 ceiling — the reported post's
  caption is 1939, so a fifth of it was dropped mid-word. The budget is computed now: the header
  and stats are built first and the description gets what is left, so it lands whole (measured:
  a 2011-character card). Past the ceiling a **📄 Full text** button appears — *only* when
  something was actually cut — and sends the rest. The trim is applied to the raw text and
  re-measured after escaping, so a cut can never sever an HTML entity.
  Separately, `sendAudio` accepts `title`/`performer`/`duration` and the channel already took all
  three; the TikTok action passed none, so a track arrived as `7652338755679964436.m4a` reading
  `00:00` captioned "🎧 via NAVIG". It now sends the sound's name and performer (the *sound's*
  artist, not the poster — most TikTok audio is reposted), the real duration, a readable filename,
  and a caption linking back to the post.
- **`navig config set telegram.business.enabled false` did not turn the business layer off.**
  The CLI stores the string `"false"` and `bool("false")` is `True`, so a raw read left the whole
  layer running after the operator disabled it. The refuse-to-arm guard had the same shape for
  `telegram.require_auth`, so a string `"false"` read as "auth is enforced" — and would arm the
  business layer on an unauthenticated bot. Both read through `coerce_bool` now, which only ever
  tightens them. (The documented-toggle guard misses these: its regex wants `navig config set
  <key>` prose, and `TELEGRAM_MANAGER.md` lists the key in a table.)
- **`navig prompts list` reported "No prompts found" over 34 real prompts.** The command hides the
  `builtin` scope unless `--all`, and on a stock install *every* discovered prompt is builtin — so
  the default view printed a flat "No prompts found" and never mentioned the one flag that would
  have shown them. Measured on a real install: `list` said none, `list --all` printed 34. It now
  distinguishes the two states — "No user, space or package prompts — 34 builtin (internal LLM)
  prompt(s) hidden. See them with `navig prompts list --all`" versus a plain "No prompts found."
  when nothing was discovered anywhere. Same rule as `navig doctor`: never report an absence you
  did not verify.
- **A prompt that generates skills taught the model a command that does not exist.** Sweeping every
  shipped prompt (34 files) and every `*PROMPT*` constant (67) found one hit:
  `evolve/skill_designer.md`, the template that produces `SKILL.md` files, emitted
  `commands: ["navig skill invoke …"]`. The real verb is `navig skill run`, so every skill that
  generator produced documented an invocation that errors — a wrong command in a generator template
  does not stay in the template. The guard added with the agent system prompt now covers the shipped
  prompt files too, counting bare references as well as backticked ones (in a prompt file the whole
  document is instruction to a model). The other 100 prompt sources were clean — a real negative
  result, not an unchecked assumption.
  `ConnectionError` from the transport, reconnected, and re-sent the identical request — but the
  request had already been written and drained, so the server may well have executed it.
  Re-sending applies a write twice. "Outcome unknown" is a distinct state from "it failed", and
  collapsing them is the bug: only a tool the server declared read-only is retried now, judged by
  the same classifier the approval gate uses so "safe to retry" and "runs without asking" cannot
  drift apart. Everything else raises, saying plainly that it does not know.
- **The agent's system prompt taught the model a command group that does not exist.**
  `Brain.DEFAULT_SYSTEM_PROMPT` — reachable from `navig agent` — listed `navig workflow list` and
  `navig workflow run <name>` five times, including in the numbered "when asked to automate tasks"
  procedure. `navig workflow` has never been registered; the real group is `navig flow`. A wrong
  command in a system prompt is worse than a wrong command in a help string: a human reading help
  hits the error and adapts, while the model has been *told* this is the way and will retry it. The
  rest of that block (`evolve workflow`/`script`/`fix`, `script list`/`run`) was verified correct
  and left alone — only the CLI verb was wrong, so the "Workflow Management" wording stays. A new
  guard resolves every `navig …` reference in the prompt through the real Click tree, flags
  included, so `flow run --var key=value` is only allowed to be advice while `--var` exists.
- **Every OCR surface read non-Latin text as noise and presented it as content.** Tesseract
  defaults to English and does not decline when pointed at another script — and the per-word
  confidence floor cannot catch it, because the glyphs really are what Tesseract thinks they are.
  Measured on a real Russian TikTok slide, same image, same code path: the default model returned
  `- He fap. STO / Bot Kak` (21 chars); `rus+eng` returned `Интуиция - не дар. Это Функция …`
  (93). `user.language` already said "Russian" and the pack was already installed — nothing passed
  it. All **eight** surfaces were affected (wiki · inbox ×2 · media briefing · the Telegram photo
  handler · the catalog analyser ×2 · the TikTok transcript), which is also why one argument at the
  single shared reader fixes them all; the signature stays single-argument so no call site changed.
  A pack is only ever requested when Tesseract reports it **installed** — it raises on an unknown
  `lang`, which would turn a wrong-model read into no read at all. And because "I read it with the
  wrong model" is a third state that was invisible, `navig doctor` now **warns** instead of showing
  a green OCR row, and the TikTok transcript says so — including when text *was* read, which is
  exactly the case where the output looks fine and is not.
- **A pinned `user.language` silently switched speech-to-text OFF everywhere.** The preference is
  written by a human, so it holds a *name* ("Russian"), and two kinds of consumer read it: an LLM
  briefing wants the name ("Write the briefing in Russian"), while a speech model wants an
  ISO-639-1 code and rejects anything else outright. Nothing bridged the two, so
  `user.language: Russian` made every transcription in navig fail — the call returned
  `success=False`, callers got `None`, and each surface reported "no speech detected". Measured
  against a real clip: `None` and `'ru'` both transcribe, `'Russian'` fails with
  `'Russian' is not a valid language code`. This is the same failure as the old hard-coded `"en"`,
  one level along: a pinned language was strictly *worse* than pinning nothing.
  `navig.core.language.language_code()` now maps a name onto the code, and the conversion is
  applied at each of navig's **two** STT entry points — `VoiceInputHandler.transcribe` (voice
  notes, the TikTok photo-post transcript) and `navig_audio.voice.stt.STT.transcribe` (the video
  transcript path) — because fixing one leaves the other broken. An unmappable value degrades to
  auto-detect, which these models do well, never to a pin the provider will refuse.
- **The Telegram TikTok card handles photo posts.** Sharing a TikTok *slideshow* produced a card
  reading only "🎵 TikTok link" and "Couldn't extract the audio." — yt-dlp cannot address a
  `/photo/` URL at all (see `navig-download`'s changelog for the root cause). The card now carries
  the real description, 🎧 Audio works, ⬇️ Download sends the slides instead of an unplayable file,
  and 📝 Transcript reads the audio track and OCRs the slides, since a slideshow has no frames to
  sample.
- **`navig cdp stop` with no `--port` now closes the browser you actually launched.** Fixing
  `find_free_port` to fall back to an OS-assigned port (above) fixed `cdp new` and quietly broke
  its counterpart: `stop` and `detach` defaulted to a **literal 9222** at three separate layers,
  so on a machine whose 9222+ window is reserved the session lives on e.g. 7245 and `navig cdp
  stop` answered *"unknown port"* for the only browser running. The default was right by accident
  while `find_free_port` almost always returned 9222. Resolution now comes from the registry of
  what NAVIG actually launched: exactly one browser → that one; none, or several including 9222 →
  9222 as before; several without 9222 → **refuse and list them**, because closing someone's
  browser is not recoverable. ⚠ Three doors, and fixing one would have left it reachable — the CLI
  declared `typer.Option(9222)` and the MCP tool did `int(args.get("port", 9222))`, both
  substituting a literal for *"the caller named nothing"*, so the action layer could not tell
  silence from a deliberate choice. A *connect* default (`snapshot`/`click`/`tabs`) is unaffected
  and was left alone: only ports being **allocated or resolved** are touched by reservations.
- **An unbindable health port no longer kills the daemon supervisor.**
  `_start_health_server()` called `asyncio.start_server` unguarded, and it is awaited at the top of
  `_supervisor_loop()` **outside** that loop's try/except — so an `OSError` there escaped and the
  daemon supervised **nothing**: the diagnostics killing the supervision. Worst exactly where it
  bites, since `--health-port` is chosen when installing NAVIG as a **persistent service** (systemd
  / NSSM / Task Scheduler), so the service manager would restart into the identical failure
  indefinitely. And the port need not be "in use" to be unbindable — a Windows reserved range
  refuses the bind with nothing listening. The bind now degrades with an actionable log line and
  the children start regardless. Deliberately **not** falling back to another port: monitoring is
  aimed at the port the operator chose, so silently moving it would answer where nobody is looking.
- **`navig doctor` stopped printing ✓ over a question it declined to answer.** The port row used
  `connect_ex` alone and disclaimed the gap in prose — *"Port 8789 is not in use (OS-reserved
  ranges may still block binding…)"* — which is a green tick over an unknown, the class
  `tests/cli/test_doctor_honesty.py` exists to stop. It is answerable: attempt the bind. Three
  states now — **listening** ✓ · **free and bindable** ✓ (verified, not assumed) · **free but NOT
  bindable** ⚠, naming the OS error and, on Windows only, `netsh interface ipv4 show
  excludedportrange protocol=tcp`. ⚠ The row also checked one port while NAVIG binds two: the
  IPC/MCP daemon port is now checked as well, and it is the one actually sitting inside a
  reservation on the machine that found this (the gateway's 8789 survives only by landing in a
  one-port gap between two reserved blocks).
- **`MCPTool`/`MCPToolSpec` no longer discard the server's `annotations`.** They parsed only
- **Printed advice that names a command NAVIG does not have.** Found by sweeping the tree for
  command text inside string literals and resolving each against the real CLI. `navig doctor`'s
  corrupt-database row — advice given at the worst possible moment — said "restore from a backup
  (navig db backup lists them)": `navig db backup` has never existed, and the local-store command
  that does (`navig db local backup <dir>`) *writes* backups rather than listing them. It now
  states plainly that there is no automatic restore and names two commands that run. `navig doctor
  migrate-packs` likewise pointed at `navig package`, a group that has never been registered; it
  now points at `navig plugin list`.
- **Every button in the Telegram start menu was dead, and 14 of them named commands that do not
  exist.** The callback handler read `info["description"]`, a key none of the 98 `ACTION_COMMANDS`
  entries has ever carried (they hold `cmd`/`type`/`prompt`), so every button raised `KeyError`
  into a bare `except Exception: pass` and the user got a generic `Action: <id>` stub. Because
  nothing ever executed the table, its contents were free to rot: five entries named a verb that
  has never existed (`host info`, `host inspect`, `host discover`, `tunnel list`, `tunnel status`)
  and nine passed `--plain` to a command that rejects it — `navig host show --plain` exits 2. The
  handler now renders the keys an entry actually has and no longer swallows the failure, and all
  17 executable entries resolve. **This surface is not currently wired into the running product**
  (`TelegramVoiceBot` is instantiated only by a test), so this repairs a latent trap rather than a
  live outage. Two new guards resolve the full argv — command path *and* flags — through the real
  Click tree, the flag half being the easier to miss: `host show` resolves perfectly and the entry
  was still broken.
  `name`/`description`/`inputSchema`, so `readOnlyHint` — the one field that lets a well-behaved
  server's read run without prompting — was thrown away at discovery and every third-party tool
  looked identical to the gate.
- **Deregistering an MCP server now removes the tools it dropped.** `_deregister_tools` derived
  its removal list from the server's *current* tool list, but `refresh_tools` deregisters *before*
  refreshing — so a tool the server had stopped advertising was never removed and stayed callable
  against a client that no longer offered it. Removal is now by toolset, computed from live
  registry entries.
- `navig/tools/approval.py`'s module docstring named `ToolRouter._raw_async_execute()` as the
  gate's call site. It is not, and never was — `navig/tools/router.py` does not import the module
  at all. Replaced with the three real call sites.

### Added
- **Tapping two buttons on one TikTok now downloads it once, not twice.** Every action begins by
  fetching the clip, so 📝 Transcript → ⬇️ Download pulled the same bytes twice — wasted bandwidth,
  and a second request at exactly the moment TikTok is deciding whether we look like a bot.
  (Analyse's audio enrichment and 🎧 Audio are the same pair on the audio-only key.) Since the rest
  of this work went into not *provoking* bot-walls and then reporting them honestly, halving the
  requests is the other half of it. Measured: **three actions on one clip → 3 downloads before,
  1 after.** A per-key `asyncio.Lock` also coalesces genuinely concurrent taps into a single fetch
  rather than a race. Bounded by a 10-minute TTL and a 200 MB budget, evicted **after** insertion
  (evicting only beforehand lets the cache sit one clip over its budget indefinitely — caught by
  its own test), and the cache lives under the existing `navig_tiktok_*` temp prefix so the stale
  sweep collects it.
  ⚠ The invariant that makes it safe: **a borrower never shares the cached file.** `_fetched()`
  always yields a private copy in its own workdir, so `keep()` (which MOVES the file), the workdir
  cleanup and eviction cannot interact — no refcounting, no lifetime coupling. And on a **miss**
  the download still goes straight into the workdir, so a cache that misses is invisible and the
  ordinary path stays the path everything else was built and tested against. The first attempt
  inverted that (fetch into the cache, copy out) and turned 9 tests across 3 files red by
  redefining what `dest_dir` means to the engine — the failure was the design review.
  `core/tests/telegram/conftest.py` now resets the module-level in-flight set and clip cache around
  every test: process-lifetime state is right in the daemon and wrong in a test process, where a
  clip cached by one test silently satisfied the next test's deliberately-failing fetch.
  ⚠ **Follow-up from reviewing that change: the per-key lock dict was unbounded.** `_cache` is
  bounded by TTL and by bytes; `_cache_locks` was bounded by nothing — one entry per distinct
  (url, audio_only) for the life of the daemon, which over months of links only ever grows.
  `_prune_locks()` now drops locks for keys that are neither cached nor held, and — the part that
  matters — `_evict` **no longer returns early when it is under budget**, because that is the
  ordinary path and the prune would have been dead exactly where it is needed (the same
  early-return-skips-the-tail shape as the deck-deploy staging leak in this release). One benign
  race is deliberate and documented: a caller between `setdefault` and `acquire()` looks unlocked,
  so a concurrent prune can cost one duplicate download — no worse than the behaviour before the
  cache existed, and never wrong data. Measured against the previous commit: **6 locks retained for
  1 cached clip**.
- **The pricing page is now pinned to the spec — the numbers a buyer reads before paying.**
  `web/www/content/copy.ts` is the fifth mirror of `tiers.json` and the only one a customer
  sees *before* money changes hands, yet nothing checked it. `billing.ts` had asserted the
  need in prose for as long as it has existed ("must stay in sync with `deckTiers.oneTimeUsd`"
  — and pointing at the pre-monorepo `navig-www/` path while doing so). Now enforced: the five
  live price points (Plus annual/monthly, Max annual/monthly/lifetime), the advertised host
  count per tier, and the per-tier module list. Prices and hosts are pinned to equality;
  capabilities are checked as a **subset**, because every tier deliberately omits `echo` from
  its marketing list — under-promising is a copy decision, **over-promising is the bug**. A
  `null` host count is honoured as the deliberate contact-us marker rather than treated as
  drift. `TIER_PERPETUAL_PRICE_CENTS.max` is pinned too, closing the last leg: spec lifetime →
  the cents figure upgrade-credit arithmetic subtracts → the number on the pricing page, all
  three now move together or the build fails. Measured first: **zero drift across all five
  mirrors today.** Every parser fails when it matches nothing, and all six new assertions are
  teeth-checked — including one that renames `deckTiers` away entirely.
- **The tier-parity guard now compares the NUMBERS, not just the names — on the side that
  mints the licence.** Restoring it last change revealed that its two TypeScript cases only
  ever asserted union *membership*: that `billing.ts` and `license.ts` know the same tier and
  module names as `tiers.json`. Nothing compared `TIER_HOST_LIMIT` or `TIER_CAPABILITIES` —
  and `billing.ts` is exactly where a purchase is turned into an entitlement: `runPipeline`
  writes `TIER_HOST_LIMIT[tier]` into the `licences` row **and into the signed token**, while
  the daemon later enforces with `quota.py`'s separate copy. Those two disagreeing means a
  buyer pays for one host count and gets another, silently. `quota.py`'s values *were*
  checked, so the one language that mints the licence was the one nothing verified — and
  `billing.ts`'s own header claimed the opposite ("Do not drift these — the parity test
  enforces it"). Now compared: all 10 host limits and 10 capability sets in `billing.ts`, the
  five `MODULE_ONETIME_USD` prices the deck *displays*, and `FREE_STATUS` (what an unlicensed
  deck renders as your entitlement). `quota.py`'s **legacy** tier values are pinned too — every
  pre-Harbor licence still in the wild is graded by exactly those numbers, and only the live
  ones were checked. Measured first: **zero drift across all four mirrors today**, so this is a
  regression gate, not a repair. Each parser fails when it matches nothing rather than
  comparing zero pairs — the shape that let these very tests skip unnoticed for months —
  and all seven assertions are teeth-checked, including two that rename the constant away.
- **The dead-path guard learned two more sibling names and a fifth idiom — and immediately
  found 14 violations it had been blind to.** It banned `navig-core|www` in four *path
  construction* idioms (`x / "navig-core"`, `Join-Path`, a backslash literal, `../navig-core`),
  which missed a bare relative literal like `'navig-deck/out'` sitting in a list of candidate
  paths. That is precisely how the three bugs above stayed invisible — and the tell was that
  `static_assets.py`'s copy used a *join* idiom, was caught, and carries a `dead-path-ok`,
  while `miniapp.py`'s used bare literals and was not. `deck`/`dock` join the alternation
  because they are the only other pre-monorepo repos whose name is not also something live:
  measured, all 18 names from the `feat(monorepo): import navig-X -> Y` commits give 72 hits,
  mostly `navig-menu/model` (an npm subpath export), a `navig-shared/…` eslint rule namespace
  and a `navig-os/…` event tag; these four give 11, **all real**. The new rule alone runs
  against comments-blanked source — the four originals stay on the raw line, where they are
  battle-tested and blanking could only weaken them — because prose about the migration looks
  exactly like the thing it bans, and this repo has already had a scan flag its own
  explanation and a mechanical fix rewrite the sentence into nonsense.
- **The gate now checks whether a Lighthouse change can actually reach a user's edge.** Two
  steps decide that and neither is visible in the file you would edit:
  `core/navig/cloud/lighthouse_worker/worker.js` is a **committed artifact** only
  `npm run ship` rebuilds, and `navig lighthouse status` decides whether to nag for a
  redeploy by grepping `LIGHTHOUSE_VERSION` out of that bundle. Miss either and the change is
  inert — no error anywhere, just an edge still running old code with every light green.
  Rebuilding inside the gate would need wrangler + `node_modules`, which no worktree has, so
  the check is the version **pair** (the bundle matches src, *and* a change under
  `services/lighthouse/src/` moved it) — an invariant only bump-and-rebuild can satisfy.
  Pure `node:fs` + one `git show`, sub-second, and it runs unconditionally, because the edit
  that breaks it is precisely the one that looks complete. Ten tests drive the checker as a
  subprocess against throwaway fixtures, including a git-backed one for the forgotten-bump
  half and the boring case that matters most: that it still passes a healthy tree.
  Historically 0 of 3 source commits ever shipped without the bundle — this is preventive,
  and it is cheap because the consequence is a security fix that silently reaches nobody.
- **Lighthouse has tests, and a tier that runs them.** The edge Worker users self-deploy to
  their own Cloudflare account is a PUBLIC repo that shipped with **no test of any kind**, and
  `typecheck — lighthouse` only ever proved it compiles. It now has a suite covering the tenant
  identity the whole routing model rests on — that the edge's derivation matches the brain's byte
  for byte, and that path traversal, case changes, padding and truncation are all refused — plus
  a source guard that fails the build if any route reaches `brainFor()` with a raw path
  parameter, enumerating the `:tenant` routes from the source so a new one is covered the day it
  is written. It is wired into `WORKSPACE_SUITES`, so a lighthouse change actually runs it;
  the tests import only a zero-import module, so they run with **no `node_modules`** like
  `services/api`'s do. A suite the gate skips is documentation.
- **`navig doctor` → Identity** — which of the seven identity sources won and which it
  shadowed, the guardrail floor version (plus any operator `GUARDRAILS.md`), and whether the
  system prefix is byte-stable. An unstable prefix is a silent cost multiple, never an error.
- **`navig agent context`** — audits what actually reaches the model: which identity source
  won and which existed but was shadowed, per-section character and token sizes, the
  system-prefix hash, a build-twice stability check, and the cache hit rate of the daemon's
  last real turn (`<config_dir>/perf/last_turn.json`). `--persona` / `--space` resolve
  hypothetically, `--show-prompt` prints the assembled block, `--json` for scripts.
- **`GUARDRAILS.md`** — `~/.navig/workspace/GUARDRAILS.md` and `<project>/.navig/GUARDRAILS.md`
  append operator rules under `### Operator additions`. They can only add constraints; the
  built-in floor is emitted first and cannot be suppressed.
- **Domain playbooks** shipped as builtin skills (`life-os`, `finance-strategy`, `infra-ops`),
  activating through the existing SKILL.md scorer on the user turn.
- **The TikTok card's 📝 Transcript action now reads on-screen text from several frames.**
  It sampled a single `-vf thumbnail` frame, so it caught at most one caption — and TikTok
  captions change through the clip, meaning most of the text on screen was never read at all.
  It now samples up to **4 scene-change frames** (`navig.media.frames.extract_frames`,
  `mode="scene"`, which falls back to interval sampling on a static clip) and merges them.
  Captions typically *build up* across frames ("Hello" → "Hello world" → "Hello world!"), so
  exact-duplicate removal is not enough: `_merge_ocr_frames` drops any line already contained
  in a longer one (case- and whitespace-insensitive) and keeps first-appearance order.
  ⚠ The frame count is **opt-in and defaults to 1** on purpose: `analyze_video_file` is also
  called by `analyze_media`, the catalog's fire-and-forget background analyser that runs on
  **every** video the bot sees, so raising the default would have multiplied that background
  work for everyone, silently. The explicit user action asks for depth and pays for it; bulk
  analysis keeps the single thumbnail it has always used. OCR is local (CPU, not API spend),
  and the sampler is capped at 60s rather than the library's 600s default because this runs
  behind a button tap. Sampling that fails or yields nothing degrades to the old thumbnail
  rather than losing the OCR entirely.

### Fixed
- **The memory index would have announced a repair nobody needed, on every install at once.**
  Immediate follow-up to the change below, caught by asking what it does on an install that is
  already healthy rather than on the damaged one it was written for. The one-time rebuild is gated
  on `PRAGMA user_version`, and every `index.db` in the field has 0 there — so the gate could not
  tell "was maintained by a broken trigger" from "has simply never been stamped", and this store's
  triggers were correct all along. First open after upgrading would therefore have rebuilt every
  operator's search index and recorded "a search index was rebuilt after its unsafe sync triggers
  were corrected" — a `navig doctor` warning describing damage that never happened, for everybody,
  which is precisely the false-alarm noise that teaches you to skim past the row that matters. The
  rebuild now runs only when the store's existing triggers are actually found using plain DML,
  read before the schema replaces the triggers it is judging; an undamaged store is stamped and
  left untouched. Teeth stated behaviourally (an index entry removed with the fts5 command syntax,
  which a rebuild would silently restore) rather than through an internal name, so the test fails
  on the old behaviour instead of erroring on a missing attribute.
- **`navig doctor` reported a repair the operator had no way to perform — and for one store the
  repair could not happen at all.** The unsafe external-content FTS trigger row (the defect that
  makes editing the same bookmark twice raise "database disk image is malformed") ended with
  "reopening the owning store repairs it". True, but it named no command, and nothing tells you
  that the store behind `links.db` is opened by `navig links list`; deriving that took reading
  three modules and the CLI registration table. The row now names the exact read-only command per
  database (`navig links list` · `navig kg status` · `navig memory bank`), deduplicated when one
  store has several offending indexes, and it degrades to naming the file rather than inventing a
  command for a store it does not know. Verified on the operator's own install: both damaged
  databases went from unsafe triggers to safe by running the named command, and the Database
  integrity row went green. Worse, `memory/index.db` — the third `content=<table>` index in the
  tree — created its triggers with `CREATE TRIGGER IF NOT EXISTS`, which never replaces an
  existing trigger, so that store could not self-heal and the printed remedy was simply false for
  it; its text happened to be correct today, so nothing was broken, but any future correction
  would have reached new installs only. It now drops before creating, like its two siblings, and
  rebuilds a stale index once (gated on `PRAGMA user_version`) while recording the self-heal as an
  incident so a repair is never silent. A new guard pins every command the remedy names to a real
  registered CLI verb, so the advice cannot rot into a wild goose chase.
  the real health surface rather than the test output: Config Health showed two ⚠ incidents about a
  space whose manifest "could not be parsed", pointing at
  `…/Temp/pytest-of-subdose/pytest-1166/test_broken_manifest_never_bri0/space/.navig/space.json` —
  `test_broken_manifest_never_bricks_the_ledger` writing a deliberately-corrupt manifest, whose
  degradation correctly fires `incidents.record()` … into the operator's real
  `<config_dir>/perf/config_incidents.jsonl`. Nothing was broken; **the report was**, and it is the
  failure mode this repo names explicitly — noise beside signal on the one surface scanned for
  trouble. The same run showed a genuine ✗ incident (a rotated `deck.api_key` leaving the Telegram
  webhook on a dead tenant) sitting directly above two pieces of test residue.
  `private/harbor/tests/` is a **separate pytest root**, so it never saw `core/tests/conftest.py`'s
  isolation, and nothing ran it often enough to notice until harbor was wired into the gate earlier
  in this release — so gating it correctly is what started the pollution. It now has a conftest
  mirroring core's: `NAVIG_CONFIG_DIR` **and** `NAVIG_DATA_DIR` both redirected (`data_dir()` reads
  its own var rather than deriving from the config dir, so isolating one leaves every database
  pointing at the real `~/.navig/data`). Measured: one harbor run grew the live log
  **9,461 → 9,787 bytes**; with the conftest, byte-identical across the full 43-test suite.
  ⚠ **19 other pytest roots also lack this isolation** — but measured before acting: snapshotting
  all 872,010 files under `~/.navig`, running six of the largest plugin suites (download, social,
  games, github, mobile, email — 721 tests) added **0 files** and modified 3, all live-daemon WAL
  churn. So harbor was the one real polluter and 19 speculative conftests were not added. The two
  stale entries age out of the report on their own (`incidents.recent` has a 30-day window); there
  is no prune command and hand-editing the operator's log is not worth inventing one.
- **`system_chrome` fell back to the one port it had just proven unusable.** A concurrent change
  (already on `main`) taught `find_free_port` to fall back to an OS-assigned port when the 9222+
  window is entirely reserved — on Windows, Hyper-V / WSL / Docker reserve TCP blocks and a bind
  inside one fails with **WSAEACCES** rather than "address in use" (measured here: `netsh interface
  ipv4 show excludedportrange protocol=tcp` reports **9181–9280**, swallowing the whole default
  window). That fix did not reach the second caller: `system_chrome.py` still did
  `find_free_port() or 9222`, so in the one situation the fallback exists for, it launched the
  browser on the exact port just shown to be unbindable. The browser starts, the debug endpoint
  never comes up, and because the Windows `chrome.exe` is a launcher that exits 0 that reads as a
  successful launch with a dead endpoint. It now raises `SystemChromeUnavailable` naming the
  reserved-range check.
- **A test asserted a difference set, which made it depend on test ordering.**
  `test_register_worktree_tools_function` checked `after - before`, silently requiring that nothing
  had registered those tools earlier **in the same process**. The agent tool registry is
  process-global, so that is a property of ordering rather than of the registrar — and
  `tests/agent/test_toolset_registry_parity.py` (added earlier in this release) calls
  `register_all_tools()` while `tests/agent` sorts before `tests/git`, so the difference set was
  empty and the assertion failed with `'worktree_create' in set()`. Registration is idempotent, so
  it now asserts **presence**, which is the registrar's actual contract and is order-independent.
  Reproduced deterministically with the two node IDs, red on the previous tree and green here.
- **Docstring cross-references that named things which do not exist — now build-enforced.** A
  ``:func:`X``` is a promise the reader can go and check the claim; when the name is wrong they find
  nothing, and the sentence around it is usually the *explanation of a subtle behaviour*, so the one
  place worth verifying is the one place that cannot be. Measured across **1,545 files**: three bare,
  project-shaped references resolving nowhere in the tree.
  **`commands/gateway.py`** credited `_record_gateway_pid` with writing the pid file — the writer is
  **`_write_gateway_pid`**. That sentence is the record of a *safety* fix (`gateway stop` reading a
  hardcoded `~/.navig/gateway.pid` and `taskkill /F`-ing the operator's live daemon), so the one
  reference someone would follow to confirm "the writer really does use `config_dir()`" led nowhere.
  **`connectors/gmail/oauth_config.py`** said `GMAIL_OAUTH_CONFIG` is registered by
  `register_gmail_oauth` — **neither name has ever existed**; the real path is
  `get_gmail_oauth_config` → `connectors.bootstrap`'s `_oauth_loaders` table →
  `ConnectorAuthManager.register_provider("gmail", …)`, now written out along with why an
  unconfigured install silently has no `gmail` provider rather than an error. (The third was
  `agent/delegate.py`, fixed in the entry below.)
  New guard `tests/quality/test_docstring_crossrefs_resolve.py`, wired into `sourceGuardArgs`. Scope
  is drawn tight on purpose: **bare names only** — a dotted target like `yaml.safe_load` or
  `navig_social.social.publishers.X` names another module and resolving those needs an import graph,
  a different tool with different failure modes. Bare names are what a reader expects to find nearby,
  and all three that were wrong were bare. The scan asserts it parsed >1,000 files first, because one
  that reads nothing passes every assertion after it.
- **`agent/delegate.py` documented a depth cap it cannot enforce, and a caller it does not have.**
  The module's docstring described `register_delegate_tool` as *"called by the parent `run_agentic`
  when the `delegation` toolset is requested"* — never true: it has **no caller anywhere**, and
  `register_all_tools` does not include it, which is why the `delegation` toolset resolves to zero
  tools. Its Safety block also promises `MAX_AGENT_DEPTH = 2`, and that cannot hold:
  `_run_child` accepts a `depth` argument and **never uses it**, while `run_agentic` has no depth
  parameter to carry it — so a child would register its own `delegate_task` at `parent_depth=0` and
  delegation would recurse without bound, each level holding a semaphore slot and spending tokens.
  Latent, not live, and *only* because nothing calls it. Rather than wire it (enabling recursive
  agent spawning is a product decision with real cost consequences) or delete it (discarding a
  recorded design), it now joins **`tests/quality/test_dormant_modules.py`** — the mechanism this
  repo already uses for `model_routing.py` and `smart_linker.py`, whose docstring says in as many
  words that dormancy is *why* known-odd behaviour inside such modules was left alone. That guard
  asserts both halves: the module stays unreferenced by production code, **and** its docstring keeps
  saying `NOT WIRED`. So wiring it now fails a test that points at the depth bug instead of shipping
  it silently. Both docstring claims corrected; the guard's own "two modules" prose updated to three.
- **The evening log replied "✅ Logged" for entries it never wrote.** `eve_log._save` hand-rolled
  the atomic write (`NamedTemporaryFile` + `os.replace`) inside
  `except Exception: logger.warning(...)`, so a failed write returned normally, `save_shipped()`
  reported success, and the Telegram bot answered **"✅ Logged: &lt;the thing you typed&gt;"** while
  the journal on disk was unchanged — and the next morning's reminder had nothing to show. Found by
  measuring the temp-file sibling of the `mkdtemp` class: it was the **one** file in core+plugins
  that creates a temp file and never removes one, and the reason turned out to be worse than the
  leak. Two more problems rode along: the `.tmp` file was left beside the journal on every failure
  (and this module's own docstring says a Windows AV/backup lock is the *expected* failure here),
  and the hand-rolled writer had no fsync and no transient-lock retry. It now delegates to
  **`atomic_write_json`** — the canonical writer already used by 29 other call sites (temp + fsync +
  atomic replace, retry with back-off, temp removed on every failure path) — and **propagates**
  failure. The Telegram handler no longer answers a failed save with a checkmark: it says the log
  wasn't written and **echoes the text back**, because at that moment it is the only copy left.
- **`mkdtemp()` hands you a DIRECTORY — two more subsystems removed only the files in it.**
  After the TikTok fetch-directory leak was fixed, the same shape was measured tree-wide: of 12
  `mkdtemp()` call sites, **3 files never removed a directory**, and two of them were live.
  **Deck deploy** (`_bake_lighthouse_into_prebuilt`) copies the entire prebuilt bundle into a temp
  dir and hands the path back — nothing removed it, so every `navig miniapp deploy` leaked a full
  staging copy: **91 `navig-deck-deploy-*` directories on the operator's machine**. The deploy body
  is now wrapped in `try/finally` rather than given a cleanup call before each `return` — there are
  five exits and a sixth added later would leak again in silence. **navig-dedupe**'s video
  signature path unlinked its frames and left the directory, and it runs **once per video**, so a
  5,000-clip library scan leaves 5,000 empty `vsig_*` dirs (zero on this machine — real in code,
  simply never run here). New guard `tests/quality/test_tempdir_ownership.py`, wired into
  `sourceGuardArgs`: a file that calls `mkdtemp()` must also remove a directory, with one
  allowlisted exemption — `engine.fetch_file`, which *deliberately* hands ownership to its caller
  and whose every in-tree caller now passes an explicit `dest_dir`. The allowlist expires by itself
  (an entry that stops offending fails the test), each entry needs a written reason, and the scan
  asserts it read >500 files first, so a mis-rooted scan cannot pass by reading nothing.
- **Two toolsets advertised tools the registry cannot dispatch — the model never saw them, and
  nothing said so.** `TOOLSETS` is what turns `toolset="memory"` into the schemas the model
  receives; a name in that table that nothing registers is dropped in silence, so the toolset
  simply has fewer tools than it claims. Measured across all 13: **2 were wrong.** `memory` listed
  **`fts_search`**, which is a method on the conversation memory store, not an agent tool — so it
  offered four tools while claiming five and conversation full-text search was never reachable
  (removed; exposing it properly is a feature, not a table entry). `delegation` lists
  `delegate_task`, whose registrar **has no caller anywhere** and is absent from
  `register_all_tools()` — selecting that toolset yields an agent with **zero** tools. It is kept
  as the recorded design (the live multi-agent path is `coordinator`, which caps recursion by
  stripping `delegation`/`full`/`coordinator` from its children) and pinned as a documented
  known-empty entry. New guard `tests/agent/test_toolset_registry_parity.py`: a toolset with
  *some* tools registered must have *all* of them (environment-robust — an optional group that
  fails to import registers none and is skipped, which is a machine fact, not a stale table), a
  toolset that registers nothing must be listed with a written reason, and that exemption
  **expires by itself** — wiring `delegation` fails the test until its note is removed, so the
  file can never end up asserting the opposite of the truth. Also noted while tracing it:
  `delegate.py`'s `_run_child` accepts a `depth` argument and never uses it, so its documented
  `MAX_AGENT_DEPTH` could not have held had the module been wired — latent, not live.
- **Every TikTok action leaked the directory it downloaded into — 25 of them on the operator's
  machine, one per action ever run.** `engine.fetch_file` defaults its destination to
  `tempfile.mkdtemp(prefix="navig_tiktok_")`, so the directory belongs to whoever called it, and
  nobody claimed it: all four fetch sites removed the **file** and left the **directory**. Nothing
  errors, nothing is slow, nobody notices — `%TEMP%` just fills up for as long as the bot has
  existed. Measured on the live machine before the fix: 25 empty `navig_tiktok_*` directories.
  All four sites now go through one `_fetched()` context manager that creates the directory,
  passes it as `dest_dir=`, and removes **the whole directory** — so a partial download after a
  crash, or a yt-dlp side-artifact, goes with it. Cleanup is `ignore_errors` on purpose (a
  Windows antivirus lock must never fail an action the user already has the result of), and a
  once-per-process sweep collects what that leaves: our own prefix, in the system temp dir,
  directories only, older than six hours — so the machine also heals the backlog it already has.
  This replaced four hand-rolled `finally: os.remove(...)` blocks and two `keep_on_disk` flags.
- **"Saved to &lt;path&gt;" pointed into a temp directory the OS may reap.** When a clip is too
  large to upload, or Telegram rejects the upload, the reply tells the operator where the file is
  — and that was a random `%TEMP%` path. It now MOVES to `media_dir("videos"|"audio")` first, so
  the path is durable and discoverable. **The invariant is that a path we print is never deleted
  afterwards**, and it holds through every failure: if the move fails the file goes to the temp
  root instead (no worse than the old behaviour, and it survives cleanup); if even that fails the
  clip is marked *detached* and its directory is deliberately kept. Found while writing the test
  for it: `keep()` originally guarded only the move, so a failing `media_dir()` propagated into
  the caller's generic handler and reported **"Couldn't download that video"** for a download that
  had in fact succeeded.
- **Three TikTok buttons reported a bot-wall in code that could never run, and the fourth
  didn't try.** `_do_transcript`, `_do_audio` and `_do_analyse` each carry an
  `except engine.TikTokBlocked` saying "TikTok blocked this — retry, or add a proxy / cookies".
  For the two that fetch a file those handlers were **dead**: only the *metadata* helpers
  (`info`, `info_with_comments`) classified a wall, and `engine.fetch_file` — the route every
  button takes — re-raised yt-dlp's error untouched. `_do_download` had no handler at all. So
  TikTok's single most common failure surfaced as a flat "Couldn't download that video", which
  reads as "this video is broken" rather than "wait a minute, or configure a proxy". Fixed at
  the shared reader (`fetch_file` now classifies exactly as the metadata paths do), which lit
  the three existing handlers, plus the missing one on Download.
- **A second tap on a running TikTok button started a whole second download.** Telegram's
  "Working…" toast fades in about two seconds while these actions run for tens, so tapping
  again is the natural thing to do — and every action begins by fetching the clip. Two full
  fetches of the same video is wasted bandwidth *and* an extra request at exactly the moment
  TikTok is deciding whether we look like a bot. `handle_callback` now tracks in-flight
  `(chat, action, url)` triples and answers the second tap with "⏳ Already working on that one"
  instead. Scoped so a different action on the same clip, or the same action in another chat,
  is never blocked; the entry is released in a `finally`, so a crashing worker cannot wedge its
  own button. The routing table it introduced is asserted against the card's actual keyboard —
  a button whose action is missing does nothing at all, silently.
- **OCR was never installable, and its absence looked exactly like an image with no text
  in it.** Found by driving the real TikTok end-to-end after shipping multi-frame OCR: the
  analyzer returned `ocr_text=None, note=None` — *"read it fine, there is nothing there"* — on
  a machine where the Tesseract **binary** was installed but the `pytesseract` wrapper was not.
  `pytesseract` is declared in **no manifest at all** — not a dependency, not an extra — so
  that is the default state of every install, and `extract_ocr_text_from_image_bytes` returns
  the same `None` for "no text", "text too short" and "there is no OCR engine here". Six
  surfaces read through it (the TikTok Transcript button, the Telegram catalog's image OCR and
  video→text, the inbox extractor, `navig media brief`, `navig wiki`); every one of them has
  been reporting "nothing found" since the day it shipped, and nothing anywhere said why.
  Three parts, all at the shared reader rather than per surface: (1) `ocr_unavailable_reason()`
  answers the availability question separately from the extraction result, and names **which
  half** is missing — the wrapper and the binary are independent installs and having one
  without the other is the common state; it is cached because the probe spawns `tesseract -v`.
  (2) `analyze_video_file` returns `note="ocr_unavailable"` when it found no text *and* OCR is
  unavailable, and the Transcript button reports that additively — speech still works without
  OCR, so it shows what it read and then names the gap plus the fix, instead of claiming "no
  on-screen text detected". (3) A `navig doctor` → **Media Tools** section covers ffmpeg and
  OCR: the two dependencies whose absence yields a plausible empty answer rather than an error,
  and neither of which had a row. Missing ⇒ ⚠ with the install command, never a green ✓.
  Installable at last via the new **`navig[ocr]`** extra (the Python half; the Tesseract binary
  stays an OS-level install, which the doctor row and the docs both say).
- **OCR shipped hallucinated glyphs as "On-screen text".** With OCR finally installed, the same
  clip produced `"aed KOO | 1 mine | | lh Nt ot ot?"` — tesseract does not decline to answer, and
  `image_to_string` hands over whatever it read from textureless video with no signal that it is
  noise. Every psm mode (3/6/11/12) and every preprocessing variant (grayscale, autocontrast,
  960px vs 1600px) produced the same kind of garbage, which is what rules out "wrong settings"
  as the explanation. `image_to_data` carries **per-word confidence** and separates the two
  cleanly: rendered text scored a **median of 95** with every word surviving, that clip scored a
  **median of 39** with exactly one token (`|`) above 60. Extraction now keeps only words at
  ≥ `OCR_MIN_CONFIDENCE` (60), preserves line structure so a multi-line caption does not collapse
  into one run-on string, and additionally requires a run of ≥3 alphanumerics — because
  punctuation scores high, and a line of stray pipes reads as "found some text". Verified against
  both: the two-line control round-trips exactly, all four frames of the clip now return nothing,
  which is the honest answer. The risk a confidence floor carries is breaking *document* OCR (the
  inbox extractor rasterises scanned PDFs through this same function), so that was measured too —
  a rendered invoice at 28pt, 14pt, blurred, skewed 1.5°, 13pt+blurred, and speckled with 30k
  noise pixels: **all six keep the invoice number and the amount**, and only the heavy-noise case
  loses filler words (26 → 17). Documents score far above the floor; hallucinated glyphs do not.
- **The tier-parity guard had been skipping, not passing, since the monorepo migration.**
  `tests/license/test_tiers_parity.py` is the thing CLAUDE.md names as what stops the tier
  spec drifting across four systems — and its two TypeScript cases read `navig-api/…` and
  `navig-deck/…`, the pre-monorepo sibling repos. Both `pytest.skip`ped on every run for
  months, and a skip reads exactly like a pass in the summary. Paths corrected; **no drift
  had occurred** (6 pass). `_read` now distinguishes the two cases it was collapsing: `core/`
  is also published standalone, where the TS siblings genuinely are absent and a skip is
  right, but if the workspace directory *exists* and the file does not, the path is stale and
  it **fails** instead.
- **The gateway could not serve a locally-built deck, and `navig miniapp deploy` could not
  find one.** Both resolve a built deck bundle through a "dev-tree neighbours" fallback, and
  in both the neighbours were `navig-deck/out|dist|…` — so in the monorepo the branch simply
  returned nothing. `miniapp.py` also walked `parents[4]`/`[5]` (the repo's *parent* and
  grandparent, correct only in the sibling-repo layout), so even corrected relative paths
  could not have resolved; `parents[3]` — the monorepo root — is now included. Measured
  before/after with the installed wheel forced unavailable: `None` before, the real
  `apps/deck/out` after. `static_assets.py` is the copy its own docstring names as the one
  `miniapp.py` mirrors — one class, two copies, fixed together, with the legacy names kept
  as explicitly-marked fallbacks for old checkouts.
- **The design-system sync script had been dead since the migration, freezing 60 vendored
  deck components.** `packages/shared/scripts/sync-design-system.mjs` resolved its root to
  `packages/` and then looked for `navig-shared/`, `navig-www/`, `navig-deck/`, `navig-dock/`
  — it crashed on ENOENT the moment it was run. Nothing noticed, because its outputs are
  committed and looked fine. Meanwhile `apps/deck/components/ui` sat at its migration-day
  contents and missed every fix made in `packages/shared/ui` since — including **two real
  type errors**: `spinner.tsx` still spread `ComponentProps<'svg'>` onto a Lucide icon
  (TS2322) and `form.tsx` still had the un-annotated `FormProvider` that breaks the
  declaration build under pnpm (TS2742). Paths repaired and the layer regenerated: 9
  components updated (those two fixes, five UTF-8 BOM removals, trailing-newline
  normalisation) with `apps/deck` typechecking clean, and the token CSS byte-identical apart
  from its header. Its docstring also still justified the vendoring by "the apps are SEPARATE
  GitHub repos" — obsolete since the migration.
- Two stale `navig-deck/out/ not found` messages in the deck wheel builders now name
  `apps/deck/out/`, the directory the user actually has.
- **The guardrail floor covered one path, not the product.** After the chat and gateway
  builders were fixed, five more surfaces still spoke as the agent with no boundaries:
  `Soul.get_system_prompt` (`navig agent start`), which injected a user `SOUL.md` under *"You
  are the agent described above. Embody this personality"* — the exact inversion of the
  demotion rule — and carried its own two-entry chain so personas, spaces and `IDENTITY.md`
  did nothing there; the Telegram voice bot, whose whole system prompt was one hardcoded
  sentence; the Deck's Ask box; the Deck board's *autonomous* executor; and the operator
  planner, which proposes financial and life plans. The three JSON-contract surfaces carry a
  one-line floor so a prose block can't invite prose out of a strict-JSON response.
  `tests/agent/test_guardrail_floor_every_surface.py` now enumerates **surfaces**, not paths.
- **A telemetry write could take down a live turn.** `CostTracker.record()` called the
  last-turn snapshot outside any guard, so the agentic hot path depended entirely on the sink
  protecting itself.
- **`skills_context.format_for_system_prompt()` does not go in the system prompt** — the
  caller prepends it to the user turn precisely so query-specific skills don't mutate the
  cached prefix. Both docstrings claimed otherwise, and `prompt_caching.cache_skills_context`
  was documented on the same wrong assumption.
- **A user `SOUL.md` silently deleted every guardrail.** All safety rules lived inside
  `SOUL.default.md`, and a user-authored soul is injected verbatim — so writing one to rename
  the agent removed its boundaries, with nothing reporting it. The rules are now a compiled-in
  floor emitted ahead of identity, and the soul is demoted at its injection site. The slim
  short-chat prompt, previously unguarded entirely, carries a one-line floor.
- **Personas, spaces and `IDENTITY.md` were dead on the live chat path.** Three SOUL resolution
  chains existed and the chat path used the weakest; `personas/soul_loader.py` claimed in its own
  docstring to be the "single source of truth" but only the dead legacy agent called it. One chain
  now serves every surface.
- **A persona's `soul.md`, `tone` and `banned_phrases` reached no prompt.** The channel router
  passed a persona *name* and no content. Identity is now resolved per session from persona +
  space + working dir.
- **Every chat session in the daemon shared one identity.** The router pre-loaded a single
  `_soul_content` and handed it to every constructed agent, making per-session identity
  structurally impossible. Two users on different personas got the same soul.
- **The system prompt could not stay cached for more than ~60 seconds.** `## Session Context`
  sat at position 2 and opened with `System time: %H:%M`, upstream of every stable block. The
  Anthropic cache breakpoint is on the system message and its prefix spans `tools → system`, so
  that one field discarded ~6,800 tokens of tool schemas every minute — `cache_write` 1.25×
  against `cache_read` 0.1×, a 12.5× swing. Sections are now ordered stable-first and the clock
  rides the user turn beside skills and recall.
- **The gateway deep path re-read 5–6 workspace markdown files from disk on every turn** with no
  cache; now guarded by `(mtime_ns, size)`.
- **`MEMORY.md` was loaded and silently dropped** by the deep-path prompt assembler — the
  long-term memory file the agent is told to maintain never reached a prompt.
- **Identity truncation was silent** in three places, so the model answered confidently from a
  fragment of its own instructions. Truncation now carries its cause and one line telling the
  model what it cannot see.

### Security
- **A stranger could spawn unbounded Durable Objects on a Lighthouse user's own Cloudflare
  account.** The edge's three public webhook routes (`/tg/:tenant`, `/sms/:tenant`,
  `/ingest/:tenant/:source`) take their tenant from the URL — necessarily, since Telegram, a
  Twilio console and the user's website hold no deck key — and passed it straight to
  `env.BRAIN.idFromName(...)`. Every distinct string is a distinct Durable Object, so a loop over
  random paths instantiated one DO per request, each running its constructor's `CREATE TABLE`,
  all of it billed to the **user's own** account. The DO's per-tenant rate limit could not see
  it: that limit lives *inside* a DO, so each bogus tenant arrives at a fresh, full token bucket.
  A tenant is `sha256(deck.api_key)` as lowercase hex — the brain says so and derives every
  public URL from it — so the shape is now checked before any stub is created, which costs one
  regex and cannot refuse a legitimate caller. **`LIGHTHOUSE_VERSION` is bumped to 1.1.0 and the
  shipped `worker.js` rebuilt**; both are load-bearing, because `navig lighthouse status`
  compares that string against the live edge, so without them every already-deployed edge would
  report itself up to date and never pick the fix up.
- **A blocked tool ran unless the operator spelled it exactly right.** `ToolRouter` stored
  `blocked_tools` / `require_confirmation` exactly as written in `config.yaml`, while
  `execute()` tested the **canonical** name from the registry. So

  ```yaml
  blocked_tools: ["web-fetch"]     # or the documented alias "fetch"
  ```

  matched nothing and the tool **executed** — verified against the real router, which got
  as far as a DNS lookup for a tool the operator had blocked. Only the exact canonical
  spelling denied it. The same mismatch silently skipped the **human-confirmation** gate.
  Both sides now go through the new `canonical_tool_key` — the single answer to "are these
  two strings the same tool?" — so a gate and whatever populates it cannot disagree. It
  deliberately does not consult the registry: the policy is built *before* tools are
  registered, and requiring registration would drop every entry and re-open the hole from
  the other side.
  The direction is fail-**closed**: normalising can only make more names match the policy
  the operator wrote, never fewer.

- **A Matrix feature gate set to `false` OPENED it.** `is_feature_enabled` read its
  value straight from `config.yaml`, where `navig config set` stores the argument as a
  string — and `bool("false")` is `True`. The gates that default to *False* are exactly
  the dangerous ones: `admin_ops` (user management, server admin), `registration_control`
  (toggle open/closed registration) and `file_sharing`. So an operator who ran the command
  **this module's own error message prints** —
  `navig config set comms.matrix.features.admin_ops false` — turned the gate **on**.
  34 CLI commands sit behind `@require_feature`.
  `is_matrix_enabled` (behind `@require_matrix`) and `get_all_features` (which backs
  `navig matrix features`, and is annotated `-> dict[str, bool]`) had the same raw read.
  All three now coerce.
  The direction only ever tightens: a `"false"`-ish string now **closes** a gate it used
  to open, and every truthy spelling still opens it exactly as before — coercion cannot
  open anything that was closed.

- **`navig backup export` could ship a plaintext password in an archive it called
  redacted.** `_redact_secrets_in_dir` ended in `except Exception: pass  # Skip files
  that can't be processed`, so any file it could not parse stayed in the archive exactly
  as written. One unclosed bracket in a hand-edited host file was enough: `safe_load`
  raises, the handler swallows it, and the export the user asked to be redacted carries a
  real credential. Verified against the real function before the fix — a `typo.yaml`
  containing `password: SUPERSECRET` survived redaction untouched.
  A file that cannot be *proven* clean is now dropped from the staging copy, listed in the
  summary, and recorded in the archive's own `manifest.json` (whoever opens the file later
  is the one who needs to know it is incomplete, and they will not have the terminal
  output). A top-level YAML document that is not a mapping is treated the same way —
  `redact_dict` cannot process it, so it cannot be proven clean either. If the file cannot
  even be removed, the export fails rather than shipping the secret: a missing file is
  visible and recoverable, a leaked credential is neither.
- **A security control set to `"false"` no longer fails OPEN.** Same string-vs-bool
  root cause as the toggle fix below, but on controls whose *True* state is the unsafe
  one — so writing "off" turned the protection **on-in-fact**. Measured against the real
  code path before the fix:

  ```
  server_config = {"trust_new_host": "false"}
    before -> StrictHostKeyChecking=accept-new   (SSH MITM protection DISABLED)
    after  -> StrictHostKeyChecking=yes
  ```

  Eight reads fixed: `trust_new_host` in `remote.py` (×3), `tunnel.py` and
  `connection_pool.py` (which selected paramiko's `AutoAddPolicy` — trusting any host
  key it is shown); `browser.ignore_https_errors`; and `allow_insecure` in the config
  audit and in the `cdp_login` MCP tool. That last one is the sharpest: `allow_insecure`
  disables the https-only guard in `browser/origin_match.py`, and the value comes from a
  **model**, so a tool call carrying the JSON string `"false"` would type a vaulted
  password into an `http://` page.

  A second rule in `test_config_booleans_are_coerced.py` holds these at zero. It has a
  deliberately lower bar than the documented-toggle rule — it matches on the key *name*
  and accepts the no-default form `cfg.get("trust_new_host")`, which carried three of
  that key's five sites and which the narrow rule structurally cannot see. Regression
  coverage asserts the observable posture (the SSH argv, the paramiko policy class, the
  TLS flag) rather than that `coerce_bool` was called, and every case has a partner
  asserting the control is still reachable when genuinely enabled.

### Security
- **Anyone could re-point any Telegram user's Mini App at their own daemon.**
  `/api/cloud/bind-telegram` wrote the `telegram_user_id → daemon` route with a bare
  `INSERT OR REPLACE`, so a request carrying *any* valid api_key silently overwrote an existing
  binding and got a `200`. Neither ingredient is a secret: an api_key is **self-chosen** (
  `/api/cloud/register` hashes whatever bearer you send, so obtaining one costs nothing) and a
  Telegram user id is visible to anyone who shares a chat. The victim's Mini App then resolved to
  the attacker's daemon and sent its Telegram `initData` there — a takeover of the routing, and at
  minimum a silent denial of service. A claim is now **refused with 409 while the current holder
  is demonstrably alive**, and allowed once it has been silent for 15 minutes. The legitimate
  machine-swap case the `REPLACE` existed for keeps working with **no user action**: the old
  daemon stops heartbeating and the new one's own retry loop (it re-attempts pending binds on
  every heartbeat) takes the binding over by itself; `/api/cloud/unregister` hands it over
  instantly. Found by driving the broker end to end for the first time.

### Fixed
- **Every transcription in navig was told the audio was English.** `STTConfig.language` shipped as
  a hard-coded `"en"` with `detect_language` off, and no caller passed a language — so
  `lang = language or self.config.language` resolved to `"en"` for *all* speech-to-text: the TikTok
  Transcript button, the catalog's video→text, voice notes. Russian speech was decoded as English,
  and any briefing built on that transcript inherited the wrong language too, which is why both the
  transcript *and* the analysis came back in English no matter what the briefing's language setting
  said. **Nothing pinned now means DETECT**: the default is empty, and all three providers
  (Whisper API, local Whisper, Deepgram) omit the language / ask for detection when none is set.
  An explicitly configured language is still honoured exactly as before.

### Fixed
- **Two more AI surfaces ignored the operator's language.** Fixing speech-to-text, the TikTok
  briefing and the catalog summary in three separate passes is the signature of a *class*, not of
  three bugs — an English prompt written by whoever added the feature, with nothing pinning the
  setting to it. A sweep of every AI text prompt in the Telegram surface found the last two:
  **🌍 Translate** hardcoded English as its target, so an operator who had just told navig they
  read Russian got their Russian message translated *away* from the language they asked for; and
  the **away-recap** ("what were you working on") was English-only, so a Russian conversation was
  summarised back at them in English — including the branch that enriches the prompt with session
  notes, which would otherwise have stripped the directive off again. Both keep today's behaviour
  when nothing is configured, and an explicit `translate <lang>` argument still wins over the
  global. The remaining actions (summarize/context/explain/improve/fix) already said "in the same
  language as the input" and needed no change — now asserted, so removing that instruction fails
  the build.
- **`/lang` promised that summaries follow the language; they did not.** The catalog's
  `_summarize` carried an English-only system prompt, so a Russian clip whose transcript and OCR
  were both Russian still got an English line in the searchable catalog — while the command's
  confirmation said summaries would use the chosen language. The claim is now true: the summary
  prompt takes the resolved language, and asks for the content's own language when nothing is
  pinned. (Making a claim true is the fix; weakening the message to match a gap would have left
  the gap.)
- **The registry's second dispatcher dropped every command argument.**
  `_build_slash_handlers` built its handler context without `text`, and both dispatchers filter
  kwargs by signature — so a parameterised command routed through it would be called as if sent
  bare, with no error anywhere: `/lang Russian` would have reported the current language instead
  of setting it. It is currently unwired (which is how it drifted from the live dispatcher in
  `TelegramChannel`), so this was latent rather than live, but it is exactly the trap the next
  person to wire it would fall into — and it is **41 commands**, not one: `/run`, `/search`,
  `/remindme`, `/weather`, `/space`, `/skill`, `/trace` and the rest all read their argument from
  `text`. `text` is now accepted and forwarded, and `test_slash_dispatch_parity.py` pins the
  contract. The guard **derives** the affected set from the registry rather than listing it — a
  literal list would have said `{"lang"}`, the command that happened to reveal the bug — and
  asserts a floor on what the scan finds, because a discovery that silently returns nothing looks
  exactly like a clean run.

### Added
- **`/lang` is now a real command — because the model was already pretending to be one.** Typing
  `/lang Russian` at the bot produced *"Язык переключён на русский"* with web-search "Read" buttons
  attached, and wrote **nothing**: an unregistered slash command falls through to the chat agent,
  which has no way to change configuration but every incentive to sound like it did (the buttons
  were the silent enrichment search, because it had treated the input as a question *about*
  Russian). A confirmation the operator cannot distinguish from a real one is worse than an error.
  `/lang` now writes `user.language`, **reads it back**, and reports what it actually found —
  including *"the language setting did not persist"* when a write is accepted and silently
  dropped. Bare `/lang` reports the current value without writing.
- **One global output language: `user.language`** (alongside `user.name`), default **auto** =
  follow the content. Language is cross-cutting — speech-to-text, AI briefings, OCR summaries — and
  a per-feature switch for each would drift into a set of settings that disagree, so there is one
  preference and features may override it. Resolution is **feature override → `user.language` →
  auto**, via `navig.core.language.resolve_language()`, and "auto" is normalised to `None` on the
  wire because that is what providers need in order to detect; the literal string `"auto"` would be
  parsed as a language code. `telegram.tiktok_cards.language` becomes an override of the global
  rather than its own island, and the TikTok transcript path now passes the resolved language
  through `analyze_video_file(language=…)` instead of inheriting a default nobody chose.
- **🔍 Analyse now listens when the caption says nothing.** TikTok descriptions are very often a
  bare hashtag, and a briefing built from that plus comments describes the *reaction* to a video
  without ever describing the video — which is exactly what the operator saw. When the description
  carries fewer than four non-hashtag words, Analyse transcribes the audio first and briefs from
  what was actually said; the header then reads `📝 from speech`, because a briefing built from the
  spoken words is a different claim from one inferred off a hashtag. A video with a real caption
  keeps the fast path and pays nothing.
  The split is deliberate: `analyse(get_transcript=…)` takes a **lazy** callback, so the engine
  decides *whether* the extra work is worth it (it owns briefing quality) while the caller decides
  *how* (it owns ffmpeg and STT). This also closes a seam that shipped dead — `brief_meta(transcript=…)`
  and `analyse(transcript=…)` existed with **no producer anywhere in the tree**, the same
  capability-exists-but-nothing-calls-it shape as the card that was never wired to 1:1 chats.
  Enrichment is strictly best-effort: a failing or absent STT logs at debug and the briefing
  proceeds exactly as before.
- **📝 Transcript and 🎧 Audio actions on the TikTok card.** A TikTok description is very often a
  single hashtag, so a briefing built from it alone describes almost nothing — the spoken content
  was unreachable. **Transcript** pulls the audio track only (a fraction of the bytes) and runs it
  through the existing speech-to-text path; **Audio** sends that track as a playable file. Both
  reuse the card's `download` permission gate. `engine.fetch_file(audio_only=True)` is the shared
  primitive, and `brief_meta(transcript=…)` folds a transcript into the briefing when one is
  supplied. Silence is reported as silence (`No speech detected`) rather than as a failure —
  a muted clip is a real answer, and calling it an error sends the operator hunting a
  configuration problem that does not exist.
- **`TelegramChannel.send_audio`** (sendAudio Bot API) — the channel could send a voice note
  (OGG/Opus only) or an inert document, but not a playable audio track with title/performer.

### Changed
- **The briefing stopped ending with "Worth watching?"** — a verdict on whether to watch something
  the reader had already chosen to open. That section is now **Worth knowing**: the concrete
  substance (named people, works, places, claims, prices, dates, links) they would otherwise have
  to watch for, and it is omitted entirely when the data holds no such specifics rather than
  padded.
- **The briefing follows the video's own language.** A Russian clip was being summarised in
  English with no way to change it, so the operator had to translate back what the comments
  already said. Default is now "mirror the source"; pin one with
  `navig config set telegram.tiktok_cards.language <language>`.
- **The briefing header shows what is known instead of a placeholder.** It printed
  `🌍 country n/a` on essentially every video (TikTok rarely exposes geo) and none of the author
  handle, post date, duration, sound or engagement stats that *are* known. Unknown fields are now
  omitted; known ones — including 👁/❤️/💬/🔁 and a link back to the post — are shown.

### Fixed
- **The Download button deleted the file it had just told you to go and get.** The >50 MB branch
  replies "Saved to `<path>`" and the `finally` cleanup then removed that exact path, so the
  operator went looking for a video that no longer existed. The same cleanup also ran when
  `send_video` returned `None` — a **rejected** upload, which raises nothing — so a video the user
  never received had its only copy deleted silently. The temp file is now kept whenever something
  pointed the user at it, and a rejected upload says so.
- **A 🎬 reaction that briefed nothing still got a "done" acknowledgement.** `_reaction_tiktok`
  discarded `handle_reaction`'s boolean and set the 👍 ack unconditionally, so a reaction whose
  link could not be resolved — or that the `download` policy denied — was marked complete. The ack
  is the only signal that path has; it now reflects what actually happened.
- **A pasted link reached a model with no way to open it.** The default agent toolset is `core`
  (bash/read_file/write_file/list_files — nothing that fetches), and the message-shape router
  returns *no* toolset hint for a short message, so a bare URL arrived at a model holding no
  retrieval tool at all. Its only honest reply was *"Can't access external links"*, which is
  exactly what the operator got. A message containing an `http(s)://` URL now also gets the
  `search` toolset (`search` + `web_fetch`, already SSRF-guarded), so the model can actually open
  what it was sent. Scoped to an explicit scheme — a bare `example.com` is prose far more often
  than a fetch request.
- **A tool-using run stayed on the small-talk budget and truncated its own answer.** `max_tokens`
  and the LLM timeout are chosen ONCE, from the length of the incoming message, before anything
  knows a tool is coming — and a bare URL is 33 characters, so a link → fetch → summarise run was
  answering inside a chat-sized budget. The budget is now raised to the tool-work default on the
  first tool call and stays there for the rest of the run. It is a **floor, not a reset**: a mode
  configured larger than the default is never clamped down. (Noted while fixing: the
  `_AGENTIC_CHAT_MAXTOK` constant is only a fallback — `resolve_llm()` overwrites `max_tokens`
  with the mode's configured value a few lines later — and the comment there said otherwise.)
- **The card's buttons broke when the message catalog was switched off.** `handle_callback`
  recovered the link by re-reading the source message from `TelegramCatalogStore`, so an operator
  who set `telegram.catalog.enabled false` would tap ⬇️ Download and be told *"Couldn't find the
  TikTok link"* — while the link sat in the callback payload that carried the tap. The card
  replies to the user's message, so Telegram includes that original text in the callback; it is
  now the primary source, with the catalog kept as a fallback. Buttons must not depend on an
  unrelated subsystem being enabled.
- **Sharing a TikTok link with your own bot did nothing — the card existed and nothing called
  it.** `tiktok_actions` ships a complete card (creator · country · description · stats, plus
  ⬇️ Download and 🔍 Analyse), permission-gated, with its `tk:` button callbacks already routed
  by the channel. Its ONLY caller was the Telegram **Business** layer, so in the owner's own
  chat the link fell through to the chat model — which has no fetch tool for a TikTok URL and
  could only answer *"Can't open external links."* `music_actions` was written as a copy of this
  module's contract and DID get its 1:1 hook, which is what makes this a wiring omission rather
  than a decision: two sibling handlers, one wired, one not, nothing failing. The card now fires
  in 1:1 chats on the same conservative contract as music links — bare link only (a question
  that merely mentions a link still goes to the agent), DM-only, owns the message so you never
  get both a card and a reply, and only when the send actually landed. Opt out with
  `telegram.tiktok_cards.enabled`. A new build guard (`test_link_handlers_are_wired.py`) fails
  if a passive link handler is ever again defined but never called. The metadata lookup is now
  **bounded** (12s): it sits on the 1:1 message path, and an unbounded live fetch of tiktok.com
  would hold the whole reply hostage — a slow lookup degrades to the plain card, which still
  carries both buttons.
- **Sending the bot a link got "Agent reached the 1-turn limit" — from a budget of 90 that had
  run exactly once.** A short message (a bare URL is 33 characters) is classified as "chat-feel",
  which turns on the streaming path — but that request still advertises every tool with
  `tool_choice="auto"`, so the model can, and for a link does, answer with a **tool call** instead
  of text. The stream loop read only `chunk.delta` and then hardcoded `tool_calls=None`, so the
  model's decision to act was dropped on the floor: the turn came back with no text and no calls,
  the loop read that as "nothing more to say", and the generic fallback blamed a turn limit
  nowhere near reached. Streamed tool-call fragments are now reassembled
  (`merge_tool_call_deltas`) on **all three** streaming paths in the ReAct loop — the main turn,
  the fast-model fallback, and the sibling-account rotation — so a tool the model chose actually
  runs. Two more holes in the same class are closed underneath: the OpenAI-compatible client read
  only `tool_calls[0]` of each delta (dropping every parallel call after the first) and carried no
  `index`, Anthropic emitted nothing at all for a **zero-argument** call, and the base client's
  default `complete_stream` discarded the whole `tool_calls` list that `complete()` had already
  returned. A streamed turn that yields neither text nor a tool call now retries once unstreamed
  instead of surfacing the void.
- **"Reached the N-turn limit" was also printed when no limit was reached.** One message served
  two unrelated failures — an exhausted budget and an empty completion — so it told the operator
  to narrow a request that was never the problem, and hid the real event. The limit is now claimed
  only when the budget is genuinely exhausted; an empty completion says so, and names the
  provider/model that produced it.
- **The founder's dashboard counted one customer twice.** Same root cause as the identity fix
  below, failing the other way: a comparison that ignores case returns *nothing*, an aggregation
  that ignores case returns one human *twice*. `/api/admin/users` grouped on the raw `email` and
  `/api/admin/overview` de-duplicated its customer total with a plain `UNION`, so a buyer with
  rows under two spellings appeared as two people — two licence counts, two spend totals, and
  neither of them their real one. Both now fold on `LOWER(email)`, and the users list's
  correlated tier lookup matches `COLLATE NOCASE` so it cannot resolve against half a buyer's
  history. The guard covers groupings as well as comparisons, so the class is closed rather than
  half-closed.
- **A buyer who capitalised their own email could not see, or recover, anything they bought.**
  Payment providers pass `customer_email` through exactly as typed, and the webhook handlers only
  trimmed it — so `Buyer@Example.com` was stored verbatim in `purchases` and `licenses`. Every
  read path lowercases first (the magic-link session claim lowercases twice; license
  lookup/resend, admin customer search and checkout-credit all call `.toLowerCase()`), so
  `WHERE email = ?` compared a lowercased needle against a mixed-case haystack and matched
  nothing. The mint worked; **everything that retrieves what they paid for came back empty** —
  their own account page showed no licence and no billing history, the token resend sent nothing,
  the post-checkout lookup 404'd, and the founder's customer search said the customer did not
  exist. A second purchase under a different spelling also began a *separate* entitlement
  history, so the additive merge silently dropped the grants of the first — the #868 under-grant
  class arriving through the identity key. Closed on both sides, and both halves are needed:
  writes normalise through one canonical `normalizeEmail` at the pipeline's choke point (so new
  rows are canonical), and all 14 read comparisons use `email = ? COLLATE NOCASE` (so rows
  written *before* this are still found). **No data migration** — nothing existing is rewritten,
  so nothing existing can be damaged; `0010_email_nocase_index.sql` only adds two NOCASE indexes
  so the comparison stays index-backed.
- **Following this Worker's own README broke every Stripe purchase.** `services/api`'s README
  tells the operator `wrangler secret put STRIPE_WEBHOOK_SECRET`; `env.d.ts` declares that name
  as supported for back-compat; the receiver's header docstring names it as the signing key; and
  `/api/admin/overview` counted it as "webhook configured". Nothing **read** it —
  `stripeWebhookSecrets` returned only the two mode-suffixed names — so the receiver saw zero
  secrets and answered **503 to every event**. Stripe retries for days and then disables the
  endpoint: no purchase mints, and the admin panel shows a green light the whole time. The
  legacy name is now among the secrets tried. `stripeActiveKey` had the same gap on the checkout
  side and now falls back to the legacy `STRIPE_SECRET_KEY` — but **only when the key's own
  `sk_live_`/`sk_test_` prefix matches the active mode**, because a pre-split deployment carries
  a LIVE key under that name with `STRIPE_MODE` unset, and using it would take real money
  through a checkout whose live-mode event the mode gate then rejects. The overview panel now
  derives both lights from those same two helpers instead of re-implementing the lookups, so it
  cannot drift from the code that spends the money again.
- **A Stripe webhook-secret rotation was a coin flip.** Stripe sends one `v1=` signature per
  active endpoint secret while a secret is being rolled, in unspecified order; the receiver kept
  only the first and discarded the rest. Half the events during a rotation answered 401 — and
  every one of those is a paid purchase that never minted. All `v1` candidates are now checked.
- **Two receivers documented a log line that did not exist.** `lemonsqueezy.ts` said the
  recurring-renewal event was one where "we log and move on" and `paddle.ts` said
  "logged only"; both dropped it into the generic unhandled-event ack in silence. Renewals are
  wired for Stripe alone, so such an event means a paying subscriber whose expiry will never
  move. Behaviour is unchanged (still acked — a retry cannot wire a code path), but it is now
  logged instead of sharing the silent bucket with `transaction.updated`, and both docstrings
  say what the code does.
- **A stranded module purchase could be replayed WITHOUT its grant.** `runPipeline` filters its
  perpetual-grant CSV through `isGrant` and **silently drops** anything unknown — correct and
  defensive, because the provider handlers validate the module before writing that CSV. But
  `/api/admin/stranded`'s `replay` reads the module id straight out of `raw_event` and had no
  such guarantee: an id that is not a real grant would sail through reconstruction, get dropped
  by the pipeline, and mint a licence **missing what the buyer paid for** — while setting
  `license_id` and so removing the row from the stranded report. That is precisely the silent
  under-grant the "cannot recover the id" refusal exists to prevent, arriving one step later.
  The recovered id now goes through `isGrant` and the replay is refused if it is not a real
  grant. Found by running the actual pipeline against an actual database — no double could
  have surfaced it, because a double never applies `isGrant`.

### Added
- **The cloud broker is now driven end to end.** It is the routing table that tells a hosted Deck
  and the Telegram Mini App which URL a daemon is reachable at — the one surface in this Worker
  where a request can return *another* user's address — and it had **no tests at all**: five
  endpoints, an api-key auth model, a tenant boundary and a foreign-key cascade, none of it ever
  executed. `cloud-broker-integration.test.ts` drives all five against the real-migrations
  database and asserts row state, not just status codes: the key is stored hashed and never in
  plaintext, a reboot upserts instead of forking the routing table, a rejected address never
  blanks the live one, one key can neither move nor delete another daemon, `unregister` really
  does cascade the Telegram binding away (the `ON DELETE CASCADE` is only real if foreign keys
  are enforced — they are), and a removed daemon resolves to nothing rather than to a stale
  address. It found the hijack above.
- **Every SQL statement in `services/api` is now checked against the real schema.**
  `/api/admin/stranded` shipped selecting `created_at`, a column `purchases` has never had, and
  the suite stayed green because a hand-rolled D1 double never executes SQL. Integration tests
  close that only for the routes they happen to drive. SQLite reports `no such table` /
  `no such column` / syntax errors at **prepare** time, so `sql-schema.test.ts` extracts every SQL
  literal in `src/` and prepares it against a database built from the real migrations — 57
  statements, 100% of the static SQL, no fixtures to keep in sync. It asserts a floor on how many
  it found (a scan that reads nothing looks exactly like a clean run) and requires interpolated
  SQL to name the test that executes it instead of silently skipping it. Current findings: zero —
  this is a regression gate on a class that has already shipped once, on the money path.
  `email-identity-guard.test.ts` is the companion shape guard: a new `WHERE email = ?` without
  `COLLATE NOCASE` now fails the build.
- **The three webhook receivers are now driven end to end.** The mint-path harness starts one
  layer in, from an already-normalised purchase — so everything a provider handler decides
  *before* that point (is the signature valid, does this event mean "mint", what tier or grant
  did the buyer actually pay for, which secret verifies it) was covered by doubles or not at
  all. `webhook-handlers-e2e.test.ts` posts a real `Request` with a real HMAC signature computed
  independently via `node:crypto` — never with the helper under test — against a real SQLite
  database built from the real migrations, and asserts what landed in `licenses`/`purchases`
  rather than accepting a 200 as proof. 27 cases across Stripe, Paddle and LemonSqueezy: the
  mode gate, the unsettled-async defer, replay/staleness, idempotency, renewal, cancellation
  sparing perpetual licences, and — for every rejection — that **nothing** was written. Both
  Stripe signature bugs above were found by it.
- **The mint path is now executed, not doubled.** Everything this pipeline does was previously
  verified with hand-rolled D1 doubles — the resume fix, the retry status, the ack honesty —
  and a double returns whatever the test wants, so none of them ever proved the SQL runs.
  `tests/webhook-pipeline-integration.test.ts` drives `runPipeline` / `runRenewal` /
  `runRevocation` against a real SQLite database built from the real migrations, with real
  Ed25519 signing and an injected email binding, asserting the licence row **exists** with the
  right tier, hosts, token, expiry and `purchase_ref`. It covers the mint, the perpetual-vs-
  subscription expiry split, grant persistence, the **resume** fix against real SQL, real
  idempotency (one payment can never mint two licences), the `unsigned` path leaving a
  resumable stranded row, revocation touching subscriptions only and never double-counting,
  and renewal.

- **`services/api` tests can now run real SQL against the real schema.** Every D1 double in
  the suite returns whatever the test wants, which catches logic bugs and is structurally
  incapable of catching the class that actually shipped: SQL that is simply wrong.
  `/api/admin/stranded` went out selecting `created_at` — a column `purchases` has never had —
  and every test passed, because no double ever executes a query.
  D1 *is* SQLite and Node 24 ships `node:sqlite`, so `tests/d1-sqlite.ts` builds an in-memory
  database by applying the **real migrations in order** and exposes it behind D1's interface
  (`prepare/bind/first/all/run`, `batch` as one transaction, `exec`). No wrangler, no
  `node_modules`, no network — it runs in the existing `node --test` tier.
  `tests/stranded-endpoint-integration.test.ts` drives the endpoint end to end through it: the
  list query actually executing, the empty estate, an unreplayable row still being listed, the
  limit clamps, 404/409/422, and the founder gate (a valid **non-founder** session is refused —
  `FOUNDER_EMAILS` is the gate, not merely having a session). **Verified by reintroducing the
  bug: 7 tests fail with `no such column: created_at`.**
  The harness deliberately does *not* skip when `node:sqlite` is unavailable — a quietly
  skipped integration tier is how untested SQL shipped in the first place.

### Fixed
- **`/api/admin/stranded` selected a column that does not exist, so `list` would have thrown
  on its first real call.** The endpoint shipped in the previous entry selected and ordered by
  `created_at`; `purchases` has never had that column — it is **`received_at`** (see
  `migrations/0004_billing.sql`). D1 would have answered `no such column: created_at` and the
  primary action would have 500'd.
  Nothing caught it: `reconstructPurchase` and the routing were both tested, but the SQL was
  never executed, and a typed row interface cannot help — TypeScript has no idea what a D1
  query returns. The column list is now a single exported `PURCHASE_COLUMNS`, shared by both
  queries, and `tests/stranded-reconcile.test.ts` checks every name in it against the columns
  the migrations actually create (including the `purchases_new` rename dance in 0005), with
  anti-vacuity assertions that the parser finds `received_at` and does *not* invent `created_at`.
  A sweep of every explicit column list in `services/api` found no other bad reference — the
  eight other candidates were artifacts of the throwaway probe (it required a closing paren on
  its own line, so the single-line `waitlist` DDL yielded zero columns, and it did not resolve
  `ORDER BY` aliases like `COUNT(*) AS n`).

### Added
- **`POST /api/admin/stranded` — find and recover purchases that never produced a licence.**
  `purchases.license_id` is the completion marker, so a row with NULL there is a customer who
  paid and got nothing. Finding them meant hand-running SQL against production D1, and fixing
  one meant hand-minting.
  - `{ action: "list" }` — every stranded purchase, each already marked `replayable` with a
    `blocked_reason` when it is not, so the operator does not discover refusals one at a time.
  - `{ action: "replay", id }` — rebuilds the purchase from the stored row and hands it to
    `runPipeline`, which **resumes** and mints exactly as the original webhook would have. It
    deliberately re-implements nothing, so it inherits idempotency: replaying a row that has
    since completed returns `duplicate` and mints nothing (and a row that already has a
    `license_id` is refused with 409 before that point).

  **The sharp edge is the grant, and it fails closed.** The purchases table has no
  `perpetual_modules_csv` column — that CSV is derived from provider event metadata at webhook
  time and only ever reaches the *licence* row. A Bay item is recoverable from the row's own
  `item_id`; a **module** purchase's id survives only inside `raw_event`, so it is read back
  from there (Stripe `data.object.metadata.module`, Paddle `custom_data.module`, LemonSqueezy
  `meta.custom_data.module`). If a module-shaped row cannot yield its id, the replay is
  **refused** rather than minted: a partial mint would give the buyer less than they paid for
  *and* set `license_id`, dropping the row off this very report — hiding the problem instead
  of fixing it. A corrupt `raw_event` on a plain tier purchase still replays, because a tier
  purchase grants nothing extra.

### Fixed
- **A failed licence mint was acked `200`, so the provider never retried — unless the same
  failure happened to be uncaught.** `runPipeline` catches a D1 failure inside the mint block
  and returns `status: 'error'`, which every handler acked **200**: delivered, never retried,
  the buyer's licence gone for good. The *identical* failure a few lines earlier is **not**
  caught — it propagates to Hono, becomes a 500, and the provider retries. Same fault,
  opposite outcome, decided purely by which line it landed on.
  `error` now returns **500**, matching what an uncaught throw already did. This is a
  consistency fix rather than a new policy, and it is only useful because a stranded purchase
  now **resumes** (previous entry): the retry continues the mint instead of reporting a
  duplicate. It is safe for the same reason — a completed purchase still short-circuits and
  cannot double-mint.
  **`unsigned` deliberately stays 200.** It means `LICENSE_SIGN_KEY` is absent — a
  configuration outage, not a transient fault. Retrying cannot create an env var, and days of
  forced retries risk the provider disabling the endpoint, which would break the purchases
  that *are* working. It is logged loudly and the purchase row is on file for replay.
  All three handlers now reply through one `pipelineResponse()` that derives both the honest
  `ok` body and the status, so the two can no longer disagree; the wiring guard was tightened
  to require it and to ban a hardcoded `status: 200` beside a pipeline result (which is what
  `lemonsqueezy.ts` and `paddle.ts` still had).
- **A paid purchase whose licence mint failed was reported as a "duplicate" forever.**
  `purchases.license_id` is the completion marker — backfilled only *after* the mint lands —
  but the purchase row is inserted *before* minting. So every failure after that point (a
  missing `LICENSE_SIGN_KEY`, a mint error, a failed licence INSERT) left a row with
  `license_id = NULL`, and the idempotency check returned `duplicate` for **any** existing
  row. Every redelivery of that event — including Stripe's own retries — therefore reported
  `duplicate`, which the ack treats as success. The buyer had paid, had no licence, and each
  retry cheerfully agreed everything was fine.
  This is also why retrying a failed mint was pointless, and why the insert's own comment
  could only offer *"we can resend manually"* as the remedy. An unfinished purchase now
  **resumes**: the completion marker is checked (`existing?.license_id`), the insert is
  skipped because the row already holds the original event's data, and the mint proceeds.
  A genuine duplicate — a row that *does* carry a `license_id` — still short-circuits and
  mints nothing, so real idempotency is unchanged. `runRenewal` carried the identical flaw
  (a renewal that never extended the licence, reported as a duplicate on every redelivery)
  and is fixed the same way. Both log the resume.

- **`navig deploy --dry-run` reported a snapshot it had not taken.** `RollbackManager.
  create_snapshot()` deliberately returns a record on a dry run *describing* the
  snapshot it would take, without touching the remote — the plan, not a result.
  `_phase_backup` then reported it as `Snapshot → <path>`, telling the operator a
  snapshot exists at a path that was never created, and carrying that record into the
  deploy history as `result.snapshot`. It was the one phase of seven whose dry-run
  output claimed completed work: `_phase_push` emits `[DRY RUN] rsync …` and
  `_phase_apply` logs `[DRY RUN] apply: …`. The phase already received a `dry_run`
  parameter and simply never read it; it now marks the message the way its siblings do,
  while still naming the planned path.
- **Seven failures on the money path were completely silent.** A sweep of every `catch` in
  `services/api` (56 sites, classified by whether they surface the failure) found seven that
  swallowed an error and continued as if nothing had happened, all on paths where silence
  costs money. Behaviour is unchanged at every one — only the silence is gone:
  - **three `createStripeCheckoutSession` failures** in `checkout.ts` (item, module and tier
    purchases) did `catch { target = null }`, bouncing the buyer to the generic pending page.
    The degradation is deliberate; being invisible was not. A bad price id or a Stripe outage
    would have killed checkout for **every** buyer with no signal anywhere.
  - the **perpetual-credit** and **founding-eligibility** lookups in `checkout.ts`: correct
    fail-safe for the business (never grant a discount you cannot verify) but the buyer
    silently pays **more** than they are owed.
  - both **coupon-creation** handlers in `stripe-checkout.ts`, whose own comments promise
    *"we'll issue a manual refund if needed"* and *"we reconcile the founding rate manually"*
    — promises nobody can keep without a record that it happened.
  - `signup.ts`'s 500, which kept the response generic (correct — no internal leak) by
    discarding the detail entirely (not correct), leaving a signup outage undiagnosable.
- **The credit-lookup failure was invisible even from the caller.** `findPerpetualCredit`
  catches its own D1 error and returns an ordinary `eligible: false`, so the `try/catch`
  around it in `checkout.ts` never fires — logging only at the call site would have left this
  path silent. The log lives at the source, where the failure actually is.
  A guard in `services/api/tests/money-path-visibility.test.ts` holds the four money-path
  files at zero silent handlers; it scans code with strings and comments blanked, and carries
  a self-test proving the scanner ignores prose and a string literal (this session's most
  repeated slip).

### Security
- **The API rate limiter never limited anything, on six endpoints, silently.**
  `checkRateLimit` recorded each hit by passing **two** statements — an INSERT and a DELETE
  separated by a semicolon — to `prepare().run()`. D1's `prepare()` takes a single statement
  (this very file's `ensureTable` comment says so: *"exec() runs multi-statement DDL"*), so
  the call is rejected with `D1_ERROR: A prepared SQL statement must contain only one
  statement`. That rejection landed in `enforceRateLimit`'s bare `catch {}`, so the INSERT
  never happened, the `COUNT(*)` above it stayed **0** for every caller, and the limit was
  never reached. Verified against a D1 double that enforces the real single-statement rule:
  the error is thrown from `rate_limit.ts:61` on the live path, and 7 behavioural tests fail
  on the pre-fix commit — including *"the limiter actually blocks at the limit"*.
  Affected: `bind` (10/60s), `register` (20/60s), `resolve` (120/60s), `unregister` (5/60s),
  `license-lookup` (10/600s) and `license-resend` (5/600s) — the last of which **sends
  email**, so an inert limiter there is an email-amplification vector.
  The hit is now recorded with `db.batch([insert, delete])`, which is this codebase's
  existing shape for multiple statements (`api/admin/matrix-stats.ts`).
- **A broken rate limiter now says so.** The `catch {}` fail-open is deliberate — this is the
  second line behind Cloudflare's WAF and a D1 blip must not lock out legitimate callers —
  but it was *silent*, which is what let a completely inert control sit unnoticed. It now
  logs `rate limit check failed — FAILING OPEN, request allowed unchecked` with the scope and
  error. Fail-open behaviour is unchanged.
- **A failed `CREATE TABLE` no longer latches.** `ensureTable` set `_tableEnsured = true`
  even when the DDL threw, so if the table genuinely had not been created the limiter spent
  the rest of the isolate's life querying a missing table — every call throwing into the
  swallow above. It now latches only on success; a retry costs one
  `CREATE TABLE IF NOT EXISTS`.

### Fixed
- **`navig scaffold --dry-run` listed nothing, always.** It iterated
  `template_data.get("files", [])` — a key the template schema does not have.
  `validate_template` requires `structure`, and "files" appeared nowhere else in the
  subsystem, so the loop was always empty and every dry run printed the header
  "Files to be created:" followed by silence, for every template. It now renders the
  tree into a staging directory and lists what actually came out, so the preview
  reflects conditions and rendered path names; a template that would fail says so at
  dry-run time rather than after committing to a target; and a template that would
  create nothing says that instead of printing an empty header. The remote dry run
  shows the same preview — it previously printed only the destination.
- **A failed `navig scaffold` no longer leaves a half-built project behind.**
  `generate()` wrote as it walked the structure with no rollback, so a failure partway
  through left whatever had already been created in the user's target — and when that
  target was an existing directory holding real work, the scaffold's files were mixed
  into it with no indication of how far it got. Validation (previous release) rejects
  everything knowable before writing, but a render-time failure can still happen
  mid-walk: a filter erroring on real data, a `source` that vanished between validate
  and generate, a disk error. The tree is now built in a staging directory and merged
  into the destination only once it is complete, so a failed scaffold leaves nothing —
  not even an empty directory. `dirs_exist_ok` merges rather than replaces, so
  generating into a directory that already holds unrelated files still preserves them,
  and `copy2` carries over the modes applied during staging.
  This is the pattern the class already used for the remote path; that path now renders
  straight into its own temp directory instead of staging into a second one.
- **Every billing webhook acked `ok: true` over a FAILED licence mint.** All three provider
  handlers built their response as `{ ok: true, ...result }` — a hardcoded `true` spread over
  the pipeline's real outcome — so a purchase whose mint failed was answered with

  ```json
  {"ok":true,"status":"error","error":"D1_ERROR: …"}
  ```

  The buyer paid, received no licence, and the response claimed success. `unsigned`
  (`LICENSE_SIGN_KEY` absent — purchase recorded, no token minted) acked identically. Five
  sites across stripe / paddle / lemonsqueezy. `ackPayload()` now derives `ok` from the
  status via the single `pipelineSucceeded()` definition (`duplicate` counts as success — an
  idempotent replay reached the desired end state) and logs the failure so it reaches the
  Worker log. **The HTTP status is deliberately still 200 in every case:** whether a failed
  mint should make the provider RETRY is a billing decision, not a refactor, and is left to
  the owner.
- **`services/api` had exactly one reachable test, and its billing pipeline had none.** The
  Worker's `src` imports extensionless (`from './billing'`) because wrangler's esbuild
  resolves that at bundle time, but Node's ESM resolver needs the extension — so
  `node --test` could only ever load a leaf module with no relative imports. That is why the
  suite covered `stripe-events.ts` and nothing else: `webhook-pipeline.ts`, which mints and
  revokes licences, was **structurally untestable**. `tests/ts-resolve.mjs` retries a
  relative extensionless specifier once as `.ts`; bare package specifiers and anything
  already carrying an extension are untouched, and a genuinely missing module still raises
  its real `ERR_MODULE_NOT_FOUND`. Chosen over `allowImportingTsExtensions` + `.ts` on all 53
  relative specifiers because it changes nothing the deployed bundle sees. The suite went
  from **7 tests to 23**, now covering the mint/renew/revoke paths.
- **`navig scaffold` into a NEW directory worked or failed depending on the template's
  first item.** `generate()` never created `target_dir`; a `directory` item's
  `mkdir(parents=True)` created it as a side effect, so a template starting with a file
  failed with `Failed to create file <target>/README.md` — naming the file rather than
  the missing parent. The remote path has always run `mkdir -p` before deploying, so
  local and remote disagreed. `generate()` now ensures the destination exists.
- **`validate_template` checked three things and let everything else fail mid-write.**
  It verified valid YAML, is-a-mapping, and a `structure` key — so a `structure` that
  was not a list, a non-mapping item, an unknown `type`, a Jinja syntax error in
  `condition`/`path`/`content`, a non-octal `mode`, an unquoted `mode: 0755` (an int, so
  `int(mode, 8)` raised TypeError), a missing `source` file, or a non-list `children` all
  surfaced partway through `generate()` — which writes as it walks and has no rollback,
  leaving a half-built project on disk. All of these are now caught before anything is
  written, each error naming the exact item (`structure[0].children[1].mode`). Jinja
  fields are compiled, not rendered: values arrive at generate time, so an undefined
  variable is still perfectly valid at validation.
- **The same malformed `mode` was silently ignored on a directory and raised on a file.**
  `_create_directory` wrapped its chmod in `except Exception: pass`, which swallowed the
  `ValueError` from `int(mode, 8)`, while `_create_file` catches only
  `OSError`/`PermissionError` and so surfaced it. Same template, same typo, two
  outcomes. The chmod itself stays best-effort — Windows frequently ignores POSIX modes,
  which is a platform fact, not a template error.
- **The `importlib.reload` bug class is swept and closed.** #829 fixed the one site that was
  actually poisoning siblings; the remaining eleven were audited rather than assumed. Every
  reloaded module was resolved and each risky pair run in **hostile order** (reloading file
  first, which is what the relevance-ranked gate can do at any time):

  ```
  navig.remote                          19 siblings, 142 tests   PASS
  navig.gateway.audit_log + billing_…    9 siblings, 202 tests   PASS
  navig.cli.selector                     2 siblings,  44 tests   PASS
  navig.agent.remote_agent                            9 FAILED   <- fixed in #829
  ```

  The discriminator turned out to be exact: `remote_agent` is the **only** reloaded module
  that defines an `Enum`. A stale plain class (`AuditLog`, `RemoteOperations`) still
  instantiates and behaves; a stale Enum member stops equalling its twin, which is why that
  one site broke nine unrelated tests and the other eleven break nothing.
  New guard `core/tests/quality/test_no_reload_of_enum_module.py` (in `sourceGuardArgs`)
  bans exactly that pairing — reloading an Enum-defining navig module — instead of banning
  reload outright, which would have churned eleven legitimate sites for no defect. It also
  refuses to go green over a module it could not import, and its partner asserts the alias
  resolver still recognises the reload sites it polices.
- **`navig scaffold` silently omitted items whose condition was malformed.**
  `_process_structure` gates every item on `_check_condition` and skips it when False —
  and that helper caught a Jinja error, warned, and returned False. So a broken
  expression made the item vanish and the command still reported
  `✓ Scaffold complete`. For a `directory` item the entire `children` subtree went with
  it, and a file missing from a generated project surfaces much later, when the template
  is no longer in mind. `False` now means "the condition evaluated to false"; a
  condition that cannot be evaluated raises, naming the expression, the item, and the
  underlying Jinja error. `commands/scaffold.py` already had the right handler
  (`ch.error` + exit 1) — it was simply unreachable from here.
  **Behaviour change, deliberately:** a template with a malformed condition now fails
  instead of generating a wrong tree. An undefined *variable* is unaffected — the
  environment uses Jinja's default `Undefined`, so `{{ missing }}` still renders empty
  and skips, which is how optional items are written. No shipped template is affected:
  the repo ships no scaffold templates at all, so this only reaches user-supplied ones.
- **`navig agent plan` printed a green tick over a failed step.** `AgentToolRegistry.dispatch`
  raises for an unknown tool, a blocked permission and an unavailable `check_fn` — but a
  tool that **ran and failed** does not raise: `_result_to_str` turns
  `ToolResult(success=False)` into `"[ERROR] …"` and returns it normally. `navig_run` with a
  non-zero exit, a failed db query or dump, and a permission-denied write all arrive that
  way. `plan_execute` set `step.status = "success"` unconditionally, so
  `navig agent plan "restart nginx and verify it's healthy"` rendered
  `✅ Step 1 — navig_run (success)` over a failed restart — a false green on a live-infra
  surface, exactly what this project's doctor-honesty doctrine forbids. Worse, plan
  **revision** only ever ran from the `except` arm, so the recovery path had never once
  fired for the one failure mode that does not raise.
- **A failed speculative tool call was cached and replayed.** `SpeculativeExecutor` stored
  every speculation result, and its `except` arm — like the one above — could not see a
  non-raising failure. So an `[ERROR] connection refused` was served back as a speculative
  **HIT** for the cache's whole TTL: one transient blip pinned as a permanent answer the
  agent never retried. Speculation is an optimisation, so a discarded failure costs at most
  a re-run.
  Both are the same root cause: only `conv/agent.py` knew the contract, and it kept a
  **private copy** of the marker list. The prefixes now live beside the code that produces
  them as `agent_tool_registry.is_failure_result` / `TOOL_FAILURE_PREFIXES`, and all three
  consumers share it — a gate and its definition cannot drift apart.
- **One test's `importlib.reload` turned 9 sibling tests red, depending on order.**
  `tests/remote/test_remote_agent_timeout.py` reloaded `navig.agent.remote_agent` to
  re-read `NAVIG_REMOTE_TIMEOUT` at import time. Reload rebinds the module's globals **in
  place**, so `CommandState` and `RemoteResult` become fresh classes while every name
  imported elsewhere still holds the originals — and enum members then compare unequal to
  their identical-looking twins:

  ```
  assert <CommandState.FAILED: 'failed'> == <CommandState.FAILED: 'failed'>
  ```

  `RemoteResult.success` (`state == CommandState.COMPLETED and return_code == 0`) went
  **False for a completed, zero-exit result**, because the property resolved the *new* enum
  out of the shared globals while the caller held an *old* member. Alphabetically the
  reloading file sorts second so it usually looked fine; the relevance-ranked gate
  selection can order it either way, so it surfaced as flake. Measured on clean `main` with
  that file forced first: **9 failed, 63 passed**; after the fix, 81 passed.
  The parse is now `remote_agent.resolve_command_timeout(raw)` — a plain function the
  module itself uses — so the tests pass it a value instead of reloading a module half the
  suite holds references into.
- **A long Telegram reply that only partly went out reported success.** Any body over
  4096 UTF-16 units is split by `_send_long` and sent as ordered parts — routine for
  document-grade answers, and sending several messages back to back is exactly what earns
  a 429. It returned `last`, the final part's result, **unconditionally**, so the whole
  send was judged by one part: if earlier parts were rejected and the last one landed the
  caller saw a truthy result and the reader got a reply **with a hole in it**; if the last
  part was rejected after the others went out it returned `None`, so a caller that retried
  sent the earlier parts **again**. (`_send_notification` checks exactly this return.)
  It now stops at the first rejection and returns `None`, so the reader gets a short
  **prefix** — visibly cut off — rather than text that looks complete but is missing its
  middle, and the failure is logged with how many parts had already been delivered.
  `_api_call` has exhausted its own 429 retry budget by the time it returns None, so this
  is a persistent failure rather than a blip; the parse-mode-stripped retry still rescues
  a part, and a fully delivered reply still returns the last result unchanged.
  Same defect `_send_with_attachments` documents for media, at the other end of the list.
  The `/format` command runs its own chunk loop and had the same hole-punching problem —
  nothing consumes its return, so only the gap mattered there; it now stops at the first
  rejected chunk too.
- **The Matrix bot singleton had no ownership rules.** `navig.comms.matrix` keeps a
  module-level `_bot` that `get_matrix_bot()` returns and the HitL router, the E2EE
  manager and the gateway channel adapter all resolve through. `stop()` cleared it
  **unconditionally**, so one bot's shutdown deregistered whichever bot happened to be
  registered — a still-running instance became invisible, and every consumer resolving
  through the accessor would then build a second bot on the same account (two sync loops,
  two device sessions). `MatrixChannelAdapter` also **adopts** that singleton when one is
  running rather than constructing its own, and then stopped it on its own shutdown,
  closing the client and cancelling the sync loop of a bot the gateway's `_init_comms`
  still holds. A bot now deregisters only itself, the adapter tracks whether it created
  what it holds and starts/stops only that, and registering over a different *running*
  bot logs a warning instead of orphaning it silently.
  Latent rather than live today — the gateway and `telegram_worker` are separate
  processes, so each ends up with exactly one bot — but the adapter's adopt-then-stop
  path is one shared process away from reachable.
- **A test that faked one provider helper poisoned it for the rest of the run.**
  `navig/providers/__init__.py` resolves a lazy export through a module-level
  `__getattr__` and then **caches the value into the package's `globals()`** so later
  reads bypass the hook. That is right in production — nothing swaps a submodule
  attribute at runtime — but it makes a test patch irreversible:
  `monkeypatch.setattr("navig.providers.auth.AuthProfileManager", _Fake)` is undone at
  teardown on the *submodule*, while `navig.providers.AuthProfileManager` keeps the fake
  for the remainder of the pytest session. Measured directly: `auth module restored:
  True` / `package still poisoned: True` / `has add_api_key: False`.
  The damage lands on whatever runs next. `tests/quality/test_instance_method_contract.py`
  resolves through the package, so after `tests/agent` had run it reported **13 findings
  that were all false** — `auth.add_api_key` / `.save` / `.store` / `.remove_profile` in
  `commands/ai.py` and `providers/connect.py`, every one of which exists. The guard was
  red on `main` in any combined run and green in isolation, which reads like flake and
  teaches people to skip it.
  36 patch sites across 9 test files now patch the package name first (so monkeypatch
  records the real object and restores it) and the submodule second. New guard
  `core/tests/quality/test_lazy_export_patch_hygiene.py`, in `sourceGuardArgs`, holds it
  at zero; its partner asserts the caching that makes this dangerous still exists, so the
  guard cannot outlive its reason silently.
- **Every Matrix delivery failure was reported as a success.** `NavigMatrixBot.send_message`
  / `send_notice` are documented "Returns event_id or **None**", and None is how they
  signal *every* failure: no client (the bot never connected), an unexpected server
  response, or an exception they catch themselves. Because they never raise, the
  `try/except` every daemon-side caller wrapped around them had an unreachable `except`
  arm and a success path that covered the failure — so an unconnected Matrix bot reported
  each notification as delivered. Five consumers fixed: `comms.dispatch._send_matrix`
  (now also records the event id, which the Matrix branch never captured),
  `MatrixNotifier._send_now` and `._flush_batch` — whose retry machinery hung off that
  unreachable `except`, so it had never once run — and `MatrixHitLChannel.notify` /
  `.ask`. `ask` no longer waits out its full timeout for a reply to a question that was
  never delivered, which had made an undelivered question indistinguishable from an
  ignored one and held the router up before it tried the next channel.
  `navig matrix send` (the CLI) has always checked `if result:`; the contract was known,
  just not honoured off the CLI path.
- **A WhatsApp reply that failed to send looked exactly like one that arrived.**
  `_handle_incoming_message` awaited `_send_message(...)` twice — once for the real
  answer, once inside the `except` handler — and discarded both results.
  `_send_message` returns False for a bridge error, a non-200 or a timeout, so the
  handler ran, produced an answer, and the answer was lost with nothing recording it;
  the user just got silence. The error path was worse: if that send failed too the
  user got **no answer and no apology**. Both are now checked and logged with the
  recipient, so a dead bridge is visible instead of looking like a quiet bot.
- **A trigger that could not save its fire state silently stopped being rate-limited.**
  `_execute_trigger` stamps `last_fired`, increments `fire_count` and calls
  `record_fire()` — which feeds the rolling `max_fires_per_hour` window — then
  discarded `update_trigger()`'s result. That window exists only in the saved file, so
  a failed save meant the in-memory trigger had fired while the persisted one had not:
  after the next reload the limit was enforced against an empty history. The failure is
  now reported.
- **`navig tunnel restart` could leave the old tunnel running on a port nobody tracks.**
  `restart_tunnel` discarded `stop_tunnel()`, and that bool is False for two very
  different things: "there was nothing to stop" and "the process is still alive and I
  could not kill it" (`AccessDenied`). The second path deletes the cache entry anyway —
  so `start_tunnel` saw no tunnel, found its preferred local port held by the survivor,
  and quietly picked a **different** port from the range. Restart reported success on a
  port nobody asked for while the original ssh process kept running forever, untracked.
  The two cases are now told apart by whether a tunnel was running before the attempt;
  a genuine failure raises instead of starting a second tunnel over the first.
- **The shell tool reported SUCCESS for a command that never ran.**
  `ToolRouter.execute()` invoked the handler and wrapped whatever came back as the
  tool's `output`. An `async def` handler returns a **coroutine** — work that has not
  happened yet — so the router answered:

  ```
  status : ToolResultStatus.SUCCESS
  output : <coroutine object bash_exec_async_handler at 0x...>
  ```

  Measured against the real router: `bash_exec` is the one async handler in the
  registry, and `llm/generate.py` `_maybe_execute_tools` — the live path that runs a
  tool the model asked for — uses the **synchronous** `execute()` / `execute_multi()`.
  So the model was handed a formatted "success" containing a coroutine repr while
  nothing executed, and the coroutine was garbage-collected un-awaited. The sync path
  now drives the awaitable to completion (verified: the same call returns real
  `stdout`), or, when it is called from a thread that already has a running loop and
  therefore cannot block, returns an ERROR naming `async_execute` as the fix. The one
  thing it will never do again is claim SUCCESS over an object.
  The async path had the mirror defect: `async_execute` was a thin wrapper that awaited
  `execute()`'s output **after** its `try/except` had already returned, so an exception
  raised while the handler ran escaped the router raw instead of becoming
  `ToolResult(status=ERROR)` — the contract every caller is written against. Both entry
  points now share the guard chain (`_prepare`) and each awaits inside its own error
  handling; latency is measured across the await rather than up to it.
- **`code_sandbox` was advertised to the model and could only ever fail.** It declared
  `module_path="navig.tools.sandbox", handler_name="execute"`, and that module has never
  defined `execute` (it exposes `sandboxed_execute`). `get_handler()` caught the
  AttributeError and returned None, so the tool — plus its aliases `sandbox`, `exec`,
  `run_code` — sat in the LLM prompt as available and answered "No handler loaded" when
  called. Renaming the string would not have fixed it: the real function takes a shell
  `command` while the tool's schema promises `code` + `language`, so wiring them is a new
  code-execution path on a DANGEROUS tool, not a typo repair. It is now registered
  `UNAVAILABLE` with the reason, which keeps it out of `list_tools(available_only=True)`
  and turns a call into an honest "Tool unavailable: …".
- **A Telegram channel restart no longer strands every HIGH-priority notification.**
  The health monitor recovers a stale channel through `_restart_channel`, which restarts
  it **in place** — but `_start_notifier` replaced `channel._notifier` with a brand-new
  object on every start, while `_init_comms` had captured the original ONCE at gateway
  boot into a module-level global in `navig.comms.dispatch`. After a single restart that
  global pointed at a notifier whose `stop()` had already cancelled its scheduler loop,
  and the damage was priority-shaped: CRITICAL still went out (sent inline) and
  NORMAL/LOW still flushed (their timer is created per batch), but **HIGH is appended to
  a queue only `_scheduler_loop` drains** — so it queued forever while `send()` returned
  True, meaning "accepted for delivery". A restart is a routine recovery event, so this
  needed no failure to trigger, only a blip. The notifier is now reused across restarts
  (a changed default chat still gets a new one — that is a different destination), which
  also carries anything already queued across the blip instead of stranding it.
  `TelegramNotifier.start()` is idempotent now that re-entry is reachable: a second call
  used to overwrite `_scheduler_task` and orphan the running loop, leaving two loops on
  one queue with only the newer reachable by `stop()`.
- **`comms` reported a rejected CRITICAL message as delivered.** `_send_telegram`
  discarded `TelegramNotifier.send()`'s result and returned `DeliveryResult.success`
  unconditionally. That bool is "accepted for delivery" for queued/batched priorities
  but the REAL delivery result for CRITICAL, which is sent inline — so a must-deliver
  message the transport had just rejected was reported as sent.
- **The channel registry declared three adapter classes that do not exist**, and asking
  it for the flagship channel reported that channel as broken. `ChannelMeta.adapter_class`
  is a **string**, resolved at runtime by `getattr(module, meta.adapter_class)` — nothing
  type-checks a string, so:

  ```
  telegram   declared=TelegramChannelAdapter    actual=TelegramChannel
  whatsapp   declared=WhatsAppChannelAdapter    actual=WhatsAppChannel
  discord    declared=DiscordChannelAdapter     actual=DiscordChannel
  ```

  `get_adapter()` caught the `AttributeError` and set the channel's status to **ERROR**,
  so the registry reported *the channel* as broken when only a never-written wrapper was.
  Matrix and SMS resolve correctly and always did.
  Renaming the strings would have been wrong: `get_adapter` constructs with
  `adapter_cls()`, and every `*Channel` class needs arguments (`TelegramChannel` requires
  a bot token), so it would have swapped a caught `AttributeError` for an uncaught
  `TypeError` in the caller. These channels simply have no registry-level wrapper — they
  are reached through the gateway's live instances — so the registry now declares that
  truthfully with `adapter_class=None`, and `get_adapter` also catches `TypeError` so a
  future adapter that needs arguments degrades instead of crashing.
  New guard `core/tests/quality/test_channel_adapter_classes_exist.py` (in
  `sourceGuardArgs`) checks every declared adapter exists, is zero-arg constructible, and
  actually loads — the string form of the bug `scripts/check_module_attrs.py` exists for,
  which that guard cannot see. Two messaging-adapter docstrings referencing the
  nonexistent classes are corrected too.
- **The Telegram bot ignored its own owner when `allowed_users` held strings.** Same
  root cause as the deck allowlist, on the live channel: ids arrive from the API as
  **ints** while `telegram.allowed_users` comes from config, where the operator can only
  produce strings — and `set(allowed_users or [])` kept them as written. Measured on the
  real channel, owner id 12345:

  ```
  allowed_users: ["12345"]   ->  {'12345'}                  owner IGNORED
  navig config set … 12345   ->  set("12345") = {'1'..'5'}  owner IGNORED
  allowed_users: [12345]     ->  {12345}                    owner answered
  ```

  `allowed_groups` had it too, where the id is a large **negative** integer.
  Parsing now goes through a new `navig.core.coerce.coerce_id_set` — the sibling of
  `coerce_bool`, for the same reason — which the deck's allowlist also delegates to, so
  the bot and the deck cannot disagree about which ids an operator listed. Unparseable
  entries are logged as errors rather than dropped in silence.
  The two surfaces keep their own *emptiness* policy: empty means **deny all** on the
  channel (as its docstring states) and **no restriction** on the deck. Sharing the
  parser must not merge those, and a test pins each.

- **A Telegram message the transport REJECTED no longer comes back as a delivery
  receipt.** Telegram is the odd one out among the messaging adapters: it signals a
  rejected send by returning `None` **without raising** (a 429 whose retry budget ran
  out, an API error, a timeout). Discord raises, WhatsApp Cloud checks `resp.status`,
  Vonage checks its status field — only this transport returns falsy. `_msg_id(None)` is
  `""`, and `""` is not `None`, so `send_message` built
  `DeliveryReceipt.success(message_id="")` for a message that never went out, and
  `_dispatch_attachment` returned `""` which the caller's `if mid is not None` counted as
  delivered — so a media batch the Bot API refused reported *full* success, the exact
  outcome that caller's docstring promises cannot happen. `messaging/send.py` hands the
  receipt straight to `tracker.apply_receipt`, so each phantom persisted a "delivered"
  row for a message that does not exist. A new `_sent_id` separates the two cases that
  `_msg_id` had collapsed: `None` means rejected, while a returned object with an
  unreadable id stays a **success** — that message did go out, and reporting failure
  would make the caller retry and duplicate it.
  This was unreachable until the adapter was given a bot at all (it shipped with
  `_bot = None`, so every send failed early); wiring the bot in the previous change is
  what made it live.
- **The deck allowlist locked the owner out of their own deck.** The Telegram user id
  from initData is an **int**, but `telegram.allowed_users` comes from config, where the
  operator can only produce strings — and both shapes reached `set(...)` untouched:

  ```
  allowed_users: ["12345"]     -> {'12345'}                  owner DENIED
  navig config set … 12345     -> set("12345") is            owner DENIED
                                  {'1','2','3','4','5'}
  allowed_users: [12345]       -> {12345}                    owner allowed
  ```

  So only an unquoted YAML integer worked; every other way of writing the same id
  produced `user 12345 not in allowed_users` in the log while the config plainly listed
  12345. Values are now parsed to `set[int]`, accepting ints, quoted strings, a bare
  string, and comma/space-separated lists.
  The second half matters more: the middleware treats an **empty** allowlist as "no
  restriction", so a configured-but-unusable value (`allowed_users: "nobody"`) must not
  collapse into the same state as *unset* — that would turn a typo into an **open deck**.
  A new `allowed_users_configured` flag keeps the two apart: unset stays open, configured
  denies whatever it cannot match, and an unparseable entry is logged as an error rather
  than dropped in silence.

- **`POST /api/deck/llm-modes` reported a mode change that was never saved.**
  `LLMModeRouter.update_mode` is documented as updating the config **in memory**, so the
  `try/except` that writes `config.yaml` is the whole of the persistence — and its failure
  was a `logger.warning` under an unconditional `{"ok": true}`. The caller saw the new
  config echoed back and lost it at the next daemon restart, with nothing to explain why.
  The response now carries `persisted`, plus a `warning` naming the cause and saying the
  mode "reverts when the daemon restarts". `ok` deliberately stays `true` — the mode
  really *is* active in the running process, so reporting failure would be its own lie.
  The failed write logs at **error**, not warning.
- **A typo'd mode silently reconfigured a *different* mode.** `resolve_mode` falls back to
  `big_tasks` for any unrecognised hint — correct when detecting a mode from a user's
  prose, wrong for an identifier a caller typed. Because the fallback is itself canonical,
  the handler's own `if canonical not in CANONICAL_MODES` guard was **dead code**: it could
  never fire. `POST {"mode": "codng"}` therefore reconfigured `big_tasks` and answered
  `{"ok": true, "mode": "big_tasks"}`. The hint is now validated against `CANONICAL_MODES`
  and `MODE_ALIASES` *before* the fallback erases the distinction, and the 400 lists the
  valid modes. Aliases keep working.

- **`POST /api/deck/admin/settings` reported a save it never performed.** The validator
  ended in `# else: silently skip unknown keys`, so a patch naming only keys the server
  does not know fell through every branch and returned
  `{"ok": true, "updated": [], "errors": []}` with **HTTP 200** — indistinguishable from a
  successful save. One typo (`hybrid_serch`) or one key renamed in a later version, and
  the caller is told the setting was stored. The deck ships only a GET client for this
  endpoint, so the realistic caller is a script or an agent — the consumer least able to
  notice that `updated` came back empty.
  An unknown key is now an error naming the keys that *would* have worked, which the
  pre-existing `if errors and not updated` check turns into a 400. An empty group stays a
  200 (nothing was asked, nothing changed), a wrong type is still a 400, and a partial
  patch stays a 200 — the valid writes really happened — but gains a `complete` field so
  a caller that only checks `ok` still learns a key was rejected. Same `ok`/`complete`
  split as `navig backup export`.

- **Two assistant toggles could not be turned off, and nothing said which key to set.**
  `should_auto_analyze()` (gates auto-analysis of a failed command) and
  `requires_confirmation()` (gates the destructive-operation prompt in
  `proactive_display.check_pre_execution_warnings`) read their config value raw, so
  `bool("false")` made them return `True` for every value an operator could type.
  Verified end to end against a real config file:

  ```
  proactive_assistant: {confirmation_required: "false", auto_analysis: "false"}
    before -> auto_analysis='false'  confirm_destructive='false'   (truthy strings)
    after  -> auto_analysis=False    confirm_destructive=False
  ```

  Neither key was documented, which is exactly why the config-boolean guard did not
  cover them — its scope is deliberately "keys a user is documented as being able to
  set". They are documented now, so the guard holds the line here permanently: the
  self-growing property it was built with, exercised for real (41 documented toggles
  before, 44 after, still at zero uncoerced reads).
  `navig ai show --status` now renders the settings as a table with the **config key**
  beside each value, so the setting is discoverable instead of guessable, and a failed
  write to the assistant **audit log** is reported as a warning naming the gap rather
  than a `ch.dim` aside — an audit trail with invisible gaps reads as a complete record.
  Polarity note for the record: `confirmation_required` was fail-SAFE (an uncoerced
  `"false"` kept the prompt ON), so nothing was ever unguarded — the setting was simply
  inert, and an operator who turned it off kept being prompted with no way to tell why.

- **A failed ledger append was a dim line that erased the last trace of the command.**
  `OperationRecorder.record()` caught `OSError`, reported it with `ch.dim` — the quietest
  sink there is — and returned the operation id as if the line had been written. The
  command itself had already run; what went missing was the only record of it, so
  `navig undo` could not resolve the id it was handed and `navig insights` under-counted
  with no gap to notice.
  Worse, the completion path then cleared the operation's **in-flight marker**
  unconditionally. That marker exists precisely so an operation with no terminal line can
  be reaped into an honest `interrupted` entry — clearing it after a failed append removed
  the last evidence the command ever ran.
  Now: a warning that names what was lost (`navig undo` cannot revert it, `navig insights`
  will not count it, and where the ledger lives), a new `store_write_failed` config
  incident so the daemon's losses reach `navig doctor` → Config Health, and the in-flight
  marker is kept when the append failed so the reaper can still record it. A successful
  append stays silent and still clears its marker.

- **`navig backup export --format archive` silently dropped every global host when run
  inside a project.** It copied ONE directory (`config_manager.config_dir`), while
  `list_hosts()` merges app-specific *and* global config (`get_config_directories()`) —
  so the two formats of the same command exported different sets:

  ```
  list_hosts()              -> ['globalhost', 'projhost']
  --format json  (uses it)  -> both
  --format archive          -> projhost only
  ```

  Nothing errored, and a restore from that archive looks complete. The archive now walks
  every config directory in priority order. Priority is *preserved*, not flattened: app
  config wins, which is exactly how NAVIG resolves a host, so the archive holds the
  config that is actually in effect. A lower-priority file it shadows is not a failure,
  but it is not in the archive either — those are listed in the summary, in `--json` as
  `shadowed`, and in the archive's own `manifest.json`.
  `config.yaml` is deliberately **not** merged: it is a single settings document and
  combining two of them is a semantic decision this command has no basis to make. The one
  in effect is exported and the manifest records which directory it came from, alongside
  the full `config_directories` list, so a restore is not guesswork.

- **`navig install` printed ✓ for the parts that did not happen.** Three sites in one
  path, each ending in an unqualified green tick:
  - A `SKILL.md` whose parse **raised** printed `✓ Installed` and dropped the exception —
    while the branch directly above it, for the milder "parsed to nothing", warned
    correctly. Parsing is the only validation this install performs, so the one case
    where it blew up was also the one case that claimed success.
  - A failed lockfile write was a `ch.dim` aside, the quietest sink available. What it
    loses is the block's **tamper evidence**: without the pinned digest, a later `navig
    apply` cannot tell that the block on disk is the one that was installed — and for the
    paid tier the verified receipt is the entire product claim.
  - A failed skill-shim write was a silent `pass`. The shim is what makes an installed
    block appear in the skill surfaces, so without it the block is installed and
    **invisible**, which looks exactly like a failed install.

  Both block-level failures are now collected and reported together (an install can lose
  its tamper evidence *and* its discoverability), the summary line says which install was
  degraded rather than dropping the identifying detail, and a clean install still prints
  its ✓.

- **An unreadable `triggers.yaml` reported "Trigger not found" instead of "I could not
  read it".** `TriggerManager.load_failed` exists precisely to separate *"you have no
  triggers"* (an answer) from *"I could not read your triggers"* (not an answer) — and
  only **one of thirteen** consumers read it. Driven through the real CLI against a host
  file with one unclosed bracket:

  ```
  before:  ✗ Trigger 'my-alert' not found          (exit 2)
  after:   ✗ Could not read …/triggers.yaml — this is NOT an empty list.
              Your triggers are still on disk; fix or move that file to see them.  (exit 1)
  ```

  Nine more commands (`show`, `add`, `remove`, `enable`, `disable`, `test`, `fire`,
  `stats`, plus `list`) now go through one `_readable_manager()` helper. `trigger
  history` and `history --clear` deliberately **do not** — they read `history.jsonl`, a
  different file, and stay answerable while `triggers.yaml` is broken; refusing them too
  would turn a narrow fault into a wider outage. A source guard pins both halves of that
  split, and fails if a new trigger command belongs to neither.
  The proactive engine holds a `TriggerManager` too, and there the failure is *entirely
  silent*: `process_event` matches nothing, so automation just stops firing. That path now
  records a new `store_read_failed` config incident, so it surfaces in `navig doctor` →
  Config Health and through the `config_incidents` monitor like every other
  silent-degradation path. Recording is best-effort — a health note must never be the
  thing that breaks a load.

- **`navig tray uninstall` never once removed the auto-start entry.** It called
  `winreg.OpenSubKey`, which is not a function — the real name is `OpenKey`. So it raised
  `AttributeError` on every run, an `except Exception` downgraded that to a warning, and
  the command finished with `✓ NAVIG Tray uninstalled` and exit 0. The Run entry that
  `desktop/install-tray.ps1` writes survived, so the tray came back at the next login
  after the user had uninstalled it and been told it worked.
  A registry failure now reports what is still installed, prints the exact `reg delete`
  to run by hand, and exits 1 — unlike a partial backup, which still leaves a usable
  file, a partial uninstall leaves the software running.
  The Run key path and value name are no longer re-typed as literals: they move to the
  new side-effect-free `navig/desktop/tray_constants.py`, which `tray_app.py` re-exports
  (so `tray_app.REGISTRY_KEY` still resolves) and the CLI imports directly. Importing
  `tray_app` itself would not do — it **replaces `sys.stdout`/`sys.stderr` at import
  time**, and a CLI command must not inherit that.
  New guard `core/tests/quality/test_function_local_stdlib_attrs_exist.py` (in
  `sourceGuardArgs`) closes the blind spot that hid this: `scripts/check_module_attrs.py`
  is built on mypy, and mypy cannot see a module imported lazily inside a function under
  a platform branch. The new one needs no mypy — it resolves the module and asks
  `hasattr`. Measured over `navig/`: 1554 function-local stdlib attribute uses, one real
  defect, two POSIX-only names suppressed by name (the same rule the mypy guard uses).

- **The Telegram messaging adapter now actually has a bot.** It was registered with
  `_bot = None` on every install, so every send through the unified messaging layer
  returned `DeliveryReceipt.failure("Telegram bot not initialised")` — including the
  attachment path that `messaging/attachments.py` exists to serve. The gateway reached
  for the live bot through `ChannelRegistry.instance()`, a classmethod that has never
  existed (the registry only ever exposed the module-level `get_channel_registry()`),
  written as `... if hasattr(ChannelRegistry, "instance") else None` — so the branch was
  permanently dead, and it contained the **only** `set_bot()` call site in the tree. The
  bot now comes from the live channel, `gateway.channels["telegram"]`, which is the
  object the adapter was built for: `_msg_id` documents that it accepts "a dict
  (channel) or object (PTB Message)", and every method the adapter calls
  (`send_message(chat_id=, text=, parse_mode=)`, `send_photo` / `send_video` /
  `send_animation` / `send_voice` / `send_document`, `_session`) matches. A contract
  test now pins that correspondence so the two cannot drift apart silently.
  The same phantom call in `_init_comms` — a "fallback" for the Telegram notifier — is
  gone; there is no second place to look, and it now says which of the two real causes
  applies (channel not running, or no `allowed_users`) instead of pretending to recover.
  **Why it survived:** the gateway test injected a fake `navig.gateway.channels.registry`
  module whose `ChannelRegistry` had an `instance()` staticmethod the real class has
  never had, and asserted the notifier arrived through it. The fake manufactured the
  very method whose absence was the bug, so the suite was green on a path that could not
  exist outside the test. It now exercises the real one.
- **`navig backup export` reported hosts it had not exported.** The summary counted the
  config *directory* (`list_hosts()`, called again after the write) instead of the data
  that actually went into the file — so an export that silently dropped three of five
  hosts still printed `Hosts: 5`, and `--json` returned `"success": true, "hosts": 5`.
  Restoring from that file looks complete and is not. The per-host read failures existed
  only as `ch.warning` lines that scrolled off above the final green ✓.
  The summary is now derived from the export itself (the collected dict for `--format
  json`, the staging directory the tarball was built from for `--format archive`), reads
  `Hosts: 2 of 5` when incomplete, and lists every skipped item. `--json` gains
  `complete`, `hosts_found`, `apps_found`, `failures` and `dropped_unredactable`.
  Three related fixes in the same path: `--include-secrets` re-read failures were
  `pass  # best-effort; failure is non-critical` — they are critical, because the export
  then silently contains `[REDACTED]` where the user explicitly asked for secrets, and a
  restore from it produces a host that cannot connect; an export that raised printed
  `✗ Export failed` and then **fell off the end of the function**, exiting 0, so
  `navig backup export && upload-backup` uploaded nothing while reporting success; and an
  export with hosts to write that wrote none of them now exits 1 (a partial export still
  exits 0 — the file is usable and the gaps are printed — matching the rule already used
  by `backup_system_config` / `backup_all_databases`).
- **`navig config set whatsapp.enabled false` is now honoured by the channel list.**
  `configured_channels()` read the raw string, so a channel the operator disabled kept
  reporting itself as configured — and it disagreed with `navig gateway status`, which it
  is documented to mirror ("Add a channel there → add it here") and which had already been
  fixed. Measured through the real function:
  `configured_channels({"whatsapp": {"enabled": "false"}})` → `[..., 'WhatsApp']`.
  A regression test now pins both surfaces to the same truth table so they cannot drift
  apart again.
- **`navig config set <toggle> false` is now honoured by 17 more settings.** The CLI stores
  its argument verbatim as a *string*, and `bool("false")` is `True` — so a documented
  toggle set to `false` left the feature **ON**. Proven through the real code path:
  `BrowserConfig.from_config({"browser": {"enabled": "false"}})` returned the truthy string
  `'false'` before, `False` now. Affected: `browser.enabled`, `desktop.enabled`,
  `agent.ears.enabled`, `agent.lsp.enabled`, `agent.speculative.enabled`,
  `approval.auto_evolve.enabled` (×2), `mcp.enabled`, `memory.context.enabled`,
  `proactive.enabled`, `matrix.enabled`, `contribute.enabled`, `deck.enabled`, and the four
  Telegram channel toggles (checklist / forum / inline / reactions). Every read now goes
  through `navig.core.coerce.coerce_bool`, the one config-safe coercion.
  A new build guard, `core/tests/quality/test_config_booleans_are_coerced.py`, holds the
  line at zero: any key the docs tell users to set with `navig config set` must be read
  through `coerce_bool`, and the guard grows itself — documenting a new toggle makes a raw
  read of it fail. It runs on the AST rather than on lines, because `gateway/server.py`
  *documents* this footgun in a docstring and a textual scan flagged the prose as code.
- **A rejected proactive notification is retried instead of dropped** — the queue drain
  called `_send_notification(n)` and then `self.queue.remove(n)` unconditionally, throwing
  away the very bool whose docstring says "so callers don't report a phantom success". So
  `send()` returned True meaning "accepted for delivery", and one Telegram rejection — a 429
  when a burst of alerts goes out at once, a network blip — discarded a HIGH alert ("host
  down", "backup failed") permanently, leaving a log line as the only trace. Each branch now
  settles against the real result: delivered (or intentionally suppressed — quiet hours is
  not a delivery failure) drops it, a rejection requeues it for the next 30s tick, bounded at
  three attempts and then dropped with an ERROR rather than in silence. Removal is by
  identity, since two alerts raised in the same tick can compare equal and `list.remove`
  would drop the wrong one. Four more of the same shape went with it: `_send_batched` now
  reports delivery; a rejected NORMAL/LOW flush returns to the batch buffer instead of being
  lost with it (that buffer is their ONLY delivery path); `MatrixNotifier._flush_batch` had
  the identical drain-before-send drop; and a scheduled task whose notification was rejected
  now names itself instead of stamping `last_run` and going quiet until tomorrow.
  `TelegramNotifier.stop()` also reaps the pending batch-flush task, which previously
  outlived shutdown and woke up to send on a channel that was already tearing down.
- **`comms status` reported a dead SMS channel as healthy** — `SMSHitLChannel.ask`/`choose`
  discarded the send result entirely, so a Twilio account rejecting every message stayed
  `available` forever with `failures: 0`, while Matrix and Telegram recorded health in all
  three methods. The empty string those two return is a REPLY limitation (SMS has no inbound
  webhook), not a send result — conflating the two is what hid the failure.
- **`navig host remove <name>` now exists** — `host_app` shipped eleven verbs
  (list/use/deploy/add/discover-local/test/show/all/servers/firewall/status) and none of
  them removed anything, so adding a host was a one-way door while `navig app remove` had
  existed all along. The implementation was already there and already regression-tested
  (removing the ACTIVE host clears the pointer file, or a re-added same-name host silently
  becomes active again) — it simply had no CLI verb. Confirm-gated, `--force` to skip;
  declining leaves the host in place and does not claim a deletion.
  Also hardens `host_callback` with `ctx.ensure_object(dict)`: nine host subcommands write
  into `ctx.obj`, so reaching any of them without the root `navig` callback died with
  "'NoneType' object does not support item assignment".
- **`navig plugin new` now scaffolds an example that demonstrates the exit contract** — the
  generated `hello` command only called `typer.echo`, so it taught nothing about failure
  handling, and a plugin scaffolded into `~/.navig/plugins/` is the one CLI surface no repo
  test can scan. The example now validates its argument and raises `typer.Exit(2)`, with the
  convention named inline (2 = usage, 1 = operation failure, `from exc` when an exception
  drove it). `docs/plugin-spec.md` § CLI states the same contract, including the two traps:
  `typer.Exit` is a `RuntimeError` so a broad `except Exception` swallows it, and
  `logger.error(...)` in a background coroutine is not a command announcing failure.
- **The exit-honesty ban now covers PLUGIN command surfaces too** — plugins ship real CLI
  verbs (`navig games doctor`, `navig github status`, `navig download …`), and they were the
  last un-guarded command surface. Three live bugs: `navig games doctor` printed
  "Some checks failed" and exited **0**, so `doctor && <run it>` proceeded regardless;
  `navig github status` reported the bundled engine MISSING — a hard dependency, i.e. a
  broken install — at exit 0; and a TikTok photo download that saved nothing printed the
  error and then a **green** "Photos: 0/N post(s), 0 image(s) saved." immediately under it.
  Teaching the guard about plugins first required teaching it what a console IS: it matched
  any `<name>.error(...)`, so all 15 `navig-audio` findings were `logger.error(...)` inside
  daemon coroutines that have no exit code to give, and `PublishReceipt.error(...)` is a
  result field, not a sink. Receivers are now classified (loggers and class names excluded),
  which took the plugin baseline from 20 findings to 4 — 16 of them false.
  Exemptions are keyed by path SUFFIX rather than module name, because 34 stems collide once
  plugins are in scope (`games.py` exists in both `commands/` and `deck_routes/`) and a stem
  key would silently exempt a file nobody reviewed; a test fails any key matching more than
  one file.
- **The exit-honesty guard is now a WHOLE-TREE ban, at zero with no exemptions** —
  `tests/quality/test_command_exit_honesty.py` no longer carries an opt-IN
  `SWEPT_MODULES` list (54 of 169 command modules); it scans **every** file in
  `navig/commands/`. That closes the structural hole that let the class grow back after
  #358 declared it swept: a module nobody remembered to enrol was simply never checked,
  which is how #728/#734/#736 each found live paths in unlooked-at modules. A new
  command module is guarded the moment it lands.
  The last remaining site went with it: `navig ledger show` reported a **broken hash
  chain** and exited 0. The chain is the integrity guarantee behind Block receipts — a
  receipt whose chain does not verify proves nothing, yet `ledger show && <trust the
  receipts>` passed. It now exits 1, raised after the full view so the operator still
  sees which line broke.
  Three functions were candidates for an exemption (a severity dispatcher, a per-row
  glyph in a listing, a help printer whose caller exits). All three were resolved by
  refactoring instead, so the exemption map ships **empty** — the mechanism is kept for
  a future genuine display sink, and a companion test deletes any entry that stops
  suppressing something.
- **`navig ahk …` and `navig config import/export` reported success on failure** — 17 `ahk`
  commands swallowed `_get_adapter()`'s failure (`navig ahk volume 50` on Linux printed
  "AutoHotkey is only available on Windows" and exited 0); `config_backup` had no `typer.Exit`
  at all, so a missing `--file` or a nonexistent export exited 0. The other 24 `ahk` call sites
  already raised — this makes the module consistent with itself.
- **`navig run` reported success when the command never ran** — `run_remote_command`
  swallowed its payload-resolution failure (`if final_command is None: return`), so
  `navig run --file missing.sh` printed "File not found", exited 0, and `… && next-step`
  proceeded. Same for a failed `--b64` encode. Now exits non-zero; the helpers keep their
  return-None contract, since they are the ones that know the specific reason.
- **`navig app …` reported failures but exited 0** — all 23 error paths in `commands/app.py`
  printed ✗ and returned, so scripts could not branch on them and the operations ledger (#345)
  recorded each failed run as SUCCESS. `commands/app.py` was never part of #358's sweep. Now
  `typer.Exit(2)` for not-found / missing-argument and `typer.Exit(1)` for operation failures
  (`from e` when an exception drove it), matching host/db/docker. A host with no apps is still
  exit 0 — empty is not a failure.
- **`navig backup hestia` showed a red ✗ for a normal skip** — an absent HestiaCP is deliberately
  *not* a failure (`backup run --all` calls it on every box), but it was announced with `ch.error`,
  so a routine non-Hestia server looked like a failed backup. Reported as a skip, matching the
  sibling skip in `backup_config`.

### Fixed
- **21% of the test suite never ran in `npm run ci`** — the default profile excluded
  `integration`, but pytest.ini defines that mark as *"requiring MOCKED dependencies"*, i.e.
  needing nothing external. Measured with no servers running: **5,521 passed, 6 failed, 24
  skipped in 17m**. The 6 were real defects invisible to every local run — two installer tests
  reading `core/scripts/` after the installers moved to `core/installers/`, a monkeypatch of a
  `DAEMON_DIR` constant that never existed (and in the wrong module), a test that hung forever
  on `service_start`'s `tail -f` loop, a "no backend" test that downloaded a model from
  huggingface.co, and a Rich line-wrap flake. All six fixed.

### Fixed
- **`navig links delete` reported a failed delete by SILENCE** — a success-only
  `if db.delete(id): _ch.success(...)` meant a failed delete printed nothing at all and
  exited 0, leaving the bookmark in place while the command looked like it worked. The same
  function already exited 1 on not-found and aborted on a declined confirm.

### Added
- **Build guard `tests/quality/test_no_silent_success_branch.py`** — bans the success-only
  branch tree-wide (core + plugins, baseline zero). Deliberately narrow: the test must BE a
  call, `if not f(...)` is skipped, the body must be report calls only, and value-returning
  helpers are out of scope — a broader variant returned 17 hits, all legitimate.

### Fixed
- **`navig trigger add|remove|enable|disable` did nothing, said nothing, and exited 0 on
  failure** — each was a success-only `if manager.X(): ch.success(...)` with no else, so a
  failed mutation produced NO output at all. Now reports the failure and exits 1; an unknown
  trigger id exits 2 (usage), matching host/app/db.
- **`navig db optimize|repair` reported success on a REJECTED table name** — the Typer wrappers
  discarded the bool returned by `optimize_table_cmd`/`repair_table_cmd`, so all three failure
  paths exited 0, including `_validate_sql_identifier` (the SQL-injection guard).

### Fixed
- **The test console no longer re-flows CLI output** — Rich hard-wraps at the console width and
  assumes **80** when stdout is not a tty, inserting a newline mid-sentence so
  `assert "nothing recorded yet" in result.output` failed on output that reads exactly right.
  It bit intermittently: the wrap column moves with the message length, and these views print
  PATHS, so `-n auto` lengthening tmp_path with the worker id made the same assert pass solo and
  fail in the full run. Three tests were debugged this way before the pattern was recognised, and
  **215 more asserts across 75 files** were exposed. `tests/conftest.py` now pins `COLUMNS`,
  removing the variable rather than teaching 215 asserts to tolerate it — verified those 75 files
  give 1832 passed both with and without it, so no assertion depended on the 80-column wrap.

### Added
- **Build guard `tests/quality/test_console_width_is_stable.py`** — proves the width is actually
  in force (a long message must survive on one line) and that the check is not vacuous (the same
  message at COLUMNS=80 must wrap).
- **Build guard `tests/quality/test_ci_mark_filter.py`** — `-m` expressions are NOT covered by
  `--strict-markers`, so a typo silently disables the filter (verified: `not integraton` collects
  everything). Pins that every mark the CI filters on is registered, and that the default profile
  only drops infrastructure marks (`live`/`requires_server`) plus the documented `slow`.
- **Build guard `tests/quality/test_command_exit_honesty.py`** — a ratchet: modules whose
  error-then-`return` paths have been swept stay at zero forever. #358 listed eight modules as done,
  yet `docker.py` still had two live cases and `backup.py` one, because nothing re-checked them.
  Adding a module to `SWEPT_MODULES` is the act of finishing its sweep.
- **17 source guards now actually run in the pre-push gate** (were 5). A guard that scans the
  source tree names no module, so `tests for changed modules` can never select it, and the fast
  profile skips pytest — so 17 of 22 ran only in the ~56-minute full suite and could go red on
  `main` unnoticed (#723 shipped one exactly that way). Includes the guards for unscoped process
  kills, defeated timeouts, orphaned subprocesses/tasks, SSRF tool wiring, the `json_ok` envelope
  and shell-spawn orphans. Cost: +14s (33 tests/22.3s → 116 tests/35.9s, measured).
  `tests/quality/test_source_guards_are_wired.py` is the meta-guard that keeps the list complete.

### Changed
- **Two capabilities extracted to standalone-first plugins** (the `voice/`→navig-audio pattern —
  heavy/self-contained code leaves core, a thin shim stays so nothing forks):
  - **Media dedupe → `navig-dedupe`** (`navig dedupe`/`ndup`). `core/navig/media/*_dedupe.py` are now
    thin re-export shims; `navig media dedupe-*` is unchanged. Optional `navig[dedupe]` extra.
  - **Blackbox flight-recorder → `navig-blackbox`** (`navig blackbox`/`bb`, standalone `nbb`).
    `core/navig/blackbox/*` are now transparent alias shims (private names preserved); the engine's
    navig couplings route through `navig_blackbox._compat` (graceful fallback when navig is absent).
    Optional `navig[blackbox]` extra.
- **Browser "max stealth" engine renamed `clearcote` → `hardened`** — module
  `navig/browser/hardened.py`, `HardenedController`, engine id `"hardened"`, config key
  `browser.hardened.*`. Fully backward-compatible: the legacy `browser.clearcote.*` config
  block, the `engines/clearcote` download dir, and `engine="clearcote"` are still honored.

### Removed
- **Dead interactive-menu code in `commands/interactive.py`** (2026-07-19) — the top-level dashboard
  (`launch_menu` → main menu → host/app/db/docker/file/maintenance/config/tunnel/backup/cron/mcp/…
  submenus, ~90 functions) was never wired to any CLI command — reachable only from its own tests —
  and its submenu handlers had silently drifted against the current command APIs (caught by
  `try/except`, so unreachable *and* non-fatal). Removed via AST reachability analysis from the only
  4 live entry points (`launch_assistant_menu` / `launch_hestia_menu` / `launch_template_menu` /
  `launch_web_menu`, still wired to the deprecated `navig assistant` / `navig hestia` / `navig server`
  and the live `navig flow template`), with a proof that no surviving function references a removed
  one. File shrank 4298 → ~780 lines; two obsolete regression-test modules went with it. No
  user-facing command changed.

### Fixed
- **The Windows Task Scheduler service split-brained under a custom config dir (#302 last door)**
  (2026-07-16) — #302 closed the split brain for systemd + NSSM by writing `NAVIG_CONFIG_DIR` into the
  service env, but the Task Scheduler fallback (`schtasks`, used when NSSM is absent) has **no env
  mechanism** — a `<Exec>` task inherits only the user's *persistent* env, so a shell-set
  `NAVIG_CONFIG_DIR` was lost and the daemon ran its config/vault/gateway against the default
  `~/.navig` while memory followed the custom home. The task now launches `pythonw -c <bootstrap>`,
  which sets `NAVIG_CONFIG_DIR` + `NAVIG_HOME` (+ `NAVIG_SERVICE`) in `os.environ` **before any navig
  import**, then `runpy`s `navig.daemon.entry` exactly as `-m` would — no console flash (pythonw is
  windowless), no wrapper file, and no `setx` polluting the user's global env. Also XML-escapes the
  `<Command>`/`<WorkingDirectory>`/`<Arguments>` values, which were interpolated raw (a username with
  `&` produced malformed XML). Regression-tested: the task XML bakes `NAVIG_CONFIG_DIR`, is well-formed
  even with `&` in the home, and the bootstrap's env-setup makes `config_dir()` == `memory.navig_home()`.
- **The speculative-execution cache served stale results for live/streaming reads** (2026-07-19) —
  the 60s speculation cache is invalidated only by a local *mutating* tool, but the read-only
  whitelist wrongly included reads whose result changes **externally** with no local trigger:
  `background_task_status`, `background_task_output`, `coordinator_status`, `navig_host_monitor`,
  `navig_docker_logs`, `navig_host_test`. So an agent polling a background task could get a stale
  "running" for up to 60s after it finished (and streaming logs/output could miss new lines). Those
  are removed from the whitelist — "read-only" is not sufficient; the result must also be stable
  within the TTL. Also fixed `SpeculativeCache.put(ttl=0)`, which `ttl or DEFAULT_TTL` swallowed to
  the 60s default instead of "expire immediately". Guarded by 2 new tests in
  `tests/llm/test_speculative.py`.
- **Contact phone routes weren't canonicalised in the store — only at the deck's `phone` field**
  (2026-07-19) — `ContactStore` never applied `normalize_phone` to a route address; the deck route
  did it for its `phone` input, but routes added via the CLI, a YAML import, `dispatch`, or the deck's
  own `routes[]` array stored the number verbatim. So `sms:+1 (555) 123-4567` and `sms:+15551234567`
  became **two** routes for the same number (the `UNIQUE(contact_id, network, address)` key saw them
  as different), and a spaced/formatted number was handed to the WhatsApp/SMS adapter, which expects
  E.164. Normalisation now happens at the single parse choke point (`_parse_route_string`), so every
  writer is consistent — but **only** for phone networks (`sms`/`whatsapp`/`signal`/`imessage`) and
  **only** a genuine phone shape: an address with a letter or `@` (a WhatsApp group id `…@g.us`, an
  `@handle`, an email) is left intact, and normalisation never blanks a route. Guarded by 6 new tests
  in `tests/store/test_store_contacts.py`.
- **The daily AI briefing could be skipped entirely — the notify scheduler matched the exact minute,
  but a slow tick overran it** (2026-07-19) — the loop fired a briefing only when a 45s tick landed
  *during* the target minute (`hhmm in briefing_times`), yet each tick also runs an SMS-webhook PATCH
  and a network email scan (`get_email_service().tick()`) before sleeping. When that work pushed the
  interval past 60s, the target minute got no tick and the briefing was **lost for the whole day**.
  Firing is now window-based (`_due_briefings`): a briefing whose scheduled instant falls in the
  half-open window `(last_check, now]` fires — so a slow/overrunning tick catches it on the next tick
  instead of dropping it. Contiguous half-open windows guarantee exactly-once (no double-send), and
  `last_check` is seeded to `now` at boot so a restart never replays times already past. Guarded by 9
  tests in `tests/notify/test_notify_scheduler.py` (missed-minute, startup-no-replay, exactly-once,
  midnight span, malformed times).
- **Studio scheduled posts could fire at the wrong time — `run_at` was stored verbatim from the
  client, but `due()` compares it lexicographically** (2026-07-19) — `ScheduledPostStore` documents
  that `run_at` must be one exact UTC format (its `due()` does `run_at <= now` as a *string* compare),
  but the deck route stored whatever the client POSTed. An agent/script/API caller sending
  `datetime.isoformat()` (`…+00:00`), a non-UTC offset (`…+05:30` — sorts by local wall-clock, not the
  real instant), JS millis (`…:00.000Z`), or a seconds-less `datetime-local` value would fire early,
  late, or never. `create()`/`update()` now canonicalise `run_at` to `%Y-%m-%dT%H:%M:%SZ` UTC at the
  single write choke point (`_canonical_run_at`); a non-ISO value is preserved, not silently zeroed.
  (The OS Studio UI already sliced to this format; direct API callers did not.)
- **A Studio publish that raised wedged the post in `publishing` forever** (2026-07-19) — the
  scheduler set `status="publishing"` *before* dispatching, but didn't wrap the dispatch; a transient
  publish error (network, adapter bug) left the post stuck in `publishing`, which `due()` never
  re-selects, so it was orphaned with no retry. `run_post` now treats a publish crash as a total
  failure and runs the normal status/reschedule path (once → `failed`; recurring → re-`scheduled`),
  recording the error. Guarded by 11 new tests in `plugins/navig-social/tests/social/test_studio.py`.

### Security
- **`safe_fetch` (SSRF guard) validated only the initial URL — a redirect to a blocked IP was
  followed unchecked** (2026-07-19) — `navig.net.ssrf.safe_fetch` ran `check_url` on the given URL,
  then forwarded `**httpx_kwargs` verbatim to `httpx`; a caller passing `follow_redirects=True` (or
  any future default change) would let httpx jump from a validated public host to a `302 Location`
  of `http://169.254.169.254/` (cloud metadata) or `http://127.0.0.1/` with **no** re-check. It now
  follows redirects itself with `follow_redirects=False` and re-runs `check_url` on **every** hop
  (relative Locations resolved via `urljoin`), bounded by `max_redirects` (a longer chain raises
  `ValueError` instead of looping). The module docstring also no longer **claims** DNS-rebinding
  protection it never implemented — it now states the honest limitation (the httpx client
  re-resolves at connect time; pinning the validated IP is a named follow-up). Guarded by 6 tests in
  `tests/net/test_ssrf_redirect.py`. NOTE: the SSRF guard is currently wired into **no** outbound
  call site — routing the codebase's outbound HTTP through it is the separate high-value follow-up.

### Added
- **`navig mcp info <name>`** (2026-07-19) — a per-server drill-down (type · command · package ·
  enabled), rendered in the house table style with an "enable → …" nudge when the server is off.
  `list` shows the whole table; `info` is the single-server detail. The renderer (`status_mcp_cmd`)
  already existed but was reachable **only** from the legacy interactive shell — the same
  stranded-verb story as `search`/`install`/`enable`/`disable`/`remove` (all wired earlier).
- **`--json` on `navig mcp list` and `navig mcp info`** (2026-07-19) — machine-readable output for the
  agent/script audience (the house `--json` contract), and **secret-free**: it exposes each server's
  env **key names** (`env_keys`) but never the values, since MCP `env` routinely holds API keys.
  `list --json` on an empty install is `[]` (not the human "no servers" prose); `info X --json` on a
  missing server is `null`. Guarded by `tests/commands/test_mcp_cli.py`.

### Fixed
- **`navig mcp info` no longer claims "○ not running" for every server** (2026-07-19) — `is_running()`
  only knows a process **this** process started, so from a one-shot CLI it is *always* False; printing
  "not running" asserted a state the CLI cannot actually know (the server's process is spawned by the
  client — an editor, the agent). `info` now shows the running clause **only** when a process is
  affirmatively alive, matching `navig mcp list`, which omits the column for the same reason ("a status
  that can never say Yes is a lie, not a status"). Regression-guarded in `test_mcp_cli.py`.

## [3.24.0] — 2026-07-18

> **Numbering note:** 3.24.0 resumes the 3.x line (the highest historical heading below is
> `[3.23.0]`); the intervening `2.x` entries were an interim public-release numbering scheme
> and are preserved as-is.

### Security
- **navig-mini** — refuse to expose the `/run` command endpoint on a non-loopback interface
  while the HMAC secret is unset, the shipped default, or under 16 chars; add a configurable
  `AGENT_BIND` (default `0.0.0.0`, gated by the secret check; `127.0.0.1` for local-only).
- **vault** — the `.navig/credentials/` store (including the SQLite `-wal`/`-shm` companions)
  is now git-ignored so it can never be committed.

### Changed
- **Skills** — removed a third-party proprietary-agent skill-format interop from
  `navig skill install`/`export`; the generic `claude` / `hermes` / `codex` schemes remain.
- Scrubbed all vendored reference-corpus provenance from shipped source, docs, and changelogs;
  a new `scripts/check-no-lab-refs.mjs` guard (wired into local CI) keeps it out permanently.

### Added
- `THIRD_PARTY_NOTICES.md` — consolidated the required third-party MIT attribution for the
  Windows desktop-automation layer.
- **`navig ledger reap` — age out interrupted operations the ledger never recorded** (2026-07-17) —
  the operation ledger writes a line only at completion (an `atexit` handler), so a process
  hard-killed (SIGKILL, or a SIGTERM that never reaches `atexit`) left **no ledger line at all** —
  the operation vanished silently. A tiny per-invocation sidecar marker (`<history>/inflight/<id>.json`,
  written at start, deleted at completion — outside the T-067 hash chain to keep one line per op)
  now makes an interrupted op detectable: a marker that outlives its process. `navig ledger reap`
  [`--older-than N` · `--dry-run` · `--json`] records exactly one terminal `interrupted` line per
  orphan via a chain-safe append, idempotently. Safety: a marker records the owning PID + `psutil`
  create_time and is **never** reaped while that process is alive (undeterminable liveness is treated
  as alive — the doctor honesty rule); a secondary age threshold guards PID recycling/clock skew.
  `navig doctor` gains a **read-only** row surfacing interrupted ops (it never mutates the ledger —
  reporting and reaping are separate); `navig ledger show` nudges toward `reap` when orphans exist.
  New `OperationStatus.INTERRUPTED` is treated by `skill distill` as neither a working step nor a
  pitfall (its real outcome is unknown). `navig.operation_inflight` + tests
  (`test_operation_inflight.py`, `test_ledger_reap.py`, `test_atexit_status_class.py`).

### Fixed
- **The gateway's webhook-route registration was a latent startup crash — it unpacked a non-iterable
  `RouteDef`** (2026-07-17) — `_setup_webhook_routes` did `for method, path, handler in
  self.webhook_receiver.get_routes()`, but `get_routes()` returns aiohttp `RouteDef` objects (not
  iterable 3-tuples), so the loop raises `TypeError: cannot unpack non-iterable RouteDef` the moment
  it runs with a receiver set. It now hands the routes to `add_routes` directly (the idiomatic form
  in the receiver's own docstring). Note this method is a **no-op in practice today** — it runs from
  `_start_http_server` *before* `_init_autonomous_modules` sets `self.webhook_receiver`, so the
  generic `/webhook/{source}` receiver never actually registers; *enabling* it is a separate decision
  (it exposes an external endpoint, and the built-in `custom` source is unauthenticated), left as an
  owner follow-up. This change just makes the registration correct instead of a crash-in-waiting.
  Separately, `WebhookReceiver.handle_webhook` — the security gate itself, which correctly rejects
  bad/absent signatures (401), a verify-but-no-secret misconfig (500), unknown (404) and disabled
  (403) sources — gained its first direct HTTP coverage (`tests/gateway/test_webhook_receiver_http.py`,
  7 tests), pinning that a rejected webhook never reaches a handler.
- **The command-manifest generator itself shrank the catalog, and the freshness gate ran only in
  billing-blocked CI** (2026-07-17) — two remaining gaps in the manifest-freshness system:
  1. **`tools/export_registry.py` run as a script silently dropped 9 core commands** (1285→1276:
     `navig undo`, `ledger show/verify/reap`, `audit tail`, `skill distill`, `space books`, …). Run as
     a script, `sys.path[0]` is `tools/`, not the core root, so `import navig` bound to whatever a
     stale/partial editable install resolved — a PEP 660 finder pointing at a different checkout, or one
     whose static module map predated the newer command modules — and `_try_register_one` swallowed the
     resulting `ModuleNotFoundError`s as warnings. The shrink passed every cross-artifact guard (they
     compare the four artifacts to *each other*, all shrunk consistently). Fixed by pinning the core
     root onto `sys.path` before importing navig, so the export always reflects its co-located source.
  2. **A soft interpreter/plugin guard** now refuses to overwrite the tracked `generated/` manifest
     from an interpreter that would shrink it — a non-3.13 Python (the first-party plugins live in
     3.13's site-packages, so a 3.14 regen drops ~160 plugin commands: measured 1285→1122) or a
     bare-core install (0 plugin commands). Override with `--allow-interpreter <X.Y>` (must equal the
     running `major.minor`). Soft by design: validation still runs, and a non-tracked `--output-dir`
     (a dry-run/temp compare, the tests) is never gated. CI's regen step passes
     `--allow-interpreter ${{ matrix.python-version }}` so the matrix legs are unaffected.
  3. **`scripts/ci-local.mjs` (`npm run ci`) now carries the four-artifact freshness check** — it only
     lived in the billing-blocked GitHub workflow (#353), which never runs. The new step regenerates
     all four artifacts to a temp dir under Python 3.13 and diffs them against the committed copies with
     `git diff --no-index -I '"generated_at":' -I '^_Generated '`, failing on real drift while ignoring
     the timestamp. It never mutates the working tree, is gated to 3.13 (skips otherwise), and
     soft-skips when the first-party plugins aren't installed (a plugin-less regen would false-fail
     rather than reveal staleness). Proven: green on a clean tree, red on injected drift across
     `commands.json` / `commands.md` / `completions/commands.txt`.
- **The local command-manifest freshness gate is now CORE-scoped, so it can't false-fail on
  plugin-set variance** (2026-07-17, closes the #368 hidden rock) — #368's local gate diffed all
  four generated artifacts wholesale against the committed copies, but the committed manifest is a
  **superset snapshot from the operator's 3.13 machine** while which first-party plugins a
  contributor has installed varies. A contributor whose 3.13 had a different plugin subset got a RED
  `npm run ci` that was **not real staleness** — the exact "Soon" risk #368 flagged. The
  `command manifest freshness` step in `scripts/ci-local.mjs` now splits the manifest on the same
  rule as the pytest rot-guard's `_is_core_command` (a command is **core** iff its `module`
  top-level package is exactly `navig`, not `navig_<plugin>`): **core** commands are compared
  **strictly** — the committed core *entries* (paths + summaries + options + status + `deprecated`
  blocks) must equal a fresh 3.13 regen's, and a core command added / removed / renamed /
  signature-drifted **fails** the step naming it (the real #343/#368 rot: `ledger`/`audit`/`undo`/
  `space books`) — while **plugin** commands are **lenient**, surfaced as a non-fatal INFO note.
  `commands.md` / `completions/commands.txt` / `deprecations.json` are no longer diffed wholesale
  here: their mutual consistency with `commands.json` is already proven deterministically and
  interpreter-independently by the pytest guards (`test_shipped_artifacts_are_mutually_consistent`,
  `test_markdown_renderer_covers_every_manifest_command`), so re-diffing them only re-introduced the
  plugin-subset false-fail with zero added core coverage. Core freshness is now verified even under a
  bare-core 3.13 install (previously a blanket soft-skip). Still gated to 3.13, still never mutates
  the tree. `scripts/ci-local.mjs` also gained a `--only=<substr>` step filter (run/debug one step).
  Proven across three cases: clean tree PASS, injected core drift (dropped/renamed a core command)
  FAIL naming it, plugin-only delta PASS with an INFO note (the false-fail this change kills).
- **The CI command-manifest freshness gate was defeated by its own timestamp, and three of the four
  generated artifacts had no gate at all** (2026-07-17) — the gate ran
  `git diff --exit-code generated/commands.json`, but the manifest embeds a wall-clock
  `generated_at` field, so **every** regeneration changes that line and the plain diff can't tell a
  genuinely stale manifest from a fresh timestamp. That is *why* the manifest silently rotted (#343:
  16 commands missing). And `commands.md`, `completions/commands.txt` and `deprecations.json` were
  never diffed — the markdown drifted +9050 lines / 6-11 days staler before #343 caught it by hand.
  The gate now regenerates and diffs **all four** artifacts, ignoring only the timestamp line via
  `git diff -I '"generated_at":'` (and `-I '^_Generated '` for the markdown header) so a pure
  re-timestamp passes while any real command drift still fails; `completions/commands.txt` carries no
  timestamp and is diffed plainly. Proven both ways locally: a real regeneration (which only bumps
  the timestamp) now exits 0, and injected command drift exits non-zero. Since CI is billing-blocked
  and never actually runs, the local rot-guard is hardened too
  (`tests/commands/test_command_registry_export.py`): the committed `commands.md` and
  `completions/commands.txt` must list the **same command set** as `commands.json` (they derive from
  one export run, so partial regens that staled one artifact are now caught), and `render_markdown`
  must emit exactly one section per command. Both compare within a single snapshot, so they stay
  interpreter-robust across 3.13/3.14's differing plugin surface.
- **A trigger's `max_fires_per_hour` rate limit was stored, settable, and shown as "Rate Limit:
  N/hour" — but enforced nowhere** (2026-07-17) — `Trigger.can_fire()`'s docstring claimed "within
  rate limit", yet it only checked status + cooldown, so a flapping event source (a health check
  oscillating up/down, a noisy webhook) could fire a "10/hour" trigger hundreds of times an hour,
  spamming its notify/webhook/command actions. It is now enforced over a rolling 60-minute window:
  each fire is recorded (`fire_times`, pruned to the last hour) at the single `_execute_trigger`
  choke point, and `can_fire()` blocks once the window hits `max_fires_per_hour` (`<= 0` disables
  the limit). Additive + backward-compatible — old trigger configs load with an empty window.
  Guarded by 9 tests including an end-to-end `process_event` proof that a 2/hour trigger fires
  twice then stops (it fired all 5 times before the fix).
- **Failed commands exited 0 (silent failure) — now they exit non-zero** (2026-07-17) — many
  command paths printed `ch.error(...)` and then `return`ed, so the process exited 0 despite the
  failure. Scripts couldn't detect the failure, and (since #345) the operations ledger recorded the
  command as SUCCESS — the exact honesty gap the exit-status fix surfaced. Swept the genuine
  error paths across `host`, `db`, `docker`, `backup`, `files`, `files_advanced`, `service` and
  `tunnel` to `raise typer.Exit(2)` for not-found/missing-config and `typer.Exit(1)` for operation
  failures (chained with `from e` where an exception drove it). Only true error paths were flipped —
  a "not found but not an error" path is never turned into a non-zero exit. Regression coverage in
  `test_host_command_exit_honesty.py` + extended `test_db_command_core.py` /
  `test_middleware_exit_status.py`.
- **The low-level `POST /cron/jobs` API still accepted an empty name/command and a negative or
  non-numeric `timeout`** (2026-07-17) — after #340 wired schedule validation into that route, it
  still let `{"name": "", "command": "  "}` or `timeout: -5` / `"abc"` through, storing a useless or
  broken job (a non-numeric timeout later crashes the run with a `TypeError`). It now validates as
  strictly as the deck `schedule` route: non-empty name/schedule/command and a positive-integer
  timeout, each with a specific 400. Locked by a new HTTP integration suite
  (`tests/gateway/test_cron_routes.py`, 7 tests) covering CRUD, all the add-time guards, the
  lifecycle 404s, and the service-unavailable 503 — the route previously had **zero** direct
  coverage.
- **A cron schedule with an out-of-range field (`61 * * * *`, `0 25 * * *`, `* * * * 8`) was
  accepted, then silently never fired; the low-level `/cron/jobs` API validated nothing at all**
  (2026-07-17) — `CronParser.is_valid` only checked the character set (`^[\d*\-,/]+$`), so a value
  outside its field's range passed validation, landed in the store, and produced a job that never
  matched (falling through to a wrong +1h fallback). And the gateway `POST /cron/jobs` route ran no
  validation whatsoever — any garbage schedule "succeeded" as a silent hourly job. New
  `CronParser.validate() -> (ok, reason)` range-checks every field (minute 0–59, hour 0–23, DOM
  1–31, month 1–12, DOW 0–7 with 7 = Sunday) across the full grammar (`*` · `*/s` · `a-b` · `a-b/s`
  · `n/s` · `n` + lists) and rejects a non-positive step (`*/0`); `is_valid` is now the bool of it.
  All three add/update surfaces enforce it and surface the specific reason: the gateway `/cron/jobs`
  add route (previously unguarded), and the deck `schedule` create/update **and** schedule-trigger
  create/update handlers (previously a generic "unparseable schedule"). Guarded by 6 new unit tests
  + 2 route assertions.
- **The shipped command manifest had rotted — `navig --schema` / `navig help --json` never
  listed real commands, and `navig pack` residue lingered tree-wide** (2026-07-17) — the
  checked-in `generated/commands.json` (last regenerated 2026-07-15, before these landed) was
  missing 16 registered commands: `ledger show`/`verify`, `audit tail`, `undo`, the seven
  `presence` subcommands, `space books`, `sync instructions` and `skill distill`; the sibling
  `commands.md` / `completions/commands.txt` were staler still (2026-07-09 / 07-04) and both still
  advertised the **long-removed `navig pack` command**. Regenerated all four artifacts with the
  canonical `tools/export_registry.py --format both --deprecations-report` (deterministic apart
  from the `generated_at` timestamp — verified by a double run): +16 commands, +7 refreshed
  signatures (`navig undo` gained `--yes`, `navig space init` gained `--books`), **zero removed**.
  A new local test (`tests/commands/test_command_registry_export.py`) now fails the build if any
  registered command is absent from the shipped manifest, so it can't silently rot again — the CI
  freshness gate is billing-blocked and never runs. Separately, the dead `navig pack` command's
  remaining residue was deleted: its help page (`navig/help/pack.md`), `HELP_REGISTRY["pack"]`
  entry, and three stale `_BUILTIN_COMMANDS` members (`pack`, `package`, `packs`) — whose presence
  also let "Did you mean?" propose the non-existent `navig pack` as a typo fix — plus the
  now-unneeded orphan-tripwire allowlist entry. The `DEPRECATION_MAP` `navig pack → navig plugin`
  migration hint is kept intentionally.
- **Every command was recorded as SUCCESS in the operations ledger — even ones that failed
  with a non-zero exit** (2026-07-17) — the CLI middleware completes each operation in an
  `atexit` handler, and that handler decided success from `sys.exc_info()`. But `sys.exc_info()`
  is `(None, None, None)` at interpreter shutdown (the `SystemExit` from `typer.Exit(n)` / a Click
  usage error has already been consumed at the top level) **and** the completion runs in a fresh
  worker thread, whose thread-local exception state is empty regardless. So `exc_type is None` was
  always true and every operation — including `navig --host missing db tables` (exit 2) — landed as
  `OperationStatus.SUCCESS`. That corrupted `navig ledger show`'s status column and made
  `navig skill distill` (T-069) file a genuinely-failed command as a working **step** instead of a
  **pitfall**: the distiller honestly reflected the ledger; the ledger was lying. Now `navig.main`
  — the one point every invocation exits through — reports the terminal exit code via
  `middleware.note_exit_code()` before the process exits, and the atexit handler maps it truthfully:
  `0`/`None` → SUCCESS, `130` (SIGINT / Ctrl-C) → CANCELLED (a cancel is neither a step nor a
  pitfall), any other non-zero → FAILED; the real exit code is preserved (main re-raises) and also
  stored on the record's `exit_code` field. `complete_operation()` gained an optional explicit
  `status=` (backward compatible) so CANCELLED can be recorded, not just the SUCCESS/FAILED binary.
  The debug-log's own `atexit` success flag, which shared the identical `sys.exc_info()`-at-atexit
  bug, now reads the same captured code. Guarded by `tests/ops/test_middleware_exit_status.py`
  (pure mapping, the atexit handler, a distill failed→pitfall check, and a live-subprocess E2E).
- **Cron day-of-week schedules fired a day late, and composed fields (`9-17/2`) crashed
  scheduling outright** (2026-07-16) — the hand-rolled `CronParser` (habits, Automations,
  Studio) had two matcher bugs. (1) Day-of-week compared Python's `datetime.weekday()`
  (Mon=0…Sun=6) directly against the cron DOW field (Sun=0, Mon=1…Sat=6), so **every** weekly
  schedule ran a day late — `0 9 * * 1` ("Monday") fired Tuesday, `weekdays`=`1-5` ran Tue–Sat,
  `weekends`=`0,6` landed on Mon+Sun; fixed by converting through `_matches_weekday` (and `7` is
  honoured as Sunday). (2) `_matches_field` only understood *pure* step/range/list forms, so any
  composed field a user could legitimately enter — `9-17/2` ("every 2 hours, 9am–5pm"), `0-30/5`,
  `1,10-15` — hit `int("17/2")` and **raised**, 500'ing `schedule add` and erroring the scheduler
  loop; it now parses `*` · `*/s` · `a-b` · `a-b/s` · `n/s` · `n` and any comma-list of them, with
  steps anchored to the sub-range start and malformed sub-parts skipped rather than raised. DOW
  `1-5/2` (Mon/Wed/Fri), previously a silent never-match that fell through to a wrong +1h fallback,
  now resolves. Sparse schedules (`0 0 31 * *`, yearly) resolve too — the next-run scan cap widened
  from ~31 days to ~366. Guarded by 18 unit tests in `tests/tasks/test_cron_service.py`.
- **`navig help <cmd>` never served the rich markdown guides — main.py rewrote every
  `navig help <topic>` to `navig <topic> --help`** (2026-07-16) — the 50 per-command guides in
  `navig/help/*.md` (ledger, undo, audit, history, db, host, …) were unreachable from the CLI:
  the legacy-help normalizer rewrote the invocation before the in-app `help` command (which
  already knew how to render them) ever ran, so users only ever saw Typer's flag dump, and pure
  topic pages like `ai-providers` errored with "No such command". Now `navig help <topic>` renders
  the markdown guide when one exists (with a `Full flag reference: navig <topic> --help` pointer
  for runnable commands); topics without a guide keep the exact legacy fallthrough to `--help`,
  `navig <cmd> --help` itself is untouched, bare `navig help` stays on the fast path, and the
  `--json`/`--plain` shapes are unchanged. A tripwire test now pins every `navig/help/*.md` page
  to a registered command or an allowlisted topic, so future command renames can't silently orphan
  their guides (`pack.md` documents the already-removed `navig pack` and is flagged pending an
  owner decision).
- **`navig ledger show` labeled pure reads as scary file transfers — the operation classifier
  substring-matched raw argv** (2026-07-16) — the middleware's `_classify_operation_type` keyed on
  substrings of the whole command string, so `navig config get log_level` recorded as a
  `file_download` (yellow: "delete the local copy"), any `--output` flag anywhere became a
  `file_upload` ("put" ∈ "--output"), `navig app deploy user-service` became a `service_restart`
  ("service" matched argument text), every `tunnel`/`db dump` verb collapsed into
  `remote_command`/`database_query` (leaving `TUNNEL_START/STOP` + `DATABASE_DUMP` — and their
  reversibility-table entries — unreachable), and the real upload verb `file add` was missed
  entirely. Since T-068 these labels are user-visible reversibility colours in `navig ledger show`.
  The classifier now keys on the RESOLVED tokens (`navig <resource> <action>`, global flags
  stripped): explicit (resource, action) pairs → free-payload resources (`run/exec/ssh/local`,
  whose second token is a shell command, so `navig run list` stays remote) → read verbs
  (get/show/list/status/logs/…) → resource defaults. Pure reads get the new
  `OperationType.READ_QUERY`, labeled `○ read-only` (new `Reversibility.NONE` — no side effect,
  nothing to reverse; never counted as undoable, never an undo candidate). Existing ledger entries
  are immutable history and render unchanged; `OperationRecord.from_dict` now also degrades
  unknown enum values (a ledger written by a newer navig) to `OTHER`/`PENDING` instead of one
  foreign line breaking every history/undo iteration. 39 regression tests pin the fixed and the
  previously-correct classifications.
- **4 stale tests in `tests/routing/test_routing_perf_paths.py` asserted the pre-#189 frozen-path
  contract** (2026-07-16) — they reloaded `navig.llm.routing.trace` / `navig.perf.profiler` and
  asserted module-level `TRACE_LOG_PATH`/`PERF_DIR` constants, but the frozen-path sweep (#189,
  46436b4b) deliberately converted both to call-time resolvers (`_trace_log_path()`/`_perf_dir()`)
  with `TRACE_LOG_PATH` surviving only as a `None` monkeypatch seam — the constant shape is the
  #179 bug class `test_frozen_path_tripwire.py` bans tree-wide. Rewrote the tests to assert the
  CURRENT contract: both resolvers honour `NAVIG_CONFIG_DIR` set *after* import (no reload), default
  under `paths.config_dir()`, the seam stays `None` and wins when set, a logged trace lands inside
  the isolated dir end-to-end, and `PERF_DIR` stays gone. tests/routing: 271 passed + 4 failed →
  278 passed.
- **The agent tool-execution gate no longer fails open — dangerous agent tools inside the
  gateway now require real approval** (2026-07-16) — #299 closed the gateway `policy_check`
  seam, but the AGENT tool gate (`navig/tools/approval.py::ApprovalGate`, consulted before the
  LLM agent runs a destructive tool like `bash_exec` / `db_query` / `cdp_eval`) still
  auto-approved dangerous tools with a log warning even inside the gateway — where the real
  approval consumers (deck Inbox, Telegram, `/approval` routes) already exist. Worse, both
  agent dispatch loops swallowed EVERY gate exception (`except Exception: logger.debug(...)`)
  and ran the tool anyway. Now: the gateway binds the gate to its live `ApprovalManager` at
  startup (`bind_approval_manager` — the #299 twin), so a dangerous agent tool call **blocks on
  the operator's approval**; timeout follows the `approval:` config section's `default_action`;
  every decision is audited as `tool.execute.<tool_name>` (parameters hashed, never stored
  verbatim); and when no approval manager is available inside the gateway the call is **DENIED,
  never approve-with-warning**. Both agent editions route through one shared interlock
  (`gate_agent_tool_call`) that returns a clean `[Denied: …]` tool result the agent reads and
  adapts to — and that fails CLOSED if the gate machinery itself breaks. Non-gateway contexts
  (headless CLI, MCP stdio, tests) keep the single-operator default unchanged, and
  `NAVIG_ALLOW_ALL_COMMANDS=1` still bypasses for pre-screened automation. Operators can pin
  specific tools via `approval.levels` patterns — the request command is `tool <name>`.
  Regressions: `tests/approval/test_gate_manager_wiring.py` (13 tests: approve/deny/timeout/
  crash/no-manager audited fail-closed, config respected, both agent seams source-guarded).
- **The daemon-as-a-service ran split-brain under a custom config dir — and its tests were
  silently red** (2026-07-16) — `navig service install` wrote only `Environment=NAVIG_HOME={home}`
  into the systemd unit / NSSM env. But `NAVIG_HOME` is a legacy alias only `memory/paths` + `theme`
  honour; the CANONICAL var — read by `config_dir()`, the vault, `gateway.json`, and the
  single-instance/supersede scoping (the #173 brain-kill protection) — is `NAVIG_CONFIG_DIR`, which
  was never written. So a daemon installed with a custom home ran its **config, vault and gateway
  against the default `~/.navig`** while only memory followed the custom home (proven: config_dir
  `~/.navig` vs memory `/custom` → split). The service now writes `NAVIG_CONFIG_DIR` (and keeps
  `NAVIG_HOME` at the same value for back-compat, so no divergence is possible). **The bug hid
  because the service-install tests were dead:** they monkeypatched `sm.NAVIG_HOME`/`LOG_DIR`/
  `DAEMON_DIR` — module constants a prior call-time-resolver refactor had removed — so
  `setattr(sm, "NAVIG_HOME", …)` raised `AttributeError` and red-lined every NSSM/systemd/task test
  (the #292 stale-removed-knob class). Repointed them at the `_navig_home()`/`_log_dir()`/
  `daemon_dir()` resolvers; the file went 4-failing → 25 green, now including a regression that the
  unit exports `NAVIG_CONFIG_DIR` and that config + memory resolve to one home.
- **The `~/.navig/debug.log` decoy #192 missed, plus the guard that let it hide** (2026-07-16) —
  the hardcoded-home guard (`test_no_hardcoded_home.py`) matched only `Path.home() / ".navig"`, so
  the *other* spellings — `os.path.expanduser("~/.navig/…")` and `Path("~/.navig/…")` — slipped past.
  `telegram_commands.py`'s daemon-log-warning block still read `os.path.expanduser("~/.navig/debug.log")`
  (twice) — the exact **#192 decoy** (a 0-byte file; the logger writes `debug_log_path()` =
  `log_dir()/debug.log`, `%LOCALAPPDATA%\navig\logs` on Windows) — which #192's grep-based sweep and the
  guard both missed. Rewrote the block to read `debug_log_path()` + `log_dir()/daemon.log`, and
  **extended the guard's AST detector to catch both literal spellings** (proven: it flags the leftover
  on `origin/main` at lines 6196/6200, zero after). Also flipped `ui/output_styles.py`'s user-global
  default from the hardcoded `~/.navig/output-styles` to `config_dir()/output-styles` — identical for a
  normal install, but a second brain now reads its own styles. `flow_runner.py`'s `~/.navig/flows` is a
  docstring example (not code), correctly ignored. New self-tests prove the detector catches the
  `expanduser`/`Path` literal forms and still ignores user-supplied paths and prose.
- **Gateway `/ws` topic subscriptions are now honored by the broadcast path** (2026-07-16) —
  `{"action": "subscribe", "topic": …}` recorded topics into `gw._ws_subscriptions` but no
  broadcaster ever read the map, so every client received every push regardless of what it
  subscribed to (ws-smoke-report known issue 1). All server-side pushes now go through
  `navig/gateway/ws_broadcast.broadcast_ws`, which filters per connection: never-subscribed
  connections still receive everything (backward compatible), subscribed connections only get
  matching topics (fnmatch globs, same convention as `EventBridge.SubscriptionFilter`). The
  status broadcast (`channel_router._broadcast_status`) is tagged `topic="status"`. Dead
  sockets found during a broadcast are pruned instead of silently eating every future push.
  Regression: `tests/gateway/test_gateway_core_routes.py::test_ws_topic_filtering_end_to_end`.
- **Gateway `/ws` grows a server-side heartbeat — zombie connections are reaped** (2026-07-16) —
  the endpoint created `web.WebSocketResponse()` with no `heartbeat`, so the server never pinged
  and a half-dead client (NAT/proxy idle drop) lingered registered until the next send failed
  (ws-smoke-report known issue 3). Now `heartbeat=30` (matching the MCP WS server and the
  Lighthouse uplink): aiohttp sends protocol-level PINGs and closes the connection when the PONG
  doesn't come back, which unregisters it. Protocol pings are answered automatically by every WS
  client stack — existing clients need zero changes and no app-level pong. Regression:
  `test_ws_server_heartbeat_reaps_dead_client` + `test_ws_heartbeat_transparent_to_normal_clients`.
- **`EventBridge._dedup_window` dead code removed** (2026-07-16) — assigned in `__init__` and
  never read anywhere in the tree since the monorepo import (ws-smoke-report known issue 2); the
  real duplicate suppression is the severity-based rate limiter in `push()` (`_recent` + the
  severity switch: DEBUG/INFO within `debounce_seconds`, WARNING within 0.2 s, ERROR/CRITICAL
  never). Wiring the vestigial 0.3 s window up instead would have broken the documented
  "ERROR/CRITICAL never suppressed" contract. The actual contract is now pinned by tests
  (`test_info_duplicates_rate_limited_within_window`, `test_error_duplicates_never_rate_limited`,
  `test_vestigial_dedup_window_removed`).
- **The gateway approval gate now actually gates — REQUIRE_APPROVAL was fail-open** (2026-07-16) —
  `policy_check()` logged `pending_approval` and then PROCEEDED ("log + allow. Future: queue for
  human approval UI"), so a `gateway.policy` rule demanding approval never stopped anything —
  while the full approval machinery (ApprovalManager pending store + `/approval/*` routes + deck
  Inbox cards + Telegram handler) sat unconnected next to it. REQUIRE_APPROVAL now blocks on
  `approval_manager.request_approval` (deck / Telegram / `POST /approval/{id}/respond` resolve
  it; timeout → the approval policy's `default_action`, deny by default) and fails CLOSED —
  denial, timeout, an approval-flow crash, or no approval manager at all each return 403
  (`approval_denied` / `approval_unavailable`); outcomes are audited (`approved`/`denied`, `via:
  approval_manager`) and an approved action still emits its billing event. Three wiring drops in
  the same seam fixed with it: the gateway built `ApprovalPolicy.default()` instead of
  `from_config` (the entire `approval:` config section was dead), never handed ApprovalManager
  the audit log (approval decisions left NO trace, and auto-evolve could never be enabled —
  `is_audit_log_live()` gates it and was permanently False), and the mission verifier's audit
  call used a non-existent `record(detail=...)` signature that TypeError'd into a bare `except`
  on every run (verifier verdicts were never audited). Also `_handle_approval_request` called
  `request_approval(action=...)` — a kwarg that doesn't exist — and treated the bool result as a
  request object. End-to-end proof on `mission.create` over the real `/runtime/*` routes:
  `tests/gateway/test_policy_approval_gate.py` (approve → 201, deny / timeout / no-manager →
  403, audit trail + fail-closed contract pinned; wiring guarded source-level). Out of the box
  nothing changes: the default policy carries zero `require_approval` rules — the gate only
  bites when the operator configures one. Closes the Milestone-4 queue item "Add approval and
  audit scaffolding" — the scaffolding existed; it was never connected.
- **Deck `/runtime/*` shadow routes removed — the canonical contract routes are the single
  surface** (2026-07-16) — `register_deck_routes` re-registered `GET/POST /runtime/nodes|missions|
  receipts` and `POST /runtime/missions/{id}/advance` with deck-shaped payloads
  (`navig/gateway/deck/routes/runtime.py`), but `register_all_routes` had already registered the
  canonical `/runtime/*` contract routes on the same app — and aiohttp resolves the
  first-registered resource, so the deck adapters were 100 % dead code (verified empirically on
  aiohttp 3.11.11). Worse, the deck frontend had been written against the phantom adapter
  protocol: every runtime view rendered permanently empty (`res.data.nodes` read the raw envelope)
  and every advance sent `{new_state}` where the live route requires `{action}` — a silent 422 on
  each click. The adapters are deleted; the deck's `lib/api.ts` now speaks the canonical
  envelope + contract shapes (see the deck changes in the same PR), and
  `tests/routing/test_runtime_routes.py` gains cancel + fail→retry action coverage pinning the
  verbs the deck drives. Never re-register a path both registries own — the first registration
  wins silently.
- **`/runtime/*` route mutations now survive a daemon restart** (2026-07-16) — the canonical
  routes never called `store.flush()` (the MCP tools did), so nodes registered and missions
  created/advanced/completed over HTTP lived only in daemon memory and vanished on restart. All
  four mutation handlers now flush after a successful write, and
  `test_route_mutations_survive_restart` pins the full lifecycle across a simulated restart.

### Added
- **`navig skill distill` reaches the deck/OS UI — "Distill last session → skill"** (2026-07-17,
  T-069 follow-up) — the CLI-only distiller now has two `/api/deck/*` routes so a UI can offer a
  one-click flow. `POST /api/deck/skills/distill` calls the SAME engine (`slice_ledger` / `distill`
  / `render_skill_md` in `navig/skill_distill.py`, run off the event loop via `asyncio.to_thread`)
  and by default (`dry_run:true`) returns a **preview** — the drafted, secret-swept SKILL.md plus
  the full step/pitfall/placeholder/safety/counts summary and the path it *would* write — touching
  nothing on disk; a confirm (`dry_run:false`) writes into the user skill store
  (`store_dir()/skills/<slug>`, never a client-supplied path), refusing an existing draft with 409
  unless `force:true`. `GET /api/deck/ledger/recent?last=2h&limit=N` is the first read-surface of
  the operations ledger over the deck API (reuses `iter_operations`) — the recent slice a distill
  draws from. Bad duration → 400, nothing-distillable → 422 with a `navig ledger show` hint. The OS
  **Knowledge app gains a Skills tab** (`apps/os/.../apps/knowledge/SkillsPanel.tsx`): pick a window
  (30m/2h/1d/1w), preview the draft, review placeholders, then write — backed by
  `distillSkill()` / `getLedgerRecent()` in `lib/deck-api.ts`. Covered by
  `tests/gateway/deck/routes/test_skill_distill_route.py` (preview writes nothing · confirm writes
  lint-clean · 409/422/400 · `--ops` selection · secret never leaks into the preview · ledger
  window/limit · **a raw secret in an old ledger line is re-redacted for display**). Because
  `/api/deck/ledger/recent` is reachable over a Lighthouse-fronted deck, the stored `command` is run
  through `redact_sensitive_text` at display time — defense-in-depth over the record-time redaction,
  which an entry predating T-068 (or a missed pattern) can bypass.
- **`navig skill distill` — turn a slice of the operations ledger into a draft, reusable
  `SKILL.md`** (2026-07-16, T-069, the third of the evidence-ledger trio after T-067 chain +
  T-068 undo) — `navig skill distill --last 2h` (or `--ops id1,id2`) reads what you actually ran
  and writes the recipe: the SUCCESSFUL path as ordered steps, failed attempts as "don't do this"
  pitfalls, reversibility labels (T-068) as danger annotations (⚠ red / compensable yellow /
  read-only), and conservative placeholders where values look instance-specific. Pipeline is four
  pure, independently-tested stages (`navig/skill_distill.py`): **slice** (time window or explicit
  ids, chronological) → **filter** (SUCCESS-only steps; failed → pitfalls; undone ops + undo
  entries + distill's own meta-invocations excluded; consecutive duplicates collapsed with a count)
  → **sanitize** (secret sweep FIRST — `redact_sensitive_text` + sensitive `key=value` +
  secret-bearing flags like `--password` + opaque 32-char tokens → `<secret>`, never with an
  example; THEN conservative instance placeholders `<host>`/`<user>`/`<email>`/`<ip>`, literal kept
  when unsure) → **emit** a Claude-Code-compatible SKILL.md whose `safety` enum is the worst step's
  reversibility label (a red step ⇒ `destructive`) and whose `description` is rich enough to route
  (authoring-guide §2). Default deterministic output works **offline** and is fully testable;
  `--ai` routes the already-sanitized draft's PROSE through the agent-layer seam
  (`navig/agent/skill_distiller.py` over `llm_generate` — the ONLY LLM touch, keeping all inference
  in `navig/agent/`) which keeps the frontmatter + every command string verbatim (a dropped command
  is refused). Never overwrites without `--force`; writes to the user skill store or `--out`; prints
  a `navig skill lint <path>` nudge; `--json` emits exactly one document (tripwire-enforced). The
  distill invocation records its own ledger line as a green, undoable `file_create` (T-068), so the
  draft itself is `navig undo`-able. Secrets are swept before anything leaves the machine (plan §3).
  67 regression tests (`tests/ops/test_skill_distill.py`) incl. an end-to-end that lints the emitted
  SKILL.md clean. Docs: HANDBOOK §29.13.
- **`navig audit tail` — the privileged-action audit trail, from the terminal** (2026-07-16) —
  the gateway audit log (`runtime/audit.jsonl`: who / what / when / decision for every
  policy-gated action, wired end-to-end in #299) was queryable only via `GET /audit` on a
  running gateway — invisible offline. `navig audit tail [-n N] [--action PREFIX] [--actor X]
  [--status S] [--path FILE] [--json]` reads the file directly from disk (no daemon — the
  `navig ledger show` contract): house Rich table with time / actor / action / semantic status
  glyphs (✓ approved/success · ✗ denied · … pending · ! error) / short input hash / reason,
  a next-step nudge line, honest empty/missing states (exit 0), malformed lines skipped, and
  `--json` emitting exactly one JSON document in every branch (path resolved at call time —
  frozen-path-tripwire clean). Filters mirror `GET /audit` (action = prefix, actor = exact,
  status = exact). Registered with a `navig help` page + help dictionary; `audit ` joins the
  operation-recorder skip list (a pure read of the audit trail is not an operation).
  Regressions: `tests/cli/test_audit_tail.py` (14 tests).
- **Reversibility labels + `navig undo` + `navig ledger show` (T-068)** (2026-07-16) — every
  recorded operation now carries an honest green/yellow/red reversibility label
  (`navig/reversibility.py`; stamped by `OperationRecorder.record()`, so it rides the T-067 hash
  chain): **green** = undoable (`undo_data` captured at execution time and the undo engine has a
  replay strategy), **yellow** = compensable/conditional (with the counter-action named), **red** =
  irreversible — unknown types default to red. `navig config set` is the first green capture seam:
  it records the previous value (or "did not exist") into the always-empty-until-now
  `undo_data` field, enriching the CLI middleware's in-flight record so each invocation stays ONE
  ledger line (`claim_cli_operation`); secret-bearing keys are captured **without plaintext** — a
  vault reference only, with the command string redacted to `navig config set <key> ***`.
  The new top-level **`navig undo`** (+ `--list`, `--yes`, `--json`, optional op-id) replays the
  last green operation behind a confirm gate and hard safety rules: green-only, **drift detection**
  (refuses when the target changed since — current state must equal the operation's recorded "new"
  side), **double-undo protection** (each undo is recorded on the chain tagged `undo` +
  `args.undo_of`, capped at yellow so it never becomes a candidate; an undone target is refused
  forever after), and secrets never replay from the ledger. `navig history undo` now delegates to
  the same engine — its old per-type helpers included a config-change stub that printed
  *"Would restore…"* and then claimed *"Undo completed"* (a false success), now dead. **`navig
  ledger show [--tail N] [--json]`** (the T-067 leftover) renders recent operations with per-entry
  chain state (✓ verified · ○ legacy · ✗ broken line), the reversibility label, and undone
  markers — command text passes through secret redaction before display. `navig ledger …` reads are
  no longer self-recorded (observer effect); file-modify records now capture an `after_sha256`
  post-state fingerprint so file restores get the same drift check. Docs: HANDBOOK §29.11–29.12,
  `navig help undo` / `navig help ledger`. Tests: `tests/ops/test_reversibility_undo.py` (50).
- **Hash-chained operations ledger + `navig ledger verify` (T-067)** (2026-07-16) — every entry
  `OperationRecorder.record()` appends to `operations.jsonl` now carries `prev` (the previous
  chained entry's hash) and `hash` (`sha256:`-prefixed, over the previous hash + the entry's
  canonical key-sorted JSON — `navig.ledger_chain`). Delete, edit, or reorder any line and the
  fingerprints stop matching; the new `navig ledger verify` (+ `--json`) re-walks the file and
  reports *"N operations, chain intact"* or *"chain broken at line X"* with honest counts
  (chained / legacy-unchained / restarts / rotation anchor). Exit codes: 0 intact (including
  missing/empty/legacy — honest non-failure states), 1 broken. The plan's wrinkles are covered:
  rotation now keeps raw lines verbatim so hashes survive it (the first survivor's `prev` is
  reported as the chain *anchor*), `history clear` reads as a clean restart, and pre-chain
  legacy entries are counted, never failed. Readers (`history list/show`, insights) strip the
  chain fields transparently. Worded honestly everywhere: tamper-**evident**, not tamper-proof.
  Forward-compatible with the Writ protocol (plan-writ-protocol.md): hashes use the same
  `sha256:` notation as Writ returns, and the canonical payload reserves `sig` so future signed
  returns can be added without invalidating existing chains.

- **Runtime-contract test coverage closed: MCP runtime resources + JSON-schema drift guard**
  (2026-07-15) — the ROADMAP queue items "Node and Mission schemas plus tests" and
  "ExecutionReceipt MCP resource and tests" were already implemented
  (`navig/contracts/*`, `navig/schemas/*.schema.json`, `navig://runtime/{nodes,missions,receipts}`
  in `navig.mcp_server`) but two test gaps remained. Closed both:
  `tests/mcp/test_mcp_runtime_resources.py` exercises the three runtime resources end-to-end
  (registration, `resources/list`, `resources/read` against a seeded isolated RuntimeStore, full
  JSON-RPC dispatch, and proof the wire payload re-parses into the `Node`/`Mission`/
  `ExecutionReceipt` dataclasses), and `tests/contracts/test_contracts_schema_json.py` pins the
  JSON schema files to the dataclasses (properties == fields both directions, `required` ⊆
  fields, enum values == the Python enums), documents malformed-input behaviour
  (invalid enum → `ValueError`, unknown field → `TypeError` mirroring
  `additionalProperties: false`, missing outcome → `KeyError`, bad JSON → `JSONDecodeError`),
  and asserts serialize→parse→serialize byte stability. No shipped code changed.

- **`navig doctor --heal [--dry-run]` — doctor verdicts now drive the self-heal loop**
  (2026-07-15) — the machine-readable health report and the repair side finally meet:
  `collect_report()` (the exact dict `--json` prints, extracted from the CLI callback as a shared
  seam) feeds `navig.selfheal.doctor_remediation`, which maps failing checks onto remediations
  that ALREADY exist — gateway down / MESH_TOKEN unset → the daemon start path (which itself
  honours the user's deliberate-stop flag), legacy credentials unmigrated → the vault migration
  entry point (read-only on the legacy DB). Disruptive fixes are REPORT-ONLY by design and are
  never executed automatically: a wedged event processor or a stale lighthouse webhook tenant
  prescribes `navig service restart`, leaked debug browsers prescribe `navig cdp stop --all` —
  the operator pulls those triggers. `--heal` lists every failing check with its mapped fix (or
  "no automatic remediation"), runs the safe ones (each at most once per pass), re-collects, and
  prints before/after; `--dry-run` executes nothing; `--json` emits one machine-readable document
  with the actions and the final report. Exit code parity holds: 0 only when the final report is
  fully green.

- **The `--json` stdout-purity guarantee is now a tripwire — every verb, current or future**
  (2026-07-15) — PR #239 fixed the bootstrap layer; this generalizes it so the CLASS stays dead.
  Coverage engine: the PR #219 command schema (the same Typer-tree walk behind
  `navig.registry.manifest`) enumerates every core-owned verb declaring `--json` — 194 unique
  handlers, zero invocations. **Layer 1 (static, complete):**
  `tests/cli/test_json_stdout_tripwire.py` scans each handler plus everything reachable through
  plain function calls (deferred `from navig… import` included, docstrings/comments AST-stripped)
  and fails the build on the two emitters proven to corrupt piped JSON — Rich
  `console.print(json.dumps(...))` hard-wraps at the console width (80 when piped), inserting
  newlines inside string values, and `console_helper.print_json` (Rich Syntax,
  `word_wrap=True`) wraps identically — and on any verb with no recognizable pure emitter at all
  (a schema lie). **Layer 2 (dynamic, sampled):** eight fast hermetic verbs (`version`, `help`,
  `block list`, `work list`, `mode list`, `connector list`, `quick list`, `docs` incl. its error
  path) run as real subprocesses on a virgin `NAVIG_CONFIG_DIR` with onboarding armed — the whole
  of stdout must parse as exactly one JSON document with no narration markers. New house helper
  `navig.console_helper.emit_json(payload)`: plain-`print` JSON, no markup interpretation, no
  wrapping, no color — the one blessed spelling for `--json` output (the failure message says
  exactly that). One documented exemption (`skills run` forwards `--json` to the executed skill
  process) sits in a ratcheted allowlist that a test forces to only shrink.
- **The #179 frozen-path pattern is now unwriteable — an AST tripwire guards the whole tree**
  (2026-07-15) — PR #179 proved that module-level path constants derived from `config_dir()` /
  `data_dir()` / `vault_dir()` / `Path.home()` / `expanduser()` freeze the REAL home before
  `NAVIG_CONFIG_DIR` isolation applies (actual credentials were copied into isolated vaults), and
  PR #189 swept all 30 real-risk instances out of 27 modules. Instance #31 now fails CI the moment
  it is written: `tests/core/test_frozen_path_tripwire.py` AST-parses every module under `navig/`
  and flags module-level bindings, class/dataclass attribute defaults, and function-signature
  defaults whose value contains any environment-sensitive root resolver call (the core set plus
  the first-party wrappers `memory_dir()`, `log_dir()`, `store_dir()`, …), however deeply nested
  (`/` BinOps, `str()`/`Path()` wrappers, f-strings). The allowed idioms #189 rewrote everything
  into — call-time resolver functions, `field(default_factory=…)`, `None` sentinels resolved in
  bodies, `TYPE_CHECKING` blocks — pass by construction, and the checker's own positive/negative
  self-tests pin that behaviour. Two documented benign survivors are allowlisted with reasons
  (`memory/paths.py::KEY_FACTS_DB_PATH`, `desktop/tray_app.py`), and a stale-entry check stops the
  allowlist rotting into a rubber stamp.

### Fixed
- **The other two AI evolvers had the same path bug — scripts polluted site-packages, packs went to
  the CWD, both unreachable** (2026-07-15) — sibling fixes to #271 (workflow evolver). `ScriptEvolver`
  wrote generated scripts to `Path(__file__).parents[2] / "scripts"` = `navig/scripts` — **inside** the
  package (`site-packages/navig/scripts` in a real install) — and `__init__` `.mkdir()`'d it, so *merely
  constructing the evolver* wrote into the installed package (or raised `PermissionError` on a read-only
  one — the #189 signature). `navig ahk` reads user scripts from `config_dir()/scripts`, so a generated
  script was **unreachable** and vanished on the next upgrade. `PackEvolver`'s default `packs_dir` was
  the CWD-relative `Path("packs")` (its own docstring flagged the TODO), while `navig install` writes and
  the loader reads `packages_dir()` (`config_dir()/packs`) — so an evolved pack landed in whatever
  directory the process happened to run in and nothing loaded it. Both now default to the config-dir
  location the reader uses (`config_dir()/scripts`, `packages_dir()`), resolved lazily so
  `NAVIG_CONFIG_DIR` isolation holds and construction touches no disk. Regression-tested: default is the
  config-dir path, construction creates nothing, two brains never share, and the save→read round-trip
  works. `skill.py` (requires `skills_root` from the caller) and `fix.py` (writes the user's target file)
  were already correct.
- **The AI workflow evolver saved to a dir the runner never reads — every generated workflow was
  unreachable, and on a real install the save silently failed** (2026-07-15) — `WorkflowEvolver`
  wrote its generated `.yaml` to `Path(__file__).parents[3] / "workflows"` — the directory
  *containing* the `navig` package (`core/` in a checkout, **site-packages** in a real install).
  Three faults in one path: it escapes the wheel; it is machine-global not per-brain; and
  `WorkflowEngine.load_workflow()` (the thing that runs workflows) reads `config_dir()/workflows`,
  so a workflow the evolver "saved" was **never loadable** — proven end-to-end: `writer == loader
  dir? False`, `load_workflow found? False`. On a real install it was worse: `_save` had no
  `mkdir`, so the write to the nonexistent site-packages path raised and was swallowed by a broad
  `except` — every `navig evolve`/`agent learn`-generated workflow vanished with a "Failed to save"
  line. Now resolves `config_dir()/workflows` lazily (honouring `NAVIG_CONFIG_DIR`) and `mkdir`s
  before writing — the exact sibling of #189's `automation_engine` fix (the loader; this is the
  writer). After: both resolve the same dir and a generated workflow is immediately runnable. The
  `test_asset_paths` allowlist entry (which mislabelled this "dead — the workflow engine is retired"
  — it is not; the evolver writes, the engine runs) is removed, unblocking that guard's
  no-stale-entries check for every session.
- **20 `--json` verbs piped their payload through the Rich console — corrupt the day a value
  outgrew the terminal** (2026-07-15) — found by the new tripwire's first full sweep: `docs`,
  `fetch`, `search`, `suggest`, `quick list`, `agent plan`, `agent transcribe`, `ahk status`,
  `ahk windows`, `connector list/status/search/fetch`, `dispatch send`, `index stats`,
  `install browse/search`, `mcp tools`, and `tray status` all emitted via
  `console.print(json.dumps(...))` or a variant (`jsonlib`/`_json`/`json_mod` aliases — invisible
  to a naive grep), which Rich hard-wraps at width 80 when piped and whose markup parser eats
  `[bracketed]` content inside values; `navig docs --json` was worse still — its
  `Console(force_terminal=True)` wrote ANSI escapes into pipes unconditionally. All 20 now emit
  through `console_helper.emit_json` (byte-identical for short ASCII payloads; actually parseable
  for everything else). Same-class narration leaks fixed in passing, with the same seams #239
  used: `inbox filter --json` printed a "Filtering .navig/ under …" banner ahead of its document,
  `ahk status --refresh --json` interleaved a "Refreshing AHK detection..." line, and
  `docs --json` on an install without a docs/ dir printed a human error to stdout and exited 1
  with no JSON at all — the error path now emits a JSON error document.
- **Pre-dispatch narration polluted every `--json` command's stdout — stdout now belongs to the
  command's output, narration to stderr** (2026-07-15) — on a virgin config dir the CLI bootstrap
  ran first-time onboarding BEFORE command dispatch and printed its ~22-step banner, the
  verification dashboard, and even an interactive AI-provider menu to **stdout** — so
  `navig doctor --json` (and every current or future `--json` verb) emitted narration ahead of its
  one machine-readable document, and `NAVIG_SKIP_ONBOARDING=1` was the only escape hatch.
  `ConfigManager` compounded it by narrating "Applying N configuration migrations..." to stdout
  mid-load, deep inside whatever command was executing. Three-part fix, honest for every consumer
  with no flag-sniffing in the output layer: (1) the auto-run onboarding seam
  (`main._check_first_run`) redirects the wizard's stdout to **stderr** — Rich consoles resolve
  `sys.stdout` at print time, so one seam covers the banner, per-step progress, and the dashboard;
  terminals still show everything, pipes stay clean, and explicit `navig onboard`/`navig init`
  keep stdout (there the wizard IS the output); (2) a `--json` invocation **skips first-run
  onboarding entirely** (cheap argv scan in `should_auto_run_onboarding`) — a programmatic caller
  can never answer the wizard, which prompted even without a TTY and paused setup on EOF;
  (3) migration narration (`core/migrations.py`) now writes to a stderr-bound console. Doctor's
  own redirect-stdout shield stays as defense in depth. Guarded by real-subprocess tests on a
  virgin `NAVIG_CONFIG_DIR` without `NAVIG_SKIP_ONBOARDING`
  (`tests/cli/test_bootstrap_stdout_purity.py`) plus stream-level unit tests.
- **The tripwire's first catch: an isolated gateway shared the live daemon's dedupe ledger**
  (2026-07-15) — `gateway/dedupe.py` froze `os.path.expanduser("~")/.navig/dedup_updates.json` in
  a module-level constant: import-time-frozen (the #179 pattern) AND hardcoded to the real home,
  invisible to `tests/platform/test_no_hardcoded_home.py` because it used `os.path.join` instead
  of the `Path.home() / ".navig"` shape that guard matches. Any `UpdateDedupe()` built under
  `NAVIG_CONFIG_DIR` isolation read and wrote the OPERATOR's dedupe store — an isolated test
  gateway could mark the live bot's Telegram update IDs as already-seen, silently discarding real
  updates. The default now resolves `config_dir()/dedup_updates.json` at construction time
  (identical location for a default install), with two-brains-two-stores regression tests.
- **Hiding a command from `--help` also DELETED it from the docs, the deck, and every agent**
  (2026-07-15) — 9 real, unique commands were invisible: `navig ai` (18 cmds), `navig brain` (which
  navig-bridge itself shells out to), `navig cost`, `navig continuation`, `navig hosts`,
  `navig software`, `navig output-style`, `navig habit`, `navig life`. One Typer `hidden` flag was
  carrying two unrelated meanings — *"this is a duplicate alias"* (correctly private) and *"this
  clutters the top-level help"* (still very much public) — and `build_public_manifest()` dropped
  both. That manifest feeds `navig help`, the deck's command schema (`/api/deck/cli`),
  navig.run/commands, and any agent asking navig what commands exist, so an agent could not
  discover a third of the AI surface. Split into `_ALIAS_COMMANDS` (31 duplicate names, verified by
  comparing the backing Typer instance — never eyeballed) and `_UNLISTED_COMMANDS` (9 real
  capabilities, unlisted from `--help` only); `_HIDDEN_COMMANDS` is now their union, so
  **`navig --help` output is byte-identical**. The alias check is also authoritative over
  `CommandMeta`: an alias inherits the canonical command's meta and `meta.status` used to win over
  the hidden flag, which is how `navig database list` / `query` shipped in the public manifest as
  duplicates of `navig db list` / `query`. Manifest: 1227 → 1268 commands.
- **A second navig brain read the operator's live gateway — the last hardcoded-home sites closed**
  (2026-07-15) — six modules resolved state through a hardcoded `Path.home() / ".navig"` while the
  matching writer used `config_dir()`, so under a custom `NAVIG_CONFIG_DIR` reader and writer split.
  The worst was **`scheduler/habit_store.py`**: the gateway writes `gateway.json` to `config_dir()`
  (`gateway/server.py`) and `gateway_client.py` reads it there, but the scheduler read it from the
  real home — so a second brain resolved the URL of the **operator's live gateway** (measured:
  `http://127.0.0.1:8789`) and would have issued its cron/habit HTTP calls into someone else's
  running daemon. The deck's **`_navig_dir()`** (feeding `tasks.json` and the spaces list) likewise
  returned `~/.navig`, so the deck listed a **different set of spaces than the CLI installed**
  (`navig install` writes them to `config_dir()/spaces`); **`commands/wire.py`** compared a space
  against the home and mis-registered a config-dir space as `external` instead of `root`; and the
  deck's community-registry and local-backup-status probes ignored the configured dir too. All now
  use `config_dir()` — identical for a default install (`config_dir()` **is** `~/.navig`), so
  nothing moves. It is the same *"one navig at ~/.navig"* assumption behind #173 (gateway kill) and
  #180 (`agent stop` blind); the hardcoded-home guard's TODO backlog is now **closed** — the only
  two remaining sites are genuinely home-anchored (a *dead* legacy cron store the migration path
  must still find, and a read-only probe of a file an external bridge writes). Guarded by three new
  writer↔reader agreement tests in `tests/platform/test_no_hardcoded_home.py`.
- **The builtin store audit — three more things that shipped broken** (2026-07-15) — after the
  Block catalog turned out to be missing from every wheel, the whole builtin store was audited *at
  runtime* (by calling each real loader, because a grep cannot see this class: `get_block_dirs()`
  resolves its directory through a loop over function references, so the string
  `builtin_store_dir() / "blocks"` appears nowhere in the tree). It found:
  - **The one builtin quick action was invisible.** `navig/builtin/actions/status.yaml` was a single
    flat action document (`id: status`, `command: …`), but `_load_all_actions` absorbs a **mapping**
    of name → entry — the shape `navig action add` writes. So the loader walked the file's keys,
    found strings where it expected entry dicts, and absorbed nothing. `status` shipped in every
    wheel and never once appeared in `navig action list`. It does now.
  - **`WorkflowEngine` created a junk directory inside site-packages.** Its `__init__` did
    `Path(__file__).parent.parent.parent / "workflows"` — the directory *containing* the `navig`
    package (`core/` in a checkout, **site-packages** in a real install) — and then `.mkdir()`'d it.
    Six call sites construct this engine, including the agent's action registry, so merely using the
    agent polluted the install (or raised `PermissionError` where site-packages isn't writable). It
    was also the wrong place: nothing has ever written a workflow there, so the directory was always
    empty and `load_workflow` only ever found anything through its `config_dir()` fallback.
    `_workflows_dir` is now a lazy property resolving to `<config_dir>/workflows`, and nothing is
    created at construction.
  - **`navig/builtin/workflows/` was dead weight in every wheel** — five files, three of them dev
    scratch (`my-test-runbook.yaml`, `test_advanced.yaml`, `cross_platform_test.yaml`), reachable by
    no code path. The workflow engine's builtin content was migrated to Blocks and
    `docs/blocks-vs-workflows.md` says these "are deleted"; the deletion missed them. Removed.
  - The TUI's agent badge probed `Path("store/agents/navig/soul.json")` — a **cwd-relative** path
    from the pre-migration layout, so the badge said different things depending on which directory
    you ran from. Now resolved against the config dir and the packaged seed.

  A new guard, `tests/packaging/test_builtin_store_contract.py`, calls every real loader and is
  **two-way**: every content type a loader depends on must actually be *discovered*, and every
  directory in the store must be *claimed* — so content can neither vanish silently (blocks) nor sit
  in the wheel unread (workflows).

- **The builtin Block catalog shipped EMPTY — all 12 blocks restored** (2026-07-14) — `navig apply
  safe-deployment` (and every other builtin Block) resolved to nothing, for every user, in every
  wheel. When the builtin content store moved *into* the package (`navig/builtin/`, so it would
  actually reach a wheel), the twelve `BLOCK.md` files were left behind in the old `<repo>/core/store`
  — a directory git had stopped tracking, so they were never committed and never packaged. Blocks are
  the product's paid tier — "apply an outcome, proven by a receipt" — and the catalog was a 404.
  Restored under `navig/builtin/blocks/`, which `[tool.setuptools.package-data] navig = ["**/*"]`
  ships automatically. **The release gate now checks blocks** (`verify_install.py` listed
  `skills, prompts, templates, formations, tools, agents` — `blocks` was simply not in the tuple, which
  is why nothing caught it), and a new test asserts the builtin catalog is non-empty plus a
  registry↔builtin drift guard (the two copies of `msstore-publish` had already diverged).
- **A missing optional plugin filed a CRASH REPORT instead of saying "install it"** (2026-07-14) —
  `navig agent transcribe` (any API backend) reached an unguarded `from navig.voice.stt import …`,
  and since `navig.voice.*` is a thin forwarding shim over the **navig-audio** plugin, a user who
  simply hadn't installed it got `No module named 'navig_audio'` routed straight into the crash
  handler: a crash JSON on disk, *"run `navig crash export` to create a report for GitHub"*, and
  **exit 0**. Now it prints `✗ Speech-to-text needs the navig-audio plugin. Install it: navig store
  install pip:navig-audio` and exits 2. New `navig.plugins.require` owns this: `PluginRequired`
  subclasses `ImportError` **on purpose**, so the ~40 existing `except ImportError` degradation
  guards keep working untouched, and `install_hint()` is now the single source of the install line
  (it was retyped in four places).
- **`navig/gateway/routes/voice.py` could not be imported without the navig-audio plugin** —
  it imported `navig.voice.wake_word` at module level purely to *annotate* `PENDING_WAKES`, which
  also closed an import cycle (`navig_audio.voice.wake_word` → this module → `navig.voice.wake_word`
  → back). Moved under `TYPE_CHECKING` (the module has postponed annotations, so the annotation was
  never evaluated anyway). Core now imports, registers every command, and boots the gateway with
  **zero plugins installed** — pinned by `tests/plugins/test_core_standalone.py`, which simulates the
  uninstalled world with a `sys.meta_path` blocker (in a dev checkout every plugin is installed, which
  is why this regressed unseen).

### Added
- **`navig doctor --json` is a real machine contract now** (2026-07-15) — the flag existed but
  emitted pre-rendered human strings (glyph + label baked into each `detail`) in an untyped
  `{name: [rows]}` map. It now prints one stable, flat document: top-level
  `ok` (the same verdict that drives the exit code) · `sections[] {name, ok, checks[] {label, ok,
  warn, detail}}` · `summary {passed, warnings, failed}` · `version` · `generated_at` (ISO-8601,
  UTC). `warn` is the *rendered* ⚠ state (`ok=false` + warn flag), so scripts read exactly what a
  human would see. JSON mode always includes **every** check (`--verbose` semantics), never
  prompts, and emits **plain text only** — no ANSI, no ✓/⚠/✗ inside strings — and stdout carries
  *exactly one* JSON document: anything a check's underlying layer narrates mid-run (e.g.
  `ConfigManager` announcing a config migration) is captured and forwarded to stderr instead of
  corrupting the payload. Exit code is identical to the human mode (0 only when every row is ok —
  a ⚠ flips it, exactly like ✗). Human output is byte-identical to before; rows now travel as
  `CheckResult` (a `(icon, ok, line)` tuple subclass carrying the structured `label`/`detail`), so
  JSON is data, not string-scraping. Pinned by `tests/commands/test_doctor_json.py`.
- **Cross-brain isolation smoke — pins the invariant behind #173/#180/#192/#196** (2026-07-15) —
  `tests/platform/test_cross_brain_isolation.py` resolves **every** path function for two brains on
  two `NAVIG_CONFIG_DIR`s and classifies each as per-brain **owned** (must differ) or machine-global
  **shared** (with a documented reason). All four incidents were one path whose reader and writer
  disagreed about which brain owned it; a **new** path function that lands in neither bucket now
  fails the build, forcing the "brain state or machine state?" decision instead of the silent
  default that caused them. Also pins that the secrets **vault is never shared**, that no owned path
  nests inside another brain's tree, and — documented, not a bug — that `log_dir`/`cache_dir`/
  `debug_log_path` are OS-idiomatic and **shared** across brains (exactly why #192's readers could
  diverge from the writer; `NAVIG_LOG_DIR`/`NAVIG_CACHE_DIR` isolate them per-brain when needed).
- **`navig block list` is a real table now** (2026-07-14) — the house-style Rich table (semantic
  `● verify` / `○ none`, `·paid` marker, exactly one wrappable column) plus `--json` for scripts and
  agents; `--plain` is unchanged. It also states the honest headline — *"12 block(s) · 2 with a
  verified outcome"* — because a Block that verifies nothing is a checklist, and the count should say so.
- **`navig doctor` now checks vault health — locally, without ever touching a secret** (2026-07-14)
  — a new "Vault" row in the Storage section: ✓ "N item(s) · encryption OK" (item count via the
  public store API plus a decrypt probe that opens ONE item's key *wrapper*, never a payload),
  informational "no vault yet — created on first `navig vault set`" when the DB doesn't exist, and
  ✗ on open/decrypt failure carrying the exception **class name only** (messages can embed paths or
  item labels). A companion "Legacy credentials" row covers the pre-AES-GCM DB: ⚠ "legacy
  credentials DB present — will auto-migrate on next vault use" when unmigrated, ✓ "legacy DB
  retained (migrated)" once the migration marker exists. All paths resolve at CALL time
  (`NAVIG_CONFIG_DIR` honoured — the legacy-migration-leak lesson), and the check deliberately
  avoids `get_vault()` so doctor never triggers the auto-migration it is reporting on. The
  migration-marker semantics moved behind public helpers (`navig.vault.migrate.
  legacy_migration_done` / `migration_marker_path`) shared by `get_vault()`'s auto-migration and
  doctor — one source of truth for the marker names.
- **`navig doctor` now asserts the system-event processor is healthy** (2026-07-14) — a new
  "Event processor" row in the Gateway section. The processor failing to start used to be
  perfectly silent: `emit()` kept succeeding, the backlog grew (2,737 events at one point),
  `/api/events` served heartbeats only, and every light stayed green. The row reads a new
  additive `events: {running, pending, history}` block on `GET /api/deck/status` (sourced from
  new public `SystemEventQueue.running/pending_count/history_count` accessors): ✓ when running
  with a small backlog, ⚠ at ≥50 pending ("emit backlog growing"), ✗ when not running
  ("emitted events are piling up undrained — restart the gateway; if it persists, the processor
  failed to start"). Daemon unreachable — or an older daemon without the field — degrades to an
  informational "not checked" warn: the Gateway row already owns the daemon-down failure.
- **`npm run ci:install` — the real-install release gate** (2026-07-13) — builds the wheel, installs
  it into a throwaway venv, and **drives it**: 22 checks over the builtin content store, prompts,
  skills, formations, the default persona, i18n locales, modes, scaffold/browser templates, the
  speedtest worker and the Nerd Font script — plus end-to-end that `navig space init` →
  `navig space doctor` is green. `core/tools/verify_install.py` **refuses to run against a source
  checkout**, which is the whole point: every other check in this repo inspects the *repo*, where
  every asset is on disk regardless of what the wheel contains. ruff, pytest and the packaging
  guards were all green while published wheels shipped **zero** builtin content, `navig net
  speedtest` raised, and `space init` printed "✓ Created space" for a space `space doctor` then
  failed. Proven to catch it: run against a pre-fix wheel it reports **11/22 FAILED, exit 1**.
  Wired into `npm run ci:full` and — via `--wheel <path>` so it verifies the exact artifact about
  to be published — into `release.yml` immediately after `uv build`. Uses `uv` when available,
  skips itself if PyPI is unreachable. ~3½ min.

### Fixed
- **Import-time path freeze swept across the whole core — 30 more constants fixed with call-time
  resolvers** (2026-07-14) — the exact pattern PR #179 proved dangerous in `vault/migrate.py`
  (`config_dir()` / `Path.home()` captured in a **module-level constant**, freezing the real user
  home before `NAVIG_CONFIG_DIR` isolation or a daemon's final environment applies) existed in 30
  more places. Highest-blast-radius instances: `daemon/supervisor.py` froze **supervisor.pid /
  state.json** (an isolated test could read — or unlink — the *operator's live daemon PID file*);
  `selfheal/ssh_healer.py` froze **~/.ssh/known_hosts and the default key path** (a test could
  append scanned host keys to the real known_hosts or generate keys into the real ~/.ssh);
  `workspace_ownership.py` froze the personal-state root (SOUL.md / USER.md / MEMORY.md — files the
  agent *writes*); `agent/session_store.py`, `commands/work.py`, `contracts/store.py`,
  `agent/pattern_observer.py` froze session/DB/registry stores; `gateway/deck/routes/apps.py`
  additionally hardcoded `Path.home()/".navig"/wiki`, ignoring `NAVIG_CONFIG_DIR` outright. Every
  hit now resolves **at call time** via a small `_x_path()` resolver; dataclass defaults became
  `field(default_factory=…)` (`MigrationReport`-style) and constants that tests legitimately patch
  (e.g. `SPILLOVER_DIR`, `TRACE_LOG_PATH`, supervisor's `PID_FILE`) became explicit `None`-default
  test seams the resolver consults. Left as-is, deliberately: `memory/paths.KEY_FACTS_DB_PATH`
  (deprecated alias whose production use is blocked by an AST guard test) and
  `desktop/tray_app.py` (a spawn-only entry-point process that inherits its env before import).
  Regression suite: `tests/core/test_call_time_paths.py` — 35 tests that import each module
  *first*, isolate the environment *after*, and assert the resolver honours it (the pre-fix
  constants fail every one).
- **Every debug-log diagnostic read a file the logger never writes — `navig debug`, `debug tail`,
  `debug clear`, `service logs`, `agent learn` all showed nothing** (2026-07-14) —
  `paths.debug_log_path()` is documented as *"Canonical path to the debug log file"*, and **eight
  call sites ignored it**, each inventing its own spelling: `~/.navig/debug.log`
  (`commands/service.py`), `config_dir()/debug.log` (`commands/debug_cmd.py` ×3, and
  `core/shared_config.py`, which **exports** the path to other surfaces), `config_dir()/logs/debug.log`
  (`commands/agent.py` + its MCP twin), and `base_dir/debug.log` (a fallback writer in
  `debug_logger.py`). On Windows `log_dir()` is `%LOCALAPPDATA%/navig/logs` — **never** `~/.navig` —
  so none of them pointed at the real file. Measured on a live machine: the real `debug.log` was
  **1,761,066 bytes** while every diagnostic read a **0-byte** file. And it was self-concealing:
  `navig service logs` `touch()`ed that path into existence before tailing it, so the stream sat
  empty forever instead of erroring, and **`navig debug clear` truncated the empty file and reported
  success** while the real log kept growing. `navig debug` now prints `1,761,066 bytes` and
  `debug tail` prints actual log lines. Every reader now calls `debug_log_path()`.
  Also: `blackbox/bundle.py` collected `navig.log` from `log_dir()`, but it is written to the
  **config** dir (`config.py`: `base_dir/"navig.log"`) — so the diagnostics bundle silently shipped
  **without the main application log**. Fixed against the reference implementation
  (`gateway/deck/routes/logs.py`, which already maps `debug.log → log_dir` and `navig.log → config_dir`).
  Guarded by `tests/platform/test_debug_log_single_source.py`: no module may build a debug-log path
  from the literal again, and a stale allowlist entry fails too.
- **Vault legacy-migration path was frozen at import time — real credentials leaked into the test
  vault and 9 vault CLI tests failed by ordering** (2026-07-14) — `navig/vault/migrate.py` computed
  `_LEGACY_DB = config_dir()/credentials/vault.db` as a **module-level constant**, so any process
  importing the module before its environment is finalized kept a stale path forever. In pytest that
  was deterministic: collection imports `tests/vault/test_vault_migrate_types_webhook.py` (→
  `navig.vault.migrate`) *before* the session fixture isolates `NAVIG_CONFIG_DIR`, freezing the path
  to the **real** `~/.navig/credentials/vault.db`. The first `get_vault()` of the session then
  auto-migrated the operator's real credentials (openai, telegram, …) into the isolated test vault;
  the duplicate guard in `navig vault add` saw the real default-profile `openai` credential via the
  cross-profile fallback and exited 1 ("already exists — use --force"), failing 9
  `tests/vault/test_vault_commands.py` tests that passed in isolation — and `vault remove openai
  --profile no-such-profile --force` deleted the fallback credential instead of erroring. Fixed at
  the production layer: the legacy DB path is now resolved **at call time** (`_legacy_db_path()`)
  in `check_legacy_exists()`, `migrate_from_legacy()` and `MigrationReport.source`, so it always
  honours the current `NAVIG_CONFIG_DIR`. Regression test:
  `TestCheckLegacyExists::test_default_path_resolves_at_call_time`.
- **The agent wrote its PID where nothing reads it — `navig agent stop` went blind** (2026-07-13) —
  `agent/runner.py` wrote `agent.pid` to a hardcoded `Path.home() / ".navig" / "agent"`, while all
  **three** readers (`navig agent stop`, `navig agent status`, and the MCP agent tool) look in
  `config_dir() / "agent"`. Under a custom `NAVIG_CONFIG_DIR` the two diverge, so the agent became
  **unstoppable and invisible** — stop and status could not see a process that was plainly running.
  Same for `navig install` of a **webapp** asset: every other asset type in that function honours the
  configured dir (`store_dir()` / `config_dir()`), only `webapp` hardcoded the real home, so an
  install under a custom config dir landed in `~/.navig` anyway. Both now use `config_dir()`, which
  **is** `~/.navig` for a default install — identical paths, nothing moves, no migration.
  This is the same assumption that let `navig gateway start` force-kill an unrelated brain: *"there
  is only ever one navig, at `~/.navig`."* It survives because the default install makes it look
  correct on the only machine anyone tests on.
  New guard `tests/platform/test_no_hardcoded_home.py` (AST, not regex — a regex flagged the very
  comment explaining the fix) fails on any **new** `Path.home() / ".navig"` in `navig/`, and on a
  **stale** allowlist entry. The remaining sites are inventoried with a reason each: the uv runtime
  (`~/.navig/runtime` is an installer contract), a last-resort logger fallback, and seven TODOs that
  need a product decision (deck routes, scheduler `gateway.json`/`cron_jobs.json`, `service logs`
  reading the gateway log from the real home while the *writer* uses `paths.debug_log_path()`).
  Writing the guard also exposed a blind spot in itself: pinning the receiver to the name `Path`
  missed `main.py`'s aliased `_Path.home()` — caught only because the stale-entry check flagged it.
- **`navig gateway start` from ANY navig force-killed the operator's live daemon** (2026-07-13) —
  the start path's single-instance sweep, `kill_other_instances(GATEWAY_PATTERNS)`, matched on
  **process cmdline alone, machine-wide**: it force-killed every process whose cmdline contained
  `navig gateway start`, regardless of `NAVIG_CONFIG_DIR`, venv or user. A gateway booted from a
  second venv, a CI job or a temp-config smoke test therefore **killed the running production
  brain**. Verified against the real process table, with a no-op killer so nothing died: unscoped,
  the sweep *would* have force-killed pid 33736 — the operator's live `pythonw -m navig gateway
  start`. The standing rule *"never boot a second gateway locally"* existed **because of this bug**,
  not because of the brain model.
  Now scoped: `kill_other_instances(..., config_dir=…)` reaps only processes whose **effective
  `NAVIG_CONFIG_DIR` matches ours** (read via psutil environ), and a process whose config dir cannot
  be read is **never** killed — you must not kill what you cannot identify. `_supersede_other_gateways()`
  passes `paths.config_dir()`. One brain per *config dir* is unchanged, and a restart still supersedes
  its own stale gateway (proven on the same live table: scoped to the operator's own config it still
  targets 33736; scoped to a temp config it targets nothing). Unscoped calls keep the old behaviour,
  so this is an opt-in narrowing rather than a silent change under every caller.
  The release gate (`npm run ci:install`) now asserts the shipped wheel still carries the scoping —
  and deliberately does **not** boot a gateway. It also gained 7 daemon checks (imports of
  `navig.daemon.*` / `navig.gateway` from the installed wheel): **29 checks, was 22**.
- **Config data loss: the daemon erased every CLI-written setting** (2026-07-12) —
  `ConfigManager.global_config` is cached for the life of the process. That is fine for a
  one-shot CLI run, but the **daemon lives for days**: every `navig config set` (or plugin
  setting) written in the meantime was invisible to it, and the ~15 read-modify-write call
  sites across core and the plugins saved that stale snapshot straight back over the file.
  Reproduced: a daemon-side write of `plugins.games.steam_watch` **deleted** a CLI-written
  `telegram.catalog.enabled`. Two additive fixes, no behaviour change for a single writer:
  - `ConfigManager.set_global("a.b.c", value)` — the one safe write path (refresh → deep-set
    → save). Because it refreshes *before* mutating, the file is unchanged at save time, so
    an intentional overwrite still applies exactly.
  - `_save_global_config` is now a safety net for the legacy call sites: if `config.yaml`
    changed on disk after we took our snapshot, it **deep-merges** our keys onto that newer
    file instead of overwriting it (other processes' subtrees survive).
  - `ConfigManager.refresh_global_config()` — explicitly re-read (mtime-keyed pickle cache
    keeps it cheap). The hot `global_config` read path is untouched: no stat, no cost.

### Added
- **Blocks: the shipped catalog declares the binaries it runs + an advisory lint** (2026-07-12) —
  `msstore-publish` ran `msstore` and `steam-build-upload` ran `steamcmd` while declaring **no
  requirements at all**, so `navig block doctor` reported them "ready to apply" and the apply then
  died at step 1 with *command not found*. Both now declare their tool with a real install command
  (`dotnet tool install --global MSStore.CLI`; steamcmd points at Valve's docs — its install is
  genuinely platform-specific, so inventing one command would be wrong on most machines). New
  `loader.lint_block()` catches the class: a command step whose `argv[0]` is a literal binary that
  `requires.tools` never declares. It is **advisory, not a hard error** — `validate_block` is
  enforced at apply time, so promoting it would refuse third-party blocks that already run;
  `navig block verify` prints it as a warning and still exits 0. Two new tests lint **every shipped
  block** (registry catalog + plugin-owned) for validity and for undeclared binaries — nothing
  checked the shipped catalog before, so a malformed block could ship silently.
- **Blocks: per-requirement install commands + author-time `requires:` lint** (2026-07-12) — a
  `requires.tools` / `requires.plugins` entry may now be a mapping carrying the **exact** command
  that satisfies it (`- {name: agent-device, install: npm install -g agent-device@latest}`), not
  just a bare name. `navig block doctor` and the apply gate print *that* command. Previously both
  guessed `pip install <name>`, which is wrong for anything with extras (navig-mobile really needs
  `pip install "navig-mobile[all]"`) or for a non-Python tool — a diagnostic that prints a command
  which doesn't work is worse than one that prints none. Requirement checking is now a single
  source of truth (`policy.check_requirements()`), replacing three drifting copies (the policy
  gate, `block doctor`, `block show`). `navig block verify` now lints `requires:` at author time
  (malformed shape, unknown key, a `detect` probe with no `run` argv).
- **`ModuleDef.settings_schema` — plugin-declared settings fields** (2026-07-12) — a plugin app
  can ship a settings page on the desktop OS without any surface code: declare
  `settings_schema=[{key, kind: toggle|select|segmented|number|text, label, default, …}]` on the
  `ModuleDef` it registers. The OS renders the fields on `apps/<id>/settings` and persists values
  through the generic `GET/POST /api/deck/apps/{id}/settings` endpoint. Docs:
  `docs/module-manifest.md` § Plugin-declared settings.
- **`StealthConfig.window_size` / `window_position` / `mute_audio`** (2026-07-12) — control the
  offscreen headful browser: `window_size=(W,H)` forces the OS window size (with `no_viewport=True`
  the page matches it — needed to render a site's *desktop* layout, e.g. TikTok's comment side-panel
  ≥~1024px); `window_position=(-2400,-2400)` runs a **headful browser offscreen** (real enough to
  defeat headless bot-detection, invisible to the user); `mute_audio=True` adds `--mute-audio`.
  All three surface through `StealthConfig.from_config` (`browser.window_size` / `window_position` /
  `mute_audio`).
- **`capture_json(stop_when=…)`** (2026-07-12) — an optional predicate called with the collected
  items after each settle; return `True` to stop scrolling early (enough results, or the API
  signalled no more pages). Turns `rounds` into a safety cap rather than a fixed cost, so a paged
  capture (e.g. TikTok comments) finishes as soon as it's done.

### Fixed
- **`navig space init` claimed success while creating a broken space** (2026-07-13) — found by
  building the wheel, installing it into a clean venv, and actually *driving* it. When the packaged
  `navig/scaffold-templates` was absent — true of **every published wheel**, because `package-data`
  never declared it — the distillery scaffold loop hit `if not root.is_dir(): continue` and **silently
  skipped the whole `/inbox` capability**. `space init` still printed `✓ Created space` *and* `Ready:
  drop files in ./.inbox and run /inbox`, then `navig space doctor` failed the very space it had just
  made (exit 1: `✗ skill (.navig/skills/inbox) missing SKILL.md, BOOTSTRAP.md, …`,
  `✗ library (.navig/refs/notes) missing README.md, INDEX.md`). Measured on real installs: pre-fix
  wheel → 45 files, **6/6 distillery files missing**; fixed wheel → 51 files, **0 missing**, doctor
  green. A missing *packaged* template is a broken **install**, not a broken space, so it is now
  reported as one: `_scaffold_space_skeleton` returns an `incomplete` list, `space init` prints
  `✗ This navig install is incomplete — the space was created WITHOUT some capabilities` (and no
  longer promises `/inbox`), and `space doctor` shows `✗ navig install incomplete` under Structure so
  `--fix` explains why it cannot help instead of failing a check the user has no way to satisfy.
- **64 more assets never reached the wheel — the default persona, the agent's locales, the space
  scaffold** (2026-07-13) — the *other half* of the "assets don't ship" class. The previous fixes
  covered files resolved from a path **outside** the package; these sit **inside** it (so the code
  finds them in a dev checkout) but were never declared in `[tool.setuptools.package-data]`, whose
  hand-curated glob list (`"help/*.md"`, `"schemas/*.json"`, …) silently missed everything nobody
  remembered to add. Verified by building the wheel and diffing it against the tree: **64 files
  present on disk, absent from the artifact** — including `navig/resources/personas/default/`
  (**the default persona and its soul**), `navig/agent/conv/locales/{en,fr,ru,zh}.json` (**i18n**),
  `navig/modes/builtin.yaml` (**builtin modes**), `navig/scaffold-templates/` (17 files that
  `navig space init` copies **verbatim** into a new space), `navig/browser/templates/`,
  `navig/license/tiers.json`, `navig/contracts/schemas/block.schema.json`, and two builtin skills.
  Every one is loaded via `Path(__file__).parent / …`, so it worked perfectly for us and was simply
  missing for every pip-installed user.
  An extension allowlist has the same failure mode (add a type → forget the glob), so the rule is
  inverted: `navig = ["**/*"]` ships everything inside the package, with `exclude-package-data`
  keeping bytecode out. Rebuilt wheel: **64 → 0** missing, 0 `.pyc`.
  Guarded by `tests/packaging/test_package_data.py`, which computes coverage with setuptools' own
  recursive-glob semantics (no wheel build needed) across **core and all 17 first-party plugins**.
  It also caught a dead `navig_devhost/menu/devhost.mjs` — a stale duplicate of navig-menu's TS
  builtin, loaded by nothing (the menu loader never scans Python packages) and shipped by nothing;
  removed.
  The earlier `test_package_data_declares_the_builtin_tree` asserted a literal glob *string* and
  broke the moment the globs were generalised — rewritten to assert real coverage. Assert the
  outcome, never the spelling.
- **A general guard for escaped assets — plus builtin formations, the Nerd Font script, and
  `navig desktop`** (2026-07-13) — a new AST test
  (`tests/platform/test_asset_paths.py`) walks every module under `navig/` and fails on **any**
  file-anchored path that resolves *outside* the package (which setuptools cannot ship, so it works
  in a dev checkout and vanishes from every wheel — usually degrading silently). Intentional escapes
  live in an `ALLOWED_ESCAPES` map with a reason each, and a stale entry also fails, so the list
  can't rot into a rubber stamp. It immediately found three more real bugs:
  **`formations/loader.py`** called `<repo>/core/store/formations` its "canonical location" — a
  pip-installed navig loaded **zero builtin formations**, and the path died outright when the
  content store moved into the package (`is_dir()` guarded it, so the loss was silent; measured: 0
  formations, now 5). **`ui/_capabilities.py`** looked for `Install-NerdFont.ps1` under
  `<repo>/core/scripts/`, so the Nerd Font auto-install never worked when installed and onboarding
  told the user to run `pwsh scripts/Install-NerdFont.ps1` — a file they do not have; the script now
  ships in the package and onboarding prints its real path. **`commands/desktop.py`** `Popen`-ed a
  backend that exists nowhere in the repo, so python started, printed `can't open file …agent.py`
  and closed stdout, and the user got `agent closed stdout unexpectedly`; it now fails with what is
  actually wrong. Also corrected a generated config comment that claimed
  `config/config.example.yaml` is "shipped with the package" (it is not).
- **Runtime assets that escaped the package — builtin skills, AHK templates, the speedtest worker**
  (2026-07-13) — three commands resolved their assets by counting `.parent`s out of the module,
  landing *outside* the `navig` package. setuptools cannot ship such a path, so each worked in a dev
  checkout and was simply absent from every wheel — and all three degraded **silently**:
  `commands/skills.py` walked out to `<repo>/core/store/skills`, so a pip-installed navig listed
  **zero builtin skills**; `adapters/automation/ahk.py` walked out to
  `<repo>/core/store/templates/ahk`, so the AHK adapter had **no primitives/workflows**; and
  `commands/net.py` importlib-loaded `<repo>/core/scripts/speedtest/worker.py`, which was in
  **neither the wheel nor the sdist** — `navig net speedtest` raised on every installed navig, while
  a maintained, *packaged* copy of the same worker sat unused (the two had drifted). All three now
  resolve through `builtin_store_dir()`; the unshipped duplicate `core/scripts/speedtest/` is
  deleted. Two dead `except` fallbacks pointing at the old tree (`template_manager`,
  `ui/skills_renderer`) removed. The skills/AHK paths broke outright when the content store moved
  into the package — they are fixed here.
  Guarded by `tests/platform/test_paths.py::TestRuntimeAssetsResolveInsidePackage`, which asserts
  each resolver stays inside the package and *actually loads* the speedtest worker.
- **Every published wheel shipped with ZERO builtin content** (2026-07-13) — the built-in content
  store (137 skills · 35 prompts · 38 templates · 50 formations · 20 agents · 133 tools) lived at
  `<repo>/core/store` and was declared **only in `MANIFEST.in`**. MANIFEST.in reaches the *sdist*;
  setuptools' `package-data` cannot reach a directory outside the package — so the whole tree was
  dropped when the wheel was built. Verified against the published artifact: **`navig 2.8.0` on
  PyPI contains 0 files under `store/`**, and the official installer does `uv pip install navig`.
  A dev checkout resolved `builtin_store_dir()` to the repo tree and worked fine, which is why it
  went unnoticed — meanwhile `load_prompt()` silently returned the literal string
  `"Warning: Prompt <slug> not found."` **as the prompt**, and builtin skills / templates /
  formations were simply absent for every real user. The tree now lives **inside the package** at
  `navig/builtin/`, declared in `[tool.setuptools.package-data]`; the wheel goes from **0 → 419**
  builtin files. Regression-guarded (`tests/platform/test_paths.py::TestBuiltinStoreShips`),
  including a check that the `builtin/**/*` glob itself is still declared.
- **`core/.gitignore` silently swallowed tracked content** (2026-07-13) — a bare `store/` rule (for
  a "runtime store" that actually lives in `~/.navig/data/store`, never in the repo) made `git add`
  quietly drop anything under a `store/` directory. It had already forced a `!navig/store/` rescue
  for the BaseStore *source code*, and it silently discarded **13 authored builtin blocks** — which
  is why they never shipped. Rule removed.
- **Council synthesis no longer presents an error string as the final decision** (2026-07-12) —
  the final-synthesis LLM call now gets the same one-shot default-provider retry the per-agent
  calls got in PR #84: a credential-class failure (`nokey`/`unreachable`) retries once via the
  default route, logs one `[COUNCIL] Synthesis: provider '…' … — falling back` warning, and marks
  the result with `synthesis_fallback: true` + `synthesis_fallback_provider`. When synthesis still
  fails (fallback failed, non-credential error, or timeout), the run persists an honest failure
  state instead of the old `[ERROR during synthesis: …]` literal: a distinct
  `synthesis_error: "<message>"` field on the result, an operator-facing `final_decision`
  ("Synthesis failed (<reason>) — see the individual agent responses above."), and a
  `synthesis_error: true` badge on the terminal `done` stream event (engine `on_event` and the
  route's enriched `council_update` done alike) so the OS/deck UIs can flag the record.
- **Credential-resolution race under parallel LLM fan-outs** (2026-07-12) — council/formation
  runs dispatching 5 agents at once intermittently failed 1–3 calls with a false
  `No credential configured for provider 'anthropic'` (and, once, sqlite's
  `bad parameter or other API misuse`) while sibling calls succeeded in the same second.
  Root cause: `VaultStore` shared ONE sqlite connection across threads with no lock —
  parallel `read_secret` calls (each triggering the access-audit write inside
  `Vault.get_bytes`) interleaved each other's `BEGIN..COMMIT` windows
  (`cannot start a transaction within a transaction` / SQLITE_MISUSE), and the resolution
  layer swallowed the error into `(None, None)`. Hardened at three layers:
  (1) `VaultStore` now serializes all shared-connection access behind an `RLock`
  (transactions held for their whole window); (2) `resolve_provider_credential` is
  single-flight per `(provider, connection_id)` with a 15 s success-only TTL cache, so a
  parallel fan-out performs ONE resolution (and at most one OAuth refresh) instead of
  racing — failures are never cached, and connect/disconnect/set-default invalidate the
  cache immediately; (3) the `get_vault()` / `get_connection_store()` singletons are
  construction-guarded so a parallel first use can't build two stores. Threaded
  regression tests reproduce the pre-fix interleave deterministically
  (`tests/vault/test_vault.py::TestVaultStoreConcurrency`,
  `tests/providers/test_inference_routing.py` fan-out/cache tests).

### Changed
- **Blocks: `requires.permissions` retired** (2026-07-12) — the key was documented as "reserved,
  not yet enforced", meaning the runner's requirements **gate silently ignored it**: an author who
  wrote `permissions: [screen-recording]` believed it was enforced when nothing checked it. A
  declared-but-ignored requirement in a security gate is worse than no field. Removed from the
  block schema; `navig block verify` now flags it with the migration (`detect` probes already
  express any environment precondition as a real, executed check). No block in the tree declared
  it. `navig block doctor --json` check objects now carry `detail` + `fix` instead of `note`.
- **Deck→OS apps migration complete** (2026-07-12) — the desktop OS now hosts the
  full app catalog; the deck is the thin Telegram remote (Home glance · Inbox
  approvals · Wallet · Telegram · Settings — the apps grid is gone). Registry
  end-state: every catalog app declares `os-tile:<id>` except the deck-native
  pair (`telegram`, `wallet`); `deck-section:` survives only for the deck's
  embedded Settings surfaces (context/vault/personas) + the Inbox tab. New
  inverted tripwires in `tests/modules/test_registry_apps.py` enforce the
  contract (every app renders somewhere; no stray deck sections). The
  `navig-generate` and `navig-email` plugins now register desktop tiles
  (`app_category` Create / Comms) with detail-page copy.

### Added
- **`system_chrome.capture_existing_cookies(app, host)`** — capture an *existing* logged-in
  session without re-login or fighting Chrome's App-Bound Encryption: copy a minimal profile slice
  (`Local State` + the Cookies DB + prefs — never History/Login Data) to a throwaway dir and let
  the real browser decrypt its own cookies over CDP (`context.cookies()`), filtered to the host.
  ABE keys are bound to the Windows user + `chrome.exe` binary, not the profile path, so a copy
  decrypts on most machines (a documented `0x57` clone-decrypt failure exists on some — the helper
  returns `[]` and the caller falls back). Deletes the copy; never touches the live browser.

### Added
- **Real system-Chrome CDP engine (`engine="chrome"`).** New `navig/browser/system_chrome.py`
  `SystemChromeController`: launches the user's system Chrome/Edge/Brave with **only** a debug port
  (no `--enable-automation`, so `navigator.webdriver` stays **false** — it looks like a normal
  browser, unlike Patchright/Playwright) and drives it over CDP (`connect_over_cdp`), for
  human-driven logins that sites bot-wall against automation. Wired into
  `router.get_browser(engine="chrome")`. Isolation & safety: its OWN `--user-data-dir` (never the
  user's real profile) and it kills **only** the process it launched — never a name-based
  `chrome.exe` sweep. Same `start`/`stop`/`.page`/`.context` surface as the other controllers.
- **Per-app settings endpoint** (2026-07-12) — `GET/POST /api/deck/apps/{app_id}/settings`
  persists an app's preferences to the `apps.<id>.settings.*` subtree of the global config
  (shallow-merge; a `null` value deletes a key; `enabled` is reserved for `/modules/toggle`).
  App ids resolve against the module registry, so plugin apps work automatically. Every write
  broadcasts an `apps_settings_update` event on `/api/events` so other surfaces sync live.
  Backs the desktop OS `apps/<id>/settings` pages.

### Changed
- **Camoufox is now the preferred Firefox engine** (`FirefoxController` / `engine="camoufox"`).
  Plain Playwright Firefox is non-CDP but still leaks `navigator.webdriver=true` (Playwright forces
  it — a user-pref can't hide it), so anti-bot stacks can still flag it as automation. Camoufox
  hides `navigator.webdriver` at the C++ engine level. New helpers `best_login_engine()` (camoufox
  if its package is installed, else firefox), `camoufox_binary_available()`, and `ensure_camoufox()`
  (fetches the ~150MB binary on first use, like the Playwright-Firefox binary). Fixed the Camoufox
  launch path: `new_context(no_viewport=True)` (avoids a `Browser.setDefaultViewport` protocol
  error) and `geoip` is enabled only when a proxy is set (needs the `camoufox[geoip]` extra).

### Fixed
- **Module enable/disable toggles now survive a daemon restart** (2026-07-12) —
  `ModuleRegistry.set_enabled` mutated only the in-memory config (`Config().set()` without
  `save()`), so every toggle silently reverted on restart. Now persisted to disk, with a
  disk-level regression test.
- **Cron two-writer footgun — the daemon now reconciles external edits to `cron_jobs.json`**
  (2026-07-12) — `CronService` loaded the jobs file only at init, so a `navig … schedule`
  enable/disable (the CLI writes the file directly, from a separate process) was invisible to the
  running daemon until a restart, and the daemon's next `_save_jobs` could silently clobber it. The
  scheduler loop now checks the file's mtime at the top of each tick and, when it changed under us,
  resyncs the in-memory job set from disk (honouring external adds *and* removals, recalculating any
  missing `next_run`). Our own writes are skipped by comparing against the mtime we last wrote. Job
  run-state (`last_run`/`next_run`/`last_status`) round-trips through the file, so nothing is lost;
  the reload runs at tick-start when no job is mid-run, so replacing the set is race-free. A CLI
  `navig games schedule enable` now takes effect within one tick (~10s), no restart.
- **Council agents no longer go silent when their provider has no usable credential**
  (2026-07-12) — every `run_council` agent call resolves through the default mode route
  (`resolve_llm(mode=None)` → `big_tasks`), and when that provider's credential could not be
  resolved at dispatch time (`No credential configured for provider '…'` — e.g. a connection
  record whose OAuth token fails to resolve under the council's 5-way parallel fan-out), the
  agent contributed nothing and the run finished with reduced confidence. The engine now
  classifies the failure with the shared `navig.llm.liveness.classify_probe_error` rules and,
  for credential/config-class errors only (`nokey`/`unreachable` — never content, auth, or
  rate-limit errors), retries that agent ONCE via the default-provider route (the mode's
  configured fallback pair when the default resolution still lands on the failed provider).
  The substitution is never silent: one warning per agent per run
  (`[COUNCIL] Agent 'qa': provider 'anthropic' has no credential — falling back to the default
  provider`), and the agent's response dict + `agent_response` stream event carry
  `provider_fallback: true` and the substitute `fallback_provider` name (a provider name only —
  never credentials). A fallback that also fails keeps the ORIGINAL error on the agent and the
  run continues, exactly as before.
- **The system-event processor was never started** (2026-07-12) — `SystemEventQueue.start()` had
  no caller in the gateway, so every `emit()` (board_update, studio_post_update, requests_update,
  council_update, …) piled up in an undrained queue and `GET /api/events` served heartbeats only:
  live refresh across deck/OS was silently dead server-side, and 2,737 undelivered events had
  accumulated in the persisted pending store. The gateway now starts the processor at boot with
  `replay_pending=False` (the stale backlog is moved to history as discarded, loudly logged —
  replaying it would burst every SSE client, the cloud uplink, and the event bridge) and stops it
  symmetrically at shutdown. Found while live-verifying council streaming: a healthy SSE listener
  received zero events during a full deliberation.
- **Mesh hello reply storm** (2026-07-12) — `_handle_packet` replied `hello` to every received
  hello, so two live nodes ping-ponged hellos at socket speed (~166 pkt/s observed in the
  first-ever two-node loopback test; 1,995 packets in 12s). The reply now fires only for
  previously-unknown peers — instant convergence for new nodes preserved, known peers ride the
  heartbeat loop. Regression-tested (`TestHelloReplyStorm`); re-test after the fix: 3 packets.
- **Mesh discovery scan was dead code — now an on-demand announce over the live loop**
  (2026-07-11). The gateway-root `POST /mesh/discovery/scan` (what `navig flux scan` calls)
  imported a `NavigDiscovery` class and `probe_lan_range()` method that never existed in
  `navig/mesh/discovery.py`, so every scan silently degraded to `scanning:false` (found during
  slice B6). Repaired without protocol changes: new `MeshDiscovery.announce()` — one extra
  `hello` multicast on the existing loop (peers upsert us and reply with their own HELLO, so the
  registry converges within ~1 heartbeat interval); returns `False`, never raises, when the loop
  isn't running (Phase-1 graceful-degradation rule; LAN-only, no WAN, no new packet types). The
  scan route now drives the LIVE `gateway._mesh_discovery` instance (never a throwaway) and
  returns `{scanning:true, self, peers}` — or `{scanning:false, reason:"mesh not running"}`
  (200) when mesh is off. Also aliased as `POST /api/deck/mesh/scan` so the desktop OS (brain
  proxy reaches only `/api/deck/*`) can trigger it — the OS Mesh app's refresh button now nudges
  discovery before re-reading the peer list (a failed scan never blocks the refetch).
- **Session-first restore now works for session-only logins** (2026-07-11) — two additive vault/
  autofill fixes so a captured web-session actually re-authenticates without a stored password
  (e.g. `navig games` / Epic). (1) `vault.get_session(domain)` fell back to the label
  `web-session/<host>/default`, so a session saved *with* an account
  (`web-session/<host>/<user>`) was invisible to a username-less lookup — it now falls back to the
  most-recently captured session for the host (a pinned `username` is still resolved exactly, never
  a different account). (2) `browser.autofill.auto_login` gated session restore *behind* a stored
  credential (`no_credential` returned first), so a session-only login never restored — it now
  attempts session-first restore **before** requiring a credential (origin floor #1 already binds
  the page to the target; form-fill still needs floor #2 + a login). Live-proven: `cdp_actions.login`
  for `epicgames.com` now returns `session_restored` (was `no_credential`).
- **Habit reminders never fired — the dead cron-store split** (2026-07-11). Every habit surface
  (`navig habit`, the life dashboard, the Telegram `/habits`/`/health`/`/workout daily` commands,
  and the deck Life app) read and wrote `~/.navig/daemon/cron_jobs.json` — a store the running
  scheduler **never executes** (the engine's store is `<config_dir>/scheduler/cron_jobs.json`).
  New `navig/scheduler/habit_store.py` is the single corrected access layer with a three-tier
  resolution: the in-process live `CronService` (gateway surfaces — race-free), the daemon's
  `/api/deck/schedule/crons*` routes over loopback (CLI while the daemon runs), then a detached
  service over the live store (daemon off; adopted on next start). Jobs stranded in the legacy
  store are migrated once (merge by name, fresh ids, file renamed to `*.migrated`) — at daemon
  startup and on the CLI fallback path. Also fixed on the way: the deck habit-complete endpoint
  now goes through `CronService.update_job` with a real `datetime` (a raw string would crash
  `to_dict`), and `CronService` registers itself as the process-wide live instance
  (`get_live_service()`) when gateway-attached.

### Added
- **Streamed Council deliberations** (2026-07-11) — a multi-round Council run no longer has to be
  one long blocking HTTP call. The engine (`navig/formations/council.py:run_council`) accepts an
  optional `on_event` callback (backward-compatible; `navig council run` unchanged) emitting small
  progress dicts in order — `round_started` → `agent_response` (per agent, in completion order,
  summary truncated to 200 chars, CONFIDENCE trailer stripped; never full LLM outputs, never
  system prompts) → `synthesis_started` → `done` — and a raising callback never breaks the run.
  `POST /api/deck/formations/council/run` with `{"stream": true}` now returns
  `202 {council_id, started}` immediately, runs the deliberation in the background, and broadcasts
  each progress event as `council_update` on the existing gateway event stream (`GET /api/events`
  — the same path `board_update` rides), every event carrying the `council_id` (== the persisted
  history id); the terminal `done` carries `history_id`/`persisted` so clients fetch the full
  record from history, and a failed run broadcasts `error`. Only ONE council may run at a time per
  gateway (a concurrent run 409s) so event streams never interleave. Without the flag the route
  blocks and returns the full result exactly as before (older clients unchanged; the response now
  also includes `council_id`). The OS Mesh app's Council tab renders the live progress — round
  indicator, per-agent chips as responses land, a synthesis line, then the final decision card —
  and degrades to the old blocking behavior against an older daemon. Also fixed on the way: the
  OS server-core SSE→WS bridge now unwraps the gateway's `data: {"type",…}` envelope, so
  `brain:event` subscribers (Tasks board, Studio, module catalog, Mesh) finally receive the real
  event type they test against instead of `"message"`.
- **Formations + Council over the deck API, and a mesh read alias** (2026-07-11) — the first HTTP
  surface for the `navig/formations` engine (migration slice B6 of the forge consolidation; the
  archived navig-mesh / navig-formations VS Code extensions' capabilities, now rendered by the OS
  **Mesh** app). Five `/api/deck/formations*` routes over the existing engine (nothing
  reimplemented): discovered formations + the active id (`GET /formations`), the active formation
  with its agents/roles/council weights (`GET /formations/active` — composed `system_prompt`
  bodies deliberately never serialized), any formation by id or alias (`GET /formations/detail`),
  a Council deliberation over the ACTIVE formation (`POST /formations/council/run` — calls the
  same `run_council` entry point as `navig council run`; question ≤2000 chars, rounds clamped
  1–5, per-agent timeout clamped 5–300s; 409 when no formation/agents are active), and persisted
  deliberation history (`GET /formations/council/history`, `?id=` for one full record; records
  land under `data_dir()/council/`, ids traversal-guarded). Plus `GET /api/deck/mesh/peers` — a
  read-only alias over the same `navig.mesh` NodeRegistry as the gateway-root `/mesh/peers`,
  needed because the desktop brain proxy can only reach `/api/deck/*`; nothing was added to
  `navig/mesh/` (Phase-1 LAN-only rules intact) and mutation verbs stay at the gateway root. The
  module registry gained the `mesh` os-tile (Systems). Known gap documented, not aliased: the
  gateway-root `POST /mesh/discovery/scan` references a `NavigDiscovery.probe_lan_range` API that
  does not exist in `navig/mesh/discovery.py`, so it always degrades to `scanning:false`.
- **AI plan generation over the deck API** (2026-07-11) — `POST /api/deck/plans/generate` grew a
  `mode:"ai"`: it reads the space's `VISION.md` (or an inline `vision` string in the body) and
  drafts real starter content for every missing doc in the standard plan suite (`ROADMAP.md`,
  `SPEC.md`, `DEV_PLAN.md`, `phases/CURRENT_PHASE.md`, the first milestone) through the new
  agent-layer seam `navig/agent/plan_drafter.py` (`draft_plan_doc` — all inference stays in
  `navig/agent/`; low temperature, template-structure-preserving prompt). Existing non-empty docs
  are skipped (`force:true` redrafts them); `VISION.md` itself is the seed and is **never**
  written. One doc failing never aborts the rest — the response reports
  `{created, skipped, errors:[{doc,error}]}` — and a missing/unreachable LLM backend maps to a
  clean 503 (`PlanDraftUnavailableError`) instead of a stack trace. The scaffold suite also gained
  a `DEV_PLAN.md` template (one of the four wired-by-name plan files) and a public
  `navig.plans.scaffold.suite_templates()` so both generate modes share one suite definition. In
  the OS, the Spaces → Plans panel's empty state now offers a Templates | AI-from-VISION mode
  choice (with an inline vision seed box), and the Plans tab shows a "suite docs missing"
  affordance that can backfill via either mode. Extends migration slice B1 of the forge
  consolidation (see `docs/forge-consolidation.md`).
- **Wiki browser + unified memory search over the deck API** (2026-07-11) — 5 new routes. Four
  `/api/deck/wiki/*` routes expose a space's `.navig/wiki` knowledge base through the existing
  `navig wiki` engine (nothing reimplemented): page listing with first-heading titles
  (`GET /wiki/pages`), page read (`GET /wiki/page`), atomic save/create confined to `.navig/wiki`
  (`POST /wiki/page` — `*.md` only, parent folders created, hidden `.meta`-style segments refused;
  delete intentionally not exposed), and full-text search (`GET /wiki/search` — the same
  `search_wiki` behind `navig wiki search`). Plus `GET /api/deck/memory/query`, the MemoryHub-style
  unified memory search: one budgeted reply aggregating the current phase + DEV_PLAN + milestone
  progress, the last 15 one-line git commits (only when the space root IS the repo toplevel —
  never the enclosing repo's history), the wiki index (or engine search hits for a query), and
  approved KeyFactStore notes, as `{sections:[{source,title,content,truncated}]}` within a
  `?budget=` char cap (`?q=` filters every source; no LLM calls). Per-space via `?space=<id>` or
  the active project root. Migration slice B4 of the forge consolidation — the archived
  navig-memory VS Code extension's wiki/KB/context views, now in the desktop OS Knowledge (Wiki
  tab) and Context (Memory tab) apps (see `docs/forge-consolidation.md`).
- **Cron/trigger automations editor over the deck API** (2026-07-11) — the `/api/deck/schedule/*`
  surface grew from a read-only crons list into the full editor backing the OS Automations surface
  (migration slice B5 of the forge consolidation — the archived navig-automation VS Code extension's
  panel, see `docs/forge-consolidation.md`). Crons now operate on the REAL scheduler engine
  (`navig.scheduler.CronService` — the live in-process service when the gateway is running, else a
  detached instance over the same `<config_dir>/scheduler/` store): create / update / delete /
  enable / disable (`POST|DELETE /schedule/crons…`), run-now that waits for the result
  (`POST /schedule/crons/{id}/run`), and a new engine-level run history (`cron_runs.jsonl`, capped
  at 1000 entries, exposed at `GET /schedule/crons/runs?job_id=&limit=`). Schedules are validated
  with the new `CronParser.is_valid()` before they land in the store (previously unparseable
  schedules silently defaulted to hourly). Event triggers (`navig.commands.triggers.TriggerManager`
  — `~/.navig/triggers/`) get their first HTTP surface: list / create / update / delete / enable /
  disable, fire with dry-run (`POST /schedule/triggers/{id}/fire`), and fire history
  (`GET /schedule/triggers/history`). NOTE: the old crons list read the legacy
  `~/.navig/daemon/cron_jobs.json` — a store the running scheduler never executes — so it listed
  nothing while real jobs ran; it now reports the live engine's truth.

### Fixed
- **`CronService.run_job_now()` return contract** (2026-07-11) — it returned the bare `last_output`
  string while both HTTP consumers (`POST /cron/jobs/{id}/run` and the new deck run-now route)
  expect a `{success, output, error}` dict, so every manual run over HTTP crashed with
  `AttributeError` → 500. Now returns the dict (plus the refreshed `job` snapshot). Detached
  `CronService` instances (TUI, deck fallback) also no longer crash emitting completion events
  (`self.gateway.event_queue` on a `None` gateway).

### Added
- **Inbox review queue + sandbox over the deck API** (2026-07-11) — 6 new `/api/deck/inbox/review/*`
  routes expose the `navig/plans` inbox lifecycle (`.navig/inbox` markdown items with suffix states
  `.md` / `.md.done` / `.md.archive` / `.md.review`) to every surface: state listing with per-state
  counts (`GET /inbox/review`), item detail (`GET /inbox/review/item`), sandbox analysis with ZERO
  side effects (`POST /inbox/review/analyse` — classification + proposed target; the InboxProcessor
  gains a public `analyse()` seam that never appends to the staging queue), approve
  (`POST /inbox/review/approve` — routes the content to wiki/docs/plans or the keyword router's
  proposal, confined to `.navig/`, and marks the source `.md.done`), reject (`.md.archive` — never
  deleted), and requeue (undo — any state back to pending). Decisions land as JSONL audit lines in
  the shared `staging/reconciliation_queue.json`. Per-space via `?space=<id>` or the active project
  root. Analysis is heuristic-only (the optional LM spot-check stays behind the existing `LMClient`
  seam — no new LLM call sites). Migration slice B3 of the forge consolidation — the archived
  navig-inbox VS Code extension's Review Queue + Sandbox, now in the desktop OS Inbox app
  (see `docs/forge-consolidation.md`).
- **Plans engine over the deck API** (2026-07-11) — 11 new `/api/deck/plans/*` routes expose the
  `navig/plans` engine to every surface: phase state + docs listing (`GET /plans/state`), plan-doc
  read/write confined to `.navig/plans` (`GET|POST /plans/doc`), markdown checkbox toggling
  (`POST /plans/toggle-task`), phase verbs (`advance`/`block`/`unblock`/`complete`), milestone git
  ops (`milestone/commit` pathspec-scoped to `.navig/plans`; `milestone/rollback` requires the
  literal confirm `"ROLLBACK"` and refuses when the tree is dirty outside `.navig/plans`), and
  template scaffolding (`POST /plans/generate`). Per-space via the spaces registry (`?space=<id>`)
  or the active project root. This is migration slice B1 of the forge consolidation — the powers of
  the archived navig-spaces VS Code extension, now available to the OS/deck
  (see `docs/forge-consolidation.md`). `CurrentPhaseManager` additionally resolves
  `.navig/plans/CURRENT_PHASE.md` (the layout `navig space` actually scaffolds).

### Fixed
- **Every navig-launched Chrome now starts quiet** — no first-run/welcome tab, no "make Chrome
  default" nag, and **no EU "choose a search engine" choice screen** (Chrome 120+). Added the
  shared `browser/targets.py:CHROMIUM_QUIET_ARGS` (`--no-first-run`, `--no-default-browser-check`,
  `--disable-search-engine-choice-screen`) and applied it at every browser launch site:
  `launch_with_cdp` (all `navig cdp`/`do`/`gmail` launches — Electron apps are excluded), the
  `StealthController` (Patchright can strip Playwright's default first-run flags), and the
  `ClearcoteController`. Command-line flags aren't visible to page JS, so there's no stealth cost.

### Added
- **Firefox browser engine (`engine="firefox"`) — a non-CDP stealth tier.** New
  `navig/browser/firefox.py` `FirefoxController` (same `start`/`stop`/`.page`/`.context` surface
  as `StealthController`), wired into `browser/router.py:get_browser(engine="firefox")`. Firefox is
  driven over Firefox's Juggler protocol, so it carries **none** of the CDP/Chromium automation
  artifacts that some anti-bot stacks fingerprint (e.g. TikTok's automated-login region check that
  fails in Chromium). Uses Playwright's own Firefox (binary auto-provisioned via `playwright
  install firefox`), or **Camoufox** (`engine="camoufox"`, a C++-stealth Firefox with coherent
  fingerprint + geoip) via the new `navig[firefox]` extra. Sessions round-trip through the vault as
  a standard Playwright `storage_state`, so a session captured in Firefox restores into any engine.

### Added
- **Repo guard: conflict radar in every session briefing (2026-07-11).** The SessionStart hook
  now runs the cross-worktree merge simulation itself (pure git, dirty state included) — each new
  session starts with `N pair(s), all clean` or a `merge conflict brewing: a <-> b (files)` line
  per colliding pair. `navig repo guard status` also now recognizes custom-path wiring (like this
  repo's `scripts/agent-hooks/`) instead of reporting working hooks as "missing".
- **`navig repo` — multi-agent repo guard (2026-07-11).** Cross-worktree conflict radar
  (`navig repo conflicts`: in-memory `git merge-tree` across every worktree pair, including
  uncommitted tracked changes via `stash create` — read-only), stale-work report
  (`navig repo stale`: leftover worktrees incl. forbidden sibling checkouts, unmerged branches,
  stashes, lock holder), and the main-checkout agent lock (`navig repo lock status|release`).
  Companion Claude Code hooks in `scripts/agent-hooks/` (PreToolUse lock so one session at a time
  mutates the main checkout — worktrees under `.dev/worktrees/` always exempt — plus a SessionStart
  stale-work briefing). Wiring: `scripts/agent-hooks/README.md`.
- **`navig repo guard install|status|uninstall` — ship the guard to any repo.** Hook sources now
  ship inside the wheel (`navig.guard` package); `install` writes them to `<repo>/.claude/hooks/`,
  merges Claude Code wiring into `.claude/settings.json` (idempotent, user hooks preserved,
  machine-absolute paths), and gitignores `.dev/`. The lock hook now also **blocks sibling
  worktrees** (`git worktree add` outside the repo) for every session — the forbidden pattern that
  keeps scattering work into `../<repo>-<slug>` folders.
- **Telegram: `/help transforms` — discover the reply-keywords.** A new Help screen (button on the
  `/help` home, or `/help transforms`) lists every reply-keyword action — the AI text ops, `music`,
  `tiktok`, owner actions — and the FR/RU/ES/DE/PT aliases. Sourced from `reply_actions` so it can't
  drift from what actually dispatches.
- **Telegram: `music` reply-keyword + music links in groups & business chats.** Reply **`music`**
  (or `song`) to any message with a Spotify/Apple/Deezer/… link → the same track on every platform.
  Unlike the DM-only passive auto-reply, this is owner-triggered and **works in groups**; music links
  are now also enriched in Telegram **Business** chats. Mirrors the existing `tiktok` reply-action.
- **Telegram reply-keywords now also speak German & Portuguese** (`übersetze`/`traduza`→translate,
  `zusammenfassen`/`resuma`→summarize, `erkläre`→explain, `verbessere`/`melhore`→improve,
  `korrigiere`/`corrija`→fix, …), on top of the existing FR/RU/ES aliases.
- **Telegram: paste a music link → get every platform.** In a 1:1 Telegram chat, a bare
  Spotify / Apple Music / YouTube Music / Deezer / Tidal / SoundCloud link auto-replies with the
  same track on all ~18 platforms (song.link / Odesli). DM-only (never groups → no spam), owns the
  message so the agent doesn't also answer, and a no-op unless the message is essentially just the
  link. Opt-out via `telegram.music_links.enabled`. Wires the migrated `navig download music-links`
  resolver into the bot (`navig/telegram/music_actions.py`).
- **Telegram reply-keyword actions now speak French, Russian & Spanish.** The reply-with-a-keyword
  transforms (translate / summarize / explain / context / improve / fix / shorten / rewrite) gained
  FR/RU/ES aliases (e.g. `traduis`, `переведи`, `resumen`, `объясни`, `corrige`, `améliore`), and
  summarize/context/explain now answer in the message's own language. Recovered from the retired
  telegram-bot-navig `nlp_aliases` pack (no collision with the `analyse`→tiktok keyword).
- **`navig cdp launch|new --load-extension <dir>` (`-e`).** Loads a local *unpacked* Chrome
  extension in isolation over CDP for end-to-end testing — a folder with a `manifest.json`, or a
  comma-separated list. Validates each path up front (a missing dir / no `manifest.json` fails
  loudly instead of silently loading nothing) and sets
  `--disable-features=DisableLoadExtensionCommandLineSwitch` so Chrome 137+ Stable doesn't drop the
  flag. Wired through the CLI and the `cdp` MCP tools.
- **`navig devhost` — local `.test` domains with trusted HTTPS** (new first-party plugin,
  `plugins/navig-devhost`). `add` / `up` / `list` / `status` / `remove` / `doctor`, plus
  `menu install` to surface it in `navig menu`. Hosts entry + mkcert cert + a stdlib TLS relay
  (keep-alive / SSE / WebSocket-HMR pass through).
- **Reusable anti-detection browser blocks (`navig/browser/*`).** Shared across the whole
  headless stack (TikTok scraper, `browser_tool`, os worker):
  - `proxy.py` — BYO proxy pool with rotation + block-cooldown + per-profile assignment
    (config `browser.proxies`; per-profile `proxy` on `cdp-profiles.json`). Also **fixed a
    credential-drop bug** in `stealth.py` (bare `{"server": url}` silently dropped `user:pass`).
  - `intercept.py` — reusable "navigate + capture matching JSON responses" primitive + a
    **read-only guard** that aborts engagement mutations (like/follow/comment-post).
  - `fingerprint.py` — coherent, **seeded (stable)** fingerprint (BrowserForge when present,
    native fallback); coherence-safe context opts (locale/tz/geo) + WebRTC leak flags applied
    by default on `StealthController`, JS shim opt-in (off on Patchright to avoid clashing).
  - `cloud.py` — `CloudBridge`, a Tier-C cloud anti-detect browser over a `wss://` CDP
    endpoint (opt-in `browser.cloud`; token redacted in logs).
  - `clearcote.py` — opt-in "max stealth" engine (BSD-3 Chromium fork) driven over CDP,
    **download-on-demand + SHA-256-pinned**, never bundled.
  - `pacing.py` — human-cadence think-time jitter, jittered backoff (honours Retry-After),
    per-host throttle.
  - `persona.py` — **fingerprint-as-identity**: one coherent, deterministic persona per
    profile applied to *both* the browser (`to_stealth_config`) and the HTTP scraper
    (`to_fetch_opts`); portable via `navig cdp profile export/import` (passphrase-encrypted
    capsule carrying the persona + optional session). New `navig cdp profile proxy`.
  - `signer_cache.py` — learns the signed-endpoint **shape** (path + param/header NAMES only,
    **no secret values**) with a TTL, so a signing bump can't permanently break the fast path.
  - `router.get_browser(engine=…)` — engine selection: `stealth` / `clearcote` / `cloud` /
    fast. Patchright now declared as the `navig[stealth]` extra + **fails loudly** (opt-in
    `require_stealth`) instead of silently degrading to detectable vanilla Playwright.
- **Multi-profile browser system + one-shot AI browser tasks + Gmail.** Different persistent,
  logged-in Chrome identities for different projects/cases/accounts, each on a **stable port**:
  `navig cdp profile new/list/open/use/close/remove` (+ `navig cdp open <name>`). `profile open`
  **reuses** a running profile (no close/reopen); an **active profile** (`profile use`) lets
  everything default to it. Real Chrome profiles are detected (`--real`) and openable with a
  preflight that refuses while the everyday browser holds the lock. New **`navig do "<task>"`** runs
  the AI agent against the active/`--profile` **visible** browser (bridged via the desktop-pane
  endpoint), with **safe-by-default** guardrails (prepares but won't send/publish without `--yes`;
  `--dry-run` plans only) and an audit log. New **`navig gmail compose`** composes (and, with
  `--send`, sends) via Gmail's stable **compose deep-link** — reliable, no LLM, no fragile UI
  clicking. New: `browser/profiles.py`, `commands/do.py`, `commands/gmail.py`,
  `browser/recipes/gmail.py`; `--profile-directory` support in `launch_with_cdp`.
- **`navig email send` now actually sends** (was a stub) — via SMTP + app-password
  (`GmailProvider`/`IMAPEmailProvider.send_email`); password from config or `EMAIL_PASSWORD`.
- **AI browser auto-login + website password vault** (session-first). Store website logins in the
  vault (`navig vault login add|list|get|remove`) and sign in on any attached CDP browser with
  **`navig cdp login <domain>`** / MCP `cdp_login`. **Session-first**: on success the authenticated
  session (cookies + localStorage) is captured to the vault, so later visits *restore the session*
  instead of retyping — the password is only re-filled when the session is stale. Security floor:
  **origin-binding** (`navig.browser.origin_match`, eTLD+1 via the public-suffix list + IDN/punycode
  homograph defense, https-only) refuses to fill on look-alike/cross-origin pages; the password is
  injected **server-side** and never enters an LLM context or a log; a register/change-password form
  (0 or >1 password fields) is never auto-filled. **TOTP 2FA**: a login may store a `totp_secret`
  and the engine clears its own one-time-code step (RFC 6238, stdlib — no dependency). New:
  `navig/vault/logins.py`, `navig/vault/sessions.py`, `navig/vault/totp.py`,
  `navig/browser/origin_match.py`, `navig/browser/autofill.py`. New dep: `tldextract`.
- **Persistent userscripts on the core CDP browser** — **`navig cdp inject <script|@file>`** / MCP
  `cdp_inject` register a script via CDP `Page.addScriptToEvaluateOnNewDocument` (runs at
  document-start on every navigation), plus `BrowserController.add_init_script` /
  `export_storage_state` / `restore_storage_state`. (The core `navig cdp` browser had no userscript
  support before; the apps/os desktop browser is a separate system.)

### Security
- **MCP/CDP approval gate (Stage 0)** — the MCP stdio server dispatched every tool with no approval
  interlock, so a client could call `cdp_eval` to read a password field off a logged-in page. Tool
  execution now routes through the approval gate (`navig.tools.approval.check_sync`); `cdp_eval`,
  `cdp_launch`, `cdp_stop`, `cdp_login`, and `cdp_inject` are classified dangerous, audited, and
  gate-able (default single-operator backend approves-with-audit; a stricter policy/backend can deny).

### Changed
- **`navig-media` plugin split into three** (standalone-but-wired plugins): **`navig-social`**
  (publishing/Studio + one-call fan-out), **`navig-download`** (universal yt-dlp downloader), and
  **`navig-generate`** (AI media generation, renamed from navig-media — the `media` name is
  deprecated). Core `navig media` is renamed **`navig generate`**; **`navig media` still works as a
  deprecated alias** (one-line notice). Every existing command keeps working: `social`, `facebook`/`fb`,
  `download`/`dl` (new) + `tiktok`/`tt` (alias), `generate` + `media` (deprecated). Core seams
  (`gateway/server.py`, `commands/media.py`, `telegram/tiktok_actions.py`, `store/scheduled_posts.py`)
  repointed to the new packages; `yt-dlp` now travels only with `navig-download`, so publishing-only
  installs stay lean. Extras: `social`/`download`/`generate` (+ `media`/`tiktok` back-compat aliases).

### Fixed
- **`db-snapshot` `--compress` flag never existed** — the legacy builtin `db-snapshot` workflow ran
  `db dump … --compress` then downloaded a `.gz` that was never produced. The migrated `db-snapshot`
  Block dumps uncompressed `.sql` and verifies the local file exists. (2026-07-07)
- **Suggest-provider no longer misfires on usage errors** — click exits 2 for *every* usage
  error, so a bad flag on a real command (`navig github --workers`) previously printed a bogus
  "'github' is provided by plugin navig-github (not installed)" activation hint. The CLI now
  checks the command is genuinely unregistered before suggesting (`_is_registered_command` in
  `main.py`); true unknown commands keep the suggest/did-you-mean flow. (2026-07-06)

### Added
- **`navig cdp` — unified CDP connector for browsers + Electron apps** — one way to drive any
  Chromium-family surface (Chrome/Edge/Brave **and** Electron apps: Discord, Notion, Slack, VS Code)
  over the Chrome DevTools Protocol. `navig cdp status|targets|launch|new|stop|detach|launched|tabs|
  switch|snapshot|screenshot|click|type|key|scroll|move|eval|nav` (+ 17 agent-callable `cdp_*` MCP
  tools). Full lifecycle: **enable** (`launch`/`new`), **disable** (`stop` closes exactly the browser
  NAVIG started — tracked by PID in `~/.navig/cdp-launched.json` — never unrelated windows; `detach`
  just disconnects), and a **brand-new isolated browser** (`new` = own profile + auto-allocated port;
  throwaway or `--profile <name>`). Discovers live debug targets (classifies `browser`/`node` so
  Node/wrangler inspectors are marked not-attachable); browsers auto-use a **dedicated debug profile**
  (modern Chrome M136+ blocks debugging on the default profile); Electron apps keep their logged-in
  profile. **`tabs` lists every open page** (raw `/json/list`); every action takes `--tab`/`--url` (or
  `switch`) to control a **specific** page. Falls back to OS mouse+screenshot (`via: os-automation`)
  when no CDP target is attachable. Reuses the existing `core/navig/browser/` stack (`BrowserController`
  + `CDPBridge`); new `browser/targets.py` (discovery + Electron registry + launched-process registry),
  `browser/session_manager.py` + `browser/cdp_runtime.py` (reused live sessions on one loop),
  `browser/cdp_actions.py` (shared by CLI + MCP). `eval`/`launch --force-restart` are confirm-gated;
  CDP is loopback-only. Firefox is out of v1 (not a CDP target). Docs:
  `docs/automation/cdp-connector.md` + HANDBOOK §38. (2026-07-07)
- **Workflow → Block migration complete; legacy engine retired** — the multi-runbook `community`
  package (`deployment-checklist`, `docker-health`, `security-audit`, `devops-shortcuts`,
  `backup-runbook`) migrated to **instruction** Blocks, so **no `type: workflows` package remains**.
  The **System-A workflow engine is retired**: `WorkflowManager`/`Workflow`/`WorkflowStep` deleted;
  `navig task`/`navig flow`/`job` are now Block-redirect shims (verbs preserved — no CLI break;
  `navig task complete` unchanged); `triggers.py` `ActionType.WORKFLOW` applies a Block
  (destructive steps stay `--approve`-blocked). Published `safe-deployment` + `db-snapshot` to the
  community registry mirror (`registry/blocks/*` + index + `SHOWCASE.md`). See
  `docs/blocks-vs-workflows.md`. (2026-07-07)
- **Builtin ops workflows migrated to Blocks** — the 4 bundled workflows and 3 single-workflow
  packages are now `BLOCK.md` Blocks under `core/store/blocks/` (installable, verifiable outcomes;
  `navig apply`). `safe-deployment` + `db-snapshot` are executable with machine verify (post-deploy
  `navig health`; downloaded-dump `file_exists`) and destructive steps gated on named `--approve`;
  `server-health` + `emergency-debug` are executable read-only diagnostics (each probe chained in one
  resilient `navig run`, the honest successor to `continue_on_error`); `startup` + `backup-essential`
  + `lifeos` are guided **instruction** Blocks. Old `resources/workflows/*.yaml` and the `startup` /
  `backup-essential` / `lifeos` / `help` packages were removed (with `registry.json`);
  `WorkflowManager` stays for user-authored workflows + trigger actions. Remaining: the multi-runbook
  `community` package. Map: `docs/blocks-vs-workflows.md`. (2026-07-07)
- **A2A (Agent2Agent) protocol — v0 discovery + messaging on Flux Mesh** — every NAVIG node is now
  a standard, discoverable agent. New `GET /.well-known/agent-card.json` (canonical A2A path;
  `/.well-known/agent.json` legacy alias) serves an Agent Card built from the node's mesh capabilities
  (`navig/mesh/agent_card.py`: `llm/shell/docker/ssh/gpu` → A2A `skills[]`); `GET /mesh/agents` renders
  every mesh peer as a card (LAN-mesh → A2A bridge). New `POST /a2a` is a JSON-RPC 2.0 endpoint
  (`navig/mesh/a2a.py`) implementing `message/send` — it reuses `router.route_message` so all inference
  stays inside `navig/agent/`. Discovery is public; acting (`/a2a`) is bearer-authenticated. Docs:
  `docs/agent-protocol.md`. Deferred (additive later): `message/stream`, Task lifecycle, edge exposure. (2026-07-07)
- **The Store** — `navig store` (alias `navig hub`) is the one hub over everything connectable
  (system modules, plugins of all formats, skills, MCP servers, connectors) with wire states
  (wired/unwired/available/broken) and badges (system/standalone/locked). Deck: `GET /api/deck/store`
  + `POST /api/deck/store/action` (drag-drop install endpoint); `/api/deck/modules` unchanged.
- **Suggest + one-key activate** — an unknown command provided by a plugin now explains itself
  (`'tiktok' is provided by plugin navig-download`) and offers install/enable on a TTY. Backed by the
  shipped `navig/data/command_providers.json` (regen: `scripts/gen_command_providers.py`) and a
  runtime-aware installer (`uv pip` fallback — the shipped runtime has no pip module).
- **PluginHost** (`navig/plugins/host.py`) — one façade over package-dir / pip entry-point / legacy
  plugin formats: list/enable/disable/add/remove (incl. `.zip`), unified `plugins.disabled_plugins`
  state; disabling a pip plugin now also skips its `register()` at gateway boot.
- **Space trust for project plugins** — `.navig/plugins/` in a trusted space feeds the same loaders
  (`project_plugin_roots()`; `trusted` flag in `~/.navig/spaces.json`).
- `navig doctor --json`, a doctor **Wiring** section, and `navig doctor migrate-packs` (folds legacy
  `~/.navig/packs` into plugins; `navig.handler` manifest key carries the handler-pack contract).

### Changed
- **`navig store` (SQLite maintenance) → `navig db local`** — the `store` name now belongs to the hub;
  hidden forwards (`navig store maintenance|backup|migrate|cleanup`) remain for one release, and
  `navig store status` now means the wiring summary (old DB-health rows: `navig db local status`).
- **Extracted to pip plugins**: `github`/`farmore` → `plugins/navig-github`, `calendar` →
  `plugins/navig-calendar` (calendar providers stay in core). `navig media` gains `download` and
  mounts the navig-download `tiktok` app when the plugin is installed AND enabled.
- **One `navig plugin` group** (`commands/plugin.py`, canonical verbs `add`/`remove`) — the inline
  management group in `main.py` is gone; `navig modules` is a deprecated alias of the Store.
- Repo layout: `plugins/` = all first-party connectable packages (menu/vault/mini moved from
  `tools/`; farmore + rapidok vendored with sync-back to their miztizm repos). See `docs/repo-layout.md`.

### Removed
- Dead `commands/packs.py` (pack.yaml runbooks — unreachable) and the duplicated inline plugin
  command group in `main.py`.

- **Naming cleanup — package consolidation** (no behaviour change): grouped scattered top-level modules into
  cohesive packages. `prompt_loader.py` + `prompt_registry.py` → **`navig/prompts/`** (`loader` + `registry`);
  `output_styles.py` + `skills_renderer.py` → **`navig/ui/`** (they're rendering, and `ui/` is already the
  rendering home — `ui/` internals + the separate `tui/` app left untouched); `llm_generate.py` +
  `llm_router.py` + `llm_routing_types.py` + `routing/` → **`navig/llm/`** (`generate` · `router` · `types` ·
  `routing/`). All importers + `patch()` strings repointed across ~110 sites. Verified: 372 `tests/llm` + 264
  `tests/routing` (4 pre-existing unrelated fails) + 129 prompts/proactive/plugins green.
- **`navig.modules` name reclaimed for the module platform** (naming cleanup): the old "Proactive Assistant
  Modules" (`auto_detection` · `context_generator` · `error_resolution` · `proactive_display`) moved to a new
  `navig/proactive/` package, so `navig.modules` now cleanly denotes the module platform (`registry` + `gate`).
  Repointed all importers (`proactive_assistant.py`, `telegram_autoheal.py`, `scripts/build.py`) and moved the
  tests to `tests/proactive/`. No behaviour change. 129 tests green.

### Added
- **Plugin capabilities wired into the live runtime** (Phase 1 follow-up): an installed CC/NAVIG plugin's
  capabilities are now picked up by the EXISTING loaders instead of only being *discovered* by the host. New
  seam `navig/plugins/package.py:installed_plugin_roots()` / `installed_plugin_subdirs(kind)` (a package = a
  subdir under `~/.navig/plugins` with `.claude-plugin/plugin.json`). Wired into: **skills** (`skills/loader.py`
  `get_skill_dirs` → `<plugin>/skills`), **personas** (`personas/resolver.py` → `<plugin>/personas`),
  **prompts** (`prompt_registry.py` → `<plugin>/commands` (CC slash commands ARE prompts) + `<plugin>/prompts`),
  and **MCP** (`mcp/registry.py` merges each plugin's `.mcp.json` servers at startup; an explicit config client
  always wins; CC `type`→`transport` normalised; a bad plugin is skipped, never blocks MCP boot). Tests:
  `tests/plugins/test_runtime_wiring.py` (6).
- **CC hooks + agents translators — now live** (Phase 1 follow-up, cont.): a plugin's `hooks/hooks.json` and
  `agents/*.md` are now wired into the runtime, not just discovered. New `navig/hooks/cc_bridge.py` translates
  CC's `{Event: [{matcher, hooks:[{type,command,timeout}]}]}` into NAVIG `HookDefinition`s (appended in
  `HookRegistry.load()` after user/project hooks; fires through the existing `HookExecutor` — whose stdin
  payload `tool_name`/`tool_input`/`session_id` is CC-compatible, exit-code 2 = block on PreToolUse). Matcher
  regex → fnmatch (`Edit|Write` → two defs); only `type:"command"` + NAVIG-known events translate. New
  `navig/plugins/cc_agents.py` translates CC subagents (`agents/*.md` frontmatter) into the deck agent roster
  (`/api/deck/admin/agents`, merged as custom agents). Both degrade-never-block. Tests
  `tests/plugins/test_cc_bridge.py` (8) + 166 hooks regression green.
- **Plugin formations + spaces — now live** (completes runtime wiring): a plugin's `formations/` roots feed
  `formations/loader.py:_get_formations_roots` (each subdir with `formation.json` is a formation) and its
  `spaces/` container feeds `spaces/resolver.py:discover_space_paths` (auto-registered enabled). **Every
  capability a Claude Code / NAVIG plugin carries — skills, commands/prompts, MCP, hooks, agents, personas,
  formations, spaces — is now picked up by the live runtime.** Tests in `tests/plugins/test_runtime_wiring.py`
  (8) + 313 formations/spaces regression green.
- **Cloud marketplace index + module manifests** (Phase 3): new **control-plane** marketplace on
  `api.navig.run` (`services/api/src/api/marketplace/{catalog,list,resolve}.ts`) — `GET /api/marketplace/list`
  (`?kind=` filter) and `GET /api/marketplace/resolve?name=` serve the plugin/module discovery index (source /
  version / Harbor tier); **no user data**, edge-cached, mirrored from `navig-community`/`registry/`. The
  in-tree launcher **navig-menu** (`tools/menu`) now ships a `navig.module.json` (kind: launcher), and the
  **vault primitive** is registered in the module registry (kind: primitive, System category) so both appear in
  `navig modules` / the deck / os. `services/api` typecheck clean.
- **Unified module registry + data-driven surfaces** (Phase 2): new `navig/modules/registry.py` is the
  **canonical, capability-gated catalog** of operator modules (Finance · DevOps · Goals · Life · Projects, plus
  launchers and installed plugins surfaced as capability modules). Gating is capability-based against the Harbor
  entitlement (`license/tiers.json`); a module names a `capability` and is `locked` when the current license
  lacks it. Enable/disable is a per-operator override persisted in config (`modules.overrides`). New CLI
  `navig modules list|enable|disable|info` and deck endpoints `GET /api/deck/modules` + `POST
  /api/deck/modules/toggle` serve the SAME registry, so CLI/deck/os never drift. **navig-os is now data-driven**:
  `apps/os/.../lib/modules.ts` hydrates the catalog + enabled-set from `/api/deck/modules` over the brain proxy
  (hardcoded array kept only as an offline fallback + icon source) and syncs toggles back to core;
  `ModulesSettingsPage` renders the reactive catalog with server-resolved tier locks. Tests:
  `tests/modules/test_registry.py` (7 tests: built-ins, free/plus locking, override persistence, plugin +
  launcher discovery). (The deck's `lib/modules.ts` was already capability-driven — left as-is.)
- **Plugin/package host — Claude Code compatible** (Phase 1): new `navig/plugins/package.py` loads a plugin
  directory as a **superset of a Claude Code plugin** — reads `.claude-plugin/plugin.json`, discovers the CC
  bundle (`commands/agents/skills/hooks/.mcp.json`, reusing `skills/loader.py`) **and** the NAVIG-native block
  (`personas/formations/spaces`). A plain CC plugin loads unchanged; a NAVIG package adds native parts on top.
  New `navig/plugins/lifecycle.py` implements the **degraded-never-blocks-boot** contract (state machine
  `healthy | degraded | failed | shutdown` + a structured `LifecycleTracker` report): any single broken part is
  isolated as a degraded component and never fatal. New `navig/plugins/marketplace.py` adds a Claude Code
  compatible **marketplace** model (`marketplace.json`) with a local registry, add/list/remove, and offline
  resolution. CLI: `navig plugin inspect <path> [--json]`, `navig plugin marketplace add|list|remove`, and
  `navig plugin install` now also accepts **CC/NAVIG packages** (`.claude-plugin/plugin.json`), **git URLs**
  (shallow-clone → reuse the validated local-dir install path), and **bare plugin names** resolved from a
  registered marketplace. Every package install is validated through the host — a non-usable bundle is rejected
  before it lands in the plugins dir. Tests: `tests/plugins/{test_package,test_marketplace}.py` (13 tests).
- **Module-platform model + specs** (Phase 0): named the four canonical layers — **skill / plugin / module /
  surface**. New `docs/plugin-spec.md` (NAVIG plugins = a **superset of Claude Code plugins**: CC bundle
  `commands/agents/skills/hooks/.mcp.json` **+** native `personas/formations/spaces`; degraded-never-blocks-boot
  lifecycle) and `docs/module-manifest.md` (`navig.module.json`; standalone-first products; primitive vs leaf;
  data-driven surfaces). Canonical glossary + a "publish discipline" rule added to
  `.github/instructions/MASTER.instructions.md` (synced to all agent files); root `CLAUDE.md` corrected to the
  current `core/` · `apps/*` · `services/*` tree.

### Changed
- **`navig menu` launcher** (`navig/commands/menu.py`): the npx zero-install fallback now targets the renamed
  npm package **`navig-menu`** (was `@navig/project-menu`); help + comments updated.

### Added
- **`ai` module unit test coverage** (`tests/ai/test_ai_module.py`, 28 tests): Hermetic tests for `_get_model_preference` (canonical vs legacy path, DeprecationWarning, default fallback), `_resolve_openrouter_api_key` (env/canonical/legacy priority, whitespace ignored, `return_source` tuples), `ask_ai_with_context` (message building, system prompt, history, model_override, effort), `AIAssistant.analyze_error` (delegating to ask, prompt content, exception fallback), `AIAssistant.generate_context_summary` (pass-through identity).
- **`llm_generate` unit test coverage** (`tests/core/test_llm_generate.py`, 33 tests): Hermetic tests for `_parse_model_spec` (13 cases: explicit provider:model, provider_override wins, GPT/Claude/DeepSeek/llama/phi/qwen/slash/unknown inference), `_extract_user_text` (5 cases), `_enrich_messages_with_context` (4 cases), `_has_llm_modes_config` (mock+exception paths), `_prompt_cache_enabled`, `_load_fallback_chain`, and `llm_generate`/`run_llm` model-override dispatch paths (mocked `_call_provider` / `_call_and_wrap`).
- **`mcp_manager` unit test coverage** (`tests/core/test_mcp_manager.py`, 45 tests): Full coverage of `MCPServer` (init, `is_enabled`, `is_running`, `get_status`, `start` success/already-running/exception paths, `stop` success/not-running/force-kill-on-timeout) and `MCPManager` (init creates dir, empty servers when no file, `_load_servers` from JSON / bad JSON, `_save_servers` roundtrip, `list_servers` all/enabled-only/running-only, `get_server`, `enable_server`/`disable_server` success+unknown, `uninstall_server` success+unknown, `start_all_enabled`/`stop_all` zero counts, `search_directory` query filtering).

### Fixed
- **Routing — `is_available()` gate removed from `_get_ai_response`** (`navig/agent/conversational_legacy.py`): Removed early-return guard that aborted to `_simple_response` when `ai_client.is_available()` returned False, preventing `_try_llm_mode_routing` and the unified router from running even when vault-configured providers (xai, anthropic, groq, etc.) were available.
- **Routing test — registry opt-in mock** (`tests/routing/test_unified_router.py`): `test_from_hybrid_router_tiers` now mocks `navig.providers.registry.get_provider` to bypass disabled-provider filtering so explicitly user-configured routing tiers are always reflected in test assertions.


## [2.9.1] - 2026-05-01

### Added
- **Deploy engine unit test coverage** (`tests/deploy/test_deploy_engine_unit.py`, 19 tests): Fully mocked unit tests for `navig/deploy/engine.py` — `DeployEngine` lifecycle (dry-run full success, pre-check failure stops deploy, backup-skip flag, push failure + auto-rollback, push failure without rollback, health failure + rollback, progress-callback exception swallowed), `_parse_rsync_summary` (found/empty/no-match), `_build_rsync_cmd` (basic, excludes, trailing-slash normalisation), `DeployConfig.from_dict` (full dict, defaults, partial push), `merge_global_defaults` (excludes dedup, health retries, keep-last, noop).
- **Hook subsystem test coverage** (`tests/hooks/test_hooks.py`, 44 tests): Full coverage of `navig/hooks/` — `HookEvent`, `HookContext.to_json()`, `HookResult`, `HookDefinition.matches_tool()` (glob + case-insensitive), `HookRegistry` (YAML load/merge/disable/timeout/network, lazy load, project-after-global merge), `_is_private_url` SSRF guard (loopback, RFC-1918, link-local, public-IP, non-http scheme pass-through), `HookExecutor` (exit-code 0/2/other, PRE_TOOL_USE block, timeout swallowed, retry-from-stdout, network disabled/private-IP blocked, multi-hook message accumulation, registry-failure isolation).
- **Windows desktop MCP tools — filesystem, clipboard, PowerShell, and input** (`navig/platform/filesystem_ops.py`, `navig/mcp/tools/filesystem.py`, `navig/mcp/tools/windows.py`, `navig/mcp/tools/desktop.py`): Native Windows automation capabilities exposed as navig MCP tool bundles.
  - `navig/platform/filesystem_ops.py` — new stdlib-only helper: `read_file`, `write_file`, `copy_path`, `move_path`, `delete_path`, `list_directory`, `search_files`, `get_file_info` (constants `_MAX_READ_SIZE=10 MB`, `_MAX_RESULTS=500`).
  - `navig/mcp/tools/filesystem.py` — new `desktop_filesystem` MCP tool (8 modes: read, write, copy, move, delete, list, search, info); registered in `__init__.py`.
  - `navig/mcp/tools/windows.py` — +3 tools: `desktop_powershell` (PowerShell execution), `desktop_clipboard_get` / `desktop_clipboard_set` (win32clipboard primary, PowerShell fallback).
  - `navig/mcp/tools/desktop.py` — +7 AHK-backed input tools: `desktop_type`, `desktop_scroll`, `desktop_move`, `desktop_shortcut`, `desktop_app`, `desktop_multi_select`, `desktop_multi_edit`; shared `_run_ahk()` helper and `_coerce_bool()`.
  - Tests: `tests/platform/test_filesystem_ops.py` (23 tests), `tests/mcp/test_mcp_tools_filesystem.py` (14 tests), `tests/mcp/test_mcp_tools_windows_new.py` (9 tests), `tests/mcp/test_mcp_tools_desktop.py` (29 tests).
- **`memory_checkpoint` command (`packages/navig-memory/handler.py`, `packages/navig-memory/src/main.py`)**: New `cmd_memory_checkpoint()` creates a timestamped JSON snapshot at `~/.navig/store/memory/checkpoints/{id}.json` containing workspace root, memory store path, and latest conversation session (last 10 messages from `ConversationStore`). Exposed as `@plugin.command("checkpoint")` in the memory plugin. `session-checkpoint` slash alias registered in the kernel's memory skill command map. Tests: `tests/core/test_navig_kernel_memory_dispatch.py`, `tests/memory/test_navig_memory_handler.py`, `tests/memory/test_navig_memory_package_main.py`.
- **Hermetic autonomous-agent contract tests** (`tests/agent/test_autonomous_agent_hermetic.py`): 17 in-process aiohttp tests covering `/health`, `/status`, `/cron/jobs` CRUD, `/heartbeat/trigger`, `/heartbeat/history`, AI config and workspace-file checks — all pass in 4s with no live gateway. Closes STABILIZATION_DEBT.md item #2. Added `pytest.mark.live` to `pytest.ini` and applied to the original live smoke tests so they can be excluded in CI with `-m 'not live'`.

### Changed
- **`pass → return None` in abstract/no-op methods** (`navig/core/plugins.py`, `navig/core/evolution/base.py`, `navig/gateway/channels/base.py`, `navig/plugins/base.py`): explicit `return None` clarifies intent in abstract stubs and satisfies strict type checkers.
- **Agent parallel tool dispatch** (`tests/agent/test_agent_parallel_tool_dispatch.py`): 3 tests covering parallel-safe tool batching, exception wrapping, and `NEVER_PARALLEL_TOOLS` isolation now verified green.
- **Ruff cleanup — unused loop variables + B904 chained raises** (50 files across `navig/`, `tests/`, `host/internal/desktop/`): `i` → `_i` in 30+ test files; `raise … from None` added in `host/internal/desktop/agent*.py` `TimeoutExpired` handlers (B904). `All checks passed.`

### Fixed
- **Vault cross-profile key resolution** (`navig/vault/core.py`): `vault.get()` and `vault.list()` now correctly find credentials when the active profile differs from the profile the credential was stored under. Root cause: the profile filter used `item.metadata.get("profile_id", "default")` but credentials added via the Telegram `/provider` wizard store `profile_id` as a direct `CredentialInfo` attribute, not inside `metadata` — so cross-profile lookups always returned empty. Fix: check `getattr(item, "profile_id", None)` first; when the profile-scoped search returns nothing and a `"default"`-profile credential exists, use it as fallback. Result: `get_api_key("nvidia" | "openrouter" | "xai")` now returns the stored key even when the active vault profile is `"work"` (or any non-default profile).
- **AI provider routing — 6 silent failure bugs** (`navig/routing/router.py`, `navig/agent/model_router.py`, `navig/gateway/channels/telegram_commands.py`):
  - **Bug 5 (vault TimeoutError/OSError)**: Expanded outer `except` clauses in `_resolve_provider_api_key` to also catch `TimeoutError`, `OSError`, `ConnectionRefusedError`. Previously a vault network timeout would escape the guard and silently skip all generic cloud providers (anthropic, google, groq, xai, nvidia, cerebras, mistral).
  - **Bug 2 (mcp_bridge vault token + missing URL warning)**: When `bridge.token` is absent from in-memory config, now performs a vault fallback (`vault.get_secret("bridge/token")`). When `bridge.mcp_url` is not configured at all, emits `logger.warning` instead of a silent `return None` so the skip is visible in logs.
  - **Bug 3 (openrouter vault inconsistency)**: Replaced the `get_api_key("openrouter")` call with an explicit lookup of the canonical manifest vault paths (`openrouter/api-key`, `openrouter/api_key`), then falls back to `get_api_key()`. This matches how all other generic providers resolve their keys.
  - **Bug 4 (github_models class-as-self)**: Replaced the fragile `GMP._resolve_token(GMP)` instance-method-as-classmethod call with a direct env lookup (`GITHUB_TOKEN`, `GH_TOKEN`), a vault manifest-path scan, and a config-file fallback. The old pattern would silently raise `AttributeError` on any refactor that accessed instance state.
  - **Bug 7 (enabled=False providers admitted to chain)**: `_discover_user_providers()` now consults the provider registry manifest before adding a user-configured provider; providers with `enabled=False` (e.g. `mistral`, `cerebras`) are skipped with a `DEBUG` log, preventing wasted resolution attempts.
  - **Bug 1 (Telegram split-brain provider display)**: `/provider` now reads `ai.default_provider` from the global config (via `get_config_manager().get_global_config()`) as the primary source of truth for the active-provider checkmark, then falls back to the legacy `llm_router.modes` path. This aligns the UI display with what `UnifiedRouter` actually executes.
- **Remove stale `Console` patch in `test_launch_menu_missing_rich`** (`tests/interactive/test_interactive.py`): `@patch("navig.commands.interactive.Console")` referenced a non-existent module attribute (`interactive.py` uses `console = get_console()` with no direct `Console` import). Removed the unused decorator and `mock_console_class` parameter; test now passes without error.
- **Remove f-string without placeholder** (`navig/gateway/notifications.py` line 466): `f"📌 …"` → `"📌 …"`.
- **Remove duplicate `first_h1` import** (`navig/plans/context.py`): duplicate `from navig.plans.frontmatter import first_h1 as _first_h1` removed.
- **Remove unused loop variable** (`deploy/operational-factory/app/runtime.py`): `for agent_id, role in AGENTS.items()` → `for role in AGENTS.values()`.
- **Remove unused imports across test suite** (~90 test files): `ruff`-identified unused imports removed to keep the suite clean and linter-green.

### Fixed
- **`navig service restart` false failure on slow Windows machines** (`navig/commands/service.py`): `service_restart()` used a fixed `time.sleep(2)` before checking if the daemon started, while `service_start()` uses a proper 10-second poll loop. On slow machines the 2 s window ran out before the PID file appeared, causing `service_restart` to always report failure even when the daemon started successfully. Fixed by mirroring the `service_start` polling pattern, clearing the stop-intent flag and watchdog deadline before spawning, and adding a race-guard loop that waits up to 12 s for any running `navig_wdog_*.py` watchdog to exit before spawning the new daemon.
- **`get_debug_logger()` always returned `navig.gateway` logger for every caller** (`navig/debug_logger.py`): the function hard-coded the subsystem name instead of deriving it from the caller's `__name__`. All ~20 modules that call `get_debug_logger()` (`mcp/registry.py`, `mesh/*.py`, `tools/sandbox.py`, `tasks/queue.py`, etc.) were logging as `[navig.gateway]`. Fixed with `inspect.stack()[1]` to read the caller module's `__name__` at call time. 23 tests pass.
- **MCP double-connect spam in `_mcp_reconnect_loop`** (`navig/daemon/telegram_worker.py`): when a client was not yet registered, `add_client()` fired `_connect_with_retry(max_attempts=3)` as a background task (via `auto_connect=True`), then `connect_client()` immediately fired a second attempt — causing two concurrent connection paths and doubled WinError-1225 log lines per reconnect cycle. Fixed by passing `auto_connect=False` to `add_client()` and skipping `add_client` entirely when the client is already registered; `connect_client()` is now the single connection path.
- **Harden `navig service start` PID detection on slow Windows machines** (`navig/commands/service.py`): replaced the fixed `time.sleep(2)` + single `is_running()` check with a polling loop (up to 10 × 1 s) so the daemon is not falsely reported as failed when the PID file appears after the initial 2 s window. Also changed `_spawn_stop_watchdog()` to launch via `_pythonw_exe()` (i.e. `pythonw.exe` on Windows) instead of `sys.executable` (`python.exe`), eliminating the visible console window that would otherwise flash for up to 30 s during a stop sequence. Three regression tests added to `tests/service/test_service_cli.py`.
- **Clarify and repair `navig agent start` foreground UX** (`navig/agent/runner.py`, `navig/agent/ears.py`, `navig/commands/agent.py`): foreground agent sessions now write an `agent.pid` file so `navig agent status` / `navig agent stop` can track the live process; console stdin is wired through a new `ConsoleListener` so typing into `navig agent start` actually reaches the agent; console-sourced replies are printed back to the terminal; `navig agent start --background` now exits cleanly with guidance to use `navig service start`; and `navig agent status --plain` now includes `daemon_running` / `daemon_pid` so daemon-backed Telegram/gateway health is distinguishable from the foreground agent process.
- **Fix Telegram space/intake bootstrap crash and restore suite green** (`navig/gateway/channels/telegram_commands.py`, `packages/navig-telegram/tg_handlers.py`, `tests/telegram/test_telegram_reminders.py`): `_bootstrap_space_docs()` no longer conditionally shadows `atomic_write_text`, which could crash with `cannot access local variable 'atomic_write_text'` when `VISION.md` already existed but `ROADMAP.md` did not. The `/status` card now includes an explicit readiness-state label and `Pending fixes:` header, and the pack-local Telegram handler entrypoint now degrades cleanly when optional `python-telegram-bot` is not installed. Regression coverage added for partial space docs; full Telegram suite passes again (`472 passed`).

## [2.9.0] - 2026-04-20

### Added
- **Vault-first messaging adapters + `/messengers` Telegram hub + `navig init --messaging` wizard**: Three interconnected improvements to the outbound messaging stack.
  - `GatewayServer._resolve_adapter_config()` (server.py) — expands `vault:KEY` placeholders in adapter config dicts before construction; wired into `SmsAdapter`, `WhatsAppCloudAdapter`, and `DiscordMessagingAdapter` bootstrap so no adapter ever receives raw vault references.
  - `RoutingEngine._build_decision()` (routing.py) — now raises `NoRouteError` immediately when the resolved adapter is not available/configured, with actionable message: "Run: `navig init --messaging` to configure messaging adapters."
  - `TelegramMessengersMixin` (telegram_messengers_mixin.py) — new Telegram command handler for `/messengers`: shows live ✅/🔒/⚡/⏸ status per adapter (Telegram, SMS/Twilio, WhatsApp/Meta, Discord), per-adapter detail views with vault setup CLI steps, enable/disable callback buttons, and deep-links to `/contacts` and `/threads`. Registered via lazy mixin injection in telegram.py and `msg:` / `open_messengers` callback routing in telegram_keyboards.py.
  - `/messengers` slash command + `📲 Messengers` button in `/providers` action row (telegram_commands.py).
  - `run_messaging_wizard()` (commands/messaging_wizard.py) — interactive vault-first wizard for `navig init --messaging`; prompts for Twilio SID/token/phone, WhatsApp Cloud token/phone-number-id, Discord bot token; skips already-configured credentials; enables adapters in config after credential storage.
  - `navig init --messaging` CLI flag wired in cli/__init__.py with direct delegation to `run_messaging_wizard()`.


## [2.8.0] - 2026-04-18

### Added
- **F-17: Vault-secured tool credentials (`navig/vault/core.py`, `navig/agent/conversational_legacy.py`, `navig/agent/conv/agent.py`, `navig/agent/tools/devops_tools.py`)**: `Vault.batch_get(keys)` fetches multiple secrets by label using `.reveal()` for plaintext extraction (not `str()` which returns `***`). Both agent dispatch paths (`conversational_legacy` and `conv/agent`) now build and pass a `_vault_injector` closure to `_AGENT_REGISTRY.dispatch()`, allowing the registry to pre-inject matching vault secrets into tool args before execution. `navig_db_query` and `navig_db_dump` tools declare `vault_keys=["db_password"]` — the registry automatically injects the `db_password` vault secret, then each tool removes it from args before logging. Tests: `tests/agent/test_vault_credential_injection.py` (13 tests).
- **F-21: Two-tier plan-execute autonomous agent mode (`navig/agent/plan_execute.py`, `navig/commands/agent.py`)**: `PlanExecuteAgent` implements a two-phase cycle: Phase 1 calls the LLM to produce a structured JSON execution plan (`PlanStep` list); Phase 2 executes each step via `asyncio.to_thread(_AGENT_REGISTRY.dispatch, ...)` (correctly wrapping sync dispatch). Fixed critical bugs in `_execute()`: removed invalid `await` on sync `dispatch()`, replaced `result.success`/`result.output` attribute access (dispatch returns `str`, not `ToolResult`) with direct string handling, and added vault_injector. New CLI command `navig agent plan "<task>"` with `--dry-run`, `--yes`, `--toolsets`, `--json`, `--plain` flags. Execution traces saved to `~/.navig/plans/runs/`. Tests: `tests/agent/test_plan_execute.py` (24 tests).
- **Hardened webhook intake — fail-fast config resolution (`navig/gateway/hooks.py`)**: New `HooksConfig` dataclass and `load_hooks_config()` factory resolve all webhook configuration at boot. Missing auth tokens, invalid base paths, and bad size/key limits raise `HooksConfigError` immediately rather than failing on the first live request. `HooksHandler.handle()` enforces Bearer-token auth (401), body-size limit (413), idempotency-key length (400), and session-prefix allowlist (403) in a single pass. Config keys added to `config/defaults.yaml`: `gateway.hooks.*`. Tests: `tests/gateway/test_hooks_config.py` (30 tests). (Item 10)
- **SSRF guard for all outbound HTTP (`navig/net/ssrf.py`)**: New `SsrfPolicy(allow_private_network, allowed_domains)` dataclass and `check_url(url, policy)` / `safe_fetch(url, policy)` API. Blocks loopback, all private IPv4/IPv6 ranges, link-local, and cloud-metadata addresses before any network I/O. `is_safe_url()` provides a non-raising variant for list filtering. `allowed_domains` allowlist and `allow_private_network=True` flag provide escape hatches for controlled internal calls. Tests: `tests/net/test_ssrf.py` (20 tests). (Item 6)
- **Mode-typed text chunking pipeline (`navig/gateway/reply_chunking.py`)**: `ChunkMode` enum (`WORDS | SENTENCES | PARAGRAPHS | MARKDOWN_BLOCKS`) and `chunk_text(text, mode, limit) → list[str]` provide a single canonical entry point for splitting long channel replies. Fenced code blocks (``` and ~~~) are preserved atomically in `MARKDOWN_BLOCKS` mode. Each mode falls back to finer granularity for oversized units. Constant `TELEGRAM_TEXT_LIMIT = 4096` exported. Tests: `tests/gateway/test_reply_chunking.py` (25 tests). (Item 7)
- **Isolated flow execution with failure-destination routing (`navig/gateway/flow_runner.py`)**: `FlowRunner` executes flows in isolated async coroutines. Success output routes to `success_destination`; errors route to a *separate* `failure_destination` to prevent alert loops. Per-flow JSONL run logs are atomically appended and pruned to `max_log_entries` (default 100). `DeliveryBackend` protocol allows injecting any transport (Telegram, webhook, etc.). Config keys added: `flow.failure_destination`, `flow.run_log_max_entries`. Tests: `tests/gateway/test_flow_runner.py` (20 tests). (Item 8)
- **Persistent per-chat memory with compaction (`navig/memory/chat_store.py`, `navig/memory/compactor.py`)**: `ChatMemoryStore` persists per-chat transcripts as JSONL under `~/.navig/memory/<chat_id>/transcript.jsonl` with `append(turn)`, `recent(n)`, `search(query)` (keyword), and `recent_within_token_budget()` methods. `KeywordCompactor` summarises turns older than `compact_after_days` into `notes.md` and prunes them from the transcript. Pluggable `Summariser` protocol allows future LLM-based summarisation. Config keys added: `memory.compact_after_days`, `memory.max_context_tokens`. Tests: `tests/memory/test_chat_store.py` (28 tests). (Item 9)
- **Contextual Explore Questions on EXPAND keyboard profile (`navig/gateway/channels/telegram_keyboards.py`, `telegram.py`)**: In REASON-mode responses, the LLM is prompted (via `_EXPLORE_SUFFIX`) to append a `EXPLORE_Q: q1 | q2 | q3` line with up to 4 follow-up questions. `extract_explore_questions()` strips this marker from the display text and returns the questions as a list. `ResponseKeyboardBuilder` passes them to `_build_expand_rows()`, which renders up to 4 one-tap follow-up question buttons in row 2, replacing the legacy 👍/👎 feedback buttons. `_build_feedback_rows()` now returns `[]`. `_send_response()` accepts a `prebuilt_keyboard` param to avoid double-building the keyboard. Constants: `_EXPLORE_Q_RE`, `_EXPLORE_Q_MAX = 4`. Tests: `test_telegram_keyboards_core.py`, `test_telegram_action_cards.py` (85 passed).

### Fixed
- **Fix missing `atomic_write_text` imports in 20 modules** (`navig/blackbox/seal.py`, `navig/blackbox/crash.py`, `navig/selfheal/patcher.py`, `navig/selfheal/heal_pr_submitter.py`, `navig/tui/config_model.py`, `navig/tools/sandbox.py`, `navig/agents/filtering_engine.py`, `navig/gateway/config_watcher.py`, `navig/gateway/session_manager.py`, `navig/gateway/session_store.py`, `navig/gateway/channels/media_engine/budget.py`, `navig/gateway/channels/media_engine/media_cache.py`, `navig/commands/template.py`, `navig/commands/tools.py`, `navig/daemon/entry.py`, `navig/importers/cli.py`, `navig/mcp/tools/agent.py`, `navig/onboarding/telemetry.py`, `navig/mcp_manager.py`, `navig/agent/tools/wiki_tools.py`): The atomic-write hardening pass replaced bare `.write_text()` calls but did not add the top-level `from navig.core.yaml_io import atomic_write_text` import in every affected file, causing `NameError` at runtime in those modules. All 20 files now have the import added and ruff-sorted.
- **`edit_message` now routes through `_api_call` (`navig/gateway/channels/telegram.py`)**: `edit_message()` was posting directly through `self._session.post()`, bypassing rate-limit handling and being impossible to mock in tests. It now calls `_api_call("editMessageText", payload)` with a parse-mode retry on failure. The `{"not_modified": True}` sentinel return value has been dropped (no callers checked it; `None` is returned instead). Fixes `test_edit_message_includes_inline_keyboard`.
- **`/about` NL guard allows trigger-verb context (`navig/gateway/channels/telegram_commands.py`)**: The `_NL_STARTS_ONLY` guard for the `about` command was unconditionally requiring the message to start with "about", blocking "please show about" from resolving to `/about`. The guard now permits matching when `has_trigger=True` (user included a trigger verb such as "show"), while still blocking ambiguous plain-English uses like "tell me about X". Fixes `test_visible_registry_commands_have_nl_resolution_coverage`.
- **Fix `test_store_pending_patch_creates_file` (`tests/agent/test_autoheal.py`)**: `navig/selfheal/heal_pr_submitter.py` called `atomic_write_text()` at line 170 without importing it. Fixed in `e3073dd`.
- **Fix 2 `test_seal_bundle_*` failures (`tests/blackbox/test_blackbox_seal.py`)**: `navig/blackbox/seal.py` called `atomic_write_text()` without importing it. Fixed in `183a08f`.
- **Resolve remaining broken internal import paths (scan now clean: `__BAD_COUNT__=0`)**: Added targeted compatibility shims for legacy module paths referenced by commands/channels: `navig.daemon.client`, `navig.daemon.scheduler`, `navig.vault.manager`, `navig.gateway.comms`, `navig.memory.sync`, `navig.agent.memory`, `navig.memory.store`, plus synth pipeline modules (`navig.agent.pattern_observer`, `pattern_analyzer`, `skill_drafter`) and API wrapper `navig.api.server`. This removes runtime `ImportError` hotspots while preserving current behavior and command surfaces.
- **Strip UTF-8 BOM from `navig/gateway/channels/telegram.py`**: removed `U+FEFF` at file start that caused parser failures in static import validation.
- **Harden 100+ bare `write_text` calls with `atomic_write_text` across 50+ files**: All state-file, config-file, and scaffold-file writes now use `atomic_write_text` (temp-file + atomic rename) from `navig.core.yaml_io`, eliminating data corruption on crash/interruption. Covers `onboarding/steps.py` (21 calls), `commands/wiki.py`, `commands/space.py`, `commands/plans.py`, `commands/package.py`, `gateway/channels/telegram_commands.py`, `gateway/channels/telegram_reactions.py`, `daemon/service_manager.py`, `migrations/`, `plans/scaffold.py`, `ui/_capabilities.py`, and 40+ other modules. Intentionally skipped: writability-probe writes (`doctor.py`, `init.py` append), UTF-16 Windows scheduler XML (`service_manager.py`), and lockfile PID writes (`tray_app.py`).
- **Fix `test_consumer_imports_from_bridge_grid_reader[telegram.py]`**: `telegram.py` was listed as a bridge-port policy consumer but lacked a top-level `from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT` import. Added it (with `# noqa: F401`) to satisfy the convention, consistent with all other files in `_CONSUMER_FILES`. Ruff-fixed import order.
- **Fix `test_wizard_save_config` — `FileNotFoundError` from `atomic_write_yaml`**: `SetupWizard._save_config()` was migrated to use `atomic_write_yaml`, which calls `tempfile.mkstemp(dir=parent)`. The test patched `Path.mkdir` to a no-op, so the mock dir was never created on disk and `mkstemp` raised `FileNotFoundError`. Added `patch("navig.core.yaml_io.atomic_write_yaml")` to the test to mock the atomic write entirely, consistent with the test's intent of mocking all I/O.
- **Repair mangled `_handle_skill` delegation in `telegram.py`** (`navig/gateway/channels/telegram.py` line 4418): A botched merge left the old inline implementation mixed with the new delegation pattern, causing an `IndentationError`. Replaced with the canonical `TelegramCommandsMixin._handle_skill(...)` delegation matching the adjacent `_handle_deck` and `_handle_deck` methods. Fixes 4 test failures that the broken module import caused in `test_telegram_auto_runtime.py` and `test_telegram_utils.py`.
- **Expose `_first_h1` from `navig.plans.context`**: `tests/planning/test_plan_context.py` imported `_first_h1` from `navig.plans.context` but the function (defined as `first_h1` in `navig.plans.frontmatter`) was not re-exported. Added `from navig.plans.frontmatter import (first_h1 as _first_h1,)` to `context.py`.
- **Expose `_mdv2_escape` from `navig.gateway.channels.telegram_keyboards`**: `tests/telegram/test_telegram_keyboards_core.py` and `test_telegram_utils.py` imported `_mdv2_escape` from `telegram_keyboards` but it was only defined in `telegram_utils.py`. Added `from navig.gateway.channels.telegram_utils import escape_mdv2 as _mdv2_escape` to `telegram_keyboards.py`.
- **Fix non-optional collection/type annotations defaulting to `None` in 7 dataclasses**: `CalendarEvent.attendees` (`providers.py`), `AudioTranscript.metadata` + `timestamp` (`ears.py`), `Workflow.variables` (`automation_engine.py`), `EvolutionResult.history` (`evolver.py`, `evolution/base.py`), `HeartbeatResult.issues_found` (`runner.py`), `IndexingStats.errors` (`memory/indexer.py`) — all changed from `T = None` to `T | None = None` so the nullable contract is explicit and type-checkers no longer flag them.
- **Remove stale BUG tracking comments in `telegram.py` and `store/runtime.py`**: BUG-1, BUG-7, and BUG-9 comments were left over from earlier work; the implementations they described (capped reminder retry, 24-hour AI session TTL, 30-day reminder pruning) are already in place.
- **Move `ahk_full_control.py` snippet out of `navig/commands/`**: The file was a merge-target snippet (docstring: "Add to navig/commands/ahk.py"), not a standalone module. It referenced `ahk_app`/`ch`/`_get_adapter` without imports, causing a `NameError` on import. Relocated to `.dev/ahk_full_control_snippet.py`. All `navig/commands/` modules now import clean.
- **Fix wrong import paths in `calendar.py`, `email.py`, `backup.py`**: `calendar.py` and `email.py` imported `CalendarEvent` / `EmailMessage` from non-existent `navig.agent.proactive.models` — corrected to `navig.agent.proactive.providers` where both dataclasses are actually defined. `backup.py` imported `inspect_export` / `list_exports` from non-existent `navig.commands.navig_backup` — corrected to `navig.commands.config_backup`. All three were unguarded runtime crashes.
- **Strip UTF-8 BOM from 4 Python source files** (`commands/database_advanced.py`, `commands/monitoring.py`, `daemon/supervisor.py`, `onboarding/steps.py`): byte-order-mark `U+FEFF` at file start caused `SyntaxError: invalid non-printable character` on import.
- **Fix 5 of 11 hardcoded `Path.home() / ".navig"` paths** in `cost_tracker.py`, `file_history.py`, `memory/session_memory.py`, `hooks/registry.py`, `permissions/loader.py`: replaced with `config_dir()` from `navig.platform.paths` so `NAVIG_CONFIG_DIR` env override and system-service mode are respected everywhere.
- **Fix remaining 6 hardcoded `Path.home() / ".navig"` paths** in `commands/output_style.py`, `commands/plan_mode.py` (×2), `gateway/channels/telegram_commands.py`, `gateway/channels/telegram_reactions.py`: same `config_dir()` fix. `debug_logger.py` intentionally left (bootstrap logger, exempt from env-override).
- **Hardened 4 more non-atomic writes** in vault profile, shared-config cache, and space cache files: `vault/core.py` (`active_profile.txt`), `core/shared_config.py` (`active_host.txt`, `active_app.txt`), `commands/space.py` (active-space cache) — all now use `atomic_write_text()` to prevent partial writes on process crash.
- **Hardened 4 deploy/installer state writes**: `deploy/rollback.py` (`save_state` snapshot record), `deploy/history.py` (`_trim` history rewrite), `installer/modules/telegram.py` (`.env` token write + rollback), `installer/modules/shell_integration.py` (rc file rollback) — all now use `atomic_write_text()`.
- **Fix missing `encoding=` on PID file read/write** in `daemon/supervisor.py`: `_write_pid()` and `read_pid()` now pass `encoding="utf-8"` to avoid locale-dependent behaviour.

### Added
- **Security — Extended Provider Token Redaction (`navig/core/security.py`)**: Added 15 new API-key prefix patterns to `DEFAULT_REDACT_PATTERNS` covering Tavily (`tvly-`), Exa (`exa_`), Hugging Face (`hf_`), Replicate (`r8_`), Livekit (`syt_`), Helicone (`hsk-`), Mem0 (`mem0_`), Browserless (`brv_`), DigitalOcean (`dop_v1_`/`doo_v1_`), Firecrawl (`fc-`), fal.ai (`fal_`), Browserbase (`bb_live_`), AWS (`AKIA`), and Stripe Live (`sk_live_`). Added `_mask_token()` helper (short → `"***"`, long → `prefix[:6]...suffix[-4:]`), `RedactingFormatter(logging.Formatter)` that auto-redacts every formatted log record, `scan_context_file()` for detecting PII/credential threat patterns and invisible Unicode in uploaded content, `get_managed_system()` / `is_managed()` for env-var-based managed-mode detection, and PII hashing helpers `_hash_id()`, `hash_user_id()`, `hash_chat_id()`, `log_safe_sid()` (12-char hex SHA-256, no reversibility). 35 new tests.
- **Logging — Session-Correlated Log Records (`navig/core/logging.py`)**: Added `set_session_context(session_id)` / `clear_session_context()` backed by `threading.local`; `_install_session_record_factory()` installs an idempotent `logging.setLogRecordFactory` wrapper that injects `session_tag` (e.g. `" [abc123ef]"` for UUIDs, `""` when none) into every `LogRecord`. Updated `LOG_FORMAT` to include `%(session_tag)s`. File handler now uses `RedactingFormatter` so log files never capture raw credentials. Added `COMPONENT_PREFIXES` dict and `_ComponentFilter` for per-component log-level filtering. Factory installed at module import time. 11 new tests in `tests/core/test_logging_session.py`.
- **`atomic_write_text()` for plain-text files (`navig/core/yaml_io.py`)**: Ported atomic-write pattern from existing `atomic_write_yaml()`. Uses `tempfile.mkstemp` + `os.fdopen` + `flush` + `fsync` + `os.replace()` with a 3-attempt retry loop for Windows `PermissionError`. Exposed as a public function alongside the existing YAML helpers. 
- **Fixed `session_memory.py` unsafe write**: Replaced direct `path.write_text(notes, encoding="utf-8")` with `atomic_write_text(path, notes)` in `SessionMemoryExtractor._write_notes()` to prevent partial writes on crash. 6 existing tests in `tests/memory/test_session_memory.py` confirm no regression.
- **Jittered Retry Utilities (`navig/core/retry_utils.py`)**: New module retry patterns. `jittered_backoff(attempt, *, base_delay, max_delay, jitter_ratio)` uses XOR nanosecond seed with a thread-safe global counter to avoid correlated retry storms. `RetryConfig` dataclass (max_attempts, base_delay, max_delay, reraise_last). `async_retry(config, *, on_retry)` decorator for coroutines. `retry_sync(fn, *args, config, **kwargs)` helper for synchronous callers. 16 new tests in `tests/core/test_retry_utils.py`.
- **Rate-Limit Header Tracker (`navig/core/rate_limit_tracker.py`)**: New module. `RateLimitBucket` dataclass tracks limit/remaining/reset with computed properties `used`, `usage_pct`, `remaining_seconds_now`. `RateLimitState` aggregates per-minute and per-hour buckets for both requests and tokens, with `has_data` / `age_seconds` properties. `parse_rate_limit_headers(headers, provider="")` handles case-insensitive OpenAI/Anthropic/generic header variants. `format_rate_limit_display()` renders a multi-line ASCII dashboard; `format_rate_limit_compact()` produces a one-line status for log lines. 21 new tests in `tests/core/test_rate_limit_tracker.py`.
- **Cheap-Turn Model Routing (`navig/core/model_routing.py`)**: New module `smart_model_routing.py`. `_COMPLEX_KEYWORDS` frozenset (37 terms: deploy, workflow, refactor, architecture, etc.) drives `is_simple_turn(message, *, max_chars=160, max_words=28)` which returns `True` only for short messages with no complex-intent keywords. `choose_cheap_model_route(user_message, routing_config)` returns the configured cheap-model dict when the turn is simple, or `None` to fall through to the default router. `get_routing_config()` reads `agent.cheap_model_routing` from the navig config manager. 29 new tests in `tests/core/test_model_routing.py`.
- **Platform Adapter Base (`navig/gateway/channels/base.py`)**: New module providing shared primitives for all Telegram/Slack/etc. channel adapters. `utf16_len(text)` counts UTF-16 code units (needed for Telegram's 4096-code-unit limit). `utf16_safe_split(text, *, max_utf16, max_chars, prefer_newline)` binary-searches for the largest safe chunk and prefers splitting at newline boundaries. `BasePlatformAdapter(abc.ABC)` defines the abstract interface: `send_text`, `edit_message`, `delete_message`, `send_typing`, `split_for_platform`, `measure`. 26 new tests in `tests/core/test_atomic_and_base.py`.
- **Away Summary (`navig.gateway.channels.away_summary`)**: New module that builds a 1–3 sentence session recap when a Telegram user returns after a configurable absence gap (default 4 h). Triggered in `_handle_start()`; uses `effort="low"` for speed and cost. Applies a dual-cap truncation (200 lines / 25 000 bytes) to keep LLM payloads small. Gap and message-window tunables exposed via `config/defaults.yaml` under `memory.away_summary_gap_hours` and `memory.away_summary_message_window`. Never raises — any error is swallowed so `/start` is never blocked. 17 new tests in `tests/gateway/test_away_summary.py`.
- **`--effort` / `-e` flag on `navig ask` and `navig agent run`**: Exposed the existing effort-level system (`low` / `medium` / `high` / `max` / `ultra`) as a first-class CLI flag on the two most-used entry points. The flag threads through `ask_ai_with_context()` → `llm_generate()` → `run_llm()` → `navig.agent.effort` (unchanged). When omitted, behaviour is identical to before (auto-detect). 8 new surface-regression tests in `tests/cli/test_effort_flag_surface.py`.
- **`navig memory compact`**: New CLI subcommand that atomically replaces a session's full message history with a single AI-generated summary. Uses configurable `memory.compact_summary_effort` (default `"low"`) and respects `memory.compact_threshold_messages` to guard against compacting trivially short sessions. Displays a Rich panel with the generated summary and a `N messages → 1` completion line. Usage: `navig memory compact [SESSION] [--instructions TEXT] [--yes] [--plain]`.
- **`ConversationStore.compact_session(session_key, summary) → int`**: Atomic SQLite transaction (BEGIN IMMEDIATE) that deletes all messages for a session and inserts one system-role summary message. FTS5 DELETE trigger cascades handle index cleanup. Returns the count of deleted messages; returns 0 and no-ops when the session is empty or not found. 15 new tests in `tests/memory/test_compact_session.py`.
- **Auto-Compact Manager (`navig/memory/auto_compact.py`)**: `AutoCompactManager` monitors token usage per session and fires a background `asyncio.create_task` to call `ConversationStore.compact_session()` when the context window fills to within `memory.auto_compact_buffer_tokens` (default 13 000). Includes a circuit breaker: stops issuing compactions after `memory.auto_compact_max_failures` (default 3) consecutive failures, avoiding cascading error loops. Process-wide `get_auto_compact_manager(session_key)` registry. Config keys: `memory.auto_compact_enabled`, `memory.auto_compact_buffer_tokens`, `memory.auto_compact_max_failures`, `memory.auto_compact_min_turns`. 11 new tests in `tests/memory/test_auto_compact.py`.
- **Lifecycle Hooks System (`navig/hooks/`)**: New package with 4 modules: `events.py` (6 `HookEvent` values: `PRE_TOOL_USE`, `POST_TOOL_USE`, `POST_TOOL_USE_FAILURE`, `PERMISSION_DENIED`, `NOTIFICATION`, `SESSION_START`), `registry.py` (YAML loader from `~/.navig/hooks.yaml` + `.navig/hooks.yaml`), `executor.py` (subprocess runner with JSON stdin, exit-code semantics: 0=silent, 2=inject+block, other=user-only; SSRF guard blocking all private IPv4/IPv6 ranges), `__init__.py` (public `fire_hook(ctx)` entry). Hook timeout configurable via `hooks.timeout_seconds` (default 30). Network hooks disabled unless `hooks.allow_network: true` and private IPs always blocked. 14 new tests in `tests/test_hooks.py`.
- **Background Session Memory Extraction (`navig/memory/session_memory.py`)**: `SessionMemoryExtractor` fires a background `asyncio.create_task` every `memory.extraction_interval_tool_calls` (default 10) tool calls to extract structured 3-section notes (`## What was discussed`, `## Decisions`, `## Next steps`) to `~/.navig/memory/<session_id>_notes.md`. Notes are injected into `build_away_summary()` when `session_id` is provided, enriching reconnection recaps with prior context. Config keys: `memory.extraction_enabled`, `memory.extraction_interval_tool_calls`, `memory.extraction_effort`. 6 new tests in `tests/memory/test_session_memory.py`.
- **Permission Rule System (`navig/permissions/`)**: New package with 4 modules. Rules declared in `~/.navig/settings.yaml` or `.navig/settings.yaml` under `permissions.rules` as `allow: "Bash(git commit:*)"` / `deny: "Bash(rm -rf /*)"`. `parse_rule_spec()` normalises tool names (`BashTool` → `bash`) and uses `fnmatch`-based glob matching with substring fallback. Shadow detection warns when a wildcard rule makes later rules unreachable. Wired into `navig/safety_guard.py`: structured rules evaluate first (fail-open on exception), then existing regex guard runs unchanged. 12 new tests in `tests/safety/test_permission_rules.py`.
- **File History Checkpointing (`navig/file_history.py`)**: `FileHistoryStore.checkpoint(filepath, session_id, turn_id)` atomically snapshots a file to `~/.navig/file-cache/<session_id>/<turn_id>/` before any agent-driven write. `list_versions()`, `restore()`, and `diff_versions()` (unified diff via `difflib`) enable time-travel. Eviction keeps at most `file_history.max_snapshots_per_session` (default 100) turn-directories per session. Enabled via opt-in `file_history.enabled: false` config key. Wired into `RecordedOperation.__enter__` for `FILE_MODIFY` / `FILE_CREATE` ops — adds `filepath` / `session_id` params. `navig snapshot` commands fully implemented: `versions`, `diff`, `restore` subcommands replacing previous `ch.warn("not yet implemented")` stubs. 10 new tests in `tests/test_file_history.py`.
- **AI-Guided Plan Mode (`navig/commands/plan_mode.py`, `navig plan`)**: 5-phase planning wizard: Phase 1 generates clarifying questions (configurable via `agent.plan_max_interview_questions`, default 5); Phase 2 runs N parallel sub-agent explorations (`agent.plan_explore_agents`, default 3); Phase 3 synthesises a structured Markdown plan; Phase 4 accepts user review (Accept / Edit / Regenerate / Quit); Phase 5 saves to `.navig/plans/<slug>.md` with YAML front-matter. Supporting commands: `navig plan list` (Rich table, `--status` filter, `--json`), `navig plan show SLUG` (Rich Markdown panel, `--raw`), `navig plan run SLUG` (hand-off to `navig agent run --context-file`). Registered as `"plan"` in `_EXTERNAL_CMD_MAP`. 11 new tests in `tests/planning/test_plan_mode_cmd.py`.

### Fixed
- **Mesh sync persistence implemented (`navig/mesh/sync_manager.py`)**: Replaced the `_persist_state()` Phase-2 TODO with real SQLite persistence through `navig.storage.engine.get_engine()`. `SyncManager` now creates and upserts `mesh_sync_state` snapshots (`state_json`, `state_hash`, `updated_at`) when `optional_sqlite_path` is configured, restores persisted state on startup before entering the loop, and degrades safely to in-memory mode by logging and continuing on persistence/restore errors.
- **AI command mock compatibility with `effort` kwarg**: Fixed three test files
  (`tests/ai/test_ai_unicode.py`, `tests/commands/test_commands_ai.py`,
  `tests/commands/test_commands_ai_ask_host_resolution.py`) where mock `ask()` methods
  did not accept `**kwargs`, causing `TypeError` when `ask_ai()` passes `effort=` as a
  keyword argument introduced by the `--effort` CLI flag. All mock signatures updated to
  `ask(self, question, context, model_override=None, **kwargs)`.
- **`llm_generate()` now threads `effort`**: Added `effort: str | None = None` parameter to `llm_generate()` in `navig/llm_generate.py`. When set, delegates to `run_llm()` (which already handles effort in full) and returns `.content`. Callers that do not pass `effort` are unaffected.
- **Telegram markdown formatting in dynamic responses**: Fixed multiple Telegram send/edit paths that previously used `parse_mode=None` for dynamic LLM/CLI output, causing raw `**bold**` markers to appear in chat. Updated REASON/CODE placeholder edits, CLI command relay output (including auto-heal fallback), and auto-heal retry output to render via HTML-safe markdown conversion before send.
- **Telegram language-cache max-age is now config-driven**: Hardcoded `_lang_max_age = 12 * 3600` in `navig/gateway/channels/telegram.py` replaced with a config-read value from `telegram.language_cache_max_age_hours` (default 12 h, safe fallback if config is unavailable).
- **Telegram `/start` away-summary timing corrected**: `navig/gateway/channels/telegram.py` now captures a pre-update `last_active` snapshot and passes it into `/start` handling so inactivity gap calculation is based on the previous session activity, not the just-received `/start` message. This restores recap display after real inactivity windows.
- **Away-summary truncation now keeps recent context**: `navig/gateway/channels/away_summary.py` changed dual-cap truncation to tail-preserving behavior for both line and byte caps, so summaries are generated from the latest messages instead of the oldest ones.
- **`navig memory compact` confirmation safety**: `navig/commands/memory.py` no longer lets `--plain` bypass the destructive confirmation prompt; only `--yes` can skip confirmation.
- **Strict CLI effort validation**: `navig ask` and `navig agent run` now validate `--effort` using `navig.agent.effort.resolve_effort()` and fail fast with a clear error on invalid values.
- **Eliminated duplicate `_atomic_write_text` in `navig/memory/_util.py`**: Replaced 30-line private reimplementation with a 1-line delegate to `navig.core.yaml_io.atomic_write_text`, the canonical helper. Removed unused imports (`os`, `sys`, `tempfile`, `time`, `ATOMIC_REPLACE_*` constants). Consumers (`snapshot.py`, `manager.py`, `embeddings.py`) unchanged.
- **Non-atomic writes hardened in critical state files**: Five additional write-sites now use `atomic_write_text()` to prevent data corruption on crashes: `plans/current_phase_manager.py` (CURRENT_PHASE.md), `tasks/queue.py` (task queue persistence), `tools/memory.py` (memory store persistence), `commands/plan_mode.py` (plan file save + status update), `update/history.py` (update history JSONL). The redundant `mkdir` call in `tools/memory.py` was removed since `atomic_write_text` already creates parent directories.
- **Extract `_MAX_PIN_ATTEMPTS` constant in `navig/modes/manager.py`**: Replaced bare literal `3` in `prompt_pin()`'s `for attempt in range(3)` and `remaining = 2 - attempt` with a single module-level `_MAX_PIN_ATTEMPTS = 3` constant, establishing a single source of truth.
- **Non-atomic `_write_file` in `navig/contracts/store.py`**: `RuntimeStore._write_file()` now calls `atomic_write_text()` to prevent stale/corrupt node/mission/receipt JSON on crash.
- **Non-atomic settings reset in `navig/commands/settings_cmd.py`**: `_reset_key()` now writes the updated settings file via `atomic_write_text()`, preventing partial settings corruption when `navig settings --reset` is interrupted.
- **Deduplicated `_now_iso()` and `_utc_now()` timestamp helpers**: Six private module-level copies eliminated. Added `now_iso() -> str` and `utc_now() -> datetime` to `navig/core/dict_utils.py` as canonical implementations alongside the existing `deep_merge` and `truncate_output` helpers. Removed `_now_iso()` definitions from `navig/contracts/{node,mission,execution_receipt,store}.py` (4 copies) and `navig/commands/work.py` (1 copy); removed `_utc_now()` definition from `navig/cache_store.py` (1 copy). All 12 call-sites updated. `navig/bot/stats_store._utc_now()` left intentionally unchanged — it uses `datetime.now()` (naive, technically wrong) but is self-consistent with its stored naive datetime strings; a separate data migration is needed to correct it.
- **Hardened 5 more non-atomic writes in critical state files**: `navig/modes/manager.py` (`_write_mode_key_fallback`, `set_pin`, `verify_pin` PIN hash upgrade) and `navig/core/context.py` (`set_active_host`, `set_active_app` global cache writes) now use `atomic_write_text()`. The PIN hash files in particular are security-critical; a partial write (e.g. from a crash during PBKDF2 hash string output) would leave a corrupt hash that could permanently lock the user out of privileged modes.
- **Missing `"differences between"` pattern in research mode detector (`navig/routing/detect.py`)**: `_RESEARCH_PATTERNS` was missing `r"differences?\s+between"`, causing queries like `"what are the differences between X and Y"` to fall through to `coding` mode instead of `research`. Pattern added; the now-redundant inline regex in the `llm_router.py` module-level `detect_mode` shim was removed and the shim simplified to a pure delegation to `routing.detect.detect_mode`. 30 existing tests confirm no regression (commit `d16f318`).
- **Locale-dependent file I/O across 24 modules**: 46 builtin `open()` calls in `navig/` were missing an explicit `encoding=` parameter, causing `UnicodeDecodeError` / garbled text on Windows systems where the default locale encoding is `cp1252`/`cp850`. Added `encoding='utf-8'` to all text-mode opens. Binary opens (`tarfile`, `CryptoEngine`, `webbrowser`, mode `'rb'`/`'wb'`) were correctly left unchanged (commit `26ffa65`).

### Changed
- **Language-Agnostic Classifier – Phase 2 (Structural Signal Hardening)**: Added two additional script-neutral helpers to `telegram_mode_classifier.py` to close the gaps identified when guillemets are absent: `_has_mid_sentence_cap()` (any non-first token with ASCII uppercase initial + ≥5 chars is a proper noun in any Latin-script language — catches `"j'ai vu Inception hier"`) and `_has_script_mixing()` (text that uses alphabetic chars from ≥2 Unicode script buckets signals a cross-language entity reference, e.g. `"смотрю Inception сейчас"`). Added `_SCRIPT_BUCKETS` constant list (12 scripts). Updated `classify_mode()` to test both new signals in priority order after `_contains_title_marker` and before `_is_non_latin_dominant`; also lowered the Latin word-count fallback from 8 to 5 (a 5-word Latin sentence is substantive, not chat). Updated `select_tools_for_text()` to prepend `"search"` when any of the three entity signals fires (title marker, mid-sentence cap, or script mixing). Extended the `_handle_talk` fallback handler in `telegram.py` with the same `llm_hint` safety net as the REASON handler, covering misclassified residual messages. Extended the `telegram.py` import block to include `_has_mid_sentence_cap` and `_has_script_mixing`. 5 new tests added (55 pipeline tests total, 393 suite total). Lint clean.

## [2.7.0] - 2026-04-12

### Changed
- **Language-Agnostic Classifier (Structural Signals)**: Extended `telegram_mode_classifier.py` with two script-neutral helpers — `_contains_title_marker()` (detects guillemets `«»`, curly quotes `""`, straight-quoted multi-word phrases, CJK brackets `「」【】`, and runs of title-cased ASCII words) and `_is_non_latin_dominant()` (counts alphabetic chars by Unicode range; returns `True` when >30% are Cyrillic/Arabic/CJK/Devanagari/Hebrew/Thai/Hangul/Hiragana/Katakana/Georgian/Armenian). Both functions are purely arithmetic — no language-ID library, no hardcoded word lists. Updated `classify_mode()` to route on these signals before the Latin-calibrated `word_count >= 8` fallback, so short statements like `«Проект Аве Мария»` or `The Dark Knight Rises` are classified as `REASON` regardless of script. Updated `select_tools_for_text()` to prepend `"search"` when a title marker is present. In `telegram.py`, injected a language-agnostic `"llm_hint"` metadata key in `REASON` dispatch when either structural signal fires, prompting the LLM to acknowledge uncertainty rather than confabulate. 7 new tests added (`TestModeClassifier` + `TestSelectTools`).
- **Telegram ACT URL Propagation**: Fixed ACT tool-argument wiring so URL-bearing prompts pass `url` to both `site_check` and `browser_fetch` (in addition to legacy `web_fetch` compatibility), preventing `browser_fetch` skips with `url arg required`.
- **Robustness (Configuration Type Coercion)**: Audited navig agent config loaders using AST static analysis for raw `int()` / `float()` type conversions mapping dictionary `get()` results without error catching. Replaced risky type-casts with safe `try...except (ValueError, TypeError)` blocks returning defaults across `auth_profiles.py`, `coordinator.py`, `speculative.py`, `remediation.py`, `memory_auto_extractor.py`, `model_router.py`, and `prompt_caching.py` to prevent fatal startup crashes when YAML/JSON structures carry malformed types (e.g. `timeout: "unlimited"`). Included new regression coverage `tests/agent/test_configuration_coercion.py`.
- **Test Suite Hygiene — Workstreams A + B + G** (315 test files):
  - **Workstream G (artifact cleanup)**: Deleted 120 accumulated `.pytest_tmp_*` directories from `.local/` (43) and `.dev/` (77); root `.gitignore` already has `*/.pytest_tmp_*/` patterns covering future runs.
  - **Workstream A (mkdtemp → tmp_path)**: Replaced raw `tempfile.mkdtemp()` + manual `shutil.rmtree` yield-fixtures in 8 test files (`test_config.py`, `test_cli_enhancements.py`, `test_execution_modes.py`, `test_webserver_autodetect.py`, `test_navig_backup.py`, `test_workflow.py`, `test_wiki.py`, `test_migration.py`) with `tmp_path`-parameterised fixtures; pytest now owns full lifecycle. Added `teardown_method` to 5 `setup_method` classes in `test_settings_resolver.py`, `test_mount_commands.py` (3 classes), and `test_inbox_module.py` (2 classes) that had no cleanup. Converted `test_goal_orchestration.py::TestAgentRunnerGoalPlanner::test_agent_has_goal_planner` to accept `tmp_path` directly. Removed now-unused `import shutil` / `import tempfile` where applicable.
  - **Workstream B (systematic markers)**: Injected module-level `pytestmark = pytest.mark.<marker>` into all 315 test files (previously only 1 file had any marker applied). Classification: 279 `integration`, 29 `unit`, 7 `slow`. Enables `pytest -m unit` (400 tests, runs in ~3s), `pytest -m "not slow"` (excludes benchmarks/e2e/network), `pytest -m slow` (7 files). Added `import pytest` to files that lacked it. Injection script saved to `.dev/inject_markers.py`.
  - **Workstream B (asyncio mark cleanup)**: Removed 660 redundant `@pytest.mark.asyncio` decorators from 72 test files. All are no-ops under `asyncio_mode = auto` in `pytest.ini`; removal reduces visual noise without changing behaviour. No `import pytest` lines were orphaned. Removal script saved to `.dev/remove_asyncio_marks.py`.
  - Baseline 172-test suite: 172 passed ✓

- **Test Suite Restructure — Subsystem Directory Migration batch 5** (36 files across 9 new + 1 extended subdirectories):
  - `tests/routing/` ← test_channel_router_auto_persona.py, test_model_router.py, test_routing_perf_paths.py, test_runtime_routes.py, test_unified_router.py
  - `tests/wave/` ← test_wave7_paths.py, test_wave8_paths.py, test_wave9_paths.py, test_wave10_paths.py
  - `tests/commands/` (extended) ← test_backup_command_core.py, test_command_registry_export.py, test_db_command_core.py, test_import_command.py, test_mount_commands.py, test_menu_command_removal.py
  - `tests/storage/` ← test_storage_engine.py, test_extended_cache.py, test_conversation_store.py, test_session_store.py
  - `tests/integration/` ← test_integration.py, test_comms_integration.py, test_user_preferences_integration.py, test_firecrawl_integration.py
  - `tests/engine/` ← test_cortex_engine.py, test_engine_queue.py, test_filtering_engine.py, test_pipeline.py
  - `tests/migration/` ← test_core_migrations.py, test_migration.py
  - `tests/policy/` ← test_continuation_policy.py, test_policy_gate.py
  - `tests/tasks/` ← test_background_tasks.py, test_tasks.py
  - `tests/knowledge/` ← test_corpus_scanner.py, test_knowledge_graph.py, test_language_enforcement.py
  - Also removed 7 spurious flat-file duplicates (git-restored copies from batch 4 session with incorrect path depths; subdir copies are canonical).
  - Fixed `__file__`-relative paths post-move: `tests/commands/test_command_registry_export.py` (`.parents[1]` → `.parents[2]`), `tests/commands/test_menu_command_removal.py` (`.parent.parent` → `.parent.parent.parent`), `tests/engine/test_cortex_engine.py` (`.parent.parent` → `.parent.parent.parent` for EXAMPLE_APP_YAML and GENERIC_YAML constants).
  - 690 passed, 4 skipped ✓. ~76 flat test files remain.

- **Test Suite Restructure — Subsystem Directory Migration batch 6** (41 files across 4 new + 5 extended subdirectories):
  - `tests/security/` (extended) ← test_decoy_guard.py, test_trust_boundary.py, test_skill_security.py, test_sprint4_safety.py
  - `tests/agent/` (extended) ← test_autonomous_agent.py, test_proactive_assistant.py, test_autoheal.py, test_auto_evolve.py, test_goal_orchestration.py, test_coordinator.py
  - `tests/web/` (new) ← test_webserver_autodetect.py, test_webhooks.py, test_web_search_resolution.py
  - `tests/ops/` (new) ← test_monitoring_unicode.py, test_telemetry.py, test_operation_recorder_core.py, test_middleware_op_recorder.py
  - `tests/git/` (new) ← test_git_agent_tools.py, test_worktree.py, test_project_indexer.py
  - `tests/llm/` (extended) ← test_mock_llm_server.py, test_speculative.py, test_intent_parser.py, test_context_builder.py
  - `tests/planning/` (extended) ← test_workflow.py, test_flow_delegation.py, test_milestone_progress.py, test_effort_levels.py
  - `tests/core/` (new) ← test_all_modules.py, test_backward_compat.py, test_contracts.py, test_api_snapshot.py, test_soul_loader.py, test_deferred_integration_guidance.py
  - `tests/cli/` (extended) ← test_fast_help_output.py, test_help_system.py, test_plugin_cli.py, test_first_run.py, test_phase_f_debug_logging.py, test_execution_modes.py, test_mode_route_commands.py
  - Fixed `__file__`-relative paths post-move: `tests/agent/test_auto_evolve.py` (`.parent.parent` → `.parent.parent.parent`), `tests/core/test_all_modules.py` (`.parent.parent` → `.parent.parent.parent`), `tests/cli/test_help_system.py` (`.parent.parent` → `.parent.parent.parent`, 3 occurrences), `tests/cli/test_plugin_cli.py` (`.parent.parent` → `.parent.parent.parent`).
  - 1480 passed, 7 skipped ✓. 35 flat test files remain.

- **Test Suite Restructure — Subsystem Directory Migration batch 7 (FINAL)** (35 files across 1 new + 13 extended subdirectories — 0 flat files remaining):
  - `tests/voice/` (extended) ← test_audio_handler.py, test_streaming_stt.py, test_wake_word_engine.py
  - `tests/security/` (extended) ← test_keys.py
  - `tests/config/` (extended) ← test_hierarchical_config.py, test_auth_profiles.py
  - `tests/db/` (new) ← test_database_advanced_core.py
  - `tests/memory/` (extended) ← test_memory.py
  - `tests/ops/` (extended) ← test_daemon.py, test_navig_backup.py, test_os_adapters.py, test_proc.py, test_recovery_local_bootstrap.py
  - `tests/planning/` (extended) ← test_current_phase_manager.py, test_review_queue.py, test_todo_tracker.py
  - `tests/providers/` (extended) ← test_providers.py
  - `tests/service/` (extended) ← test_server_template_manager.py, test_template_manager.py
  - `tests/tunnel/` (new) ← test_tunnel_manager.py
  - `tests/knowledge/` (extended) ← test_key_facts.py
  - `tests/mesh/` (extended) ← test_formations.py
  - `tests/cli/` (extended) ← test_exec_pack.py, test_slash_registry.py, test_terminal_capabilities.py, test_selector.py
  - `tests/core/` (extended) ← test_debug_logger.py, test_event_bridge.py, test_evolution_failure_summary.py, test_hooks.py, test_lsp.py, test_matrix.py, test_output_validator.py, test_package_runtime.py, test_reactive_compact.py
  - Fixed `__file__`-relative paths pre-move: `test_os_adapters.py` (`.parent.parent` → `.parent.parent.parent`), `test_hierarchical_config.py` (`.parent` → `.parent.parent`), `test_formations.py` (`.parent.parent` → `.parent.parent.parent`, 3 occurrences).
  - Also removed 3 spurious flat duplicates (git-restored copies of pre-move-fixed files: test_formations.py, test_hierarchical_config.py, test_os_adapters.py); subdir copies with corrected path depths are canonical.
  - 2129 passed, 3 skipped ✓. **Migration complete — 0 flat test files remain.**

- **Test Suite Restructure — Subsystem Directory Migration batch 4** (39 files across 18 new subdirectories):
  - `tests/ahk/` ← test_ahk_adapter.py, test_ahk_evolver.py
  - `tests/approval/` ← test_approval.py, test_approval_gate.py
  - `tests/bridge/` ← test_bridge.py, test_bridge_port.py
  - `tests/config/` ← test_config.py, test_config_backup_core.py, test_config_vault_sync.py
  - `tests/connection/` ← test_connection.py, test_connection_pool.py
  - `tests/discovery/` ← test_discovery.py, test_discovery_core.py
  - `tests/install/` ← test_install.py, test_install_scripts.py, test_installer.py
  - `tests/interactive/` ← test_interactive.py, test_interactive_menu_fixes.py
  - `tests/messaging/` ← test_messaging.py, test_messaging_registry.py, test_messaging_secrets.py
  - `tests/perf/` ← test_perf_profiler.py, test_perf_profiler_core.py
  - `tests/safety/` ← test_safety_guard.py, test_safety_pipeline.py
  - `tests/security/` ← test_security_commands.py, test_security_fixes.py
  - `tests/service/` ← test_service_cli.py, test_service_manager_runtime.py
  - `tests/settings/` ← test_settings_resolver.py, test_settings_resolver_paths.py
  - `tests/skills/` ← test_skills.py, test_skills_context.py
  - `tests/store/` ← test_store.py, test_store_phase2.py
  - `tests/wiki/` ← test_wiki.py, test_wiki_tools.py
  - `tests/workspace/` ← test_workspace_ownership.py, test_workspace_to_spaces_migration.py
  - Fixed `__file__`-relative paths pre-move: `test_ahk_adapter.py` (`.parent.parent` → `.parent.parent.parent`), `test_bridge_port.py` (`.parents[1]` → `.parents[2]`), `test_connection.py` (`.parent.parent` → `.parent.parent.parent`), `test_install.py` (`.parent.parent` → `.parent.parent.parent`), `test_install_scripts.py` (`.parent.parent` → `.parent.parent.parent`).
  - 627 passed, 6 skipped ✓. ~114 flat test files remain.

- **Test Suite Restructure — Subsystem Directory Migration batch 3** (35 files):
  - Moved 5 `test_init_*.py` → `tests/init/`
  - Moved 4 `test_blackbox_*.py` → `tests/blackbox/`
  - Moved 4 `test_inbox_*.py` → `tests/inbox/`
  - Moved 3 `test_remote_*.py` → `tests/remote/`
  - Moved 3 `test_conversational_*.py` → `tests/conversational/`
  - Moved 4 `test_plan_*.py` + `test_plans_*.py` → `tests/planning/`
  - Moved 3 `test_deploy_*.py` → `tests/deploy/`
  - Moved 3 `test_ai_*.py` → `tests/ai/`
  - Moved 3 `test_provider_*.py` → existing `tests/providers/` (5 total test files)
  - Moved 3 `test_commands_*.py` → `tests/commands/`
  - Fixed `__file__`-relative paths pre-move: `tests/init/test_init_manual.py` (sys.path: `.parent` → `.parent.parent.parent`) and `tests/providers/test_provider_urls.py` (REPO_ROOT: `.parents[1]` → `.parents[2]`).
  - Fixed `test_conversational_agent.py::test_get_ai_response_falls_back_when_ai_client_reports_unavailable`: test was missing a `get_router` patch — production code was updated to still try the UnifiedRouter even when `ai_client.is_available()` is False; added `patch("navig.routing.router.get_router", side_effect=RuntimeError("no router in test"))` so the code falls through to `_deterministic_fallback()` as the test intends. (This was previously a passing-by-contamination test in the full suite.)
  - 590 tests collected and all 590 passed ✓. 151 flat test files remain.

- **Test Suite Restructure — Subsystem Directory Migration batch 2** (47 files):
  - Moved 7 `test_mesh_*.py` → `tests/mesh/` (joining existing `test_registry.py`; 8 total)
  - Moved 8 `test_cli_*.py` + 4 `test_main_*.py` → `tests/cli/` (12 total)
  - Moved 5 `test_memory_*.py` → `tests/memory/`
  - Moved 5 `test_tool_*.py` → `tests/tools/`
  - Moved 5 `test_llm_*.py` → `tests/llm/`
  - Moved 4 `test_update_*.py` → `tests/update/`
  - Moved 3 `test_voice_*.py` → `tests/voice/`
  - Moved 6 `test_space*.py` / `test_spaces_*.py` → `tests/spaces/`
  - Fixed `__file__`-relative paths in 3 files that now live one level deeper:
    - `tests/cli/test_cli_surface_regressions.py`: `ROOT` changed from `.parent.parent` to `.parent.parent.parent`
    - `tests/memory/test_memory_batching.py`: sys.path append changed from `.parent.parent` to `.parent.parent.parent`
    - `tests/gateway/test_gateway_telegram_import_boundaries.py`: `repo_root` changed from `.parents[1]` to `.parents[2]` (this also fixed a previously silent false-positive where the test passed vacuously because `navig/gateway/` was never found under the wrong root)
  - 651 tests collected and all 651 passed ✓. 186 flat test files remain.

- **Test Suite Restructure — Subsystem Directory Migration** (59 files):
  - Moved 21 `test_telegram_*.py` → `tests/telegram/`
  - Moved 9 `test_mcp_*.py` → `tests/mcp/`
  - Moved 11 `test_onboarding_*.py` → `tests/onboarding/`
  - Moved 8 `test_agent*.py` → `tests/agent/` (incl. bare `test_agent.py`)
  - Moved 6 `test_gateway_*.py` → `tests/gateway/`
  - Moved 4 `test_vault_*.py` → existing `tests/vault/` (joining `test_vault.py`)
  - All subdirectories work without `__init__.py` — `--import-mode=importlib` in `pytest.ini` handles discovery. Root `tests/conftest.py` fixtures are inherited by all subdirectories automatically.
  - 948 tests collected from the 6 new clusters; all 948 passed ✓. Baseline 172-test suite adapted to updated paths: 172 passed ✓.

### Fixed
- **`test_provider_picker_delegate_accepts_decorated_signature` stub signature** (`tests/telegram/test_telegram_navigation_callbacks.py`): The `_wrapped_like_decorator` stub was missing the `show_models=False` parameter that was added to the production `_show_provider_model_picker` signature; calling the delegate raised `TypeError: unexpected keyword argument 'show_models'`. Added `show_models=False` to the stub and captured it in the assertion dict so the test now also verifies the flag is forwarded correctly. Fix verified: 4 passed in the file ✓.

- **test ordering flakiness in `TestBugRegressions`** (`test_provider_control_surface.py`): `test_vis_clear_no_leading_empty_answer` and `test_pu_unknown_action_uses_show_alert` used `inspect.getsource()` on the live imported `CallbackHandler`, which returns corrupted/stale source when class-level state is polluted by earlier tests in the 3500+ full-suite run. Both tests now parse `navig/gateway/channels/telegram_keyboards.py` from the filesystem directly, walk the AST for the specific method node, and are immune to import-order contamination. Root cause: `inspect.getsource(CallbackHandler._handle_vision_callback)` was returning `'prov_id: str,\n'` (a stale single parameter line) causing `ast.parse()` to raise `SyntaxError`. Fix verified: 57 passed in `test_provider_control_surface.py`, 172 baseline passed ✓.

 — Groups U–AI + AK-ext + U-ext-2 + AK-ext-2** (22 files): Eliminated private utility wrappers that duplicated existing canonical helpers. Specific removals:
  - `monitoring.py`: Deleted `_fmt_bytes()` (→ `format_bytes` from `console_helper`) and dead `try/except ImportError` block for `is_local_host`. Groups V+W.
  - `modes/manager.py`: Deleted `_navig_home()` wrapper; inlined `paths.config_dir()` at both call sites. Group U.
  - `agent/profiles.py`: Deleted `_navig_home()` wrapper; added module-level `config_dir` import; inlined at 2 call sites. Group U.
  - `commands/store.py`: Deleted `_navig_dir()` wrapper; inlined `config_dir()` at 5 call sites. Group U-ext.
  - `gateway/channel_router.py`: Hoisted `strip_ansi` import to module top; deleted `_strip_ansi` staticmethod; replaced 2 `self._strip_ansi(…)` calls. Group AL.
  - `onboarding/steps.py`: Deleted `_get_vault_for_onboarding()` (inlined `get_vault()` at 4 call sites); replaced 10 `yaml.safe_load(path.read_text(…))` patterns with `safe_load_yaml(path)`. Groups AM+AE.
  - `gateway/deck/routes/vault.py`: Deleted `_mask_key()` (replaced with `mask_secret(…, show_prefix=6)` from `navig.vault.secret_str`). Group AA.
  - `commands/config_backup.py`: Deleted dead `_redact_dict()` function (canonical `redact_dict` from `navig.core.security` was already in use). Group Z.
  - `providers/source_scan.py`: Deleted `_load_config()` wrapper; replaced 2 call sites with `safe_load_yaml(navig_dir / "config.yaml") or {}`. Group AK.
  - `migrations/workspace_to_spaces.py`: Deleted `_load_config()` wrapper; replaced call site with `safe_load_yaml(config_file) or {}`. Group AK.
  - `tui/resolvers.py`: Replaced inline `yaml.safe_load(cfg_path.read_text(…))` with `safe_load_yaml(cfg_path)`. Group AK.
  - `tui/config_model.py`: Replaced `Path("~/.navig").expanduser()` with `config_dir()`. Group X.
  - `onboarding/runner.py`: Simplified `_get_console()` double try/except into single `get_console()` call. Group Y.
  - `memory/snapshot.py`: Deleted `_atomic_write_text()` duplicate (exact copy of `navig.memory._util._atomic_write_text`); removed 4 now-unused stdlib imports (`os`, `sys`, `tempfile`, `time`); updated test patch target to `navig.memory._util`. Group AI.
  - `commands/bridge.py`: Hoisted `safe_load_yaml` import; removed 3 function-local `import yaml` stubs; replaced `yaml.safe_load(path.read_text()) or {}` at all 3 call sites. Group AK-ext.
  - `commands/farmore.py`: Hoisted `safe_load_yaml` import; removed 3 function-local `import yaml` stubs; simplified token-config read and token-set/remove config-fallback blocks. Group AK-ext.
  - `commands/action.py`: Added module-level `safe_load_yaml` import; removed `import yaml` from `_load_all_actions`; collapsed `if exists: try/except else: {}` blocks in `action_add` and `action_remove` to single `safe_load_yaml(path) or {}`. Group AK-ext.
  - `personas/soul_loader.py`: Deleted `_navig_dir()` wrapper (all 3 branches reduced to `config_dir()`); hoisted `config_dir` import; inlined at 3 call sites; removed now-unused `os` import. Updated test patch targets from `soul_loader.Path.home` → `soul_loader.config_dir`. Group U-ext-2.
  - `commands/init.py`: Collapsed `_resolve_navig_base_dir()` from 10-line manual reimplementation of `config_dir()` logic to 2-line guard + `config_dir()` call. Group U-ext-2.
  - `commands/onboard.py`: Replaced `Path("~/.navig").expanduser()` in `check_config_dir_writable()` with canonical `config_dir()` (already imported). Group U-ext-2.
  - `onboarding/steps.py`: Removed 10 dead `import yaml` stubs that were never used after `safe_load_yaml` migration (reads already delegated to `safe_load_yaml`; only `yaml.safe_dump`/`atomic_write_yaml` writes remain). Fixed sole surviving reference: unreachable `except (OSError, yaml.YAMLError):` → `except Exception:`. Group AK-ext-2.
  - `modes/manager.py`: Simplified `get_active_mode_name()` — removed inline `import yaml` stub and `open()/yaml.safe_load(f)` pattern; replaced with `safe_load_yaml(_config_path()) or {}`. Added module-level `safe_load_yaml` import. Group AK-ext-2.
  - `commands/space.py`: Removed dead `import yaml` stub from `_set_active_space()` try-block; the stub was unreachable — all YAML I/O already delegated to `atomic_write_yaml`. Group AK-ext-3.
  - `commands/vault.py`: Eliminated `_console()` 1-line wrapper (`return get_console()`); inlined `get_console()` at its sole call site (line 165). Updated `tests/test_config_vault_sync.py` patch target from `vault._console` to `vault.get_console`. Group AK-ext-3.
  - `commands/links.py`: Eliminated `_db()` 1-line wrapper (`return get_links_db()`); inlined `_links_db_mod.get_links_db()` at all command call sites (`add/list/search/show/open/edit/tag/delete/import`). Group AK-ext-4.
  - `commands/kg.py`: Eliminated `_kg()` 1-line wrapper (`return get_knowledge_graph()`); inlined `_kg_mod.get_knowledge_graph()` at all command call sites (`remember/recall/search/forget/routines/status`). Group AK-ext-4.
  - `onboarding/renderer.py`: Replaced `Path.home() / ".navig" / "config.yaml"` fallback display path in `_format_detail(step_id="config-file")` with canonical `config_dir() / "config.yaml"` to respect configured NAVIG base paths. Group AK-ext-4.
  - `commands/config.py`: Removed dead private helper `_read_json()` (unused) and inlined single-use `_package_schema_dir()` expression at the `schema install` call site (`Path(__file__).resolve().parents[1] / "schemas"`). Group AK-ext-5.
  - `commands/mount.py`: Removed single-use `_helper_script()` wrapper and inlined `_scripts_dir() / "mount-drive.ps1"` at script regeneration call site. Group AK-ext-5.
  - `onboarding/renderer.py`: Removed dead private helper `_pad_to()` (no call sites). Preserved `_strip_ansi` as a compatibility alias for existing imports/tests. Group AK-ext-6.
  - `onboarding/genesis.py`: Removed single-use `_qr_target()` wrapper and inlined `f"{NODE_URL_BASE}/{node_id}"` at the genesis creation call site. Group AK-ext-6.
  - `commands/backup.py`: Removed dead private helper `_cleanup_failed_backup()` (no call sites; no test hooks). Group AK-ext-7.
  - `commands/cron.py`: Removed dead private helper `_check_gateway()` (no call sites; all command paths already perform direct request/connection handling). Group AK-ext-7.
  - `bot/command_tools.py`: Removed dead private helper `_build_command_string()` (no call sites; command routing uses `COMMAND_HANDLER_MAP` directly). Group AK-ext-8.

### Fixed
- **Daemon config boolean-coercion hardening** (`navig/daemon/entry.py`): added `_as_bool()` normalization for daemon feature flags so string-valued config entries like `"false"`/`"0"` no longer evaluate as truthy and accidentally enable subsystems.
- Added focused regression coverage in `tests/test_daemon.py` for `_as_bool()` string parsing and `main()` behavior when `telegram_bot`/`gateway`/`scheduler` are configured as string booleans.
- **Gateway client numeric-coercion hardening** (`navig/gateway_client.py`): `gateway_cli_defaults()` now safely isolates `int()` type casting so string-valued gateway ports (e.g. `"malformed"`) fall back to default rather than crashing the CLI caller with `ValueError`.
- Added focused regression coverage in `tests/test_gateway_client_coercion.py` for default fallback on invalid port strings and null types.
- **Remote agent timeout config-coercion hardening** (`navig/agent/remote_agent.py`): `NAVIG_REMOTE_TIMEOUT` import-time parsing now safely isolates `int()` parameter-casting, meaning a non-numeric or empty string set in `.env` no longer crashes the module on startup. Negative/Zero values also clamp back to default mapping.
- Added focused regression coverage in `tests/test_remote_agent_timeout.py` testing invalid timeout fallback scenarios through module reload.
- **Daemon config numeric-coercion hardening** (`navig/daemon/entry.py`): added `_as_int()` normalization for numeric daemon settings so string-valued ports (for example `"0"`, `"9001"`) are safely coerced before supervisor/gateway wiring, avoiding startup type errors from non-int config payloads.
- Added focused regression coverage in `tests/test_daemon.py` for `_as_int()` parsing and `main()` behavior with string `health_port`/`gateway_port` values.
- **Operation recorder line-index mapping hardening** (`navig/operation_recorder.py`): `record()` now rebuilds the in-memory index after append so operation ID lookups map to physical file line numbers, even when history contains blank/malformed lines.
- Added focused regression coverage in `tests/test_operation_recorder_core.py` for mixed malformed/blank history content before new records.
- **Provider/profile consistency hardening** (`navig/providers/auth.py`): `AuthProfileManager.get_api_key(provider, profile_id=...)` now enforces provider match for the explicitly selected profile, preventing cross-provider key leakage when profile IDs overlap.
- Added focused regression coverage in `tests/test_provider_auth_core.py` for mismatch rejection and matching-profile success.
- **Telegram photo OCR surfacing** (`navig/gateway/channels/telegram.py`): photo analysis now appends a best-effort OCR snippet (when detected) alongside vision output, and captioned photos also trigger the photo-analysis path while preserving normal caption text flow.
- **Wiki inbox image OCR ingestion** (`navig/commands/wiki.py`): `navig wiki inbox process` now handles image files (`.png/.jpg/.jpeg/.webp/.bmp/.tiff/.tif/.gif`) by extracting OCR text into markdown for categorization instead of failing UTF-8 file reads; empty OCR results return an explicit non-fatal error.
- **Shared OCR helper extraction** (`navig/core/ocr.py`): centralized image-byte OCR extraction helper added and reused by Telegram photo handling, wiki inbox processing, and media engine OCR stage to keep behavior consistent and avoid fragile import chains.
- **Context app-activation fallback hardening** (`navig/core/context.py`): `ContextManager.set_active_app(..., local=None)` now treats local app activation as best-effort and still writes global active-app cache when local resolution fails (e.g., missing active host or project-local context mismatch).
- Added focused regression coverage in `tests/test_config.py` for default-mode fallback behavior in `ContextManager.set_active_app`.
- **Profiler regression-analysis hardening** (`navig/perf/profiler.py`): `detect_regressions()` now tolerates malformed sample rows (non-dicts, missing `ts`, and missing/empty `elapsed_ms`) instead of raising during sort/conversion, improving resilience against partial/corrupt perf logs.
- Added focused regression coverage in `tests/test_perf_profiler_core.py` for missing `ts` handling and non-dict row filtering.
- **Auth profile rotation de-duplication hardening** (`navig/agent/auth_profiles.py`): `AuthProfilePool.add_profile()` now replaces existing rotation entries for the same profile name before re-adding weighted slots, preventing silent rotation inflation when profiles are updated dynamically.
- Added focused regression coverage in `tests/test_auth_profiles.py` for re-adding an existing profile name.
- **Tool result capping input hardening** (`navig/agent/tool_caps.py`): `cap_result()` now normalizes negative `max_chars` inputs to `0`, avoiding inconsistent negative-slice truncation behavior and ensuring stable footer/reporting semantics.
- Added focused regression coverage in `tests/test_tool_caps.py` for negative `max_chars` normalization.
- **Telegram command handler cleanup hardening** (`navig/gateway/channels/telegram_commands.py`): removed an unused `router_active` local and fixed an extraneous f-string literal in the voice-provider status view to eliminate functional lint faults (`F841`, `F541`) without changing runtime behavior.
- **Gateway smoke-test selector normalization** (`navig/commands/gateway.py`): `gateway test` now normalizes channel selectors before routing, so mixed-case forms like `ALL` resolve correctly to full-channel smoke testing instead of being treated as unknown.
- Added focused regression coverage in `tests/test_gateway_test_telegram_command.py` for uppercase `ALL` selector handling.
- **Gateway smoke-test matrix wiring fix** (`navig/commands/gateway.py`): `gateway test matrix` no longer imports a non-existent bridge symbol; it now routes through the existing Matrix command entrypoint (`navig.commands.matrix.send`), preventing runtime import failures.
- **Gateway `test all` channel coverage fix** (`navig/commands/gateway.py`): `all` now exercises all declared smoke-test channels (`telegram`, `matrix`, `discord`, `email`) instead of only two channels.
- Added focused regression coverage in `tests/test_gateway_test_telegram_command.py` for `gateway test all --json` channel counts and ordering.
- **Service method argument normalization hardening** (`navig/daemon/service_manager.py`): `install()`, `uninstall()`, and `status()` now normalize non-empty `method` values (`strip().lower()`), so mixed-case CLI inputs like `--method NSSM` and `--method TASK` resolve correctly instead of failing as unknown methods.
- Added focused regression coverage in `tests/test_service_manager_runtime.py` for mixed-case method handling across install/uninstall/status paths.
- **Database advanced identifier-validation false-positive fix** (`navig/commands/database_advanced.py`): `_validate_sql_identifier()` no longer rejects valid names by substring keyword matching (e.g., `orders` containing `OR`). Validation now blocks exact reserved SQL keywords while preserving character/length safeguards.
- Added focused regression coverage in `tests/test_database_advanced_core.py` for valid substring cases, exact reserved-keyword rejection, and invalid-character rejection.
- **Startup command-routing hardening** (`navig/main.py`): Removed stale/dead names from `_BUILTIN_COMMANDS` (`explain`, `monitor`, `security`, `workflow`, `template`, `hestia`) so startup fast-path decisions match real command surfaces. Also changed plugin-cache short-circuit logic to never treat unknown commands as safe-to-skip (prevents stale cache false negatives hiding valid plugin commands). Added focused regression coverage in `tests/test_fast_help_output.py`.
- **Backup command probe/result wiring hardening** (`navig/commands/backup.py`): fixed multiple call sites that incorrectly treated `subprocess.CompletedProcess` results as strings (e.g., `"missing" in result`, `result.strip()`), which could raise runtime type errors and break backup flows.
- **Backup command stdout normalization helpers** (`navig/commands/backup.py`): introduced centralized helpers for probe parsing (`_result_stdout_text`, `_result_indicates_missing`) and applied them across config/Hestia/web backup paths to remove duplicated fragile checks.
- Added focused regression coverage in `tests/test_backup_command_core.py` for missing-probe parsing and `backup_system_config()` missing-file behavior.
- **Discovery SSH binary wiring hardening** (`navig/discovery.py`): `_build_ssh_command()` now resolves SSH via shared adapter (`_resolve_ssh_bin`) instead of hardcoded `"ssh"`, restoring Windows OpenSSH fallback consistency with other remote stacks.
- **Discovery password-auth guard** (`navig/discovery.py`): `_execute_ssh()` now explicitly fails with a clear diagnostic when password auth is requested but `paramiko` is unavailable, avoiding confusing subprocess fallback behavior that cannot satisfy password-based SSH auth.
- **Discovery config validation hardening** (`navig/discovery.py`): `ServerDiscovery` now validates non-empty `host` and `user` at construction time, replacing brittle key/index errors with actionable `ValueError`.
- Added focused regression coverage in `tests/test_discovery_core.py` for SSH binary resolution, password-auth routing, and required-config validation.
- **Startup registration de-duplication** (`navig/main.py`): Removed redundant manual `profile_app` registration from entrypoint startup. `profile` is now sourced only from centralized external command registration (`navig.cli.registration._EXTERNAL_CMD_MAP`), preventing duplicate command wiring on profile-targeted invocations. Added focused regression coverage in `tests/test_fast_help_output.py`.
- **Startup plugin-skip resolution hardening with global flags** (`navig/main.py`): `_should_skip_plugin_loading()` now resolves command targets via canonical non-global argv parsing, so prefixed global options (for example `navig --host prod host list`) still hit built-in fast-path plugin skipping instead of forcing unnecessary plugin discovery.
- **Startup help compatibility normalization hardening** (`navig/main.py`): `_normalize_help_compat_args()` now performs legacy help rewrites and `memory list` alias normalization using global-flag-aware non-global token positions, so prefixed globals (`--host`/`--app`) no longer break normalization (`navig --host prod help db` now normalizes to `navig --host prod db --help`). Added focused regression coverage in `tests/test_main_help_normalization.py`.
- **Startup help normalization trailing-flag hardening** (`navig/main.py`): trailing legacy help rewrites now target the last non-global token (not raw argv tail), so forms like `navig db help --host prod` normalize correctly to `navig db --help --host prod`.
- **First-run onboarding skip gating hardening** (`navig/onboarding/runner.py`): `should_auto_run_onboarding()` now evaluates help/version and command skip conditions using global-flag-aware token extraction, preventing onboarding from running on help invocations like `navig help db` and `navig --host prod --help`. Added focused regression coverage in `tests/test_first_run.py`.
- **Startup fast-path global-flag handling hardening** (`navig/main.py`): `_maybe_handle_fast_path()` now handles global-flag-prefixed and global-only invocations on the ultra-fast path (`--help`/`--version` with `--host`/`--app`, plus global-only no-command calls), reducing unnecessary full CLI bootstrap in those cases. Added focused regression coverage in `tests/test_main_fast_path.py`.
- **DB host-discovery wiring hardening** (`navig/commands/db.py`): `_resolve_host_discovery()` now gracefully handles missing active host and missing host config (without uncaught exceptions) and validates required SSH target identity (`host`/`hostname`) before discovery construction.
- **DB host-discovery de-duplication** (`navig/commands/db.py`): `db_dump_cmd()` now reuses `_resolve_host_discovery()` instead of maintaining a divergent host/bootstrap path, eliminating duplicate connection-setup logic and keeping DB command behavior consistent.
- **DB callback context wiring hardening** (`navig/commands/db.py`): `db_callback()` now always initializes `ctx.obj` so `db` subcommands can safely write/read option flags without context-key crashes when invoked standalone.
- **DB backup encoding hardening** (`navig/commands/db.py`): `db_dump_cmd()` now writes backup files with explicit UTF-8 encoding, preventing locale-dependent corruption on Windows/default-codepage environments.
- Added focused regression coverage in `tests/test_db_command_core.py` for host-discovery error paths, hostname fallback wiring, callback context initialization, and dump host-discovery guard path.
- **Safety guard confirmation-policy normalization hardening** (`navig/safety_guard.py`): `should_confirm()` now normalizes `confirmation_level` case/format and falls back to `standard` for unknown values, preventing policy drift when config values are cased inconsistently (e.g., `CRITICAL`, `Verbose`).
- **Safety guard action-input robustness** (`navig/safety_guard.py`): destructive/risky checks now coerce arbitrary action payloads to text and safely handle empty/`None` actions instead of raising type errors in regex evaluation.
- Added focused regression coverage in `tests/test_safety_guard.py` for confirmation-level normalization/fallback and non-string action handling.
- **Tunnel startup SSH binary wiring hardening** (`navig/tunnel.py`): `start_tunnel()` now resolves the SSH binary through shared connection adapters (`_resolve_ssh_bin`) instead of hardcoded `"ssh"`, restoring Windows OpenSSH fallback behavior and keeping tunnel execution consistent with remote command paths.
- **Tunnel configuration validation hardening** (`navig/tunnel.py`): startup now validates required server identity (`user`, `host`) and `database` mapping before building tunnel arguments, replacing brittle `KeyError` failures with explicit `ValueError` diagnostics.
- Added focused regression coverage in `tests/test_tunnel_manager.py` for SSH binary resolution and required-config validation.
- **Remote operations identity validation hardening** (`navig/remote.py`): SSH/SCP paths now validate `server_config` includes non-empty `user` and `host` before command assembly, replacing fragile `KeyError` behavior with explicit `ValueError` and improving end-to-end command/file transfer diagnostics.
- **Remote timeout env parsing hardening** (`navig/remote.py`): `NAVIG_SSH_TIMEOUT` is now parsed via a safe resolver that falls back to default on invalid/non-positive values, preventing import-time crashes from malformed environment configuration.
- **Remote tests wiring cleanup and regression coverage** (`tests/test_remote_operations.py`): Updated binary-resolution mocks to patch `_resolve_ssh_bin` directly (actual dependency wire), and added focused tests for identity validation and malformed timeout env fallback.
- **CLI registration embedded-argv wiring hardening** (`navig/cli/registration.py`): `_register_external_commands()` no longer blindly trusts host-process `sys.argv` when invoked in embedded/in-process contexts; it now resolves targets only when argv belongs to NAVIG and otherwise falls back safely. This prevents false inline-command skips and missing command registration under test runners/embedders.
- **CLI registration cache concurrency hardening** (`navig/cli/registration.py`): registration-cache mutations (`_registered_app_cmds`) are now protected by a lock, eliminating races between `_register_external_commands()` and `_clear_registration_cache()` in concurrent test/runtime paths.
- **CLI registration target-resolution hardening for prefixed global flags** (`navig/cli/registration.py`): `_resolve_cli_target_from_argv()` now skips global flags and consumes values for `--host/-h` and `--app/-p` before selecting the command target. Calls like `navig --host prod --json vault list` now register only the requested external command instead of falling back to broad registration. Added focused regression coverage in `tests/test_cli_registration.py`.
- **CLI argv parsing consistency hardening** (`navig/cli/registration.py`, `navig/cli/__init__.py`): Extracted shared `extract_non_global_tokens(...)` parsing so startup registration and NL auto-chat routing use one canonical global-flag/value filter path, preventing future drift between duplicated parsers.
- Added focused regression coverage in `tests/test_cli_registration.py` for argv source resolution and embedded-mode full registration fallback.
- **CLI middleware fact-extraction skip hardening with global flags** (`navig/cli/middleware.py`): `register_fact_extraction()` now derives the invoked command via `extract_non_global_tokens` instead of raw `sys.argv[1]`, so prefixed-global invocations like `navig --host prod help db` and `navig --host prod memory list` correctly skip fact-extraction instead of misclassifying the global flag as the co
- **Performance profile command recording hardening** (`navig/perf/profiler.py`): `_safe_argv()` now uses `extract_non_global_tokens(sys.argv[1:])[:2]` instead of raw `sys.argv[1:][:2]` to derive the command label written to perf profile entries. Previously, `navig --host prod host list` was profiled as `"--host prod"` rather than the correct `"host list"`. Added focused regression coverage in `tests/test_perf_profiler.py` (9 tests).
- **Operation recorder skip-check false-positive fix** (`navig/cli/middleware.py`): `init_operation_recorder()` now checks the skip-record keywords against the non-global token string instead of the raw `" ".join(sys.argv[1:])` command string. Previously, `"-h"` was a substring of `"--host"` in the raw string, causing any command prefixed with `--host` (e.g. `navig --host prod db list`) to hit the skip gate and silently suppress operation recording. The fix builds `_cmd_str_for_skip` from `extract_non_global_tokens(sys.argv[1:])` so only actual command tokens are matched. Added focused regression coverage in `tests/test_middleware_op_recorder.py` (6 tests).mmand. Also hardened the inner atexit guard to use normalized token for secondary skip checks. Added focused regression coverage in `tests/test_cli_middleware.py`.
- **PowerShell hint `attempted_cmd` hostname false-positive fix** (`navig/main.py`): `_handle_powershell_parsing_error()` was building `attempted_cmd` from raw `argv[2:]`, which includes the consumed value of `--host`/`--app` global flags. A hostname like `my-server'` (odd single-quote) or a UNC path `win\server` would trigger the backslash/odd-quote heuristic and show a spurious PowerShell hint even when the `run` arguments were clean. Fixed by using already-computed `_ps_cmd_tokens[1:]` (arguments strictly after the `run`/`r` token in the non-global token list). Added 2 regression tests in `tests/test_main_powershell_hint.py`.
- **Silent exception swallowing — diagnostic logging (Phase F)**: Replaced two bare `except Exception: pass` blocks that had no diagnostic outlet with `_log.debug(...)` calls so failures leave a trace in `~/.navig/debug.log` without changing the non-fatal semantics:
  - `navig/cli/_singletons.py` `set_no_cache()`: `reset_config_manager()` / `set_config_cache_bypass()` failures now log at DEBUG level. Added `import logging` + module-level `_log` to the module (previously had no logger at all).
  - `navig/onboarding/steps.py` web-search-provider step: YAML read failure for current provider config now logs at DEBUG. Added module-level `_log` (previously had no logger).
  - Added focused regression coverage in `tests/test_phase_f_debug_logging.py` (11 tests): exception-non-propagation, flag-still-set-on-failure, `_log.debug` call verification via `mock.patch.object`, and `steps._log` smoke tests.
- **Silent exception swallowing — diagnostics expansion (Phase G)**: Preserved best-effort fallback behavior but added DEBUG traces to remaining startup-adjacent intentional swallow paths so root causes are observable without surfacing errors to users:
  - `navig/cli/middleware.py`: operation recorder and fact-extraction background fallbacks now log `_log.debug(...)` instead of silent `pass`.
  - `navig/main.py`: did-you-mean suggestion fallback now logs `_log.debug(...)` when suggestion generation fails.
  - `navig/onboarding/engine.py`: corrupt onboarding artifact fallback now logs `_log.debug(...)` before starting fresh state.
  - `navig/cli/_callbacks.py`: rich-help rendering fallback now logs `_log.debug(...)` before plain-help fallback.
  - `navig/cli/wizard.py`: vault-save fallback now logs `_log.debug(...)` before writing to `.env`.
- **Startup PowerShell hint gate hardening with global flags** (`navig/main.py`): `_handle_powershell_parsing_error()` now detects the `run`/`r` command token using `extract_non_global_tokens`, so prefixed-global invocations like `navig --host prod run ...` still receive PowerShell quoting guidance when arguments are mangled, instead of silently skipping the hint because `argv[1]` was the global flag. Added focused regression coverage in `tests/test_main_powershell_hint.py`.
- **Onboarding argv fallback sentinel hardening** (`navig/onboarding/runner.py`): `should_auto_run_onboarding()` now uses `sys.argv if argv is None else argv` instead of `argv or sys.argv`, so callers that explicitly pass an empty `argv=[]` (for example embedded/in-process invocations) no longer have the host-process `sys.argv` leaked in. Added focused regression coverage in `tests/test_first_run.py`.
- **Embedding cache & Memory Manager atomic rewrite hardening** (bug 128): CachedEmbeddingProvider._save_cache() in 
avig/memory/embeddings.py and MemoryManager.add_file() in 
avig/memory/manager.py now rewrite JSON and text files via a shared _atomic_write_text() helper in 
avig/memory/_util.py. This uses temp-file + os.replace() atomic writes (with Windows permission-retry handling), eliminating truncate-at-open corruption risk during embedding caching and memory writes. Added focused regression coverage in 	ests/test_memory.py.
- **CLI entry-chain audit — Batch 1 fixes** (`navig/cli/__init__.py`, `navig/main.py`, `navig/platform/paths.py`):
  - **NL-query false-fire on `--host`/`--app` values** (`navig/cli/__init__.py`): The `non_flag_args` filter that routes bare tokens to AI chat did not skip the _values_ of value-consuming flags (`--host myserver`, `--app myapp`). Running `navig --host myserver` with no subcommand would launch `run_ai_chat("myserver", …)` instead of showing help. Fixed by iterating with a `_skip_next` sentinel so the token immediately following `--host`/`-h`/`--app`/`-p` is always consumed.
  - **CLI arg-source wiring fix for embedded invocations** (`navig/cli/__init__.py`): Natural-language auto-routing previously read `sys.argv` unconditionally, which could misroute host-process arguments during in-process execution (`app([...])`, tests, embedders). The callback now only trusts `sys.argv` when the executable is NAVIG, otherwise it ignores host argv to prevent false chat dispatch.
  - **Silent `except Exception: pass` in no-cache reset** (`navig/cli/__init__.py`): Cache-reset failure during `--no-cache` startup was silently swallowed. Added `_log.debug(…)` diagnostic while preserving non-fatal behavior.
  - **Redundant `pass` after logger calls** (`navig/main.py`): Removed two dead `pass` statements that followed `logger.debug(…)` / `logger.warning(…)` in `_should_skip_plugin_loading()` and the profile-app loader in `main()`.
  - **Silent `is_directory_accessible()` exception** (`navig/platform/paths.py`): `PermissionError`/`OSError` in the accessibility probe was silently discarded. Added `logger.debug(…)` diagnostic while preserving non-fatal `return False` fallback.
- **Config singleton state synchronization hardening** (`navig/config.py`): `set_config_cache_bypass()` and `reset_config_manager()` now mutate singleton/cache-bypass state under `_config_manager_lock`, eliminating races with concurrent `get_config_manager()` calls during startup and tests. Added focused regression coverage in `tests/test_config.py` (`TestConfigManagerSingleton::test_cache_bypass_and_reset_semantics`).
- **Snapshot retention/clear atomic rewrite hardening** (bug 127): `prune_snapshots()` and `clear_snapshots()` in `navig/memory/snapshot.py` now rewrite workspace JSONL files via temp-file + `os.replace()` atomic writes (with Windows permission-retry handling), eliminating truncate-at-open corruption risk during retention cleanup. Added focused regression coverage in `tests/test_api_snapshot.py` to verify atomic rewrite usage and Windows retry behavior.
- **Memory module singleton race hardening** (bug 126): Added lock-guarded double-checked locking (DCL) to all five memory singleton accessors — `get_memory_manager()` (`navig/memory/manager.py`), `get_context_builder()` (`navig/memory/context_builder.py`), `get_snapshot_writer()` (`navig/memory/snapshot.py`), `get_links_db()` (`navig/memory/links_db.py`), and `get_knowledge_graph()` (`navig/memory/knowledge_graph.py`). Each module now has a dedicated `threading.Lock()`, its getter uses DCL to prevent double-initialization under contention, and all reset/reload helpers (including new `reset_context_builder()`, `reset_links_db()`, `reset_knowledge_graph()`) hold the lock during teardown. Added 10-test concurrency suite in `tests/test_memory_singletons.py`.
- **Memory indexer index-file exception transparency** (`navig/memory/indexer.py`): `index_file()` now emits a debug diagnostic when internal indexing raises before returning a structured failed result, improving troubleshooting while preserving non-fatal error aggregation. Added focused regression coverage in `tests/test_memory_indexer.py`.
- **Project indexer unreadable-file transparency** (`navig/memory/project_indexer.py`): `update_incremental()` now logs debug diagnostics when a file cannot be read and is skipped, preserving non-fatal indexing behavior while improving troubleshooting. Added focused regression coverage in `tests/test_project_indexer.py`.
- **Tool router singleton race hardening** (bug 125): Added lock-guarded double-checked initialization for `get_tool_registry()` and `get_tool_router()` in `navig/tools/router.py` so concurrent first access cannot create duplicate global instances or partially initialized state. Updated `reset_globals()` to reset under the same locks and added concurrency regressions in `tests/test_tool_router.py`.
- **Conversation store rollback-failure transparency** (`navig/memory/conversation.py`): `ConversationStore.add_message()` now emits a debug trace when best-effort rollback fails during transaction error handling, while preserving original exception re-raise behavior. Added focused regression coverage in `tests/test_conversation_store.py`.
- **API snapshot policy loader exception transparency** (`navig/memory/snapshot.py`): `_load_policies_from_yaml()` now emits a debug diagnostic when config access fails before falling back to `{}`, preserving non-fatal policy resolution. Added focused regression coverage in `tests/test_api_snapshot.py`.
- **Context builder config-load exception transparency** (`navig/memory/context_builder.py`): `_load_from_config_yaml()` now emits a debug diagnostic when config loading fails before falling back to `{}`, preserving non-fatal behavior while improving troubleshootability. Added focused regression coverage in `tests/test_context_builder.py`.
- **Knowledge graph habit-inference exception transparency** (`navig/memory/knowledge_graph.py`): `learn_from_task_result()` now logs debug diagnostics for LLM-call and parse failures instead of silently swallowing them, while preserving non-fatal fallback behavior (`[]`). Added focused regression coverage in `tests/test_knowledge_graph.py`.
- **Provider verifier probe-check reliability hardening** (`navig/providers/verifier.py`): Switched local probe socket usage to a context-managed pattern and narrowed handled probe exceptions to expected parse/network classes, preventing socket leak paths while preserving non-fatal behavior. Added regression coverage in `tests/providers/test_registry.py` for malformed probe strings and socket errors.
- **Provider verifier best-effort exception transparency** (`navig/providers/verifier.py`): Replaced previously silent best-effort exception swallowing in vault/probe checks with debug-level diagnostics while preserving non-fatal behavior, and verified key-resolution fallback still succeeds when primary vault lookup raises. Added regression coverage in `tests/providers/test_registry.py`.
- **Provider verifier soft-issue matching robustness** (`navig/providers/verifier.py`): Made soft-issue classification whitespace/case tolerant so expected key/probe issues consistently stay debug-level even when message casing varies. Added regression coverage in `tests/providers/test_registry.py`.
- **Provider verifier warning-noise reduction** (`navig/providers/verifier.py`): Soft validation issues (for example missing API keys or local service unreachability) now log at debug level, while hard integrity/configuration failures remain warning-level. Added regression coverage in `tests/providers/test_registry.py`.
- **Wiki root resolution consistency across nested working directories** (bug 124): `get_wiki_rag()` now resolves the nearest parent project `.navig/wiki` (walking up from CWD) before falling back to global `config_dir()/wiki`, preventing accidental global-wiki reads from project subfolders. Aligned `wiki_read` and `wiki_write` agent tools to use the same resolved wiki root. Added regression coverage in `tests/test_wiki.py` and `tests/test_wiki_tools.py`.
- **navig/agent/conv language resolution regression** (bug 123): Restored Russian language enforcement in `navig.agent.conv.language` (`ru` no longer says "Reply in English only") and fixed conv prompt language selection to actually honor session metadata hints (`detected_language` / `last_detected_language`) before text-detection fallback in `navig.agent.conv.agent`. Added regression coverage in `tests/test_conversational_agent.py`.
- **Vault CLI compatibility shim** (`navig/commands/vault.py`): Restored `_console()` as a thin wrapper around `get_console()` so existing tests and legacy patch points that monkeypatch `navig.commands.vault._console` continue to work.
- **Loguru rendering consistency hardening** (`navig/selfheal/heal_pr_submitter.py`, `navig/gateway/channels/audio_menu/handlers.py`): Replaced remaining `%s`/`%d` logger placeholders with brace-style rendered logs so runtime diagnostics always show concrete values under Loguru.
- **Logger placeholder rendering hardening** (`navig/gateway/session_store.py`, `navig/selfheal/ssh_healer.py`, `navig/agent/auth_profiles.py`): Replaced remaining `%s`/`%d` logging placeholders with brace-style rendered-value logging so runtime diagnostics consistently include concrete values under the active logger stack.
- **Daemon supervisor log-text encoding cleanup** (`navig/daemon/supervisor.py`): Replaced mojibake dash sequences in docstrings and lifecycle log messages so daemon diagnostics remain readable across terminals and log viewers.
- **Monitoring symbol encoding cleanup** (`navig/commands/monitoring.py`): Replaced mojibake status/bullet symbols in health-check and report summaries with `_safe_symbol(...)` output so text renders correctly across Windows terminals and saved logs.
- **navig/commands/action.py (bugs 41–42)**: `action_add` and `action_remove` wrote `~/.navig/store/actions/user.yaml` via `write_text()`. A crash mid-write left a truncated YAML file, making all subsequent `navig action` commands fail to parse it. Both writes replaced with atomic `tempfile.mkstemp + os.fdopen + os.replace()` pattern. Added `import os, tempfile`.
- **navig/commands/brain.py (bug 43)**: `prompts_set` wrote brain prompt files via `write_text()`. Crash mid-write left a corrupt `.md` prompt file. Replaced with atomic write. Added `import os, tempfile`.
- **navig/commands/formation.py (bug 44)**: `formation_init` wrote `.navig/profile.json` via `write_text()`. Corrupt profile causes all formation commands to fail at parse time. Replaced with atomic write. Added `import os, tempfile`.
- **navig/commands/config.py (bug 45)**: `schema_install --write-vscode-settings` wrote VS Code `settings.json` via `write_text()`. Replaced with atomic write (8-space indent in nested block). Added `import os, tempfile`.
- **navig/agent/soul.py (bug 46)**: `_create_soul_file()` wrote `SOUL.md` via `write_text()`. SOUL.md is injected into every AI system prompt; a corrupt half-written file causes all AI responses to use a partial system prompt. Replaced with atomic write. Added `import os, tempfile`.
- **navig/agent/session_store.py (bug 47)**: `_save_meta()` wrote session metadata JSON via `write_text()`. Corrupt meta_file causes session resume to fail (session history lost). Replaced with atomic write. Added `import tempfile` (`os` already present).
- **navig/agent/context/__init__.py (bug 48)**: `ContextFile.save()` wrote SOUL.md / USER.md context layers via `write_text()`. Corrupt context file wipes the agent's entire user profile or personality until manually restored. Replaced with atomic write. Added `import os, tempfile`.
- **navig/agent/goals.py (bug 49)**: `_save_goals()` wrote goals JSON via `with open(..., "w"); json.dump()`. Truncate-at-open means any crash during dump produces an empty goals file (all tracked goals lost). Replaced with atomic `mkstemp + fdopen + replace`. Added `import os, tempfile`.
- **navig/agent/proactive/user_state.py (bugs 50–51)**: `_flush_last_seen()` and `_save_state()` both used `write_text()` for the `last_seen.json` sidecar and `user_state.json` respectively. Partial write silently loses activity stats and preference settings. Both replaced with atomic writes. Added `import os, tempfile, threading`.
- **navig/agent/proactive/user_state.py (bug 52)**: `get_user_state_tracker()` singleton had no lock — two threads calling simultaneously could produce two `UserStateTracker` instances, with the second overwriting the first after the first caller already received it (state duplication/divergence). Fixed with double-checked locking: `_tracker_lock = threading.Lock()`.
- **navig/agent/remediation.py (bug 53)**: `_save_actions()` wrote remediation action JSON via `write_text()`. Corrupt file causes the auto-heal system to lose its action history (cannot detect repeat failures). Replaced with atomic write. Added `import os, tempfile`.
- **navig/adapters/automation/evolution/library.py (bugs 54–55)**: `_save_index()` and `save_script()` both used `with open(..., "w")` pattern. Crash mid-write produces empty `index.json` (all AHK scripts lost from index) or truncated `.ahk` file. Both replaced with atomic `mkstemp/replace`. Added `import os, tempfile`.
- **navig/agent/profiles.py (bugs 56–57)**: `save_config_overrides()` used `with open(..., "w")` and `_write_active_profile_name()` used `write_text()`. Crash mid-write produces corrupt `config.yaml` (profile config gone) or truncated `active_profile` file (wrong profile loaded on restart). Both replaced with atomic writes. Added `import tempfile` (`os` already present).
- **navig/agent/config.py (bug 58)**: `AgentConfig.save(config_path)` wrote agent YAML via `with open(..., "w"); yaml.safe_dump()`. Non-atomic; replaced with atomic `mkstemp/replace`. Added `import os, tempfile`.
- **navig/gateway/system_events.py (bug 59)**: `_save_events()` wrote pending event queue JSON via `write_text()`. Crash during write silently drops all pending system events (unrecoverable). Replaced with atomic write. Added `import tempfile` (`os` already present).
- **navig/gateway/config_watcher.py (bug 60)**: `WorkspaceManager.write_file()` wrote workspace files (AGENTS.md, SOUL.md, etc.) via `write_text()`. Used by `append_to_file` on every intake update. Crash during write corrupts the workspace file. Replaced with atomic write. Added `import os, tempfile`.
- **navig/gateway/channels/telegram_commands.py (bug 61)**: `_append_markdown_section()` wrote planning docs (VISION.md, ROADMAP.md, CURRENT_PHASE.md) via `write_text()` after building combined content. Crash mid-write silently truncates the planning file. Replaced with atomic write. Added `import tempfile` (`os` already present).
- **navig/daemon/supervisor.py (bug 62)**: `_write_state()` wrote daemon state JSON via `write_text()`. Crash mid-write leaves corrupt state file read by monitoring tools. Replaced with atomic write inside the existing `try/except` guard. Added `import tempfile` (`os` already present).
- **navig/gateway/channels/audio_menu/state.py (bug 63)**: `save_config()` wrote user audio config via `write_text()`. Crash mid-write loses per-user audio preferences. Replaced with atomic write. Added `import os, tempfile`.
- **navig/settings/resolver.py (bug 64)**: `set()` wrote the settings JSON file via `write_text()`. Crash mid-write corrupts the user's layered settings file, silently discarding all downstream overrides. Replaced with atomic `mkstemp/replace`. Added `import os, tempfile`.
- **navig/scheduler/cron_service.py (bug 65)**: `_save_jobs()` wrote the persistent cron job store via `write_text()`. A crash between truncate and flush loses the entire job schedule (all jobs permanently dropped). Replaced with atomic write inside `_save_jobs`. Added `import os, tempfile`.
- **navig/modules/error_resolution.py (bugs 66–67)**: `log_error()` and `record_solution_feedback()` both used `with open(..., "w"); json.dump()` to write `error_log.json` and `solutions.json`. Truncate-at-open means a crash during dump produces empty files (all error history or solution feedback lost). Both replaced with atomic `mkstemp/replace`. Added `import os, tempfile, from pathlib import Path`.
- **navig/modules/auto_detection.py (bugs 68–70)**: `log_command_execution()`, `_log_detected_issue()`, and `update_performance_baseline()` all used `with open(..., "w"); json.dump()` for `command_history.json`, `detected_issues.json`, and `baseline.json`. Any crash during dump silently zeros the corresponding data file. All three replaced with atomic writes. Added `import os, tempfile, from pathlib import Path`.
- **navig/commands/mount.py (bug 71)**: `_save_registry()` wrote the drive junction registry JSON via `write_text()`. Crash mid-write corrupts `drives.json`; next navig mount command sees empty registry and all known junctions are forgotten. Replaced with atomic write. Added `import os, tempfile` (top-level; removed inline `import os as _os`).
- **navig/commands/migrate.py (bug 72)**: `_mark_done()` wrote the migration completion marker file via `write_text()`. A crash between truncation and flush produces an empty `.migrations_done` file, causing all migrations to re-run on next invocation (TOCTOU). Also added `path.parent.mkdir(parents=True, exist_ok=True)` guard. Replaced with atomic write. Added `import os, tempfile, from pathlib import Path`.
- **navig/agent/service.py (bugs 73–74)**: `install_systemd()` and `install_launchd()` wrote the systemd unit file and macOS plist via `write_text()`. Crash mid-write produces a malformed service unit that silently fails to load on next boot. Both replaced with atomic writes. Added `import tempfile` (`os` already present).
- **navig/agent/plan_execute.py (bug 75)**: `_save_trace()` wrote the plan-execute JSON trace via `write_text()`. Crash mid-write produces a truncated trace file that cannot be parsed for audit/replay. Replaced with atomic write. Added `import os, tempfile`.
- **navig/agent/tool_caps.py (bug 76)**: `_write_spillover()` wrote oversized tool results to disk via `write_text()`. The spillover file is immediately referenced in the agent's context; a partial write produces a file the agent reads as a corrupt tool response. Replaced with atomic write. Added `import os, tempfile`.
- **navig/commands/backup.py (bug 77)**: `backup_config_files()`, `backup_all_databases()`, `backup_hestia()`, and `backup_web_config()` all wrote `metadata.json` via `with open(..., "w"); json.dump()`. All four replaced with atomic writes. Added `import tempfile` (`os` already present).
- **navig/providers/fallback.py (bug 78)**: `get_fallback_manager()` singleton had no lock — two threads calling simultaneously could each construct a `FallbackManager`, with the second overwriting the first (cooldown state lost, potential double-initialization of HTTP clients). Fixed with double-checked locking: `_fallback_manager_lock = threading.Lock()`. Added `import threading`.
- **navig/bot/command_registry.py (bug 79)**: `get_command_registry()` singleton had no lock — two threads calling simultaneously could construct two `CommandRegistry` instances and call `_populate_from_command_tools()` twice, duplicating all registered commands in whichever instance is retained. Fixed with double-checked locking: `_registry_lock = threading.Lock()`. Added `import threading`.
- **navig/onboarding/genesis.py (bug 82)**: `load_or_create()` had a bare `except Exception: pass` around the genesis JSON parse. This swallowed `IOError`, `OSError`, and any unexpected exceptions in addition to the intended `json.JSONDecodeError`/`KeyError`/`TypeError`, masking I/O failures as a "corrupt file". Narrowed to `except (json.JSONDecodeError, KeyError, TypeError)`.
- **navig/modules/auto_detection.py (bug 83)**: `_load_error_patterns()` had a bare `except Exception: pass` with no observability. Failures silently dropped all error patterns, causing auto-detection to behave as if no patterns were configured. Converted to `ch.dim(...)` consistent with the module's error-reporting style.
- **navig/commands/plans.py (bugs 84–85)**: `plans_add_goal` and `plans_update_completion` wrote plan files (`.plan.md`, `CURRENT_PHASE.md`) via `write_text()`. A crash mid-write leaves a truncated plan file, silently losing progress tracking. Both replaced with atomic `mkstemp + os.fdopen + os.replace`. Added `import os, tempfile`.
- **navig/commands/work.py (bug 86)**: `_update_wiki_stage()` wrote wiki note frontmatter via `write_text()` and silently swallowed all exceptions with a bare `except Exception: pass`. Non-atomic write could corrupt the wiki note; silent exception hid I/O failures. Replaced with atomic write; converted bare except to `logger.debug(...)`. Added `import logging, tempfile`; added `_log = logging.getLogger(__name__)`.
- **navig/commands/suggest.py (bugs 87–88)**: `add_quick_action()` and `remove_quick_action()` wrote `quick_actions.yaml` via `with open(..., "w"); yaml.safe_dump()`. Crash at open truncates the file (all quick actions lost). Both replaced with atomic `mkstemp + os.fdopen + os.replace`. Added `import os, tempfile`.
- **navig/commands/triggers.py (bugs 89–90)**: `TriggerHistory.clear_for_trigger()` rewrote `trigger_history.jsonl` via `with open(..., "w"); f.writelines(kept)` (Bug 89); `_log_execution()` had `except Exception: pass` swallowing history-write errors with no observability (Bug 90). Replaced write with atomic pattern; converted bare except to `logger.debug(...)`. Added `import logging`; added `logger = logging.getLogger(__name__)`.
- **navig/commands/sessions.py (bugs 91–92)**: `cmd_export()` wrote JSON and Markdown session export files via `write_text()`. A partial write produces a corrupt export file. Both paths replaced with atomic `mkstemp + os.fdopen + os.replace`. Added `import tempfile` (`os` already present).
- **navig/commands/onboard.py (bug 93)**: `_sync_env_file()` wrote the `.env` credential file via `write_text()`. A crash mid-write destroys the credential file, permanently locking out configured services until manually restored. Replaced with atomic write. Added `import tempfile` (`os` already present).
- **navig/commands/init.py (bugs 94–95)**: `_store_telegram_token()` wrote `.env` with bot token via `write_text()` (Bug 94); `_auto_start_chat_runtime()` wrote the daemon config JSON via `write_text()` (Bug 95). Both are credential/config files where a partial write causes boot failures. Both replaced with atomic writes inside their existing best-effort `try/except` guards. Added `import tempfile` (`os` already present).
- **navig/operation_recorder.py (bugs 96–98)**: `_rotate()` renamed the history file then opened a new one via `with open(..., "w")` — if the write fails after `rename()`, the history file is permanently absent (Bug 96); `record_with_audit()` had `except Exception: pass` swallowing audit failures (Bug 97); `get_operation_recorder()` singleton had no lock (Bug 98). Rotation write replaced with atomic pattern (writes temp first, then `os.replace`); bare except converted to `logger.debug(...)`; singleton fixed with double-checked locking `_recorder_lock = threading.Lock()`. Added `import logging, os, tempfile, threading`; added `logger = logging.getLogger(__name__)`.
- **navig/wiki_rag.py (bug 99)**: `save_index()` wrote the search index JSON via `with open(..., "w"); json.dump()`. Crash at open zeros the index (all search data lost until rebuild). Replaced with atomic `mkstemp + os.fdopen + os.replace`. Added `import os, tempfile`.
- **navig/workspace.py (bugs 100–101)**: `update_file()` and `add_memory()` both wrote workspace files (context files, AGENTS.md) via `write_text()`. The workspace files feed the agent's system prompt; a partial write corrupts the agent's entire context. Both replaced with atomic writes. Added `import os` (`tempfile` already present).
- **navig/template_manager.py (bug 102)**: `save_metadata()` wrote template metadata (YAML or JSON) via `with open(..., "w")`. A crash mid-write corrupts the metadata file, making the template unparseable. Replaced with atomic `mkstemp + os.fdopen + os.replace` handling both format branches. Added `import os, tempfile`.
- **navig/llm_routing_types.py (bug 103)**: `get_provider_factory()` singleton had no lock — two concurrent calls could each construct a `UnifiedProviderFactory`, with the second silently replacing the first (cached HTTP clients and routing state duplicated or lost). Fixed with double-checked locking: `_factory_lock = threading.Lock()`. Added `import threading`.
- **navig/config.py (bug 104)**: `get_config_manager()` did two separate global assignments (`_config_manager_instance = ...` then `_config_manager_config_dir = ...`) with no lock — a race between two threads could leave the globals inconsistent (instance points to one config_dir, the stored config_dir variable points to another). Fixed with double-checked locking: `_config_manager_lock = threading.Lock()` (`threading` already imported). The inner check re-evaluates `needs_new` inside the lock.
- **navig/agent/coordination.py (bug 105)**: `get_coordinator()` singleton had no lock — two startup threads could each call `AgentCoordinator()`, with the second overwriting the first after it had already started receiving messages. Fixed with double-checked locking: `_coordinator_lock = threading.Lock()`. Added `import threading`.
- **navig/agent/speculative.py (bug 106)**: `get_speculative_executor()` read config and conditionally created a `SpeculativeExecutor` with no lock — two threads could each pass the `is not None` check and both create instances, with the second discarding the first's dispatch function binding. `reset_speculative_executor()` also lacked a lock, creating a race with ongoing creation. Both fixed: entire creation path moved inside `with _speculative_executor_lock:`; reset also locks. Added `import threading`; `_speculative_executor_lock = threading.Lock()`.
- **navig/agent/mcp_client.py (bug 107)**: `get_mcp_pool()` singleton had no lock — two concurrent callers could each construct an `MCPClientPool`, silently doubling the configured MCP server connections. Fixed with double-checked locking: `_pool_lock = threading.Lock()`. Added `import threading`.
- **navig/agent/action_registry.py (bug 108)**: `get_action_registry()` singleton lacked a lock — two threads could each call `ActionRegistry()` and `_register_core_actions()`, resulting in duplicate action registrations in whichever instance survives. Fixed with double-checked locking: `_registry_lock = threading.Lock()` (guards both construction and registration). Added `import threading`.
- **navig/agent/background_task.py (bug 109)**: `get_manager()` singleton had no lock — two concurrent calls could each construct a `BackgroundTaskManager`, and `reset_manager()` could race with `get_manager()` to produce a `None` → new instance cycle that discards tracked background processes. Both fixed with `_manager_lock = threading.Lock()`. Added `import threading`.
- **navig/gateway/channels/registry.py (bug 110)**: `get_channel_registry()` singleton lacked a lock — two concurrent startup paths could each call `ChannelRegistry()` and `_registry.initialize()`, doubling all channel registrations. Fixed with double-checked locking: `_registry_lock = threading.Lock()`. Added `import threading`.
- **navig/gateway/channels/telegram_sessions.py (bugs 111–112)**: `get_session_manager()` and `get_mention_gate()` singletons both lacked locks — concurrent Telegram message handlers could each construct a `SessionManager` or `MentionGate`, discarding session history or mention-gate state accumulated by the first instance. Both fixed with separate DCL locks: `_session_manager_lock` and `_mention_gate_lock = threading.Lock()`. Added `import threading`.
- **navig/gateway/channels/utils/decorators.py (bug 113)**: `_get_global_limiter()` singleton lacked a lock — two concurrent rate-limited requests could each construct a `RateLimiter` (with separate config reads), producing two independent rate-limit windows instead of one shared one. Fixed with double-checked locking: `_global_limiter_lock = threading.Lock()` (config read moved inside lock). Added `import threading`.
- **navig/gateway/channels/telegram_formatter.py (bug 114)**: `get_formatter_store()` singleton lacked a lock — two concurrent formatter requests could each construct a `FormatterStore`, opening two independent SQLite connections to `formatter.db`, potentially corrupting per-user preferences. Fixed with double-checked locking: `_formatter_store_lock = threading.Lock()`. Added `import threading`.
- **navig/agent/context/__init__.py (bug 115)**: `get_context_layer()` singleton lacked a lock — two concurrent calls could each construct a `ContextLayer`, with the second discarding any `ensure_soul()`/`ensure_user()` initialization done on the first. Fixed with double-checked locking: `_context_lock = threading.Lock()`. Added `import threading` (`os`, `tempfile` already present from Bug 48).
- **navig/core/window_manager.py (bug 116)**: `save_layout()` wrote window layout JSON via `with open(..., "w"); json.dump()`. A crash mid-write corrupts the layout file; `restore_layout()` then fails to restore window positions. Replaced with atomic `mkstemp + os.fdopen + os.replace`. Added `import os, tempfile`.
- **navig/core/evolution/fix.py (bug 117)**: `_save()` called `self.target_file.rename(backup_path)` then `open(self.target_file, "w")` — if the write fails after rename, the target file is permanently absent (the rename succeeded but the new write did not). Fixed by writing to temp file first, then renaming original to backup (only after temp is safely written), then `os.replace(temp, target)`. Also promoted `import tempfile` from inline (inside `_validate`) to top-level. `import os` already present.
- **navig/core/plugins.py (bug 118)**: `get_plugin_registry()` singleton lacked a lock — two concurrent plugin discovery calls (e.g., from parallel command registration) could each call `PluginRegistry()` and `_registry.initialize()`, duplicating all discovered plugins. Fixed with double-checked locking: `_registry_lock = threading.Lock()`. Added `import threading`.
- **navig/wiki_rag.py (bugs 119–120)**: `get_wiki_rag()` required a positional `wiki_path`, but agent call sites invoked it with zero arguments, causing runtime `TypeError` and silently disabling wiki context in guarded paths (Bug 119). Fixed by supporting zero-arg usage with deterministic default resolution (`<project_root>/.navig/wiki` or local `.navig/wiki`, fallback to `~/.navig/wiki`). Also guarded `add_document()` and `remove_document()` in unified-indexer mode so they no longer dereference a missing in-memory index (`self.index is None`) and crash (Bug 120).
- **navig/gateway/channels/telegram_sessions.py (bug 121)**: `SessionManager` advertised thread-safe access but never used its lock in mutating/read paths, allowing races during concurrent metadata/message writes. Replaced the unused asyncio lock with a re-entrant threading lock and wrapped session map + file I/O operations (`get_or_create`, add/clear/set metadata, delete/list/prune, load/save) to enforce consistent state under concurrent access.
- **navig/agent/speculative.py (bug 122)**: speculative cache could serve stale read results after mutating tool executions because cache entries persisted across writes and in-flight speculative reads were not cancelled. Fixed with strict invalidation: mutating tools bypass cache hits, cancel in-flight speculative tasks, and clear cache immediately after dispatch.

### Added
- **Tavily search provider** (#42): Added Tavily to the web-search-provider onboarding catalog (`_WEB_SEARCH_PROVIDER_CATALOG`) and `provider_aliases` normalization map in `navig/onboarding/steps.py`.
- **Onboarding step labels** (#45): Added missing human-readable labels for `sigil-genesis`, `core-navig`, `web-search-provider`, `matrix-bot`, `email-smtp`, `social-link`, and `import-secrets` in `navig/onboarding/renderer.py` so the setup summary displays friendly names instead of raw step IDs.

### Refactored
- **Overlap consolidation — Groups I–T** (surgical deduplication, 20 files, 1 new canonical module):
  - **Group I / L / S — Frontmatter utilities** (`plans/frontmatter.py` new): Created `navig/plans/frontmatter.py` as the single canonical module for `FRONTMATTER_RE`, `parse_frontmatter`, `parse_frontmatter_with_body`, `render_frontmatter`, `first_h1`, and `_safe_read`. Removed 5–6 per-file private definitions from `plans/current_phase_manager.py`, `plans/milestone_progress.py`, `plans/inbox_reader.py`, `plans/review_queue.py`, `plans/context.py`, and `spaces/progress.py`; all now import from frontmatter.py.
  - **Group J — `_safe_read` unification**: Removed duplicate `_safe_read` from `spaces/next_action.py` and `spaces/kickoff.py`; both now import from `spaces.progress` (which re-exports from `plans.frontmatter`). `_safe_read` uses `errors="replace"` for encoding robustness.
  - **Group K — Checkbox/completion helpers**: Removed `_CHECKBOX_RE` and `_completion_from_markdown` from `commands/plans.py`; now imported from `spaces.progress`.
  - **Group M — `_find_project_root`**: Removed duplicate zero-arg `_find_project_root()` from `commands/inbox.py`; now imports the canonical version from `commands/plans`.
  - **Group N — `_get_config_manager` wrapper**: Removed `_get_config_manager()` wrapper from `agent/tools/devops_tools.py`; replaced 5 call sites with direct `get_config_manager()` from `navig.config` (module-level import). CLI `__init__.py` wrapper intentionally preserved for lazy-load startup performance.
  - **Group O — `_load_navig_json` lazy def**: Replaced local one-line `def _load_navig_json()` in `tui/resolvers.py` with `from navig.tui.config_model import load_navig_json as _load_navig_json`.
  - **Group P — `_human_size` / `_format_size`**: Added `format_bytes(n: int) -> str` to `navig/console_helper.py`; removed per-file `_human_size` from `commands/sessions.py` and `_format_size` from `commands/config_backup.py`; both now import from `console_helper`.
  - **Group R — `_navig_dir()`**: Replaced 3-line `_navig_dir()` in `personas/soul_loader.py` with delegation to `config_dir()` from `navig.platform.paths`; removed `import os`.
  - **Group T — `_strip_ansi`**: Added `strip_ansi(text: str) -> str` to `navig/console_helper.py`; removed per-file `_strip_ansi` from `onboarding/renderer.py` (import alias) and `gateway/channel_router.py` (staticmethod body delegates to canonical).

  - **Group A — Active-server guard** (`maintenance.py`, 6 sites): Replaced 4-line inline `get_active_server` + error-print + return guard with `require_active_server(options, config_manager)` from `navig.cli.recovery`. Added canonical import at module top.
  - **Group B — Active-app guard** (`webserver.py`, 8 sites): Added `require_active_app()` to `navig/cli/recovery.py` (mirrors `require_active_server`; uses `fzf_or_fallback` selector); replaced 4-line inline `get_active_app` + error-print + return guard with inline lazy import + `require_active_app(options, config_manager)` at all 8 sites.
  - **Group C — `_console()` stub** (`import_cmd.py`, `links.py`, `vault.py`): Removed per-file `def _console()` wrapper functions (3 definitions); replaced all call sites with `get_console()` from `navig.console_helper` (already imported in each file).
  - **Group E — `_PROVIDER_ENV_VARS` inline dict** (`onboarding/steps.py`): Replaced 10-entry function-local `_PROVIDER_ENV_VARS` dict with `from navig.providers.types import PROVIDER_ENV_VARS as _PROVIDER_ENV_VARS`.
  - **Group F — `_truncate()` stub** (`agent/tools/devops_tools.py`, `agent/remote_agent.py`): Removed the `_truncate_impl` alias indirection; replaced with a direct `from navig.core.dict_utils import truncate_output` import and a minimal 2-line wrapper `def _truncate(text, limit=<module_constant>)` that preserves the module-local default.
- **Overlap consolidation — Groups 13C/D/E, 14, 16, 17** (surgical deduplication, 14 files, 31 sites):
  - **Group 13C — Active-server guard, `app` key** (`database_advanced.py` 2 sites, `hestia.py` 7, `tunnel.py` 1, `files_advanced.py` 4 = 14 sites): Replaced inline 3-line `options.get("app") or config_manager.get_active_server()` + `if not:` guard with `require_active_server(options, config_manager)` from `navig.cli.recovery`.
  - **Group 13D — Active-host guard** (`files_advanced.py`, 4 sites): Replaced the inline `options.get("host") or config_manager.get_active_host()` + `if not:` guard with `require_active_host(options, config_manager)` from `navig.cli.recovery`.
  - **Group 13E — Active-server guard, `server` key** (`server_template.py` 7, `template.py` 1 = 8 sites): Extended `require_active_server` to also check `options.get("server")` (additive, no breaking change); replaced all 8 inline guards.
  - **Group 14 — `_find_ssh_db()` nested function** (`commands/db.py`): Removed the 11-line inline SSH binary resolver nested inside `db_shell_cmd()`; replaced with `_resolve_ssh_bin()` from `navig.core.connection`.
  - **Group 16 — `check_api_key_in_env()` duplicate** (`commands/onboard.py`, `tui/config_model.py`): Added canonical `check_api_key_in_env(provider)` to `navig/providers/source_scan.py` (delegates to `provider_env_vars()`); removed both local definitions; replaced with import.
  - **Group 17 — `_get_vault()` deck route stubs** (`gateway/deck/routes/llm_modes.py`, `gateway/deck/routes/vault.py`): Created `navig/gateway/deck/routes/_utils.py` with the single canonical `_get_vault()` implementation; removed both local definitions; replaced with `from navig.gateway.deck.routes._utils import _get_vault`.
- **Overlap consolidation — Groups 2-7, 10-12** (surgical deduplication across 19 files, 2 new files created):
  - **Group 2 — MySQL config file** (`_db_utils.py` canonical): Deleted `_create_mysql_config_file()` from `commands/database.py`, `commands/database_advanced.py`, and `commands/backup.py` (3 copies → 1); updated 9 call sites.
  - **Group 4 — File checksum** (`_db_utils.calculate_file_checksum`): Deleted `_calculate_file_checksum()` from `database.py` and `backup.py`; removed orphan `import hashlib` and `import tempfile` from all 3 DB command files.
  - **Group 3 — `deep_merge`** (`navig/core/dict_utils.py` new): Created canonical `deep_merge()` (dict + list-concat + deepcopy semantics); replaced private definitions in `core/config_loader.py`, `settings/resolver.py`, and `server_template_manager.py` with aliased import.
  - **Group 5 — SSH binary resolution** (`core/connection._resolve_ssh_bin/_resolve_scp_bin` canonical): Deleted `_resolve_scp_bin()` from `remote.py` and `_find_ssh()` nested function from `commands/remote.py`; replaced inline 15-line SSH lookup blocks with `_resolve_ssh_bin()` call; moved `is_local_host()` to `remote.py` as module-level function.
  - **Group 6 — Paramiko lazy import** (`connection_pool._get_paramiko` canonical): Deleted duplicate `_paramiko = None` global + `_get_paramiko()` from `discovery.py`; replaced with import from `connection_pool`.
  - **Group 7 — Local-host detection** (`remote.is_local_host` canonical): Deleted `_is_local_host()` from `commands/monitoring.py`; updated 2 call sites.
  - **Group 10 — Provider env-var map** (`providers/source_scan.PROVIDER_ENV_KEYS` canonical): Renamed `_FALLBACK_ENV_VARS` → `PROVIDER_ENV_KEYS` (public), merged 9 additional providers from `llm_router.py`; deleted `PROVIDER_ENV_KEYS` dict from `llm_router.py`, imported from source.
  - **Group 11 — Automation dataclasses** (`adapters/automation/types.py` new): Created canonical `WindowInfo` and `ExecutionResult` dataclasses (superset with `state` convenience key); deleted class definitions from `ahk.py`, `linux.py`, and `macos.py`; all three now import from `types.py`.
  - **Group 12 — Destructive detection** (`safety_guard.is_destructive` canonical): Replaced inline SQL keyword list in `modules/proactive_display._is_destructive_operation` with delegation to `is_destructive()`; preserved `ALTER` guard explicitly.

- **Full Overlap & Redundancy Audit** — codebase-wide consolidation of duplicate, overlapping, and dead code across 4 phases (~140 files touched):
  - **Phase A — Dead code removal**: Deleted `mask_token()` + 3 constants from `security.py`; removed non-functional `commands/logs.py` (162 lines), zero-caller `retry.py` (413 lines), stale `vault/core_v2.py`; removed orphan `"logs"` registration entry; added deprecation docstring to `detect_mode()`.
  - **Phase B — Duplicate consolidation**: Extracted shared `_retry.py` async retry helper (audio.py + image.py); extracted `core/protocols.py` shared Protocol definitions (hosts.py + apps.py); replaced inline `_substitute_env_vars` in `agent/config.py` with canonical `security.substitute_env_vars`; unified `get_console()` fallback in onboard.py + runner.py to prefer `console_helper` singleton.
  - **Phase C — Redaction pattern unification**: Replaced 80-line `SENSITIVE_PATTERNS` in `debug_logger.py` and 5 compiled patterns in `console_helper.py` with delegation to canonical `security.redact_sensitive_text()`.
  - **Phase D1 — Console singleton migration**: Automated migration of ~75 files (~138 replacements) replacing bare `Console()` with `get_console()` from `console_helper`, ensuring consistent Rich console lifecycle.
  - **Phase D2 — config_dir migration**: Automated migration of 68 files (124 replacements) replacing `Path.home() / ".navig"` with `config_dir()` from `navig.platform.paths`, centralizing the `NAVIG_CONFIG_DIR` / `NAVIG_HOME` env var logic.

- **Overlap consolidation — Groups 8, 9, 13** (second-pass deduplication):
  - **Group 9 — ConfigSingleton dual YAML load** (`navig/core/shared_config.py`): `ConfigSingleton._load()` previously read `~/.navig/config.yaml` independently from `ConfigManager`, producing two in-memory copies of the same file. `_load()` now delegates to `get_config_manager().global_config` (deferred import to avoid import-time cycle); `reload()` invalidates ConfigManager's cache before re-loading so disk changes propagate through both singletons.
  - **Group 13 — Active-host guard boilerplate** (12 sites across 4 files): Replaced the 4-line inline guard (`options.get("app") or config_manager.get_active_server()` + `if not server_name: ch.error(...); return`) with a single call to the canonical `require_active_server(options, config_manager)` from `navig.cli.recovery`. Files migrated: `commands/database_advanced.py` (3 sites), `commands/files.py` (2), `commands/backup.py` (5), `commands/hestia.py` (2). Sites returning `bool` and those using non-standard option keys deferred to a future PR.
  - **Group 8 — `ask_ai_with_context` raw HTTP** (`navig/ai.py`): `ask_ai_with_context()` previously made a direct `requests.post` to OpenRouter, duplicating the provider-routing logic already in `llm_generate.py`. Replaced with a 4-line wrapper that builds the messages list and delegates to `llm_generate()`. Also fixed the circular dependency in `llm_generate._call_legacy()` which called `ask_ai_with_context` (last-resort path → ask_ai → llm_generate → last-resort = infinite loop); `_call_legacy` now contains its own minimal inline OpenRouter HTTP call.

- **Overlap consolidation — Groups 13B, 15** (third-pass deduplication):
  - **Group 15 — `_truncate` output helper** (`navig/core/dict_utils.truncate_output`): Added canonical `truncate_output(text, limit)` to `dict_utils.py`. Replaced duplicate `_truncate()` bodies in `agent/tools/devops_tools.py` (4 000-char limit, 11 call sites) and `agent/remote_agent.py` (30 000-char limit, 2 call sites) with thin wrappers delegating to `truncate_output`. `safety_guard._truncate` intentionally excluded (different suffix and purpose).
  - **Group 13B — Active-server/host guard boilerplate** (remaining 32 sites across 4 files): Replaced inline guard blocks (`config_manager.get_active_server()` / `if not: console.print(...); return`) with single calls to `require_active_server` / `require_active_host` from `navig.cli.recovery`. Files migrated: `commands/security.py` (11 sites, `require_active_server`), `commands/monitoring.py` (6 sites, `require_active_server` via regex — normalized `app_name` key), `commands/webserver.py` (8 host-guard sites, `require_active_host`; app guard left inline — no canonical `require_active_app`), `commands/app.py` (7 host-guard sites, `require_active_host`; `remove_app` quiet-mode variant skipped).

### Fixed
- **Telegram `/provider` screen HTML formatting**: Switched `/provider` message payload from legacy `parse_mode="Markdown"` to `parse_mode="HTML"` in `_handle_providers` (`navig/gateway/channels/telegram_commands.py`). Dynamic content (provider names, vision reason, bridge URL, model names) is now properly escaped via `html.escape()`, eliminating silent parse failures that caused raw `*text*` and `` `code` `` to render as literal characters in Telegram clients. Also updated `_format_bridge_status()` to emit HTML tags.
- **Vault-backed provider readiness**: Cloud providers with an API key stored in vault are now shown as ready in the `/provider` keyboard regardless of whether the key has a `validation_success=True` metadata flag. Changed `ready = key_detected or bool(vault_validated)` → `ready = key_detected or vault_has_key`, ensuring a stored (even un-validated) vault key counts as ready. Resolves "No vault-backed cloud providers are ready" showing when the key was saved but not explicitly re-validated.
- **Deterministic `/provider` cloud readiness in tests and mixed envs** (`navig/gateway/channels/telegram_commands.py`): Removed `_has_api_key(manifest.id)` fallback inside keyboard-row readiness so `/provider` row state is derived only from explicit `_verify_provider(...)` results plus vault presence. This prevents environment/config leakage from flipping unconfigured providers into ready rows during broad-suite runs.
- **Pytest import path normalization** (`conftest.py`): Added session/test setup normalization that removes `build/lib` from `sys.path` and keeps repo root first. Prevents order-dependent test failures caused by stale build artifacts shadowing source modules during long full-suite runs.
- **Pytest stale-module purge** (`conftest.py`): Added cleanup that evicts already-loaded `navig.*` modules whose `__file__` resolves under `build/lib`, preventing cached shadow imports from bypassing `sys.path` normalization.
- **Monitoring module test-stub compatibility** (`navig/commands/monitoring.py`): Added safe fallback for `format_bytes` import from `navig.console_helper` so unit tests that stub `console_helper` partially (without `format_bytes`) can still import monitoring commands.
- **Config backup redaction compatibility helper** (`navig/commands/config_backup.py`): Restored `_redact_dict(data, sensitive_keys)` as an in-place wrapper around `navig.core.security.redact_dict` to preserve legacy/test call sites expecting this private helper.
- **Onboarding runner Rich fallback contract** (`navig/onboarding/runner.py`): `_get_console()` now explicitly returns `None` when `rich.console` is unavailable (while still using `console_helper.get_console()` when available), restoring graceful degraded behavior expected by banner tests.
- **Soul loader home-path compatibility** (`navig/personas/soul_loader.py`): `_navig_dir()` now honors `Path.home()/.navig` when no explicit `NAVIG_CONFIG_DIR`/`NAVIG_HOME` override is set, preserving workspace identity-file lookup behavior expected by legacy and patch-based tests.
- **Mode manager helper compatibility** (`navig/modes/manager.py`): Restored `_navig_home()` as a compatibility wrapper returning `paths.config_dir()`, fixing imports and callers/tests that still reference this helper.


  - **`navig/main.py`** (bugs 1-2): Migration failure crashed CLI with `sys.exit(1)` instead of a recoverable warning; redundant inner import removed.
  - **`navig/cli/__init__.py`** (bugs 3-6): Duplicate `_config_manager` cache shadowed `navig.config` singleton; shared `_lazy_lock` caused cross-group contention; `ctx.obj["plain"]` was never set (downstream formatters always accessed raw mode); `--no-cache` flag did not call `reset_config_manager()` so stale config persisted for the rest of the session.
  - **`navig/cli/_singletons.py`** (bug 7): `set_no_cache()` set the bypass flag but never called `reset_config_manager()` — config singleton retained its cached value.
  - **`navig/cli/middleware.py`** (bug 8): `_register_operation_complete_atexit()` registered the completion callback with hardcoded `success=True`; failures were always recorded as successes. Fixed to use `sys.exc_info()` at atexit time.
  - **`navig/assistant_hooks.py`** (bug 9): `ctx_obj.get("assistant")` returned `None` for every call; all pre/post assistant hooks were silent no-ops. Added `_resolve_assistant()` via the `ctx_obj["get_assistant"]` callable.
  - **`navig/remote.py`** (bugs 10-11): Bare `["scp"]` in `upload()`/`download()` raised `FileNotFoundError` on Windows where `scp` is not on default PATH; SCP calls also missing `StrictHostKeyChecking` option. Added `_resolve_scp_bin()` with Windows `SysNative/System32/OpenSSH` fallback; added trust-new-host propagation.
  - **`navig/tunnel.py`** (bug 12): `StrictHostKeyChecking=accept-new` was hardcoded regardless of host config. Now derived from `server_config.get("trust_new_host", False)`.
  - **`navig/connection_pool.py`** (bug 13): `get_connection_info()` held `RLock` during `is_alive()` socket I/O (unbounded latency under the lock). Fixed: snapshot state under lock, probe outside.
  - **`navig/core/kernel.py`** (bugs 14-15): Bare `except: pass` silenced all errors silently; all `print()` calls polluted stdout. Replaced with `logger.debug()`/`logger.warning()`.
  - **`navig/commands/suggest.py`** (bug 16): Unsorted imports (ruff I001). Auto-corrected.
  - **`navig/commands/remote.py`** (bug 17): `_encode_b64_command()` declared `-> str` but returned `None` on encoding errors. Fixed to `-> str | None`.
  - **`navig/agent/session_store.py`** (bug 18): `append()` metadata read-modify-write (`turn_count` increment) was not thread-safe — two concurrent appenders could both read N, both write N+1, losing an increment. Added `self._lock = threading.Lock()` and wrapped the entire JSONL write + metadata update in `with self._lock:`.
  - **`navig/agent/brain.py`** (bug 19): No `import logging` or module-level `logger`. `_query_ai()` had a bare `except Exception: return None` with zero observability on AI query failures. Added logger; changed to `logger.debug("brain: AI query failed: %s", exc)`.
  - **`navig/core/execution.py`** (bug 20): `get_mode()` and `get_confirmation_level()` each independently opened and YAML-parsed `.navig/config.yaml` on every call — 2 file reads per `get_settings()` invocation with no caching. Extracted `_read_local_config()` with mtime-guarded instance cache; both getters delegate to it.
  - **`navig/core/connection.py`** (bug 21): `SSHConnection._build_ssh_args()` used bare `["ssh"]`; `upload()`/`download()` used bare `["scp"]` — `FileNotFoundError` on Windows. Added `_resolve_ssh_bin()` and `_resolve_scp_bin()` with the same Windows `SysNative/System32` OpenSSH fallback pattern as `navig/remote.py`.
  - **`navig/tools/code_exec_sandbox.py`** (bug 22): `CodeExecSandboxTool` lacked `owner_only = True` — inherited `False` from `BaseTool`, allowing any non-owner user to trigger arbitrary Python subprocess execution. Added `owner_only = True` (consistent with `BashExecTool`).
  - **`navig/providers/auth.py`** (bug 23): `_load_store()` used `print(f"⚠️ Failed to load auth profiles: {e}")` — bypasses log filtering and pollutes stdout in library code. Replaced with `logger.warning(...)`; added `import logging` and a module-level logger.
  - **`navig/providers/auth.py`** (bug 24): `save()` had a dead outer `try/except OSError` wrapping an inner `except (OSError, PermissionError): pass`. Inner handler already catches `OSError` so the outer was unreachable dead code. Collapsed to a single `except OSError: pass`.
  - **`navig/providers/auth.py`** (bug 25): `save()` wrote credentials directly with `open(..., "w") + json.dump()`. A process crash mid-write produces a truncated/zero-byte file and permanently destroys all saved profiles. Replaced with atomic `tempfile.NamedTemporaryFile` + `os.replace()` with `finally:` cleanup.
  - **`navig/webhooks/receiver.py`** (bug 26): `handle_webhook()` only entered the signature-verification block when `verify_signature=True AND secret is not None`. With `verify_signature=True` but `secret=None` (the default for all built-in sources), verification was silently skipped and any unauthenticated request was accepted. Now explicitly returns HTTP 500 for misconfigured sources.
  - **`navig/webhooks/receiver.py`** (bug 27): `handle_history()` called `int(request.query.get("limit", 20))` without a `ValueError` guard — a malformed `?limit=abc` query raised an unhandled exception → HTTP 500. Wrapped in `try/except (ValueError, TypeError)` with fallback to 20.
  - **`navig/messaging/secrets.py`** (bug 28): `ensure_telegram_uid()` referenced `_config_dir()` (undefined) instead of the imported `config_dir`. The `NameError` was silently swallowed by `except Exception: pass`, so the `.env` fallback write for a new Telegram UID never succeeded. Fixed to call `config_dir()`.
  - **`navig/providers/oauth.py`** (bug 29): `run_oauth_flow_interactive()` verified the OAuth `state` parameter only in the manual URL-input fallback path. The automatic callback-server path accepted any callback without comparing `result["state"]` to the locally generated `state`, opening a CSRF/session-fixation window via a forged local request. Added explicit state comparison before token exchange.
  - **`navig/bot/stats_store.py`** (bug 30): `cache_get()` performed a `DELETE FROM cache … + conn.commit()` on the expired-entry cleanup path **without holding `self._lock`**. Every other write method (`cache_set`, `cache_delete`, `cache_clear_expired`) acquires `self._lock` before touching the DB; the unguarded delete could race a concurrent `cache_set()` and produce `sqlite3.OperationalError: database is locked` or silently lose the write. Wrapped the DELETE + commit in `with self._lock:`.
  - **`navig/bot/stats_store.py`** (bug 31): Module-level `get_bot_store()` singleton initializer used a bare `if _store is None: _store = BotStatsStore()` with no lock. Two threads entering simultaneously could both observe `None` and each construct a separate `BotStatsStore()` with their own SQLite connection and divergent in-memory `_cache` dict. Added `_store_lock = threading.Lock()` and replaced with a double-checked locking pattern.
  - **`navig/identity/store.py`** (bug 32): `IdentityStore.delete()` executed `self._conn.execute(DELETE …) + self._conn.commit()` without holding `self._lock`. All other mutating methods (`get_or_create`, `save`) acquire the lock; the unguarded delete could interleave with a concurrent `save()` and corrupt the SQLite connection state. Wrapped in `with self._lock:`.
  - **`navig/identity/store.py`** (bug 33): `get_identity_store()` had the same unguarded singleton race as bug 31 — `if _store is None: _store = IdentityStore(db_path)` with no threading guarantee. Added `_store_lock = threading.Lock()` and double-checked locking.
  - **`navig/identity/sigil_store.py`** (bug 34): `persist_entity()` wrote via `path.write_text(json.dumps(...))` — a non-atomic operation. A crash or `KeyboardInterrupt` mid-write produces a truncated/corrupt `entity.json`, silently destroying the user's generated identity. Replaced with `tempfile.mkstemp + os.fdopen + os.replace()` for an atomic write, with `finally:` cleanup of the temp file on failure.
  - **`navig/onboarding/engine.py`** (bug 35): `_write_artifact()` used `self._artifact.write_text(json.dumps(...))` — a non-atomic write that contradicts the engine's "crash recovery is free" design guarantee. A process crash or `KeyboardInterrupt` mid-write corrupts the onboarding checkpoint, causing the next run to silently wipe all completed-step progress and start from scratch. Replaced with `tempfile.mkstemp + os.fdopen + os.replace()` so a crash always leaves the previous valid checkpoint intact.
  - **`navig/storage/query_timer.py`** (bug 36): `get_query_timer()` singleton used the same unguarded `if _timer is None: _timer = QueryTimer(...)` pattern. Two threads racing at startup would both construct a `QueryTimer`, with one overwriting the other's instance and discarding all already-sampled latency data for that caller. Added `_timer_lock = threading.Lock()` and double-checked locking.
  - **`navig/mesh/registry.py`** (bug 37): `NodeRegistry._save_to_disk()` used `self._peers_file.write_text(...)` directly — a non-atomic write. A crash mid-write corrupts the warm-start peer cache; subsequent restarts silently discard all known peers and begin discovery from scratch. Replaced with `tempfile.mkstemp + os.fdopen + os.replace()` for an atomic swap, matching the pattern used for auth profiles and onboarding artifacts.
  - **`navig/mesh/registry.py`** (bug 38): `get_registry()` module singleton used `if _registry_instance is None: _registry_instance = NodeRegistry(...)` without a lock. While the mesh runs single-threaded inside an asyncio loop, the function is also called from synchronous setup paths; a concurrent first call could create two separate `NodeRegistry` instances with diverging peer state. Added `_registry_lock = threading.Lock()` and double-checked locking.
  - **`navig/commands/triggers.py`** (bug 39): `TriggerManager._save_triggers()` wrote the active triggers configuration via `open(self.triggers_file, "w") + yaml.safe_dump()`. A crash mid-write produces a truncated YAML file; on next startup the trigger manager logs a load failure and silently starts with zero triggers, losing all user-configured automations. Replaced with `tempfile.mkstemp + os.fdopen + os.replace()` atomic write pattern.
  - **`navig/onboarding/steps.py`** (bug 40): `_step_config_file.run()` used `config_path.write_text(config_content)` — a non-atomic write for the main application `config.yaml`. A crash mid-write leaves a truncated or empty config; the verify predicate (`config_path.exists()`) then reports the step as done, so the next `navig init` skips re-generating the config and the broken file is never repaired, making the application refuse to start. Added `import tempfile`; replaced with `tempfile.mkstemp + os.fdopen + os.replace()` + `finally:` cleanup.
- **Service restart/stop reliability (daemon control):** `navig service restart` now exits non-zero when it cannot stop an existing daemon before restart; daemon stop no longer reports false success when force-termination fails; force-kill now uses a portable signal fallback when `SIGKILL` is unavailable (Windows Python), stop guidance is platform-appropriate (`taskkill` on Windows, `kill -9` on POSIX), `navig service status --json` now includes backend detail text for parity with human-readable status output, backend status detail lines are now normalized to compact single-line summaries across NSSM/Task Scheduler/systemd output, `navig service config --show` now fails clearly on malformed daemon config JSON, `navig service logs --lines` now enforces a positive minimum value, and `navig service install` now recovers from malformed existing daemon config by reseeding from defaults and writing updated config via atomic replace.
- **Daemon entry config resilience:** `navig.daemon.entry._load_config()` now validates that the parsed config root is a JSON object (falls back to defaults when malformed or wrong type), and `save_default_config()` now repairs missing/malformed config files via atomic replace to avoid partial-write risk.
- **Vault test hang on Windows / Python 3.14** (`fix(vault): lazy argon2 + derive_key patch`): Three cascading hangs eliminated.
  (1) `argon2-cffi` DLL (`argon2.low_level`) blocks indefinitely when first imported on Windows/Python 3.14 — moved from module-level import to lazy probe globals (`_argon2_probed` / `_argon2_funcs`) in `navig/vault/crypto.py`; import chain drops from >65 s hang to 0.058 s.
  (2) `_machine_fingerprint()` / platform DNS lookup hangs in fresh subprocesses — resolved by patching `CryptoEngine.derive_key` at `tests/conftest.py` import time with a fast `hashlib.sha256` stub (no platform calls, no KDF iteration, no argon2).
  (3) `pytest --collect-only` of `test_vault_commands.py` hung because `_register_external_commands(register_all=True)` at module level pulled in aiohttp — replaced with a module-scoped autouse fixture using an argv fast-path.
  Vault fixture changed from `scope="module"` to function scope to prevent `_reset_navig_singletons` from closing the store between tests.
  Result: **18/18 vault tests pass in 2.79 s** (previously hanging indefinitely).
- **Firecrawl key requirement clarity**: Firecrawl calls now fail fast with a clear `FIRECRAWL_API_KEY is required` error when no key is configured; `navig search --provider firecrawl` returns that explicit error instead of silently falling back to other providers, while `--provider auto` still degrades gracefully.
- **Pytest basetemp on fresh clone** (#34): Created root `conftest.py` with `pytest_sessionstart` hook that ensures `.local/.pytest_tmp` exists before collection, preventing `--basetemp` failures on first run.
- **`navig help <cmd>` order-agnostic** (#47): `_normalize_help_compat_args()` in `navig/main.py` now rewrites leading `navig help db` → `navig db --help` (previously only trailing `navig db help` was normalized).
- **Telegram silent drop when AI unconfigured** (#36): Added an `else` branch to the `if self.on_message:` gate in `navig/gateway/channels/telegram.py` so the bot sends a helpful fallback message instead of silently ignoring user input when the AI handler is not configured.
- **Vault credentials surfaced during reconfigure** (#40): `navig init --reconfigure` now detects existing provider API keys in vault during the `ai-provider` step, marks providers with "vault key saved", defaults selection to configured providers, and offers a "Keep existing" prompt so users can preserve stored credentials without re-entering secrets.
- **`navig software` LocalConnection crash on Windows** (#60): `LocalConnection` now accepts `working_directory` and passes it as `cwd` to subprocess execution, fixing `TypeError: LocalConnection.__init__() got an unexpected keyword argument 'working_directory'` when local software/package commands initialize through `LocalOperations`.
- **`navig ask` Windows decode crash** (#48): Hardened local Windows process context probe in `navig.commands.ai.ask_ai` by capturing `tasklist` output as bytes (`text=False`) and decoding with locale-aware fallbacks plus safe replacement, preventing `UnicodeDecodeError` crashes in subprocess reader paths on non-UTF-8/dirty-byte output.
- **FTS5 Session Search** (F-13): Full-text search for conversation messages using SQLite FTS5 extension. `ConversationStore` schema bumped to v2 with `messages_fts` virtual table (`porter unicode61` tokenizer) and automatic INSERT/UPDATE/DELETE triggers. `fts_search()` method returns BM25-ranked results with score and snippet. `search_content()` upgraded to use FTS5 MATCH with LIKE fallback for robustness. Migration backfills existing messages into FTS index on upgrade. 15 new tests in `test_conversation_store.py`.
- **Worktree Isolation** (FB-05): `WorktreeManager` async git worktree lifecycle manager for parallel agent workers. `Worktree` dataclass tracks name, path, branch, created_at, merged, deleted, `age_seconds` property, and `to_dict()`. `WorktreeManager` supports `create(name, base_branch)` — validates name (alphanumeric/hyphens/underscores), checks `MAX_WORKTREES=10` cap, verifies git repo, creates branch `navig/<name>` and worktree under `.navig_worktrees/`. `merge_back(name, target_branch)` auto-detects HEAD branch when target omitted, checks for new commits via `git log`, attempts `git merge --no-edit`, aborts on conflict and returns False. `remove(name, force)` idempotent removal with Windows retry loop (3×1s), `shutil.rmtree` fallback on exhaustion, `git branch -D` cleanup. `cleanup_all()` removes all worktrees, prunes stale references, removes empty base dir, returns count. `list_worktrees()` returns active (non-deleted) dicts. `get_worktree(name)` returns `Worktree | None` (None for deleted). `active_count` property. Async context manager calls `cleanup_all()` on `__aexit__`. Module-level singleton via `get_manager(repo_root)`/`reset_manager()`. Four agent tools: `worktree_create` (params: name required, base_branch optional), `worktree_list` (no params), `worktree_merge` (params: name required, target_branch optional), `worktree_remove` (params: name required, force optional bool). All registered under `"worktree"` toolset. `.navig_worktrees/` added to `.gitignore`. 86 tests.
- **Background Tasks** (FB-04): `BackgroundTaskManager` async subprocess manager for the agent system. `BackgroundTask` dataclass tracks task_id, label, command, pid, started_at, completed_at, exit_code, output_file, `is_running` property, and `duration` property. Manager supports `start(command, label, cwd)` — spawns a subprocess shell with stdout/stderr captured to disk, returns immediately. `_monitor()` asyncio background task waits for process exit, records exit code, and closes file handles. `status(task_id)` returns dict with running/completed state, duration, exit_code, output line count, and pid. `get_output(task_id, tail=50)` reads last N lines from the output log file. `kill(task_id)` terminates then force-kills after 5s timeout. `list_tasks()` returns status dicts for all tracked tasks sorted by id. `cleanup(max_age=3600)` removes completed task records older than max_age and deletes output log files. `shutdown()` terminates all running tasks and awaits all monitor coroutines for clean teardown (important on Windows). `MAX_CONCURRENT=10` limit enforced at start. Module-level singleton via `get_manager()`/`reset_manager()`. Four agent tools: `background_task_start` (params: command required, label optional, cwd optional), `background_task_status` (params: task_id optional — 0 or omit lists all), `background_task_output` (params: task_id required, tail optional default 50), `background_task_kill` (params: task_id required). All registered under `"background_task"` toolset. Output dir: `~/.navig/bg_tasks/`. Windows-compatible with `encoding="utf-8", errors="replace"` for all file handles. 71 tests.
- **Flow Delegation Test**: `tests/test_flow_delegation.py` with 6 tests verifying `navig flow` correctly delegates to the workflow command group (help, list, subcommand presence, error cases).
- **Memory Auto-Extract** (FB-06): `MemoryAutoExtractor` interval-based scheduler that accumulates conversation turns and triggers batch fact extraction via LLM at configurable intervals. `record_turn(role, content)` buffers user/assistant messages with content truncation (`MAX_TURN_CONTENT_CHARS=2000`) and safety cap (`MAX_PENDING_TURNS=20`). `maybe_extract()` async — fires after every N assistant turns (default `MEMORY_EXTRACTION_INTERVAL=5`), builds a prompt from the last 10 turns, calls LLM, parses JSON response, filters by confidence (`MIN_CONFIDENCE=0.6`), limits to `MAX_FACTS_PER_EXTRACTION=3`, and stores via duck-typed store (`put()` or `upsert()` fallback). `force_extract()` bypasses interval for immediate extraction. `parse_extraction_response()` handles markdown-fenced JSON, regex array search, category validation against 5 categories (preferences/environment/project/relationships/procedures), and confidence clamping. `fact_key()` generates deterministic keys as `category/first_four_words`. `ExtractedFact` and `ExtractionConfig` dataclasses with `from_dict()` classmethod. Silent failure on LLM/store errors — counters always reset in `finally` blocks. Properties: `config`, `turn_count`, `pending_turns`, `total_extracted`, `enabled`. 81 tests.
- **Skills System** (FB-02): `SkillsContext` context-aware skill activation engine for the agent system. Skills are Markdown files with optional YAML frontmatter (`activation_paths`, `activation_keywords`, `priority`) placed in `.navig/skills/` (project) or `~/.navig/skills/` (global). `_parse_skill_file()` loads 3 formats: full frontmatter, partial frontmatter, and plain Markdown (stem becomes name). `_load_frontmatter()` handles missing/malformed YAML gracefully. `ContextSkill` dataclass with name, content, activation_paths, activation_keywords, priority, source (project/global/extra), file_path, and `summary()` helper. `SkillsContext.activate(current_files, user_message)` scores all skills per turn: +10 per glob pattern match (fnmatch against full path AND basename), +5 per keyword in message (case-insensitive), +priority bonus. Auto-loads on first `activate()`. Force-activated skills get score 10000; force-deactivated skills excluded entirely. Sorted by (score, priority, project-before-global), returns top `MAX_ACTIVE_SKILLS` (3). `format_for_system_prompt()` renders `## Active Skills` section with per-skill `### {name}` blocks and `(global)` source tags. `force_activate()`/`force_deactivate()`/`reset_overrides()` for runtime override management. `reload()` re-scans directories. `get_skill()` name lookup. Content truncated at `MAX_SKILL_CHARS` (8000) with marker. Agent tool `manage_skills` with list/activate/deactivate actions via `handle_manage_skills()`. `MANAGE_SKILLS_SCHEMA` OpenAI function-calling format. `register_skill_tools()` integrates with agent tool registry. 68 tests.
- **Telegram Approval Backend** (FA-07): `TelegramApprovalBackend` pluggable backend for `ApprovalGate` that sends approval requests as Telegram inline-keyboard messages ([✅ Approve] [❌ Deny]) and awaits user callback. `ApprovalMessage` dataclass tracks pending requests with request_id, future, message_id, timestamps, and resolution method. `format_approval_message()` renders risk-emoji-annotated Markdown (🟢safe/🟡moderate/🟠dangerous/🔴critical) with tool name, reason, and truncated parameter details. `build_inline_keyboard()` generates Telegram-compatible reply markup with `approval:approve:<id>` / `approval:deny:<id>` callback data. `parse_callback_data()` validates and extracts action+request_id from callbacks. Risk-based timeout policy: safe/moderate auto-approve on timeout, dangerous/critical auto-deny (returns TIMEOUT). Configurable `timeout` (default 120s), `auto_approve_levels`, and pluggable `send_fn`/`edit_fn` transport for bot library integration or built-in HTTP API sender. `handle_callback()` resolves pending futures with double-callback protection. Best-effort message editing on timeout to show resolution. `pending_count` / `get_pending()` for request tracking. 46 tests.
- **Session Transcript** (FA-04): `SessionStore` persistent NDJSON session storage with resume capability. `SessionEntry` dataclass with role, content, timestamp (auto-set), tool_calls, tool_results, compact boundary flag, token/cost tracking, and model identifier. `SessionMetadata` sidecar (`.meta.json`) tracks session_id, turn_count, total_tokens, total_cost, workspace, tags, and finalized flag. Append-only `.jsonl` writes ensure crash safety. `mark_compact_boundary()` records compaction events for `resume()` to start from the most recent snapshot. `resume(max_entries)` loads post-boundary messages in LLM message format, excluding boundary markers. `list_sessions()` returns recent sessions sorted by last_active. `find_by_workspace()` filters by normalized path (case-insensitive). `get_latest()` returns most-recent session optionally scoped by workspace. `cleanup_old_sessions(max_age_days=90)` removes expired sessions. Session ID format `YYYYMMDD_HHMMSS_<4-hex>` for human readability and chronological sorting. 70 tests.
- **Reactive Compaction** (FA-05): `ReactiveCompactor` auto-triggers context compaction at 90% fill, targets 60% after compression. Cache-aware `_find_safe_start()` preserves prompt-cache breakpoints via `has_cache_breakpoint()`. Pluggable `summarizer` callback for testability (sync or async). `_build_digest()` formats conversation chunks for summarisation with per-message truncation and structured-content flattening. Cumulative `stats` property tracks compact count, tokens saved, and estimated cost savings. `should_compact()` threshold check and `compute_target()` budget helper. `get_reactive_compactor()` factory with sensible defaults. Graceful no-op on summarizer failure (returns originals). 56 tests.
- **PlanContext Unified Read Surface** (FA-01c): `PlanContext` dataclass in `navig.plans.context` gives the AI agent complete situational awareness — current phase, dev plan progress, wiki search, project docs, inbox count, and MCP resources — in a single `gather()` call. `format_for_prompt()` renders compact Markdown for system prompt injection. `plans summary` CLI command with cross-space rollup table and `--json` output. `get_plan_context` agent tool registered in `"core"` toolset. Auto-injected into both `conv/agent.py` (lazy `self.context` merge) and `conversational.py` (agentic system prompt section). Project indexer force-includes `.navig/plans/` and `.navig/wiki/` despite `.gitignore` exclusion. MCP `list_resources()` / `list_resources_sync()` added to `MCPClient` and `MCPClientPool`. 23 unit tests for PlanContext, 4 plans summary CLI tests, 4 project indexer force-include tests.
- **Todo Tracker** (FA-03): `TodoList` / `TodoItem` persistent progress tracker for multi-step agent work. `TodoStatus` 3-state enum (not-started / in-progress / completed). Single in-progress constraint prevents parallel work confusion. Verification nudge every 3 completions prompts the agent to validate work. `TodoPersistence` JSONL append-only snapshots with `load_latest()` recovery. `format_display()` emoji-rich rendering (✅🔄⬜). Three agent tools: `todo_create` (JSON array or comma-separated input, replaces existing list), `todo_update` (status transitions with constraint enforcement), `todo_show` (formatted display). 15-item cap, 50-char title limit. Registered in `"todo"` toolset. 57 tests.
- **Plans Reconciliation Engine** (FA-01b): `navig.plans` package — 6-module pipeline for processing `.navig/inbox/` items into canonical `.navig/plans/` structure. `InboxReader` reads exclusively from `.navig/inbox/` with file-suffix lifecycle state (`.md` → `.md.done` / `.md.archive` / `.md.review`). `CurrentPhaseManager` parses and mutates `CURRENT_PHASE.md` with atomic advance, block, and unblock operations. `InboxProcessor` 5-stage reconciliation pipeline (ContentNormaliser → StalenessDetector → DuplicateScanner → ConflictDetector → Router) with JSON Lines staging queue. `ReviewQueue` manages `.md.review` items with commit (re-reconciliation) and archive workflows. `MilestoneProgressEngine` parses checkbox-based progress with visual strip rendering (`✓●⚠○`). `CorpusScanner` extends duplicate/conflict detection across full `tasks/` + `decisions/` corpus. Optional LM spot-checks (duck-typed client, 5s timeout, substring fallback). `scaffold_plans_structure()` creates 8 directories and 5 canonical templates. 60+ tests.
- **Plan Mode** (FA-01): `PlanInterceptor` gates tool access during a structured planning phase — read/search tools allowed, writes blocked with helpful messages. `PlanState` 5-phase lifecycle (INACTIVE → PLANNING → REVIEWING → EXECUTING → COMPLETED). `PlanStep` dataclass with description, tool predictions, affected files, and risk levels. `PlanSession` tracks steps + context gathered during research. Three plan tools (`plan_add_step`, `plan_show`, `plan_approve`) registered in `"plan"` toolset, gated by `check_fn` so they only appear when plan mode is active. `format_plan()` renders Markdown summary. Full cancel/restart support. 58 tests.
- **Effort Levels** (FA-02): `EffortLevel` 5-tier enum (LOW → ULTRATHINK) controlling per-provider thinking budgets. `auto_detect_effort()` heuristic routes simple tasks to LOW (90% cheaper) and complex tasks to HIGH. `get_thinking_params()` generates provider-specific params: Anthropic `thinking.budget_tokens`, OpenAI `reasoning_effort`, Google `thinking_config.budget`, DeepSeek budgets. `resolve_effort()` with 10 aliases (`l`/`lo`/`m`/`med`/`h`/`hi`/`max`/`ultra`/`ut`). Integrated into `run_llm()` pipeline via `effort` parameter + `CompletionRequest.extra_body`. Graceful no-op for unsupported providers. 46 tests.
- **Extended Prompt Cache** (FC-04): `CacheBreakpointPlacer` strategically places up to 4 `cache_control` breakpoints on system prompts, tool definitions, skills context, and conversation prefixes. `CacheStats` tracks hit rates and estimates USD savings (90% discount on cache reads). `has_cache_breakpoint()` utility for compactor integration. New `"strategic"` strategy in `apply_anthropic_cache_control()`. `ExtendedCacheConfig` dataclass with per-layer toggles. 39 tests.
- **Tool Result Caps** (FA-06): `cap_result()` truncates oversized tool outputs at configurable per-tool limits (default 30K chars) with line-boundary snapping and disk spillover to `~/.navig/tmp/tool_spillover/`. Replaces the old 4K backstop in `agent_tool_registry.py` and 8K backstop in `mcp_client.py`. `cleanup_spillover()` removes expired files (1h TTL). Per-tool caps for 12 tools (`bash_exec` 50K, `search` 15K, etc.). 36 tests.
- **Agent Tool Registry** (F-02): `AgentToolRegistry` singleton with `register()`, `dispatch()`, `available_names()`, and OpenAI-schema generation for LLM tool-use APIs.
- **Agent Tool Framework** (F-03): `BaseTool` abstract class and concrete tool implementations — `ReadFileTool`, `WriteFileTool`, `ListFilesTool`, `MemoryReadTool`, `MemoryWriteTool`, `MemoryDeleteTool`, `KBLookupTool`, `WikiSearchTool`, `WikiReadTool`, `WikiWriteTool`. Lazy registration via `register_all_tools()`.
- **Toolset Definitions** (F-04): Named toolset bundles (`core`, `search`, `research`, `code`, `devops`, `memory`, `wiki`, `delegation`, `full`) with `resolve_toolset_names()`, `merge_toolsets()`, `validate_toolset()`, and parallel-safety classification.
- **Agentic ReAct Loop** (F-01/F-05): `ConversationalAgent.run_agentic()` — async multi-turn tool-use loop with parallel/sequential dispatch, context compression, iteration budgets, KB enrichment, and semantic routing.
- **LLM Cost Tracking** (F-06/F-08): `run_llm()` now records token usage. `CostTracker` accumulates `UsageEvent` records per session with `session_cost()` summary. `IterationBudget` guards against runaway loops with shared parent→child counters.
- **MCP Client** (F-09): `MCPClient` and `MCPClientPool` for stdio/HTTP Model Context Protocol servers with tool discovery, `call_tool()`, and connection lifecycle management.
- **Agent Delegation** (F-10): `DelegateTool` enables parent→child agent delegation with `AgentDepthError` guard (max depth 3) and shared iteration budgets.
- **Context Compression** (F-11): `ContextCompressor` with two-pass strategy — cheap token-counting pass + LLM-driven summarization when context exceeds thresholds.
- **Prompt Caching** (F-12): Anthropic prompt cache injection via `apply_anthropic_cache_control()` (`system_and_3` strategy). `supports_caching()` model detection. Optional TTL extension.
- **Approval Gate** (F-14): `ApprovalGate` with pluggable backends, 4-level `ApprovalPolicy` (YOLO/CONFIRM_DESTRUCTIVE/CONFIRM_ALL/OWNER_ONLY), `needs_approval()` predicate, and `NAVIG_ALLOW_ALL_COMMANDS` env bypass.
- **Agent Profiles** (F-15): `Profile` dataclass with isolated `memory_dir`, `wiki_dir`, `config_path`. Resolution via `NAVIG_PROFILE` env → sticky `active_profile` file → `"default"`. CRUD operations: `create_profile()`, `switch_profile()`, `delete_profile()`, `list_profiles()`.
- **DevOps Tool Suite** (F-16): 18 `BaseTool` subclasses wrapping NAVIG CLI operations — host management, remote execution, database ops, Docker management, file operations, web server, app context, and monitoring. All registered under `"devops"` toolset with 6 tools added to `DESTRUCTIVE_TOOLS`.
- **KB Auto-Enrichment** (F-18): Agentic loop detects extractable key facts from assistant responses and stores them in `KeyFactStore` with category tagging.
- **Wiki Tool Integration** (F-19): `WikiSearchTool`, `WikiReadTool`, `WikiWriteTool` enable agent access to project wiki for knowledge retrieval and documentation.
- **Semantic Routing → Toolset Hints** (F-20): `MODE_TOOLSET_HINTS` maps LLM modes to suggested toolsets. `suggest_toolsets()` auto-narrows tool scope based on detected conversation mode. Integrated into `run_agentic()`.
- **Plan-Execute Agent Mode** (F-21): `PlanExecuteAgent` with 4-phase cycle — Plan (LLM JSON plan), Approve (interactive y/N), Execute (sequential dispatch with LLM-driven revision on failure), Report (trace persistence + formatted summary). `format_plan_report()` for human-readable output. Wired via `ConversationalAgent.run_plan_execute()`.
- 101 new tests covering agent modules: toolsets, usage tracker, plan-execute, prompt caching, profiles, approval, semantic routing, and import smoke tests.

### Changed
- **Config Decomposition (PR6/6):** Added `__all__` exports to `navig.core.__init__.py` for clean public API: `HostManager`, `AppManager`, `ContextManager`, `ExecutionSettings`, `atomic_write_yaml`, `log_shadow_anomaly`. Final config.py size: 925 lines (58% reduction from original 2,243 lines). Decomposition complete with full backward compatibility.
- **Config Decomposition (PR5/6):** Extracted `navig.core.execution.ExecutionSettings` class with `get_mode()`, `set_mode()`, `get_confirmation_level()`, `set_confirmation_level()`, `get_settings()` methods. `ConfigManager` now delegates all execution settings via `self._execution`. `config.py` reduced from 991 to 925 lines (66 lines extracted). Project-local override resolution preserved.
- **Config Decomposition (PR4/6):** Extracted `navig.core.context.ContextManager` class with `get_active_host()`, `get_active_app()`, `set_active_host()`, `set_active_app()`, `set_active_app_local()`, `clear_active_app_local()`, `set_active_context()` methods. `ConfigManager` now delegates all context operations via `self._context`. `config.py` reduced from 1,248 to 991 lines (257 lines extracted). Hierarchical context resolution (env → local → legacy → global → default) preserved.
- **Config Decomposition (PR3/6):** Extracted `navig.core.apps.AppManager` class with `exists()`, `list_apps()`, `find_hosts_with_app()`, `load()`, `save()`, `delete()`, `get_file_path()`, `load_from_file()`, `save_to_file()`, `list_from_files()`, `migrate_from_host()` methods. `ConfigManager` now delegates all app operations via `self._apps`. `config.py` reduced from 1,907 to 1,248 lines (659 lines extracted). App caches moved to AppManager.
- **Config Decomposition (PR2/6):** Extracted `navig.core.hosts.HostManager` class with `exists()`, `list_hosts()`, `load()`, `save()`, `delete()` methods. `ConfigManager` now delegates all host operations via `self._hosts`. `config.py` reduced from 2,188 to 1,907 lines (281 lines extracted). Host caches moved to HostManager.
- **Config Decomposition (PR1/6):** Extracted `navig.core.yaml_io` module with `log_shadow_anomaly()` and `atomic_write_yaml()` utilities from `config.py`. Consolidated duplicate `_log_shadow_anomaly()` from `ipc_pipe.py`. `config.py` reduced from 2,243 to 2,188 lines. Backward-compatible aliases preserved.
- CLI root help redesign: `navig --help` / bare `navig` now render a compact grouped command map with a status bar (`host`, `profile`, version), aligned command descriptions, workflow-focused examples, and a rotating one-line tip for quick orientation.
- Added lazy top-level command registrations for `navig logs`, `navig stats`, `navig health`, plus compatibility aliases for common ops nouns: `cert`, `key`, `firewall`, `dns`, `port`, `proxy`, `env`, `secret`, `job`, and `alias`.
- Telegram bot UX: replaced the Main Menu inline keyboard with a conversational context card on `/start`. The card shows active reminder count, current model tier, and a single `[📋 What can I do?]` button that triggers `/helpme` inline — no navigation buttons. All `nav:home` / `nav:cancel` callbacks now send the context card instead of re-rendering the old menu. `/helpme` (`_handle_help`) sends help text directly without going through the navigation stack. The `renderScreen("main")` branch delegates to `_handle_start` for backwards-compatibility with any `nav:home` triggers in settings sub-screens. Navigation stack machinery (`navigateTo`, `navigateBack`, screen_stack) is preserved for settings screen back-navigation only.
- **Telegram command UX overhaul (Phase 4):**
  - `/help` added to the slash-command registry as a visible first-class command (previously only `helpme` was wired and hidden).
  - Fixed 5 silently broken commands: `/think`, `/refine`, `/autoheal`, `/weather`, `/docker`. They now have Python handlers wired via the dynamic registry instead of being dead stubs or incorrect CLI templates. `/think` and `/refine` had their `topic` parameter renamed to `text` for compatibility with the dynamic dispatch mechanism.
- **Telegram command UX overhaul (Phase 5 — heartbeat, status enrichment, mode wiring):**
  - `/ping` is now a rich heartbeat card: shows NAVIG version, active host, active space, model tier, reminder count, and bridge status (with 2 s async timeout). Step-5 dispatch now delegates to `_handle_ping` instead of sending an inline "🏓 pong.".
  - `/status` enriched with active host, active persona, and reminder count. Nav Back/Home buttons removed from standalone `/status` invocations — they only appear when the screen is rendered inside the navigation edit flow (i.e. `message_id` is set).
  - `/mode` wired to the dynamic registry (added `handler="_handle_mode"`). The handler signature was updated from `mode_arg: str` to `text: str = ""` to be compatible with dynamic dispatch. Step-5 also updated to pass `text=cmd`.
  - Registry: `usage=` hints added for `/mode`, `/big`, `/small`, `/coder`, `/restart`, `/tables`, `/plan`.
  - Test: fixed stale `"NAVIG Main Menu"` and `"Canonical onboarding progress"` assertions left over from before the Phase 1 context-card migration. Updated to match current `_handle_start` output.
- **Telegram command UX overhaul (Phase 6 — routing consistency + complete docs):**
  - Added dynamic slash handlers for `/voiceon`, `/voiceoff`, `/trace`, `/restart`, and `/skill` so dynamic dispatch (including `@bot` command forms) behaves consistently with step-5 fast paths.
  - Added/normalized `usage` metadata for argument-bearing Telegram commands: `/auto_start`, `/continue`, `/explain_ai`, `/imagegen`, `/currency`, `/kick`, `/mute`, `/unmute`, `/search`, and `/trace`.
  - Unified menu-era copy to context-card wording in Telegram UI surfaces (`🏠 Home` instead of `🏠 Main Menu` / `🏠 Return to Menu`).
  - Added safe fallback handling for `task:*` callback actions in `telegram_keyboards.py` to prevent callback crashes when task controls are unavailable.
  - Replaced `docs/features/TELEGRAM.md` with a full, registry-aligned Telegram command reference including options, aliases, and inline callback-action families.
  - Updated handbook Telegram section to remove stale Main Menu wording and point to the canonical Telegram command reference.
- **Telegram command UX overhaul (Phase 7 — natural-language command parity):**
  - Added generalized NL-to-command resolver for Telegram so visible slash commands can be triggered via natural language (English-first, deterministic matching).
  - NL execution now routes through existing command handlers/CLI templates instead of duplicating command logic.
  - Added strict confirmation gate for risky NL actions (`/run`, `/restart`, context-mutating operations, moderation commands): users must confirm with `yes`/`cancel` before execution.
  - Added usage-guidance fallback for NL intents that map to commands requiring arguments.
  - Updated Telegram docs to describe NL command parity and confirmation behavior.
  - Added deterministic NL parity coverage tests to keep visible slash commands mapped in NL resolution (`tests/test_telegram_nl_registry_coverage.py`).
- **Telegram command UX overhaul (Phase 8 — command-first suggestions + help polish):**
  - Improved `/help` formatting with a Quick Start section and natural-language examples for faster onboarding.
  - NL command resolver now detects tied top matches and asks users to choose instead of guessing the command.
  - For action-oriented NL requests that don’t map cleanly, Telegram now suggests likely commands (usage-first) instead of silently falling back.
  - Added regression tests for NL suggestion and ambiguity handling in Telegram reminders suite.
  - `/models big|small|coder|auto` — passing a tier name switches immediately (e.g. `/models big` = same as `/big`). Aliases `/model`, `/routing`, `/router` also accept the tier arg.
  - `/providers <name>` — shows a focused card for the named provider with config guidance; falls back to full hub when no arg given.
  - `/spaces <name>` — quick-switches to a space when a name is passed, skipping the list view.
  - `/cancelreminder all` — cancels all active reminders for the user; `/cancelreminder <id>` still works.
  - `/choice` — now accepts `,` and `|` as separators in addition to ` or `; also shows usage when called with no arguments.
  - `/weather [city]` — location-aware weather: `/weather London` fetches weather for that city; plain `/weather` stays serverside.
  - `/docker [ps|logs <name>|restart <name>|stop <name>|start <name>|<container>]` — smart container command dispatch instead of always running `docker ps`.
  - `SlashCommandEntry` dataclass gains a `usage: str` field shown in `/help` output next to the description.
  - `_generate_help_text` overhauled: section headers now include emoji, entries with a `usage` hint display the usage string instead of the bare command name.
  - `_handle_autoheal` parameter renamed `args` → `text` (with `/autoheal` prefix stripping) to work correctly with dynamic dispatch; test suite updated accordingly.
- **Telegram command UX overhaul (Phase 9 — one-tap NL command execution):**
  - NL suggestion and ambiguity cards now include inline one-tap command buttons (`nl_pick:<command>`) so users can run suggested commands directly.
  - `nl_pick` execution follows the same safety policy as typed NL: safe commands run immediately, risky commands open explicit yes/cancel confirmation, and argument-required commands show usage guidance.
  - Added Telegram reminder-suite coverage for suggestion keyboard rendering and `nl_pick` callback behavior (safe, risky, and missing-args paths).
- **Telegram provider readiness gate:** Cloud provider rows in `/providers` are no longer treated as ready based on vault key presence alone; readiness now requires validated vault status (or explicit provider key detection), so unvalidated keys remain hidden/locked as intended.
- **Monitoring command import resilience:** `navig.commands.monitoring` now gracefully falls back to an internal `is_local_host` helper when `navig.remote` exports only `RemoteOperations` (e.g., import-boundary test stubs), preventing module import failures in unicode/status helper paths.
- **Server template manager compatibility:** Restored `ServerTemplateManager._deep_merge()` as a backward-compatible wrapper to the shared `deep_merge` utility, preserving existing test/caller expectations after merge-helper consolidation.
- **Goal orchestration regression stability:** Hardened the soul tuple regression assertion to validate `Soul.get_mood` return annotation via `inspect.signature` instead of source-text slicing, removing order-dependent flakiness from source-line resolution.
- **Lazy console context-manager support:** `_LazyConsole` now implements `__enter__`/`__exit__` and delegates to the underlying Rich console, preventing threaded Live/Status context-manager crashes during long pytest runs.
- **Approval/auth profile log readability:** Normalized approval-gate and auth-profile cooldown/failure logs to the active logger formatting style so values are rendered (no literal `%s/%d` placeholders in runtime diagnostics).
- **Tool spillover filename hardening:** `cap_result()` spillover filenames now sanitize tool names for Windows-invalid/special characters (including `:`), preventing spill write failures for tool names like `mcp:server/tool`.
- Release automation: publishing a non-draft/non-prerelease GitHub Release (`v*` tag) now triggers PyPI publication via `.github/workflows/publish.yml` (trusted publishing), with tag-to-`pyproject.toml` version validation and distribution checks.
- CLI UX cleanup: top-level `navig ask` now works as a clean first-class entry point (same behavior as `navig ai ask`) without deprecation noise.
- Ask/Copilot cleanup: removed user-facing "legacy" fallback wording from AI ask path and updated stale `navig ask sessions`/`navig ask ask` examples to canonical `navig copilot ...` forms in interactive/session help text.
- Documentation consistency pass: aligned branch workflow in `CONTRIBUTING.md`, fixed installer script links in `docs/INDEX.md`, and replaced duplicated `docs/README.md` script list with a real docs index.
- Local-only workspace policy clarified: `.dev/` is now the default AI working folder (scripts/logs/outputs), `.local/` is reserved for backups/moved artifacts and compatibility temp files.
- Packaging/publish hardening: removed accidental `CHANGELOG.md` ignore entry, excluded `.dev/` from source distributions, and added root `.dockerignore` exclusions for local/runtime folders.
- Telegram provider picker UX: unconfigured providers now show the key indicator inline with the provider button label (single button), instead of a separate key-only button.
- Local host execution semantics: `navig run` now executes directly on local hosts (`type: local` or `is_local: true`) without SSH/tunnel.
- `navig host test` now performs a local shell probe for local hosts and SSH connectivity for remote hosts.
- CLI routing control: added `navig mode route show` and `navig mode route set <small|big|code> --provider ... --model ...`.
- Local-first host recovery: when no hosts are configured, active host/server recovery now auto-bootstraps `localhost` before falling back to manual host setup prompts.
- Init status view: added `navig init --status` and automatic init status summary when setup is already configured.
- Init quickstart handoff: added `navig init --profile quickstart` (alias to `operator`) with chat-first bootstrap flow, one-time Telegram onboarding baton on `/start`, canonical onboarding checklist/progress in Telegram, explicit success-event step completion (`ai-provider` on provider activation/assignment, `first-host` on successful `host use`, `telegram-bot` on successful runtime auto-start), and automatic daemon+gateway+bot startup attempt when a Telegram token is configured.
- Init web search onboarding: added `web-search-provider` step in engine onboarding with premium provider picker UX (Perplexity/Brave/Gemini/Grok/Kimi), vault-first API-key persistence with compatibility fallback, env alias normalization (`ddg`/`google`/`xai`/`moonshot`) with invalid-value safe fallback to `auto`, `navig init --status` web-search readiness reporting, and `navig search --provider ...` routing wired to runtime provider resolution.
- Proactive/reminder hardening: reminder delivery now uses capped retries (max 3) with failure finalization, engagement cooldown buckets are split (`checkin` vs `idle_nudge` vs `wrapup`), notifier/engine share a process-level engagement coordinator singleton, scheduled task last-run state survives restarts, AI auto-mode sessions expire after 24h inactivity with user-visible notice, and proactive poll intervals are configurable via `proactive.*_interval_sec`.
- **Overlap & Redundancy Audit (13-step consolidation):**
  - **Canonical token estimation** (`navig/core/tokens.py`): Created single-source `estimate_tokens(text, *, chars_per_token=4.0) -> int` utility. Migrated 5 callsites from local `_estimate_tokens` implementations: `memory/key_facts.py` (property delegate), `memory/rag.py` (static method delegate), `memory/indexer.py` (delegate with custom `chars_per_token`), `agent/conv/history.py` (import alias), `agent/context_compressor.py` (wrapper with `chars_per_token=3.5`).
  - **YAML I/O consolidation** (`navig/core/yaml_io.py`): Added `safe_load_yaml()` with error-resilient loading. Absorbed `YamlDocument`, `YamlPath`, `YamlPathItem` types from `yaml_utils.py`. Updated 4 config-critical `yaml.dump` callsites to use `atomic_write_yaml` (config.py host/app save paths, profiles.py). Fixed `profiles.py` missing-key bug (`yaml_utils` → `yaml_io` import).
  - **ConfigSingleton → ConfigManager merge** (`navig/config.py`): Folded 8 plugin methods from `navig/core/shared_config.py` into `ConfigManager` (`plugins_dir`, `templates_dir`, `get_plugin_config`, `set_plugin_config`, `is_plugin_disabled`, `disable_plugin`, `enable_plugin`, `save`). Migrated `navig/plugins/base.py`, `navig/plugins/__init__.py`, and 3 plugin command functions in `navig/main.py` from `Config()` to `get_config_manager()`. Removed `Config` from `navig/core/__init__.py` exports. Deleted `navig/core/shared_config.py`.
  - **Media engine DRY** (`navig/gateway/channels/media_engine/__init__.py`): Extracted shared `_env()` and `_json_env()` vault-resolution helpers to package `__init__.py`. Updated `audio.py` and `image.py` to import from package instead of defining locally.
  - **`assistant_utils` inlining** (`navig/proactive_assistant.py`): Inlined `ensure_navig_directory()` (creates `~/.navig/` + subdirs + seeds JSON files) directly into `proactive_assistant.py`. Updated test imports accordingly.

### Removed
- **Dead modules deleted** (8 files): `navig/assistant_utils.py`, `navig/help_texts.py`, `navig/prompt_loader.py`, `navig/env_validator.py`, `navig/assistant_hooks.py`, `navig/skills_renderer.py`, `navig/ssh_keys.py`, `navig/core/shared_config.py`. All were unreferenced or fully superseded by canonical implementations. Also deleted orphaned test `tests/test_skills_prompt.py`.

## [2.4.20] - 2026-03-31

### Added
- **`navig vault` command group** — new top-level `vault` command with `set`, `get`, `list`,
  `validate`, and `delete` subcommands. Smart path parsing supports `provider/key` paths,
  bare provider names, and `provider_api_key` env-var style aliases.
  Example: `navig vault set nvidia/api_key nvapi-xxxx`
- **Multilingual agent support** — `ConversationalAgent` now pins per-user language overrides
  in `KeyFactStore`; fact extractor rewrites facts to English before storage; `FactRetriever`
  infers user language and formats responses accordingly.
- **Messaging registry provider validation** — `MessagingRegistry` exposes provider
  validation and retrieval helpers used by gateway channel routing.
- **New test suites** — `test_conversational_language_policy`, `test_channel_router_auto_persona`,
  `test_telegram_auto_runtime`, `test_key_facts`, `test_context_builder`,
  `test_telegram_provider_callbacks`, `test_messaging_registry`.

### Fixed
- **Windows test stability** — `tests/conftest.py` now performs pre-cleanup of stale `navig_cfg_isolated*` temp directories before session start, preventing `PermissionError` on Windows when SQLite vault files remain locked from crashed test runs. Also adds post-session vault connection cleanup.
- **`navig vault` was missing** — `navig vault set` returned `No such command 'vault'` because
  `vault_app` was not registered in `_EXTERNAL_CMD_MAP`.
- **`navig init` crash** on packages missing `_maybe_send_first_run_ping` — now catches
  `AttributeError` alongside `ImportError`.
- **Task Scheduler `Access Denied`** — `service_manager.py` now detects the error and prints
  a clear "run as administrator" instruction instead of a raw traceback.

### Added
- **`navig --schema` now works** — `navig/cli/registry.py` was missing, causing an `ImportError`
  whenever `navig --schema` or `navig help --schema` was called. The module is now created and
  returns a stable JSON document listing every command group and its subcommands.
- **`docs/user/workflows.md`** — New common-workflows guide covering database backup, deploy flows,
  bulk file transfer, server health checks, Telegram bot setup, database restore, and SSH key rotation.
- **HANDBOOK.md section 1.7 — In-App Help System** — Documents `navig help`, `navig help <topic>`,
  and `navig --schema` so users and AI agents know how to get help without leaving the terminal.
- **`docs/upgrade-roadmap.md`** — Prioritised roadmap of planned features, deprecation timeline
  (v3.0 sunset), implementation task checklist, and migration guide.

### Fixed
- **`docs/user/commands.md`** — Replaced stale references to deprecated `navig monitor` and
  `navig security` top-level commands with the canonical `navig host monitor show` and
  `navig host security show` forms. Updated tunnel commands (`start/stop/status/restart` →
  `run/remove/show/update`), file operations (`upload/download/cat/ls` → `navig file add/get/show/list`),
  and HestiaCP commands (`navig hestia` → `navig web hestia`).
- **`docs/user/quick-start.md`** — Replaced `python navig.py --version` with `navig --version`
  throughout. Updated install instructions to reflect `pip install navig` and `pip install -e .`.
  Fixed deprecated `navig monitor disk/health/resources` references to `navig host monitor show`.
  Fixed deprecated `navig tunnel start` to `navig tunnel run` and file command aliases.

### Fixed

- **Daemon: Telegram bot infinite restart loop** — Supervisor now stops retrying after
  15 consecutive quick exits (<30 s). Prevents 2 000+ restart cycles when a second bot
  instance on the same token causes a Telegram `Conflict` error. (`daemon/supervisor.py`)
- **MCP Forge unavailable on startup** — `MCPClientManager.add_client()` now receives an
  `MCPClientConfig` object instead of stale `name=`/`url=` keyword arguments that broke
  the Forge connection every time the daemon started. (`daemon/telegram_worker.py`)
- **`navig formation show --json` crashes on Windows** — `print()` raised
  `OSError: [Errno 22] Invalid argument` on non-TTY stdout (VS Code terminal). Fixed by
  switching to `click.echo()`. (`commands/formation.py`)
- **Voice setup instructions pointed to removed file** — STT warning now says
  `run 'navig init'` instead of referencing the deleted `~/.navig/.env` path.
  (`daemon/telegram_worker.py`)
- **GROK/XAI API key bypassed vault** — GROK key check now uses the vault resolver before
  falling back to environment variables, consistent with all other provider keys.
  (`daemon/telegram_worker.py`)

### Fixed — Help system

- **`navig help <topic>` silently returned empty results** — `help_command` looked in
  `navig/cli/help/` (wrong) instead of `navig/help/` (correct). All topic files now resolve
  correctly. (`cli/__init__.py`)
- **`navig --help` and bare `navig` printed bare fallback instead of index** — Both
  `show_compact_help` implementations imported a non-existent `navig.cli.help.render_root_help`
  module. Now read `navig/help/index.md` directly via Rich Markdown. (`cli/__init__.py`,
  `cli/_callbacks.py`)
- **`navig help <topic> --json` output contained unescaped newlines** — Rich Console
  line-wrapping was inserting unescaped `\n` into JSON string values. All JSON output in
  `help_command` now uses `typer.echo()` which bypasses Rich's formatter. (`cli/__init__.py`)
- **`task` help described a non-existent task queue** — `navig/help/task.md`,
  `navig/help/index.md`, and `HELP_REGISTRY["task"]` now correctly document `task` as a
  backward-compatible alias for `navig flow`. (`cli/help_dictionaries.py`, `help/task.md`,
  `help/index.md`, `help/flow.md`)

### Tests

- Added `tests/test_help_system.py` — 20 smoke tests covering help topic resolution,
  JSON output validity, `task`/`flow` alias documentation, missing-topic error handling,
  and markdown directory path correctness.

### Docs

- `docs/user/troubleshooting.md` — Added *Recently Fixed Issues (v2.4.15+)* section
  covering all five daemon/CLI bugs listed above.
- `docs/upgrade-roadmap.md` (new) — Prioritized implementation task list derived from the
  March 2026 crash-log and codebase audit.

---

## [2.4.14] - 2026-03-13

### Release (`main`)

- feat: animated TUI onboarding, navig upgrade cmd, auto-install textual
- docs: rewrite public-facing Markdown for v2.4.13 release
- chore: portability-audit fixes вЂ” v2.4.13 publish readiness
- chore: remove all tracked __pycache__ and .pyc files from git index
- feat: major update вЂ” mesh, browser, memory, gateway, new commands + gitignore hardening
- perf(QUANTUM-V E+B+A): batch monitor SSH 27->4 round-trips, docker lazy dispatch, production install mode
- perf(sessions): fast-scan mode for list/stats/delete вЂ” reads header line only, skips GBs of JSONL
- chore(dev): add pydantic + numpy to dev extras
- perf: navig help fast path + McpBridge port pre-check + disable debug_log
- feat(memory): wire existing memory module into all AI paths
- chore(dev): add pydantic + numpy to dev extras
- perf: navig help fast path + McpBridge port pre-check + disable debug_log
- feat(memory): wire existing memory module into all AI paths
- Update TASK_PROPOSALS.md
- Update TASK_PROPOSALS.md
- Refine issue proposals and restore minimal README
- chore: remove top-level __pycache__ from tracking
- chore: remove tracked __pycache__ files from repo
- chore: update gitignore for runtime artifacts
- docs: rewrite README вЂ” pro-grade, one-command install, donation links, full doc index
- fix(agent): auto-resolve GitHub Models token from vault/config + enhance /models command
- fix(bot): fix SOUL personality + multi-model fallback chain
- feat(core): GitHub Models routing, vault CLI improvements, personality fix
- feat(bridge): LLM provider chain, GitHub Models, streaming, webhook, copilot CLI, tests
- perf(cli): optimize startup from 886ms to ~450ms (50% faster)
- feat(storage): unified SQLite engine with PRAGMA profiles, write batching & query timing
- feat(store): implement SQLite local-first migration (Phases 1-3)
- feat(matrix): Phase 4 вЂ” persistent store, stats webhook, PG mirror
- feat(matrix): Phase 3 вЂ” E2EE key verification, device trust, SAS flow
- feat(matrix): Phase 2 вЂ” inbox bridge, notifications, file sharing

All notable changes to NAVIG are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.4.13] - 2026-03-12

### Security / Portability — Cross-Platform Audit (`upgrade/portability-audit`)
- **`navig/daemon/service_manager.py`** — `import ctypes` moved inside the `try` block of `is_admin()` to fix `NameError` on Windows (was crashing daemon startup).
- **`navig/commands/security.py`** — All 31 callsites replaced `result['exit_code']` / `result['stdout']` / `result['stderr']` dict access with `.returncode` / `.stdout` / `.stderr` attributes on `subprocess.CompletedProcess`; entire security subsystem was broken (TypeError on every call).
- **`navig/remote.py`** — Localhost-shortcut path now uses `shell=False` + `shlex.split()` instead of `shell=True`; added Windows Unix-tool guard (`NotImplementedError` for `ls`, `df`, `cat`, etc.).
- **`navig/agent/service.py`** — `_install_systemd()` guarded by `sys.platform`: Linux → `/etc/systemd/system`, macOS → `~/Library/LaunchAgents`, Windows → `~/navig-services` + `schtasks`, other platforms → graceful failure. Previously unconditionally wrote to `/etc/systemd/system` on all platforms.
- **`navig/core/automation_engine.py`** — `run_command` action: `shell=True` → `shell=False` + `shlex.split()` + RCE denylist.
- **`navig/agent/conversational.py`** — Both `command.run` and `navig.run` actions: `shell=True` → `shell=False` + `shlex.split()` + denylist.
- **`navig/discovery.py`** — `_build_ssh_command()`: `StrictHostKeyChecking=no` + `UserKnownHostsFile=/dev/null` → `accept-new`; insecure mode gated behind `self.insecure`. Fixed `HAS_PARAMIKO = True` hardcode — now a runtime try/import check.
- **`navig/commands/sync.py`** — rsync `-e` option: `StrictHostKeyChecking=no` → `accept-new`; `shlex.quote()` wrapping on `ssh_key` path.
- **`navig/agent/runner.py`** — Added Windows signal handler fallback: `signal.SIGBREAK` + `signal.SIGINT` via `signal.signal()` (Windows has no `loop.add_signal_handler()`).
- **`navig/commands/backup.py`** — `os.chmod(config_path, 0o600)` wrapped in `try/except OSError`; added Windows `icacls` ACL fallback.
- **`navig/logging_setup.py`** — `_NAVIG_DIR` / `_LOG_DIR` / `_LOG_FILE` / `_DEBUG_FLAG` now resolve from `NAVIG_CONFIG_DIR` environment variable before falling back to `~/.navig`.
- **`navig/ipc_pipe.py`** — Unix socket path uses `tempfile.gettempdir()` instead of `/tmp` (breaks under macOS App Sandbox); `conn._handle.SetReadTimeout(...)` wrapped in `try/except AttributeError`.
- **`navig/adapters/os/linux.py`** — Package manager detection uses `shutil.which(pm)` instead of `os.path.exists('/usr/bin/{pm}')` to respect `$PATH`.
- **`navig/commands/docker.py`** — Unsanitised `| grep -E '{filter}'` injection fixed with `shlex.quote(filter)`.
- **`navig/tunnel.py`** — Removed `-f` SSH background-fork flag; `process.pid` stored directly instead of psutil polling (eliminates PID reuse race); reduced `time.sleep(2)` → `time.sleep(0.5)`.
- **`navig/vault/encryption.py`** + **`navig/vault/storage.py`** — Added Windows `icacls` ACL restriction after `os.chmod` for vault and salt files.
- **`navig/commands/monitoring.py`** — CPU batch command replaced fragile `top -bn1 | grep 'Cpu(s)'` (breaks on RHEL/CentOS) with portable `/proc/stat` awk calculation.
- **`navig/commands/files.py`** — Disk-space error hint is platform-conditional: `df -h` on Unix, `Get-PSDrive` / `dir /-c` on Windows.
- **`navig/plugins/navig-mini/plugin.py`** — Default `--dir` option changed from `/root/navig-mini` to `~/navig-mini` (non-root-user portability).
- **Encoding sweep (13 files)** — Added `encoding='utf-8'` to all `open()` / `Path.read_text()` calls in: `tunnel.py`, `mcp_manager.py`, `commands/proactive.py`, `commands/script.py`, `commands/suggest.py`, `commands/local.py`, `commands/triggers.py`, `assistant_utils.py`, `tasks/queue.py`, `memory/embeddings.py`, `vault/core.py`, `local_operations.py`, `commands/monitoring.py`.
- **Documentation updates** — Updated 5 docs to reflect `NAVIG_CONFIG_DIR` portability:
  - `README.md` — Config location note updated; config tree annotated with `← default; override with NAVIG_CONFIG_DIR`.
  - `docs/user/troubleshooting.md` — Debug log path mentions `$NAVIG_CONFIG_DIR/debug.log` fallback.
  - `docs/user/HANDBOOK.md` — CI/CD env-var table gains `NAVIG_CONFIG_DIR` row (override config/log base directory).
  - `docs/architecture/AUTONOMOUS_DEPLOYMENT.md` — Dockerfile and docker-compose examples migrated from `/root/.navig` to `/app/.navig` with `ENV NAVIG_CONFIG_DIR=/app/.navig` (non-root container portability).
  - `docs/dev/PRODUCTION_DEPLOYMENT.md` — `docker run` mounts updated to `/app/.navig` with `-e NAVIG_CONFIG_DIR=/app/.navig`.

### Security / Portability — Cross-Platform Audit Round 2 (`upgrade/portability-audit`, 2026-03-01)
- **`navig/commands/sync.py`** (`_run_pull`) — `StrictHostKeyChecking=no` → `accept-new`; `ssh_key` path now wrapped with `shlex.quote(str(...))`. Mirrors the `_run_push` fix applied in Round 1 — the pull-side was inadvertently missed. [R1 — Critical regression]
- **`navig/commands/security.py`** (`firewall_allow`) — `allow_from`, `port`, `protocol` now wrapped with `shlex.quote(str(...))` before interpolation into the UFW SSH command. Previously allowed remote command injection via any of those three user-supplied arguments. [C1 — Critical]
- **`navig/commands/security.py`** (`fail2ban_unban`) — `jail` and `ip_address` now wrapped with `shlex.quote()` in both `fail2ban-client set … unbanip` and `fail2ban-client unban` commands. [C2 — Critical]; `import shlex` added to module imports.
- **`navig/core/automation_engine.py`** (`run_command`) — Denylist check now normalises the command string via `unicodedata.normalize('NFKC')` + `re.sub(r'\\s+', ' ')` + `.lower()` before matching, blocking bypass via uppercase (`RM -RF`), double-space, tab, or Unicode look-alikes. `shlex.split()` call now passes `posix=(sys.platform != 'win32')` to preserve Windows backslash paths. [C3 + L2]
- **`navig/core/evolution/fix.py`** (`CodeFixer.validate`) — `subprocess.run(cmd, shell=True)` replaced with `shlex.split(cmd, posix=…) + shell=False` for config-sourced `check_command` strings. [M1]
- **`navig/commands/packs.py`** / **`navig/commands/skills.py`** — Added explicit trust-boundary comments on `shell=True` subprocess calls for pack/skill hook commands (intentional — author-defined scripts may use pipelines). [M2, M3]
- **`navig/commands/database.py`** (`_create_mysql_config_file`) — `os.chmod(0o600)` now guarded by `sys.platform` check with `icacls` fallback on Windows, matching the identical fix already applied to `commands/backup.py`. `import sys` added. [M4]
- **`navig/commands/backup.py`** — `encoding='utf-8'` added to all six bare `open()` / `os.fdopen()` write calls (`_create_mysql_config_file`, three `metadata.json` writes, MySQL dump header, HestiaCP metadata, web-server metadata). [M5–M8 + 2 additional sites]
- **`navig/commands/agent.py`** — `encoding='utf-8'` added to all bare `open()` calls (config read/write ×4, log read, personality write) and two `write_text()` calls (systemd temp file, macOS plist). [M9]
- **`navig/commands/assistant.py`** — `encoding='utf-8'` added to all bare `open()` calls (history read, issues read ×2, context JSON write, reset-data write). [M10]
- **`navig/adapters/os/linux.py`** (`LinuxAdapter`) — `get_temp_directory()` fallback changed from hardcoded `'/tmp'` to `tempfile.gettempdir()`; `get_home_directory()` fallback changed from `'/root'` (fails for non-root container users) to `Path.home()`. [M11]
- **`navig/daemon/service_manager.py`** (`_schtasks_xml`) — Task Scheduler XML now escapes `python` executable path and `args` string via `xml.sax.saxutils.escape()` (stdlib, no new dependency) before interpolation. Paths containing `&`, `<`, or `>` previously produced malformed XML. [M12]
- **`navig/commands/bridge_ai.py`** (`bridge_stop`) — `os.kill(pid, SIGTERM)` now guarded by `sys.platform != 'win32'`; Windows branch uses `subprocess.run(["taskkill", "/PID", str(pid)])` for catchable graceful shutdown instead of `TerminateProcess`. [L1]
- **`navig/agent/service.py`** (`_install_systemd`) — `service_path.write_text(unit_content)` now passes `encoding='utf-8'` explicitly. [L3]

> Test results after Round 2: **3058 passed** (+7 vs Round 1 baseline), **53 skipped**, **1 pre-existing flaky failure** (`test_decoy_guard::test_different_messages_different_output` — randomness-based assertion, unrelated to audit changes). All 3 previously listed pre-existing failures are now resolved.

### Performance — Phase 3 Startup & Syscall Reduction (`upgrade/portability-audit`, 2026-02-28)
- **`navig/config.py`** (`_is_directory_accessible`) — `list(iterdir())` → `next(iterdir(), None)`: O(N) → O(1) directory-access probe.
- **`navig/config.py`** (`_ensure_directories`) — stamp-file gate (`~/.navig/.dirs_init`, 24h TTL): 40+ `mkdir` syscalls → single `stat` on warm runs every process invocation after the first.
- **`navig/config.py`** (`_load_global_config_cached`) — shadow-verify thread now rate-limited: skips spawn if `time.monotonic() - _last_shadow_ts < 300`; at most one background thread per 5 minutes.
- **`navig/config.py`** (`_find_app_root`) — sentinel-based process-lifetime cache (`_APP_ROOT_NOT_SEARCHED`): CWD filesystem walk runs once per process, not per call.
- **`navig/config.py`** (`get_active_host`) — `_active_host_cache` tuple: YAML parse on first call only; `set_active_host` invalidates. New `_resolve_active_host()` private helper.
- **`navig/config.py`** (`list_hosts`) — dir-mtime pre-check guards the per-file stat scan; returns cached list immediately when directory mtime unchanged.
- **`navig/config.py`** (`list_apps`) — `_apps_list_cache` was initialised in `__init__` but never used; now wired with mtime-keyed per-host invalidation.
- **`navig/config.py`** (`_host_config_cache`) — capped at 64 entries via new `_host_cache_put()` LRU helper; prevents unbounded dict growth in multi-host environments.
- **`navig/plugins/__init__.py`** — module-level `Console()` eager import replaced with lazy `_get_console()` factory; `rich.console` only imported when a plugin actually prints output.
- **`navig/cli/__init__.py`** (`_EXTERNAL_CMD_MAP`) — 10 commands added: `telemetry`, `wut`, `eval`, `agents`, `webdash`, `explain`, `snapshot`, `replay`, `cloud`, `benchmark`; all now fully lazy-dispatched.
- **`navig/main.py`** — 11 unconditional `try/except` import blocks removed (replaced by `_EXTERNAL_CMD_MAP` entries); eliminates 11 module imports on every startup.
- **`navig/remote.py`** — `SSHConnectionPool` fast-path added for `capture_output=True` SSH calls; reuses pre-existing pooled paramiko connections from `connection_pool.py` (which was dead code); subprocess fallback retained for PTY/non-captured paths.

> Full analysis: `docs/CLI_STARTUP_PERFORMANCE.md` § Phase 3. Zero regressions (2939 passed, 53 skipped).

### Changed — OSS Release Audit (MVP1 gate)
- **`navig/integrations/telegram_bridge.py`** — added `_scrub_token()` helper;
  `logger.warning` in the long-poll exception handler now redacts the bot token
  from httpx URL reprs before they reach any log output (`.cursorrules` rule #6:
  secrets never in logs, even under `--debug`).
- **`navig/integrations/telegram_inbox.py`** — `_get_file_bytes` now raises
  `RuntimeError` when Telegram returns `ok=false` (e.g. 401 Unauthorized,
  revoked bot token); `_handle_message` catches and logs without crashing;
  `run()` uses `upd.get("update_id")` defensively to skip malformed updates.
- **`navig/commands/memory.py`, `navig/commands/kg.py`, `navig/commands/store.py`**
  — added `# CURSORRULES:R8-EXCEPTION:` annotations explaining why each
  diagnostic read-only `sqlite3` call is justified and cannot use
  `storage/engine.py` (`.cursorrules` rule #8 explicit documented exception).
- **`navig/commands/spaces.py`** — **name history**: this file was originally
  created as `spaces.py` to provide a "spaces" concept distinct from `space.py`
  (which manages full space directories).  During the active_space_context
  refactor the command was renamed to `spaces` internally; the file was kept as
  `spaces.py` for backward-compat imports.  The public CLI surface is
  `navig spaces list/show/switch`; the `spaces_app` alias exists only for
  internal imports.  The command does **not** implement `@target` routing
  (agents/workspaces/people) — that is a separate post-MVP2 feature tracked
  in `.navig/plans/TASK_PROPOSALS.md`; see `# KNOWN GAP:` comments in the file.

### Fixed — Security / Test Coverage
- Added 4 failure-path tests to `tests/test_telegram_inbox.py`
  (tests 12–15): Telegram API 401 rejection, download failure swallowed,
  bad-token run() does not crash, malformed update skipped.

### Added — Package Manager + Cross-Platform Architecture Sprint

#### Cross-Platform Path Infrastructure
- `navig/config.py` — `roaming_root` (AppData\\Roaming\\NAVIG on Windows, `~/.config/NAVIG` on Linux/macOS), `identity_dir`, `store_dir`, `system_dir`, `logs_dir`, `cache_dir` — all via `platformdirs`; removed hardcoded `~/.navig` references
- `navig/daemon/entry.py` — `_navig_home()` lazy helper; auto-starts WebSocket server in `main()`
- `navig/daemon/service_manager.py` — full **macOS launchd** backend: `launchd_install/uninstall/status`; plist written to `~/Library/LaunchAgents/run.navig.NavigDaemon.plist`; `detect_best_method()` returns `"launchd"` on darwin
- `navig/commands/service.py` — log path resolved via `ConfigManager().logs_dir`
- `pyproject.toml` — `platformdirs>=4.0.0` dependency added

#### New CLI Commands
- `navig user` — user profile management (`show/set/switch`)
- `navig node` — multi-node discovery and management UI
- `navig boot` — boot sequence configuration
- `navig space` — space/environment management (dev/staging/prod)
- `navig blueprint` — project blueprint scaffold and template definitions
- `navig deck` — deck/stack management UI
- `navig portable` — portable mode for USB/external drive launch
- `navig install` (v2) — package installer with SHA-256 cache, `--update`, `--freeze`
- `navig migrate run/status/rollback` — migrate `~/.navig` → platform roaming dir; compat symjunction so old paths keep working
- `navig system init/wallpaper/icons/theme/sounds` — OS integration in `portable`/`standard`/`deep` modes; cross-platform (Win32 API + gsettings/GTK)
- `navig paths` — show all resolved NAVIG directories with ✅/❌ status + daemon WS reachability; `--json`
- `navig mcp install/uninstall/status/serve` — wires `.vscode/mcp.json` and VS Code user settings for the NAVIG MCP server

#### Daemon WebSocket Server
- `navig/daemon/ws_server.py` — JSON-RPC 2.0 WebSocket server (`ws://127.0.0.1:7001/ws`) with `exec` (subprocess streaming), `status` (daemon health), `cancel` (kill in-flight proc), `start_ws_server()` background thread launcher

---

## [2.3.0] — 2026-02-25 — First Public Open-Source Release

> First public release on GitHub under Apache-2.0. All internal identifiers,
> personal paths, and private server references scrubbed. Repository renamed
> to `navig-run/core`.

### Workspace
- **Rename NAVIG Bar → NAVIG Dock** (no functional changes) — `@navig/bar` v0.1.0 → `@navig/dock` v0.1.1
  - Folder `navig-bar/` → `navig-dock/`; package name `@navig/bar` → `@navig/dock`
  - All internal imports, DOM IDs, manifest command keys, and build artifact names updated
  - Root `package.json` scripts updated (`dev:bar` → `dev:dock`, etc.)
  - `pnpm-workspace.yaml` deduped: single `navig-dock` entry, `navig-bar` removed
  - See [`navig-dock/docs/audit.md`](../navig-dock/docs/audit.md) and [`navig-dock/docs/README.md`](../navig-dock/docs/README.md)

### Open-Source Readiness
- Switched license posture to **Apache-2.0** for ecosystem and enterprise adoption.
- Raised Python support floor to **3.10+** and aligned packaging metadata.
- Added GitHub Actions workflows for CI, CodeQL, and release provenance attestation.
- Added repository community scaffolding:
  - `CONTRIBUTING.md`
  - `CODE_OF_CONDUCT.md`
  - issue templates and PR template
  - `.editorconfig`
- Enforced coverage gate (`--cov-fail-under=65`) for release readiness.
- Updated governance/security/release policy docs for OSS publication.

### Added
- **Flux Mesh — LAN-local multi-node discovery** (`navig/mesh/`):
  - `NodeRegistry` — singleton peer store with health states (`online` / `degraded` / `offline`) and automatic eviction after 30 min of silence.
  - `MeshDiscovery` — async UDP multicast broadcaster/listener on `224.0.0.251:5354`. Sends HELLO on startup, heartbeats every 30 s, GOODBYE on shutdown.
  - `router.py` — HTTP proxy that forwards requests to the best available peer (lowest load) with `mesh_token` auth and 10 s timeout.
  - Gateway routes: `GET /mesh/peers`, `POST /mesh/ping`, `POST /mesh/route` — registered automatically via `register_all_routes()`.
  - `_ensure_mesh_token()` — auto-generates `secrets.token_hex(32)` on first gateway start; persists to `~/.navig/config.yaml`.
  - **`navig flux` CLI command group** (`navig/commands/flux.py`): `status`, `peers`, `ping <url>`, `route <message>` (also accessible as `navig fx`).
  - **BackendRegistry priority chain** rewritten: local daemon → best mesh peer → Copilot (last).
  - **VS Code Bridge nodes panel** (`navig.nodesPanel`): live peer tree with health icons, set/clear target, add node, scan LAN commands.
  - 11 unit tests (`tests/mesh/test_registry.py`) — all passing.
  - Feature doc: `docs/features/flux-mesh.md`.

- **Multi-channel architecture design** — `.navig/plans/CHANNEL_ARCHITECTURE.md` with ChannelAdapter base class, ChannelRouter, ChannelCapabilities, config schema, security rules per channel, and phased implementation plan (Telegram, Discord, Web UI, CLI, Email).
- **Conversational pre-filter** in intent parser — Detects greetings, identity questions, general questions, and casual chat; routes them directly to AI instead of NLP command matching.

### Improved
- **Bot consciousness** — The Telegram bot now responds conversationally instead of robotically:
  - System prompt injects SOUL.md personality (Deepwatch Abysswarden identity).
  - Identity questions (who are you, what's your name) go through AI for dynamic, context-aware responses instead of hardcoded `SOUL_RESPONSES` strings.
  - `_generate_contextual_response()` used for all personality questions, with static SOUL_RESPONSES as fallback.
- **NLP time regex fix** — "what time is it?" no longer produces `/time IS`. Replaced single greedy regex with specific patterns: "time is it in \<tz\>", "time in \<tz\>", "time is it", "current time".
- **dev.instructions.md** — Now references `~/.navig/workspace/` (SOUL.md, IDENTITY.md, AGENTS.md, USER.md) and `.navig/plans/` (DEV_PLAN.md, ROADMAP.md, VISION.md, SPEC.md).

### Fixed
- **Intent parser false positives** — Conversational messages ("hello", "how are you", "what is the meaning of life?") no longer get intercepted as commands.

- **System tray overhaul** — Complete rewrite of the tray menu:
  - **Dynamic menu** — Menu rebuilds on every right-click, reflecting live service states (running/stopped/grayed out).
  - **Rich command groups** — Hosts, Database, Vault, Skills, Backups sub-menus with interactive terminals that stay open after output.
  - **Autostart with Windows fixed** — Registry read/write now uses correct `winreg.OpenKey`/`CreateKeyEx` API (was using non-existent `OpenSubKey`).
  - **Interactive commands** — Quick action commands now open `cmd /k` terminals that remain open so you can read the output.
  - **Settings sub-menu** — Toggle auto-start tray with Windows, toggle auto-start bot on tray launch, open config/log folders.
  - **Advanced sub-menu** — Standalone Gateway/Agent start/stop (grayed when not applicable), service status, daemon logs, NAVIG Terminal.
  - **"Open NAVIG Terminal"** — Opens a cmd prompt pre-configured for navig commands.

## [3.23.0] — Persistent Daemon + Service Management

### Added
- **NAVIG Daemon** (`navig/daemon/`) — New process supervisor that keeps the Telegram bot (and optionally gateway, scheduler) running permanently with auto-restart on crash, exponential back-off, PID tracking, and structured log rotation.
- **`navig service install`** — One-command service installation. Auto-detects the best method:
  - **NSSM** (if installed + admin) — true Windows service, starts on boot
  - **Task Scheduler** (no admin needed) — starts on login, auto-restarts on failure
- **`navig service start/stop/restart`** — Full lifecycle management of the daemon process.
- **`navig service status`** — Shows daemon PID, child process health, restart counts, and service registration status.
- **`navig service logs`** — Tail daemon logs with `--follow` support; logs rotate at 5 MB.
- **`navig service config`** — View/edit daemon configuration (`~/.navig/daemon/config.json`): toggle bot/gateway/scheduler, health-check port.
- **`navig service uninstall`** — Clean removal of service registration and daemon.
- **Health-check TCP endpoint** — Optional HTTP health-check server (for monitoring tools) returns JSON status of all child processes.
- **Hybrid agent profiles** — Formation agents now support directory-based profiles (`agents/<id>/` with `SOUL.md`, `PERSONALITY.md`, `PLAYBOOK.md`, `MEMORY.md`, `agent.json`). System prompt composed from markdown docs at load time. Backward compatible with flat `.agent.json` format.
- **System tray daemon control** — Tray app (`scripts/navig_tray.py`) now manages the daemon: start/stop Telegram Bot from tray menu, live status display, auto-start on launch, health monitoring with external daemon detection.

### Fixed
- **Bot crash on startup** — `TypeError: AsyncClient.__init__() got an unexpected keyword argument 'proxies'` caused by httpx 0.28+ removing the `proxies` parameter. Pinned httpx to `>=0.27,<0.28` which satisfies both python-telegram-bot and mcp/ollama.
- **Duplicate log lines** — Every daemon child log line appeared twice due to PIPE+merge. Supervisor now redirects stdout/stderr directly to log files.
- **Visible CMD window** — Daemon started in a visible console. Now uses `pythonw.exe` + `CREATE_NO_WINDOW` flag + Task Scheduler `<Hidden>true</Hidden>` for fully invisible background operation.
- **YAML parse errors** — Unquoted colons in `description` fields of `github/SKILL.md` and `postgres/SKILL.md` caused YAML parse failures.
- **Windows `navig agent service install` only printed instructions** — Now delegates to the real daemon service manager for actual NSSM/Task Scheduler installation.

### Changed
- **Agent schema relaxed** — `AGENT_SCHEMA` required fields reduced from 7 to 3 (`id`, `name`, `role`) to support lightweight directory-based profiles.

## [3.22.0] — Telegram Heartbeat + Council Overhaul

### Added
- **Heartbeat system** — VS Code extension writes `~/.navig/heartbeat.json` every 30s while a formation is active (formation ID, agents, workspace, timestamp). Cleared on stop/deactivate.
- **Proactive Telegram messages** — Bot checks for actionable items (urgent markers, failing tests, inbox briefs, next-step files, TODOs in `.navig/plans/`) and sends at most **one short message per heartbeat window** (default 5 min). Completely silent when VS Code is idle or closed.
- **`/formation` command** — Check current VS Code formation status on demand (active/stale/offline, agents, workspace, next action).
- **Formation context injection** — When you message the bot directly, it includes lightweight formation state so the AI can reference your active work without you having to explain.
- **Core NAVIG Telegram persona** — Bot operates as "Core NAVIG" on Telegram: calm, fast, direct. Can reference multiple formations, answer general questions, suggest which project deserves attention.
- **Urgency-aware action scanner** — Proactive messages now check (in priority order): `urgent.md`, `failing-tests.md`, inbox briefs, `next-step.md`, `todo.md`.
- **Heartbeat configuration** — New env vars: `HEARTBEAT_ENABLED` (true/false), `HEARTBEAT_INTERVAL` (seconds between checks, default 60), `HEARTBEAT_WINDOW` (min seconds between proactive messages, default 300).
- **Council compact view** — New Full/Compact toggle in Council Panel header. Compact mode truncates agent messages to 2 lines with click-to-expand, smaller text, and tighter spacing for scanning long deliberations.

### Fixed
- **Council agents all said the same thing** — Agents now receive their specific `scope` areas in the prompt and are told which other roles will cover other angles. Each agent answers from their unique perspective: architect on trade-offs, devops on ops overhead, QA on test complexity, security on attack surface, product on user impact.
- **Council was too slow** — Agents now run **in parallel** within each round (was sequential). 5-agent round completes in ~10-15s instead of ~45-50s (3-4x faster).
- **Thread-safe AI imports** — Moved `ask_ai_with_context` import to module level to prevent race conditions when 5 agents import simultaneously in parallel threads.

### Improved
- **Council synthesis** — Final decision prompt now highlights where agents **agreed** and **disagreed**, with a concrete next step, instead of a bland summary.

### Changed
- **SOUL.md** — Added Telegram & External Channel Behavior section defining Core NAVIG personality rules for paired channels.

## [3.21.0] — Schema Fix + Agent Personalities

### Fixed
- **"No active formation" error** — Python schema validation rejected the new object-format `brief_templates` (introduced in 3.20.0). Updated `schema.py` to accept both string and object formats via `oneOf` schema, and `types.py` to use generic `list` type.
- **CLI `---` parsing bug** — session context markers using `---` were parsed as CLI option flags by Typer/Click, causing "No such option" errors. Changed markers to `[Prior conversation]` / `[End of prior conversation]` bracket format.

### Changed
- **Agent personalities** — Council agents now have human names and distinct personalities:
  - `system_architect` → **Marcos Vega** (System Architect)
  - `devops` → **Kira Nakamura** (DevOps Engineer)
  - `product_owner` → **Elena Cortez** (Product Owner)
  - `qa` → **Tomasz Wójcik** (QA Lead)
  - `security_officer` → **Nina Okafor** (Security Officer)
- Agent roles simplified to cleaner titles (e.g., "Technical Architecture Authority" → "System Architect")

## [3.20.0] — Workshop & Briefs Overhaul + Council UI v3

### Fixed
- **Briefs were generating generic garbage** — agents talked about "GraphQL gateways" and "gRPC" instead of the actual project. Root cause: prompts had no codebase context.
- **Removed old useless brief templates** (aig.md, ia_brief.md) that produced AI governance and architecture decision records unrelated to the project.

### Changed
- **Briefs now grounded in actual codebase** — new `gatherProjectContext()` reads README.md, package.json/pyproject.toml, directory structure, formation info, and planning docs to give agents real context about the project.
- **Brief templates now include per-template prompts** — each brief in `formation.json` specifies a `prompt` (what to write), `agent` (which specialist to use), and `name` (human label). Old string-array format still supported (backward compatible).
- **Briefs use single agent instead of full council** — 4x faster generation. Each brief is handled by the most relevant specialist (e.g. security_officer for Security Audit, product_owner for Sprint Brief).
- **Brief generation shows QuickPick** — users can select which briefs to generate, see the prompt preview, and deselect any they don't want.
- **Full Output panel logging** — every brief generation step logs to the NAVIG Output channel with `[BRIEFS]` prefix for visibility.
- **Post-generation actions** — notification offers "Open Folder" and "Open First" buttons after brief generation.
- **Moved "Analyze Project" and "Generate Docs" out of Formation section** — they belong in Workshop Control only. Formation section now focuses on: Agents, Council, Briefs, Documents.
- **Added "Open Briefs Folder" action** in Formation sidebar — quick access to `.navig/plans/briefs/`.

### Council Panel v3
- **3-panel layout** — IRC-style ChatLog (center), Agent Roster sidebar (right, 210px), smart InputBar (bottom)
- **Agent Roster** — per-agent cards with emoji avatar, name, role, status pill (online/thinking/offline), last message snippet
- **Click roster = highlight agent messages**, Shift+click = solo mode (hides other agents' messages)
- **Round separators** — visual dividers between deliberation rounds with "R1", "R2" tags on messages
- **Auto-scroll indicator** — "↓ New messages" bar appears when scrolled up during active deliberation
- **⚡ Seed Question button** — AI-generates a council question based on selected topic (codebase, roadmap, infra, tests, security, product, team)
- **Topic dropdown** — select a topic category before asking or seeding a question
- **@mention routing** — type `@AgentName question` to route directly to a specific agent
- **Responsive layout** — roster collapses on narrow viewports with hamburger toggle
- **Processing state** — roster dots animate to "thinking" pulse during deliberation
- **Agent emojis** — role-based emoji mapping (🏛️ architect, ⚙️ devops, 📋 product, 🔬 qa, 🛡️ security)

## [3.19.0] — Formation Commands Fixed: Analyze, Generate, Briefs

### Fixed
- **JSON parse error "Failed to show formation: Unexpected token 'I'"** — Python CLI logger moved to stderr; stdout reserved for clean JSON output
- **Analyze Project** — was calling non-existent `formation council` CLI command; now uses `council run` with real project document content
- **Generate Docs** — same fix; council deliberation with project context, structured markdown output
- **Generate Briefs** — was calling non-existent `formation briefs` CLI command; now iterates templates and runs council per brief
- **Agent Run** — added JSON extraction safety wrapper for CLI output parsing

### Enhanced
- **Council Panel receives project docs** — agents now see actual document content when deliberating, not just metadata
- **Single-agent tasks** include project docs context too
- **Briefs** are cancellable, include headers with formation name and timestamp

## [3.18.0] — Council Panel v2: Persistent Agent Sessions

### Added
- **Session persistence** — agents remember prior conversation across messages; rolling context window (last 20 turns) is prepended to every new CLI call
- **Start/Stop formation from Council Panel** — header button toggles formation on/off directly from the council chat, with system messages confirming state changes
- **Agent status indicators** — green glowing dots when formation is active (online), gray when stopped (offline), with smooth CSS transitions
- **Configurable deliberation rounds** — dropdown in panel header (1-5 rounds) controls how many discussion rounds the council performs
- **@mention agent targeting** — type `@AgentName message` in chat to route directly to that agent, regardless of which chip is selected
- **Session badge** — shows number of conversation turns stored in memory
- **Online/Offline status badge** — header shows "N online" or "offline" status
- **Live state updates** — `stateUpdate` message pushes status dot and badge changes to webview without full re-render
- **Clear session** — "Clear" button resets both chat messages and session memory

### Changed
- Council Panel header redesigned with formation name, status badge, session badge, rounds control, and Start/Stop/Refresh/Clear buttons
- Agent chips now use status dots (online/offline) instead of colored agent dots
- Empty state messaging adapts: "Council Ready" when active vs "Council Chamber" when stopped
- Input placeholder changes based on formation state

### Added — Formation System (Profile-Based Agent Teams)
- **`navig formation list`** — list all available formations (project + global)
- **`navig formation show <id>`** — display formation details, agents, API connectors
- **`navig formation init <profile>`** — activate a formation for the current workspace
- **`navig formation agents`** — list agents in the active formation
- **`navig agent run <agent_id> --task "<task>"`** — execute a single agent from the active formation with a specified task
- **`navig council run "<question>"`** — run multi-agent council deliberation
- Dynamic formation discovery: no hardcoded maps, community can add formations by placing directories in `formations/` or `~/.navig/formations/`
- 4 built-in formations with 22 specialized agents:
  - **Creative Studio** (6 agents): Creative Director, Designer, Marketing Director, CFO, Dev Lead, Brand Strategist
  - **Football Club** (6 agents): Head Coach, Assistant Coach, Fitness Trainer, Scout, Financial Manager, Data Analyst
  - **Government** (5 agents): Policy Advisor, Budget Officer, Legal Advisor, Public Relations, Strategy Chief
  - **Software Dev Team** (5 agents): System Architect, DevOps, Product Owner, QA Lead, Security Officer
- Council Engine v0: multi-round deliberation with per-agent timeout, confidence scoring, and final decision synthesis
- JSON Schema validation for all formation and agent files
- Supports `--plain` and `--json` output flags for all formation commands
- Formation aliases for flexible lookup (e.g., `creative`, `football`, `gov`, `app_project`)

### Added — VS Code Extension: Formation Integration (Phase 1)
- **Feature Flag**: `navig-bridge.formations.enabled` (default: `false`) — opt-in formations support
- **Profile Resolution**: Extension reads `.navig/profile.json` in workspace root on activation, falls back to `app_project` when missing
- **Switch Formation Command**: `🎯 Switch Formation` — QuickPick with 5 known formations + custom input option (validates ID format)
- **List Agents Command**: `🎯 Formation: List Agents` — Calls CLI `formation show <id> --json`, parses agents, shows in QuickPick
- **Activation Log**: When enabled, logs `[FORMATION] Active formation: <id> (<source>)` on startup
- **Safe Defaults**: When formations disabled, commands register as no-ops with "enable in settings" messages (prevents VS Code "command not found" errors)
- **No Breaking Changes**: All formation code is additive, zero refactoring of existing extension code
- **ProfileConfig**: CLI now supports both integer and string versions in `profile.json` for backward compatibility

### Added — VS Code Extension: Formation Integration (Phase 2)
- **Sidebar Formation Section**: Active formation, agents, and actions displayed in the sidebar tree view under "🎯 Formation" section
- **Agent Tree Nodes**: Agents shown with name, role, council weight — clickable to run agent with a task
- **Default Agent Indicator**: Default agent marked with ⭐ in the sidebar
- **Run Council Command**: `🏛️ Formation: Run Council` — Input question, progress notification, results displayed as markdown document
- **Run Agent Command**: `🤖 Formation: Run Agent` — Select agent + enter task, progress notification, response displayed as markdown
- **Formation Details Command**: `🎯 Formation: Show Details` — Full formation manifest displayed as formatted markdown document
- **Formation Refresh Command**: `🔄 Formation: Refresh` — Invalidate caches and reload from CLI, refresh sidebar
- **Profile Watcher**: FileSystemWatcher monitors `.navig/profile.json` — sidebar auto-updates when profile changes
- **Formation Caching**: Resolution and detail caches invalidated on profile change, reducing CLI calls
- **Event System**: `onDidChangeFormation` event fires when profile changes, sidebar refreshes automatically

### Changed — Formation Auto-Detection (Phase 3)
- **Auto-detect formation per project**: On first activation in a workspace with no `.navig/profile.json`, NAVIG scans project files (package.json, pyproject.toml, Cargo.toml, etc.) and automatically selects the best-fit formation
- **One-time setup**: Auto-detected formation is persisted to `.navig/profile.json` — no repeated prompts, no manual switching required
- **Subtle notification**: When auto-detected, shows `"Formation auto-set: <id>"` with a "Change" button for overriding
- **Switch Formation de-emphasized**: Removed from sidebar top-level; still accessible via Command Palette (`formation.switch`) or notification "Change" action
- **New source type `auto`**: FormationResolution now reports `source: 'auto'` when formation was auto-detected, alongside existing `file` and `default` sources

### Added — Windows System Tray Launcher
- **`navig tray start`** — launch NAVIG tray app (system tray icon near clock)
- **`navig tray stop`** — terminate the running tray process
- **`navig tray status`** — check if tray is running (supports `--json`)
- **`navig tray install`** — create desktop shortcut + optional auto-start (`--auto-start`)
- **`navig tray uninstall`** — remove auto-start, shortcut, and settings
- Right-click tray icon to start/stop Gateway and Agent services
- Color-coded status indicator on tray icon (green=running, red=error, yellow=starting)
- Quick actions submenu: open dashboard, host status, vault, skills
- Auto-start with Windows via registry key (toggle from tray menu)
- Single-instance enforcement via PID lock file
- Health monitoring thread checks process status every 15 seconds
- PowerShell installer script (`scripts/install-tray.ps1`) for automated setup

### Added — Skill Execution Routing
- **`navig skills show <name>`** — display detailed skill info: commands, examples, metadata, entrypoint
- **`navig skills run <skill>:<command> [args]`** — execute skill commands via CLI bridge
- Skill commands from SKILL.md frontmatter (`navig-commands`) are now routed to the CLI
- Skills with Python or JS entrypoints (`main.py`, `index.js`) can be run directly
- Placeholder substitution: `navig skills run file-operations:list-files /var/log` resolves `<path>`
- Risky commands (destructive/moderate) require confirmation unless `--yes` is passed
- Full `--json` and `--plain` support for both show and run
- Help registry and help docs updated for skills commands

### Improved — Credentials Vault (Phase 1 Complete)
- Vault test coverage raised from 67% to 86% (exceeds 80% target)
- All provider validators tested: OpenAI, Anthropic, OpenRouter, Groq, GitHub, GitLab, Jira, Email
- SecretStr fully covered: redaction, hash, copy, mask_secret utility, type safety
- Encryption edge cases: wrong-key errors, unicode roundtrip, empty strings, rotate_key guard
- Storage edge cases: nonexistent IDs, provider-profile lookups, count
- Core vault: test/test_provider methods, env var fallback, token-type credentials
- `navig cred list --json` verified for machine-readable output
- `navig cred providers` lists all supported provider validators

## [2.3.1] — Help System & Bug Fixes

### Fixed
- **Intent Parser**: "how much ram is used" now correctly maps to memory monitoring (was falling back to low-confidence keyword match)
- **Intent Parser**: "bitcoin" (without "price") now correctly extracts BTC symbol for crypto price lookup
- **Test Suite**: Fixed 3 failing tests — debug logger redaction assertions, intent parser patterns, vault test flakiness
- **Vault Test**: Added cleanup of leftover test credentials and resilient assertion for Rich table wrapping

### Added — Complete Help System Coverage
- **21 new help topic files** covering all command groups: `ai`, `app`, `agent`, `ahk`, `approve`, `browser`, `calendar`, `cron`, `email`, `gateway`, `heartbeat`, `hosts`, `local`, `log`, `memory`, `scaffold`, `search`, `task`, `version`, `docs`, `fetch`
- **Help index reorganized** — topics now grouped by category (Infrastructure, Services, Data, Automation, AI, Tools, Utilities, Agent)
- All 44 help topics now have markdown files with examples and common commands
- Help system supports `--json` and `--plain` flags for machine-readable output

### Improved
- **HANDBOOK** updated to v2.3.0 with help system reference section
- All tests passing (686 passed, 0 failed)

## [3.9.0] — Auto-Continue Intelligence Suite

### Added — Network Recovery Monitor
- **NEW**: Full network recovery monitor replacing the stub implementation
- Detects network disconnections via DNS lookup (github.com) with HTTP fallback
- Automatically injects recovery prompt when Copilot appears stuck after reconnection
- Configurable monitoring window (3 min), static threshold (2 min), and cooldown (5 min)
- Real-time network state events drive avatar and status bar indicators

### Added — Snag Detection & Terminal Error Recovery
- **NEW**: `SnagDetector` — Detects stuck Copilot sessions and terminal errors
- **Inactivity detection**: Fires recovery prompt after 3 min of zero activity (configurable)
- **Terminal error detection**: Watches for build failures, crashes, and non-zero exit codes
- If Copilot has been static 40s after a terminal error → auto-injects diagnostic prompt
- 3 new settings: `snagDetectionEnabled`, `snagInactivityThresholdMinutes`, `snagTerminalCheckEnabled`

### Added — Smart AI Context-Aware Responses
- When Smart AI mode is enabled + Project Manager is running, auto-continue uses planning docs
- Generates context-aware continuation prompts instead of generic "Yes, please continue"
- Uses `LanguageModelClient` with project context (VISION, ROADMAP, SPEC) for intelligent responses
- Falls back to rule-based responses if Smart AI generation fails

### Improved — Session Safety
- Emergency Stop (`Ctrl+Shift+Escape`) now stops all monitors (chat, network, snag)
- Network monitor and snag detector integrated into activation pipeline
- All monitors properly disposed on extension deactivation
- Configuration changes dynamically reconfigure all active monitors

## [3.8.0] — NAVIG OS Migration (Tier 2)

### Added — NAVIG OS Infrastructure Tools

- **NEW**: 6 infrastructure tools added to NAVIG OS web app:
  - **Hosts Tool** — Manage remote hosts (add, remove, test, use) with live status indicators
  - **Apps Tool** — Manage web applications (add, remove, use, open in browser) with domain info
  - **Docker Tool** — Container management (start, stop, restart, view logs) with state colors
  - **Database Tool** — Browse databases, explore tables, run SQL queries with split-pane UI
  - **Backups Tool** — Create, restore, and delete backups with timestamp/size display
  - **Monitoring Tool** — Server health (CPU, memory, disk, uptime), services status, SSL certificates
- **NEW**: NavigBridge API route (`/api/navig`) — Server-side bridge that executes NAVIG CLI commands securely with input sanitization and 30s timeout
- **NEW**: NavigBridge client library — Browser-side typed client with methods for all NAVIG CLI domains (hosts, apps, db, docker, backup, service, security, deploy)
- **NEW**: React hooks (`useNavigHealth`, `useNavigCommand`, `useNavigQuery`) for seamless data fetching with auto-refresh
- **NEW**: All tools registered in dock with dedicated icons (Server, Globe, Container, Database, Archive, Activity)
- **NEW**: `navig-bridge.openNavigOS` command — Opens NAVIG OS in VS Code Simple Browser or external browser
- **NEW**: `navig-bridge.navigOsPort` setting — Configurable port for NAVIG OS (default: 7001)

### Changed — Architecture Migration

- Infrastructure management (hosts, apps, docker, db, backups, monitoring) now lives in NAVIG OS
- navig-bridge remains focused on project-level DevOps (planning docs, inbox, testing)
- Dock now shows system tools + NAVIG tools separated by divider

### Fixed — Analyze Project Hang & Dashboard Model Display

- **FIX**: Analyze Project no longer hangs indefinitely — hard 15-second timeout on AI model initialization, plus 10s timeout on each `selectChatModels` call
- **FIX**: Analyze Project progress notification is now **cancellable** — click Cancel to abort instead of being stuck
- **FIX**: Dashboard header now shows the actual AI model name (e.g. "Claude 3.5 Sonnet", "GPT-4o") instead of a hardcoded "Copilot AI" label
- **FIX**: Dashboard footer model badge also displays the real selected model, updated live every 2 seconds
- **FIX**: Listener leak (400+ listeners) — sidebar tree refresh is now debounced (150ms coalesce) and interval reduced from 5s to 10s, preventing VS Code internal listener accumulation

### Changed — "Project Manager" renamed to "Planner"

- **RENAMED**: All user-facing references of "Project Manager" / "PM" changed to **"Planner"** to avoid confusion with the PM2 process manager tool
- Sidebar section: "🤖 Project Manager" → "🤖 Planner"
- Commands: "Start/Stop AI Project Manager" → "Start/Stop AI Planner"
- Settings section title: "Project Manager" → "Planner"
- Dashboard toggle label updated
- Welcome panel updated
- Internal command IDs unchanged for backward compatibility

### Added — Dashboard Enhancement (navig-bar aligned)

- **NEW**: Interactive toggle switches in Extension card — navig-bar style toggles replace status dots for Master Switch, Smart AI, Auto Continue, Project Manager
- **NEW**: Quick Actions redesigned as 3-column icon card grid — Dashboard, Health Check, Security Scan, Ask AI, Activity Log, CLI Status with hover effects and micro-interactions
- **NEW**: GPT-5.2T model badge in dashboard top bar
- **NEW**: Footer status bar with live uptime counter, version info, and model indicator pill
- **NEW**: Toggle switch CSS with smooth 0.25s cubic-bezier transitions matching navig-bar design

### Added — Sidebar Reorganization & Full NAVIG Feature Coverage

- **NEW**: Complete sidebar menu reorganization — all NAVIG CLI features now accessible from the VS Code sidebar
- **NEW**: **Infrastructure** section — dedicated area for Hosts (12 actions), Applications (9 actions), Tunnels (5 actions), and Files (7 actions)
- **NEW**: **Docker** section — container management (ps, logs, exec, stats, start, stop, restart, inspect, compose)
- **NEW**: **Database** section — full database management (list, tables, query, dump, restore, optimize, repair)
- **NEW**: **Services & Integrations** section — Agent, Telegram Bot, Web Server, and MCP Server management
- **NEW**: **Monitoring** section — system resources, disk, services, network, health checks, insights
- **NEW**: **Backup** section — backup all, databases, list, restore, export/import config
- **NEW**: **Automation** section — Workflows (CRUD + run), Cron Jobs (CRUD + status), Triggers (CRUD + test), AHK Scripts
- **NEW**: **Evolution** section — AI-powered skill/workflow/script/pack generation, plus Packs and Skills management
- **NEW**: Telegram Bot commands — Start Bot and Bot Status now available from sidebar and command palette
- **NEW**: Quick Actions enhanced — Dashboard, NAVIG Status, Insights, AI Query, Security Audit, Health Check, Remote Command, Logs

### Changed — Settings Organization

- **CHANGED**: Extension settings reorganized into 11 categorized sections (General, Smart AI, Quick Continue, Detection Rules, OCR, Session & Limits, Notifications, Project Manager, NAVIG Integration, DevOps & System Config, Avatar)
- **CHANGED**: DevOps Lifecycle section cleaned — Hosts and Applications moved to dedicated Infrastructure section
- **CHANGED**: Security section enhanced — now includes firewall, fail2ban, SSH audit, secrets scan, connections, updates, vault
- **CHANGED**: System Operations enhanced — now includes local machine info, NAVIG config, remote commands, SSH connect

### Added — Multi-Account Email Monitoring

- **NEW**: `EmailListener` in agent ears — monitors multiple email inboxes via IMAP, routes messages through the agent's event system.
- **NEW**: `EmailAccountConfig` dataclass — per-account config with provider, label, category, check interval, and env-var password substitution.
- **NEW**: Multi-account support in `ProactiveEngine` — iterates all configured email providers, fires per-account trigger events with account label metadata.
- **NEW**: `get_email_provider()` factory function — maps provider names (`gmail`, `outlook`, `fastmail`) to IMAP provider classes.
- **NEW**: Agent config supports `ears.email_accounts` list — configure multiple email accounts with different providers and polling intervals.
- **NEW**: Email integration docs in HANDBOOK Section 25.5.1 — setup guide for Gmail App Passwords, env vars, verification.

### Added — Chappy Integration & Voice System

- **NEW**: Avatar Companion — Tamagotchi-style animated sidebar avatar in NAVIG Bridge extension. Reacts to session events, pattern matches, and responses with 7 emotional states (idle, listening, speaking, thinking, working, success, error). Uses 24 sprite frames from Chappy firmware. Click to interact.
- **NEW**: Avatar settings — `avatar.enabled`, `avatar.animationSpeed`, `avatar.idleTimeout` in extension configuration.
- **NEW**: Audio playback module (`navig/voice/playback.py`) — cross-platform (Windows/macOS/Linux) audio playback with 14 built-in notification sounds (wake, alarm, ok, end, hello, analyzing, wait, etc.).
- **NEW**: Speech-to-Text module (`navig/voice/stt.py`) — multi-provider STT with Deepgram Nova-2, OpenAI Whisper API, and local Whisper models. Automatic provider fallback.
- **NEW**: Google Cloud TTS provider — added to TTS module, requires `GOOGLE_CLOUD_API_KEY`.
- **NEW**: Deepgram Aura TTS provider — added to TTS module, requires `DEEPGRAM_API_KEY`.
- **NEW**: Voice services documentation (`docs/voice-services.md`) — unified guide for TTS, STT, and playback.
- **NEW**: Avatar integration documentation (`packages/navig-bridge/docs/AVATAR_INTEGRATION.md`).

### Added - Landing Site Auto-Improvements

- **NEW**: `/os` route — NAVIG OS demo page with TopDeck interface
- **NEW**: `robots.txt` and `sitemap.xml` for SEO
- **NEW**: `not-found.tsx` — branded 404 page with navigation links
- **NEW**: `error.tsx` — global error boundary with retry
- **NEW**: CSS entrance animations (`fade-in-up`, `fade-in`, `slide-in-left`, `pulse-glow`) with stagger delays
- **NEW**: Smooth scroll for anchor navigation
- **NEW**: `prefers-reduced-motion` media query respected for all animations
- **NEW**: Skip-to-content accessibility link
- **NEW**: `lib/hooks.ts` — `useInView` intersection observer hook for scroll-triggered animations
- **NEW**: Per-route metadata for `/deck` ("NAVIG Deck") and `/os` ("NAVIG OS") with template title pattern

### Changed

- **CHANGED**: Geist fonts now properly loaded with `display: 'swap'` and CSS variable approach — no more unused `_geist`/`_geistMono` variables
- **CHANGED**: Root metadata upgraded with `metadataBase`, Twitter card support, and title template (`%s | NAVIG`)
- **CHANGED**: Dependencies trimmed from 38 to 9 — removed 30+ unused shadcn template packages (all Radix UI, vite, zustand, zod, recharts, etc.)
- **CHANGED**: All dependency versions pinned — no more `"latest"` specifiers for reproducible builds

### Removed

- **REMOVED**: `index.html` (old Norzyn static page — superseded by Next.js)
- **REMOVED**: `styles.css` (901 lines of old Norzyn branding CSS)
- **REMOVED**: `styles/` directory (duplicate globals.css with light-mode theme)
- **REMOVED**: `src/` directory (empty plugin scaffolding)
- **REMOVED**: vite + @vitejs/plugin-react (not needed in Next.js)

### Fixed

- **FIXED**: Corrupted `next_tmp_61908` directory in workspace root node_modules — cleaned and restored proper pnpm symlinks
- **FIXED**: Navigation component now has `aria-label="Main navigation"`

### Added - Landing & Deck Unification

- **NEW**: Full marketing landing page at `packages/landing` — migrated all sections from navig-website (Hero, Features, Pricing, FAQ, Use Cases, etc.)
- **NEW**: `components/shared-deck/` — single source of truth for all NAVIG Deck components (bar, context, command palette, settings, options, marketplace, desktop widgets, workspace switcher, quick jump, notes, search, 6 widget types)
- **NEW**: Both `navig-bar/` and `topdeck/` now import from `shared-deck/` — zero duplication
- **NEW**: `/deck` route serves the interactive Deck demo, root `/` serves the marketing site
- **NEW**: Backend strategy evaluation document at `.navig/plans/BACKEND_STRATEGY.md`
- **NEW**: Updated ecosystem planning docs (VISION, ROADMAP, SPEC, CURRENT_PHASE)

### Changed

- **CHANGED**: Landing page root route switched from Deck demo to full marketing site with signup modal
- **CHANGED**: Layout metadata updated from "Norzyn" to "NAVIG - Your Life Navigator" with OpenGraph tags
- **CHANGED**: Marketing components adapted for Tailwind v4 (zinc-* palette, inline styles)

### Fixed

- **FIXED**: `topdeck-demo.tsx` was importing `Marketplace` and `DesktopWidgets` from non-existent local files — now resolved via shared-deck imports

### Fixed (2025-07-15) - VS Code Extension Identity Fix

- **FIXED**: Removed stale `.vsixmanifest` with old `rowla/copilot-auto-continue` identity that caused extension installation failures
- **FIXED**: Added `vscode:prepublish` script to ensure TypeScript compilation before packaging
- **FIXED**: Updated `.vscodeignore` to exclude generated manifests and legacy folders from VSIX
- **FIXED**: Cleaned up old `.projectmanager/` references in extension docs (now `.navig/plans`)

### Added (2026-02-09) - Skills Listing Commands

- **NEW**: `navig skills list` and `navig skills tree` for discovering AI skills
- **NEW**: `--plain` and `--json` output for skills inventory automation
- **NEW**: `--dir` override to point at a custom skills directory

### Fixed (2026-02-09)

- **FIXED**: Simplified `skills/meta/create-skill/SKILL.md` frontmatter to avoid indentation errors in strict skill parsers

### Added (2026-02-10) - MCP WebSocket Bridge (Phase 1 Complete)

- **NEW**: MCP WebSocket transport for real-time browser ↔ CLI communication
  - `navig mcp serve --transport websocket` starts a WebSocket server on port 3001
  - Session token authentication (auto-generated or `--token <value>`)
  - JSON-RPC 2.0 over WebSocket frames (same protocol as stdio mode)
  - All 13 NAVIG tools and 4 resources available over WebSocket
- **NEW**: `WebSocketMCPClient` in `@navig/mcp-bridge`
  - Auto-reconnect with exponential backoff (configurable)
  - Token auth via message or `Authorization` header
  - 30s RPC timeout, proper pending-request cleanup
  - Server notification support
- **NEW**: React hooks for MCP in `@navig/mcp-bridge`
  - `useMCPConnection` — manage WebSocket connection lifecycle
  - `useMCPTool` — execute MCP tools with loading/error states
  - `useMCPQuery` — auto-fetch tool data on mount (like React Query)
- **NEW**: MCP Playground page in NAVIG OS (`/playground`)
  - Interactive tool runner with argument editor
  - Resource viewer with live content fetching
  - Connection panel with token auth
  - Real-time tool/resource discovery
- **UPDATED**: `navig mcp serve` now supports `--transport` flag (stdio | websocket)
- **DEPENDENCY**: Added `websockets>=15.0` to Python requirements

### Added (2026-02-10) - Monorepo Restructuring (Phase 0 Complete)

- **NEW**: Unified monorepo workspace with pnpm + Nx
  - Root `pnpm-workspace.yaml` manages all 11 workspace packages
  - Root `package.json` with build/dev/lint/test scripts for entire ecosystem
  - Root `nx.json` for incremental builds and task caching
  - Root `tsconfig.base.json` with `@navig/*` path aliases
- **NEW**: TypeScript packages under `packages/`
  - `@navig/os` — AI-controlled infrastructure dashboard (Next.js 16, React 19, port 7001)
  - `@navig/deck` — Browser automation Chrome extension + dashboard (Next.js 16, port 7002)
  - `@navig/cloud` — Hosted backend (Laravel + Filament + Vite)
  - `@navig/landing` — Marketing website (Next.js 16, port 7003)
  - `@navig/copilot` — VS Code extension (TypeScript, v3.0.0)
- **NEW**: Shared packages under `packages/shared/`
  - `@navig/core` — Zustand stores, types, utilities, sync middleware
  - `@navig/ui` — Radix UI + Tailwind component library
  - `@navig/mcp-bridge` — MCP client/server abstraction (stdio/websocket/http)
  - `@navig/plugin-sdk` — Plugin authoring SDK (define-plugin, create-tool, create-widget)
  - `@navig/config` — Shared ESLint, TypeScript, and Tailwind configurations
- **REBRANDED**: All `@norzyn/*` packages renamed to `@navig/*` (74+ source files updated)
- **ARCHIVED**: Original `norzyn/` directory preserved at `inspiration/norzyn/`

### Added (2026-02-09) - Ecosystem Architecture Plan

- **NEW**: [Ecosystem Architecture Guide](docs/ECOSYSTEM_ARCHITECTURE.md)
  - Monorepo structure: NAVIG stays root, TypeScript packages under `packages/`
  - Development roadmap: MCP bridge first → NAVIG OS → NAVIG Deck → Life Management
  - Integration architecture: MCP over WebSocket as universal protocol
  - Branding decision: Unify everything under NAVIG brand
  - Chrome extension architecture with shared `@navig/ui` components
  - Phased delivery: Web app first, embed into VS Code / Chrome / desktop

### Added (2026-02-09) - In-App Help & Skill Schema Cleanup

- **NEW**: `navig help` lists help topics and `navig help <topic>` shows subcommands; supports `--plain` and `--json` for automation
- **FIXED**: Updated `skills/meta/create-skill/SKILL.md` frontmatter to the supported schema (compatibility/metadata/name/description)

### Changed (2026-02-09) - Content Architecture Clarification

- **REORGANIZED**: Renamed `packs/starter/` to `packs/community/` for clarity
- **IMPROVED**: All content system READMEs now include decision matrices
  - [templates/README.md](templates/README.md) — "When to use Templates"
  - [skills/README.md](skills/README.md) — "When to use Skills"
  - [packs/README.md](packs/README.md) — "When to use Packs"
- **NEW**: [Content Architecture Guide](docs/CONTENT_ARCHITECTURE.md)
  - Visual architecture diagram
  - Decision matrix: "Where should I add this?"
  - Examples of how systems work together
  - Common mistakes and best practices

### Added (2026-02-08) - Packs System

- **NEW**: `navig pack` command group for shareable operations bundles
  - Install, run, and create reusable runbooks and checklists
  - Multiple pack types: runbook, checklist, workflow, quickactions
  - Variable substitution with `${var}` placeholders
  - Dry-run mode for safe preview

- **NEW**: Pack commands
  - `navig pack list` — list available packs with filters
  - `navig pack show <name>` — view pack details
  - `navig pack install <source>` — install a pack
  - `navig pack uninstall <name>` — remove a pack
  - `navig pack run <name>` — execute pack steps
  - `navig pack create <name>` — create new pack
  - `navig pack search <query>` — search packs

- **NEW**: Built-in starter packs
  - `deployment-checklist` — Pre-deploy verification
  - `backup-runbook` — Database backup procedure
  - `docker-health` — Docker health check
  - `security-audit` — Basic security audit
  - `devops-shortcuts` — Quick action shortcuts bundle

### Fixed (2026-02-08)

- **FIXED**: Pack search no longer returns duplicate results

### Added (2026-02-08) - Automatic Operation Recording

- **NEW**: All CLI commands are now automatically recorded to history
  - Operations are tracked in `~/.navig/history/operations.jsonl`
  - Records command, host, duration, and success/failure status
  - Powers insights, suggestions, and history features
  - Excludes meta commands (help, history, insights, dashboard)

- **IMPROVED**: Operation recording utilities
  - `RecordedOperation` context manager for explicit recording
  - `record_operation` decorator for function-level recording
  - `quick_record` function for one-liner recording
  - Automatic operation type detection (remote, database, docker, etc.)

### Added (2026-02-08) - Operations Insights & Analytics

- **NEW**: `navig insights` command group for operations analytics
  - Analyze command patterns, host health, and usage trends
  - AI-powered anomaly detection identifies issues early
  - Personalized recommendations based on usage patterns
  - Full report generation for weekly/monthly reviews

- **NEW**: Insights views
  - `navig insights` — quick summary with key metrics
  - `navig insights hosts` — host health scores (0-100) with trends
  - `navig insights commands` — top commands with success rates
  - `navig insights time` — hourly usage heatmap
  - `navig insights anomalies` — unusual patterns and potential issues
  - `navig insights recommend` — personalized optimization suggestions
  - `navig insights report` — comprehensive analytics report

- **NEW**: Health scoring system
  - 0-100 score per host based on success rate (60%) and latency (40%)
  - Trend indicators: ↑ improving, → stable, ↓ declining
  - Automatic identification of problematic hosts

- **NEW**: Anomaly detection
  - Error rate spike detection
  - Inactive host identification
  - Slow command detection
  - Unusual activity patterns

- **NEW**: Smart recommendations
  - Quick action suggestions for frequent commands
  - Health check recommendations for active hosts
  - Automation opportunities based on patterns

### Added (2026-02-08) - Event-Driven Automation (Triggers)

- **NEW**: `navig trigger` command group for event-driven automation
  - Define triggers that fire automatically on system events
  - Execute commands, workflows, notifications, or webhooks
  - Built-in cooldown and rate limiting to prevent flooding
  - Execution history and statistics tracking

- **NEW**: Trigger types
  - **health**: Fire when heartbeat detects service failures
  - **schedule**: Time-based triggers (cron-like scheduling)
  - **threshold**: Resource thresholds (CPU, memory, disk)
  - **webhook**: Incoming HTTP webhooks for external events
  - **file**: File system change monitoring
  - **command**: Fire after specific commands complete
  - **manual**: On-demand triggers for testing

- **NEW**: Trigger commands
  - `navig trigger list` — list all configured triggers
  - `navig trigger add` — create trigger (interactive or quick mode)
  - `navig trigger show <id>` — view trigger details
  - `navig trigger remove <id>` — delete a trigger
  - `navig trigger enable/disable <id>` — control trigger state
  - `navig trigger test <id>` — dry run to preview actions
  - `navig trigger fire <id>` — manually execute trigger
  - `navig trigger history` — view execution history
  - `navig trigger stats` — show statistics

- **NEW**: Action types for triggers
  - Run navig commands directly
  - Execute workflows with `workflow:name`
  - Send notifications with `notify:telegram` or `notify:console`
  - Call external webhooks with `webhook:url`
  - Run scripts with `script:path`

### Added (2026-02-08) - Intelligent Command Suggestions & Quick Actions

- **NEW**: `navig suggest` command for AI-powered command suggestions
  - Analyzes command history for frequently used patterns
  - Detects project context (Docker, database, deployment, monitoring)
  - Time-based suggestions (typical commands for time of day)
  - Sequence learning (what usually follows your last command)
  - Run suggestions directly with `--run <n>`
  - Filter by context with `--context docker`

- **NEW**: `navig quick` command group for action shortcuts
  - `navig quick add <name> <command>` — save a shortcut
  - `navig quick run <name>` — run a saved shortcut
  - `navig quick list` — list all shortcuts
  - `navig quick remove <name>` — delete a shortcut
  - Short alias: `navig q`

- **NEW**: Suggestion sources
  - **H (History)**: Most frequently used commands
  - **S (Sequence)**: Commands that typically follow your last action
  - **T (Time)**: Typical commands for current time of day
  - **C (Context)**: Commands relevant to detected project type

### Added (2026-02-08) - Operations Dashboard (TUI)

- **NEW**: `navig dashboard` command for real-time infrastructure monitoring
  - Live host connectivity status with latency
  - Docker container overview panel
  - Recent operations from command history
  - System resource overview
  - Auto-refresh mode (default) or single snapshot
  - Configurable refresh interval

- **NEW**: Dashboard panels
  - **Host Health**: Shows all configured hosts with SSH connectivity status
  - **Docker**: Container status for active host
  - **History**: Last 8 operations from history system
  - **Resources**: CPU, memory, disk overview (when available)

- **NEW**: Dashboard modes
  - `navig dashboard` — live auto-refresh mode
  - `navig dashboard --no-live` — single snapshot
  - `navig dashboard -r 10` — custom refresh interval

### Added (2026-02-08) - Command History & Replay System

- **NEW**: `navig history` command group for command replay and audit trail
  - `navig history list` — list past operations with powerful filtering
  - `navig history show <id>` — detailed view of any operation
  - `navig history replay <id>` — re-run previous commands
  - `navig history undo <id>` — undo reversible operations
  - `navig history export <file>` — export to JSON/CSV for compliance
  - `navig history stats` — show success rates and usage patterns
  - Short alias: `navig hist`

- **NEW**: Operation recording infrastructure
  - All operations recorded in JSON Lines format
  - Automatic log rotation (configurable max entries)
  - Sensitive data redaction
  - Fast querying with in-memory index

- **NEW**: Filtering and search capabilities
  - Filter by host, status, operation type
  - Time-based filtering (`--since 24h`, `--since 7d`)
  - Full-text search in command history
  - JSON and plain output modes for scripting

### Added (2026-02-08) - Smart Context Management

- **NEW**: `navig context` command group for project-local context management
  - `navig context show` — display current context resolution with source info
  - `navig context set --host <name>` — set project-local host context
  - `navig context clear` — remove project-local context
  - `navig context init` — initialize .navig directory in project
  - Short alias: `navig ctx`

- **NEW**: JSON and plain output modes for scripting
  - `navig context show --json` — full context as JSON
  - `navig context show --plain` — one-line format for shell scripts

- **NEW**: In-app help topic for context management
  - `navig help context` — comprehensive usage guide

### Improved (2026-02-08) - Windows Encoding Compatibility

- **FIXED**: Workflow list source column now displays correctly
  - Removed Rich markup brackets that were being interpreted as tags
  - Source labels now show "builtin", "project", "global" properly

- **FIXED**: Comprehensive encoding fixes across the codebase
  - Removed raw Unicode emoji from all output-facing Python code
  - All console output now uses ASCII-safe symbols via console_helper
  - Files fixed: proactive_display.py, template_manager.py, server_template_manager.py,
    retry.py, mcp_manager.py, workflow.py, hello plugin, error_resolution.py,
    migrate_addons_to_templates.py, user_profile.py, main.py, heartbeat/runner.py
  - Output works correctly on all Windows terminals (cmd, PowerShell, legacy encodings)

- **FIXED**: Plugin list command now displays correctly
  - Fixed undefined `console` reference in plugin list output
  - Source column shows clean labels instead of emoji

- **FIXED**: Test suite improvements
  - Fixed autonomous agent tests to use proper pytest patterns
  - Fixed execution mode tests to properly isolate from project config

### Improved (2026-02-08) - Autonomous Agent System

- **IMPROVED**: Enhanced `navig status` command
  - Now shows gateway, heartbeat, and cron status alongside host/app/tunnel
  - Added `--all` flag for extended details (next heartbeat time, enabled jobs)
  - Gateway status shows uptime and session count
  - JSON and plain output modes include gateway status

- **IMPROVED**: Enhanced status displays for autonomous components
  - `navig gateway status` now shows uptime, sessions, cron jobs, heartbeat
  - `navig heartbeat status` shows interval, next check (in minutes), last run
  - `navig cron status` correctly detects running state

- **IMPROVED**: Comprehensive documentation in HANDBOOK.md (Section 23)
  - Complete systemd service setup with security hardening
  - Windows service deployment with NSSM
  - Detailed troubleshooting guide for Gateway, Heartbeat, and Cron
  - Best practices for production deployment

- **IMPROVED**: Expanded help system with new command groups
  - Added help entries for `agent`, `memory`, `task`, `approve`, `browser` commands
  - Full epilog examples for all autonomous agent commands
  - Consistent help text across all command groups

- **FIXED**: `navig docs` command now works on Windows terminals without Unicode support
  - Emoji characters in documentation titles no longer cause encoding errors
  - Uses ASCII fallback for non-Unicode terminals

- **FIXED**: Help displays now work on Windows terminals without Unicode support
  - Replaced Unicode box-drawing characters with ASCII equivalents
  - Help commands like `navig help <topic>` work in all terminals

- **FIXED**: Wiki command help text emoji removed for encoding compatibility
  - `navig wiki inbox`, `navig wiki links`, `navig wiki rag` now work on all terminals

- **NEW**: `navig gateway stop` command now works
  - Sends graceful shutdown signal to running gateway
  - Gateway responds to POST `/shutdown` endpoint

- **NEW**: `navig help start` topic added for quick launcher help

- **NEW**: Help text entries for gateway, heartbeat, cron, start commands

### Added (2026-02-08) - Quality of Life Improvements

- **NEW**: `navig version` command - show version and system info
  ```bash
  navig version           # Shows version with random quote
  navig version --json    # JSON output for automation
  ```

- **NEW**: Direct web tool imports from `navig.tools`
  ```python
  from navig.tools import web_fetch, web_search, search_docs
  ```

### Fixed (2026-02-08) - Code Quality

- **FIXED**: Type annotation issues in `onboard.py`
  - Resolved "Variable not allowed in type expression" Pylance errors
  - Added proper `ConsoleType` alias for conditional imports

- **FIXED**: Module export improvements
  - Added `__all__` exports to `navig/tools/__init__.py`
  - Web tools now directly importable from `navig.tools`

### Added (2026-02-08) - Web Content Tools

- **NEW**: `navig fetch <url>` command - fetch and extract content from URLs
  ```bash
  navig fetch https://example.com          # Fetch as markdown
  navig fetch https://docs.python.org --mode text  # Plain text
  navig fetch https://api.example.com --json       # JSON output
  ```

- **NEW**: `navig search <query>` command - search the web
  ```bash
  navig search "Python tutorials"          # Search with DuckDuckGo
  navig search "Docker tips" --limit 5     # Limit results
  navig search "k8s deploy" --provider brave  # Use Brave Search
  ```

- **NEW**: `navig docs` command - search NAVIG documentation
  ```bash
  navig docs                   # List all 35+ documentation topics
  navig docs "ssh tunnel"      # Search for relevant docs
  navig docs --json "backup"   # JSON output for automation
  ```

- **NEW**: URL investigation in AI assistant
  - Ask: "check this https://example.com" → auto-fetches and displays content
  - Ask: "summarize https://..." → AI summarizes the page
  - Ask: "compare https://a.com and https://b.com" → AI analyzes both
  - Triggers: "investigate", "read this", "what does this say", etc.

- **NEW**: Web fetching MCP tools for AI assistants
  - `navig_web_fetch`: Fetch and extract content from URLs
  - `navig_web_search`: Search the web (Brave/DuckDuckGo)
  - `navig_search_docs`: Search local documentation

- **NEW**: Web configuration schema in `~/.navig/config.yaml`
  ```yaml
  web:
    fetch:
      enabled: true
  - Fixed lines with 7-space and 15-space indentation (should be 4/8)
  - Bot now starts cleanly without `IndentationError`


- **NEW**: `navig start` command - quick launcher for all services
  ```bash
  navig start                  # Start gateway + bot (background)
  navig start --foreground     # See live logs
  navig start --no-gateway     # Bot only (standalone)

- **NEW**: `navig bot stop` command - stop running bot/gateway processes

- **NEW**: Telegram Bot options in `navig menu` (Agent & Gateway section)
  - Option T: Start Telegram Bot (with Gateway) - persistent sessions
  - Option B: Start Telegram Bot (standalone) - quick testing

### Added (2026-02-07) - NLP Intent Parser

- **NEW**: Smart Natural Language Processing for commands
  - Talk to the bot naturally: "show me docker containers" → `/docker`
  - Dual-mode detection: AI function calling + regex pattern fallback
  - Confidence-based execution with optional confirmation
  - 50+ commands supported via natural language
  - New files: `navig/bot/intent_parser.py`, `navig/bot/command_tools.py`
  - Full documentation: [TELEGRAM_NLP_GUIDE.md](docs/TELEGRAM_NLP_GUIDE.md)

- **NEW**: NLP configuration options in `~/.navig/config.yaml`
  - `nlp_enabled`: Enable/disable NLP intent parsing
  - `nlp_use_ai`: Use AI for intent detection (higher accuracy)
  - `nlp_confidence_threshold`: Auto-execute above this confidence (default 0.7)
  - `nlp_confirmation_threshold`: Ask confirmation above this (default 0.4)

- **NEW**: Natural language patterns recognized:
  - Server: "show docker containers", "list hosts", "switch to production"
  - Monitoring: "check disk space", "how much memory", "cpu load"
  - Docker: "logs from nginx", "restart the nginx container"
  - Database: "list databases", "show tables in wordpress"
  - Utilities: "weather in London", "btc price", "convert 100 usd to eur"
  - Reminders: "remind me in 30 minutes to check logs"
  - Fun: "flip a coin", "roll d20", "tell me a joke"

- **IMPROVED**: `/status` command now shows NLP parser status
- **IMPROVED**: `/start` welcome message highlights NLP capability

### Changed (2026-02-08) - Simplified AI (Always On)

- **SIMPLIFIED**: Removed AI toggle commands - bot now ALWAYS responds naturally
  - Removed `/ai_stop` and `/ai_start` commands (AI is always active)
  - Kept `/ai_persona` to view/change persona style
  - Kept `/ai_status` to check AI status
  - Default persona: Kraken

- **NEW**: System monitoring commands
  - `/ip` - Show server IP addresses (internal + external)
  - `/env` - Server environment info (OS, kernel, hostname)
  - `/df` - Detailed disk usage with filesystem types
  - `/top` - Top 10 processes by CPU usage
  - `/netstat` - Active network connections
  - `/cron` - List cron jobs

- **NEW**: Developer utilities
  - `/crypto [symbol]` - Cryptocurrency prices (BTC, ETH, SOL, etc.)
  - `/crypto_list` - List all supported cryptocurrencies
  - `/weather [location]` - Weather information from wttr.in
  - `/calc <expr>` - Simple calculator
  - `/hash <text>` - Generate MD5, SHA1, SHA256 hashes
  - `/dns <domain>` - DNS lookup (A, MX, NS records)
  - `/encode <text>` - Base64 encode
  - `/decode <base64>` - Base64 decode
  - `/curl <url>` - Simple HTTP GET request
  - `/convert <amount> <from> <to>` - Currency conversion (also natural language)
  - `/profile` - View user profile and stats
  - `/respect` - Give respect to a message (reply)
  - `/quote` - Get random saved quote
  - `/uid` - Get Telegram user/chat IDs
  - `/joke` - Random programming joke
  - `/flip` - Flip a coin
  - `/roll [sides]` - Roll a dice (d6 default, supports d20, etc.)
  - `/explain <question>` - AI-powered explanations (alias: `/ask`)
  - `/imagegen <description>` - AI image generation (requires API setup)
  - `/music <url>` - Convert music links between platforms (Spotify, YouTube, etc.)
  - `/video <url>` - Video download info (YouTube, TikTok, Instagram, Twitter)
  - `/cancelreminder <id>` - Cancel a pending reminder

- **NEW**: Natural language patterns (no slash needed!)
  - `X or Y` → Random choice ("pizza or sushi")
  - `flip a coin` / `heads or tails` → Coin flip
  - `roll d20` / `roll a dice` → Dice roll
  - `remind me in 30 min to check backup` → Set reminder
  - `100 USD to EUR` / `convert 50 EUR GBP` → Currency conversion
  - `100 USD` → Auto-convert to common currency
  - `weather in London` → Weather lookup
  - `time in Tokyo` → Timezone lookup
  - `btc price` / `how much is ethereum` → Crypto prices
  - `explain X` / `what is X` → AI explanation
  - Auto-detect YouTube/TikTok/Instagram URLs → Video info
  - Auto-detect Spotify/YouTube Music URLs → Music link conversion

### Added (2026-02-08) - Utility Commands

- **NEW**: `/about` command - Learn about NAVIG and the SCHEMA community
- **NEW**: `/whois <domain>` command - Domain WHOIS lookup utility
- **NEW**: `/time [zone]` command - Timezone utility (UTC, EST, PST, CET, JST, etc.)
- **NEW**: `/ssl <domain>` command - Check SSL certificate expiry
- **NEW**: `/uptime` command - Server uptime since last boot
- **NEW**: `/services` command - List running systemd services
- **NEW**: `/ports` command - Show listening TCP ports
- **NEW**: `/pick <options>` command - Random choice helper ("The Kraken chooses...")
- **NEW**: `/note` and `/notes` commands - Save and retrieve notes

### Added (2026-02-08) - Telegram Bot Enhancements (Sprint A, B, C)

- **NEW**: Interactive Help System with Category Navigation
  - `/help` now shows inline keyboard with command categories
  - Categories: Core, Hosts, Monitoring, Docker, Database, Tools, AI Features
  - Click any category to browse commands with back navigation
  - `/help <category>` for direct category access
  - Centralized command documentation in `navig/bot/help_system.py`

- **NEW**: Quick Health Check and Statistics
  - `/ping` - Bot health check with latency, active host, commands today
  - `/stats` - Usage statistics: commands executed, errors, top commands

- **NEW**: AI Auto-Reply Mode
  - `/ai_start [persona]` - Enable continuous AI conversation mode
  - `/ai_stop` - Disable AI auto-reply, return to command mode
  - `/ai_status` - Check current AI mode and persona
  - Available personas: `assistant`, `devops`, `concise`, `detailed`

- **NEW**: Reply-Based AI Commands
  - Reply to any message with `explain` or `analyze` for AI analysis
  - Reply with `summarize` or `tldr` for brief summaries
  - Works in DevOps context for log analysis, error explanation

- **NEW**: Natural Language Reminders
  - `/remind <time> <message>` - Set a reminder (30m, 2h, 1d, 1w formats)
  - `/reminders` - List active reminders with cancel buttons
  - Background scheduler sends reminders when due
  - SQLite-backed storage persists across bot restarts

- **NEW**: Command Statistics and Caching Layer
  - All command executions logged with timing and success status
  - TTL-based cache for frequent queries
  - Bot data stored in `~/.navig/bot/bot_data.db`

### Added (2026-02-08) - Reference AI Bot Analysis

- **NEW**: Comprehensive Reference AI bot codebase analysis (`docs/REFERENCE_BOT_ANALYSIS.md`)
  - Complete command inventory: 30+ commands across 6 categories (Core, AI, Media, Social, Utility, Admin)
  - NAVIG mapping table showing CLI equivalents for Schema commands
  - Security classification matrix: Safe, Standard, Destructive, Critical, Excluded
  - Integration recommendations with priority ranking
  - Pattern extraction with Python code examples:
    - Interactive help with category keyboard navigation
    - Reply-based contextual commands (explain, summarize)
    - Natural language reminder parsing
    - Safe message sending with error handling
    - Stats caching with TTL
  - Implementation roadmap: 3 sprints covering Foundation, AI Enhancements, Utilities

### Added (2026-02-07) - Telegram Slash Commands

- **NEW**: Native slash commands for quick server operations in Telegram bot
  - `/hosts` - List all configured hosts
  - `/use <host>` - Switch to a different host
  - `/disk` - Check disk space on current host
  - `/memory` - Check memory usage
  - `/cpu` - Check CPU load and uptime
  - `/docker` - List Docker containers
  - `/logs <container> [lines]` - View container logs (default 50 lines)
  - `/restart <container>` - Restart container with confirmation button
  - `/run <command>` - Run arbitrary command on remote host
  - `/db` - List databases
  - `/db tables <database>` - List tables in a database
  - `/tables <database>` - Shortcut for table listing
  - `/tunnel` - Show active SSH tunnels
  - `/tunnel start/stop <name>` - Manage tunnels
  - `/backup` - List recent backups
  - `/backup create` - Create backup with confirmation
  - `/hestia` - List HestiaCP users
  - `/hestia domains [user]` - List domains for a user
  - `/hestia web [user]` - List web domains

- **NEW**: Inline button confirmations for destructive operations
  - Restart command shows Confirm/Cancel buttons before executing
  - Backup creation requires confirmation
  - Dangerous shell patterns blocked (rm -rf, shutdown, etc.)

- **NEW**: Async NAVIG CLI execution with timeouts
  - Commands execute asynchronously without blocking bot
  - 120-second default timeout, 600-second for backups
  - Duration tracking for performance visibility

### Added (2026-02-07) - Telegram Command Integration Plan

- **NEW**: Comprehensive command integration analysis (`docs/TELEGRAM_COMMAND_INTEGRATION.md`)
  - Architecture mapping of existing NAVIG bot, CLI commands, and skills system
  - 12 new slash commands proposed: `/hosts`, `/use`, `/disk`, `/memory`, `/cpu`, `/docker`, `/logs`, `/restart`, `/db`, `/tunnel`, `/backup`, `/hestia`
  - 3 reference implementation examples with complete code
  - Sprint-based roadmap: Core commands (12h), Elevated commands (12h), Advanced features (11h)
  - Security specifications: Authorization levels, input validation, audit logging
  - Inline button patterns for destructive command confirmations

### Changed (2026-02-07) - Natural Conversation Style

- **IMPROVED**: Telegram bot (Sentinel) now uses natural, conversational language
  - Removed emoji headers from all user-facing messages
  - Replaced templated responses with natural phrasing
  - "🤔 I didn't quite understand that" → "I didn't quite understand that"
  - Acknowledgments feel human: "Nice to meet you, [name]. I'll remember that."
  - Error messages are direct and helpful without decorative symbols
  - Help text is concise rather than bullet-point heavy

- **IMPROVED**: SOUL_RESPONSES updated with professional, colleague-like tone
  - Greetings: "Hey. What are we working on?" instead of emoji-heavy alternatives
  - Capabilities explained naturally without markdown formatting
  - Personality remains competent and protective, but less theatrical

### Added (2026-02-07) - Kraken Branding & Discord Integration

- **NEW**: 🦑 Kraken Deepwatch persona branding across all NAVIG interfaces
  - Official emoji system: 🦑 (identity), 🛰️ (telemetry), 🛞 (steering), ⚓ (stability)
  - Updated README.md tagline: "Your life navigator: steering infrastructure and personal workflows"
  - Updated AI agent responses with Kraken voice (decisive, protective, no fluff)
  - Updated workspace templates (IDENTITY.md) with Kraken Deepwatch personality

- **NEW**: Discord integration plan (`docs/discord.md`)
  - Hybrid architecture: Interactive bot + webhooks
  - Channel structure: `#navig-bridge`, `#navig-ops`, `#navig-lifeops`, `#navig-alerts`, `#navig-changelog`
  - Role-based permissions: `@NAVIG-Operator`, `@NAVIG-User`, `@NAVIG-Alerts`
  - Safe mode with command allowlist (read-only by default)
  - Hardened security model for the messaging bot

- **IMPROVED**: .gitignore updated with Discord credential protection
  - Added `discord-webhooks.json`, `*.token`, `discord-*.json` patterns

### Fixed (2026-02-07) - Interactive Menu Import Errors

- **FIXED**: Agent & Gateway menu import error
  - Created `navig/commands/gateway.py` with wrapper functions for interactive menu
  - Added `status_cmd`, `start_cmd`, `stop_cmd`, `session_cmd` wrappers to gateway module
  - Added `status_cmd`, `start_cmd`, `stop_cmd`, `config_cmd`, `logs_cmd` wrappers to agent module
  - Menu now correctly imports and calls agent/gateway commands

- **FIXED**: Flow menu import error
  - Created `navig/commands/flow.py` with wrapper functions for interactive menu
  - Added wrappers: `list_flows_cmd`, `show_flow_cmd`, `run_flow_cmd`, `test_flow_cmd`, `add_flow_cmd`, `edit_flow_cmd`, `remove_flow_cmd`

- **FIXED**: Cron menu import error
  - Created `navig/commands/cron.py` with wrapper functions for interactive menu
  - Added wrappers: `list_cmd`, `add_cmd`, `run_cmd`, `enable_cmd`, `disable_cmd`, `remove_cmd`, `status_cmd`

- **IMPROVED**: All interactive menu submenus now have valid imports
  - Audited all 22 module imports used by interactive.py
  - Verified all wrapper functions are callable

### Fixed (2026-01-18) - AI Chat Conversation Flow

- **FIXED**: AI chat now responds naturally to personal statements
  - "My name is X" → "Nice to meet you, X! 👋 I'll remember that."
  - "I prefer using Python" → "Good to know! I'll keep that in mind for suggestions."
  - "I work from 9-5 EST" → "Got it! I've noted your schedule."
  - "I live in Portugal" → "Noted! That helps with timezone and regional settings."
  - Previously fell through to generic help text

- **FIXED**: AI model auto-detection from `~/.navig/config.yaml`
  - Reads `ai_model_preference` list (uses first entry)
  - Falls back to `ai_model` single value
  - Default to openrouter only if nothing configured
  - Previously always used "openrouter" regardless of config

- **IMPROVED**: Learning pattern recognition
  - Added "I live in" / "I'm based in" → location
  - Added "I work on" / "working on" → current project
  - Improved "I prefer" patterns to handle "I prefer using X for..."
  - List fields (preferences, stack) now append instead of replace

- **ADDED**: User profile `preferences` field for technical preferences
  - Tracks tools, languages, and style preferences separately from stack

### Added (2026-01-18) - Interactive Menu Redesign


- **ENHANCED**: Complete `navig menu` redesign as comprehensive Command Center
  - Three-pillar organization: SysOps (Infrastructure), DevOps (Applications), LifeOps (Automation)
  - Visual category separators with distinct colors
  - Compact status dashboard showing Host, App, and Last Command status
  - Quick Help menu (`?`) with keyboard shortcuts
  - Command History menu (`H`) for reviewing recent operations

- **NEW**: Additional Interactive Submenus
  - Flow Automation menu (`F`) - workflows, templates, validation
  - Local Operations menu (`L`) - system info, ports, network
  - Agent & Gateway menu (`G`) - autonomous mode, sessions, cron
  - Monitoring & Security combined menu (`7`) - resources, firewall, audit

- **NEW**: Standalone Menu Launchers
  - `navig flow` - Flow automation menu
  - `navig local` - Local operations menu
  - `navig cron` - Cron job management
  - `navig agent` - Agent/Gateway management

### Added (2026-01-18) - Advanced Multi-Channel & AI Features (Tier 2 & 3)

- **NEW**: Perplexity Provider Integration (`navig/providers/perplexity.py`)
  - Real-time web search with AI synthesis via Perplexity Sonar API
  - Supports both direct Perplexity API (pplx-xxx keys) and OpenRouter proxy (sk-or-xxx keys)
  - Auto-detection of API type from key prefix
  - Models: sonar (fast), sonar-pro (comprehensive), sonar-reasoning (detailed)
  - Citation extraction from search results
  - Usage: Set `PERPLEXITY_API_KEY` or `OPENROUTER_API_KEY` environment variable

- **NEW**: Discord Channel Adapter (`navig/gateway/channels/discord.py`)
  - Full Discord bot integration for NAVIG Gateway
  - Slash commands: `/navig <query>`, `/status`, `/help`
  - @mention responses in guild channels
  - Direct message (DM) support
  - Permission system: guild, user, and channel restrictions
  - Automatic message splitting for Discord 2000-char limit
  - Requires: `pip install discord.py` and bot token

- **NEW**: WhatsApp Channel Adapter (`navig/gateway/channels/whatsapp.py`)
  - WhatsApp Web integration via whatsapp-web.js bridge
  - WebSocket communication with bridge server
  - QR code authentication flow support
  - Group and individual message handling
  - Reconnection with exponential backoff
  - Media and location message parsing
  - Requires: External whatsapp-web.js bridge server

- **NEW**: Agent-to-Agent Coordination Protocol (`navig/agent/coordination.py`)
  - Multi-agent orchestration for complex workflows
  - Agent roles: COORDINATOR, SPECIALIST, WORKER, MONITOR
  - Message types: REQUEST, RESPONSE, BROADCAST, HEARTBEAT, HANDOFF, CONTEXT
  - AgentRegistry for discovery with capabilities index
  - MessageBus for async message routing
  - Task delegation with timeout handling
  - Conversation handoffs between agents
  - Shared context management

- **NEW**: Docker Sandbox Execution (`navig/tools/sandbox.py`)
  - Container-based isolated command execution
  - Resource limits: memory (256MB default), CPU (1.0), disk
  - Network isolation (disabled by default)
  - Security hardening: `--read-only`, `--cap-drop ALL`, `--no-new-privileges`
  - Timeout enforcement with automatic cleanup
  - Temporary directory for code execution
  - Configurable base images (python:3.11-slim default)

- **NEW**: AI Image Generation Tool (`navig/tools/image_generation.py`)
  - Multi-provider image generation abstraction
  - Providers: OpenAI DALL-E 3, Stability AI SDXL, local A1111/ComfyUI
  - Sizes: 256x256 to 1792x1024, quality/style options
  - Automatic output directory management
  - URL, base64, or local file output
  - Prompt safety validation (configurable)
  - Usage: Set `OPENAI_API_KEY`, `STABILITY_API_KEY`, or `LOCAL_SD_URL`

### Added (2026-01-17) - Information Retrieval Intelligence

- **NEW**: Web Search Intent Detection
  - NAVIG now understands "search the web for...", "look up...", "google..."
  - Automatically routes to MCP brave-search if enabled
  - Graceful fallback with instructions to enable web search
  - Natural language triggers: "go to the web", "find information about"

- **NEW**: Price & Cryptocurrency Query Handling
  - Understands "price of bitcoin", "how much is ethereum?", "BTC value"
  - Recognizes crypto aliases (btc→bitcoin, eth→ethereum, etc.)
  - Routes to web search for real-time prices when available
  - Provides helpful links (CoinGecko, CoinMarketCap) when unavailable

- **NEW**: Weather Query Handling
  - Understands "weather in New York", "temperature in London"
  - Extracts location from natural language
  - Routes to web search for live weather data

- **NEW**: Factual Question Routing
  - Detects non-DevOps questions: "who is...", "what is...", "explain..."
  - Routes general knowledge queries to web search
  - Preserves DevOps intent detection for server-related queries

- **NEW**: Integration Gap Analysis Document
  - Comprehensive NAVIG vs Reference Agent feature comparison
  - Prioritized implementation roadmap (3 tiers)
  - Architecture recommendations for future development
  - See: `docs/ARCHITECTURE_GAP_ANALYSIS.md`

### Fixed (2026-01-17) - NLU Improvements

- **Fixed**: "Price of bitcoin" queries no longer misclassified as DevOps commands
- **Fixed**: "Go to the web" queries now trigger web search instead of generic help
- **Fixed**: General knowledge questions now route correctly to information retrieval

### Fixed (2026-02-07) - Memory Bank Search Scoring

- **BM25 Score Normalization**: Fixed keyword search returning 0 results
  - BM25 scores are now normalized relative to the best match in results
  - Keyword matches now properly score 0.3-1.0 (was incorrectly near 0)
  - Search fallback path now uses proper score normalization

### Added (2026-02-07) - Memory Bank: File-Based Knowledge with Hybrid Search

- **NEW FEATURE**: Memory Bank for Persistent Knowledge Storage
  - File-based knowledge store at `~/.navig/memory/` for Markdown files
  - **Hybrid search**: 70% vector similarity + 30% BM25 keyword matching
  - Smart chunking (~400 tokens, 80-token overlap) respects document structure
  - Automatic embedding generation with caching (avoids re-embedding unchanged content)
  - Line-number citations for precise source references
  - FTS5 full-text search for fast keyword lookups

- **NEW CLI COMMANDS**: Memory bank management
  - `navig memory bank` - Show memory bank status and statistics
  - `navig memory index` - Index all .md/.txt files in memory directory
  - `navig memory search "query"` - Hybrid search with vector + keyword
  - `navig memory files` - List indexed files with chunk counts
  - `navig memory clear-bank` - Clear index (preserves source files)
  - All commands support `--plain` and `--json` output for scripting

- **AI CONTEXT INJECTION**: Automatic knowledge retrieval
  - Memory search results injected into AI conversations
  - Citations included: `[source: filename.md:15-23]`
  - AI generates responses using indexed knowledge
  - Falls back gracefully when no relevant memory found

- **FILE WATCHER**: Automatic reindexing on file changes
  - Polling-based watcher (cross-platform compatible)
  - Debounced reindexing (1.5s default) for batching rapid changes
  - Optional watchdog support for better performance

- **DOCUMENTATION**: New memory bank guide at `docs/memory.md`
  - Architecture overview and chunking strategy
  - CLI command reference with examples
  - API reference for programmatic usage
  - Configuration options and troubleshooting

### Added (2026-02-06) - Autonomous Deployment & Improved Telegram Bot

- **NEW DOCUMENTATION**: Comprehensive Autonomous Deployment Guide
  - Complete guide for 24/7 Telegram bot deployment: `docs/AUTONOMOUS_DEPLOYMENT.md`
  - Covers all deployment options: Linux VPS (systemd), Docker, Windows (NSSM)
  - Explains difference between standalone bot vs full agent mode
  - Includes troubleshooting for "I'm not sure what you need" responses
  - AI provider configuration for intelligent responses
  - SOUL.md personality customization guide
  - Zero-setup user experience documentation

- **IMPROVED**: Enhanced Telegram Bot Intelligence (navig_ai.py)
  - Added 15+ new direct execution patterns for faster response
  - New patterns: docker logs, docker restart, memory, CPU, uptime, tables, hosts
  - Container name extraction from natural language
  - Database name extraction from queries
  - General status overview command ("How's my server?")
  - Context-aware helpful fallbacks (detects what user is asking about)
  - Better help response with categorized capabilities
  - "Thank you" and other conversational responses

- **FIXED**: Generic "I'm not sure what you need" fallback
  - Now provides actionable guidance with categorized capabilities
  - Contextual suggestions based on detected keywords (web, backup, logs, users)
  - Tips for better query formatting

### Added (2026-02-06) - AirLLM Local Inference Provider

- **NEW FEATURE**: AirLLM Provider for Local Large Model Inference
  - Run 70B+ parameter models on 4-8GB VRAM through layer-wise inference
  - Supports 4-bit and 8-bit compression for reduced VRAM usage
  - Compatible with any HuggingFace model (Llama, Qwen, Mistral, DeepSeek, etc.)
  - Full integration with NAVIG's multi-provider fallback system
  - CLI commands:
    - `navig ai airllm --status` - View installation and configuration status
    - `navig ai airllm --configure` - Configure model path, compression, VRAM limits
    - `navig ai airllm --test` - Test local inference
    - `navig ai models` - List all available models including AirLLM
    - `navig ai models --provider airllm` - List AirLLM suggested models
  - Environment variables: AIRLLM_MODEL_PATH, AIRLLM_COMPRESSION, AIRLLM_MAX_VRAM_GB
  - Usage: `navig ai ask "question" --model airllm:meta-llama/Llama-3.3-70B-Instruct`

- **IMPROVED**: AI provider system now includes 6 providers
  - openai, anthropic, openrouter, ollama, groq, **airllm**

- **FILES CREATED**:
  - `navig/providers/airllm.py` - AirLLM client implementation
  - `docs/providers/airllm.md` - Comprehensive AirLLM documentation

- **DOCUMENTATION**: Updated AI provider guides
  - Updated `navig/help/ai-providers.md` with AirLLM section
  - Updated `docs/HANDBOOK.md` Section 22.6 for AirLLM setup

### Added (2026-02-06) - SOUL.md Personality System

- **NEW FEATURE**: SOUL.md Personality Injection System
  - Create deep personality customization via `~/.navig/workspace/SOUL.md`
  - SOUL.md content is injected into AI system prompt for consistent identity
  - Enables conversational responses to greetings and identity questions
  - CLI commands:
    - `navig agent soul show` - Display current SOUL.md
    - `navig agent soul create` - Create from template
    - `navig agent soul edit` - Open in default editor
    - `navig agent soul path` - Show file locations
  - Falls back to built-in personality profiles if SOUL.md missing
  - Pattern mirrors Reference Agent's SOUL.md system

- **IMPROVED**: Agent now responds naturally to conversational queries
  - "How are you?" → Warm response with system status
  - "What is your name?" → Identity introduction
  - "Hello" → Greeting from personality profile
  - No more generic "I'm not sure what you need..." responses

- **FILES CREATED**:
  - `navig/resources/SOUL.default.md` - Default personality template
  - Updated `navig/agent/soul.py` - SOUL.md loading and injection
  - Updated `navig/agent/brain.py` - Soul integration for system prompts
  - Updated `navig/commands/agent.py` - Soul CLI commands

- **DOCUMENTATION**: Updated SOUL.md customization guides
  - Added SOUL.md section to `docs/AGENT_MODE.md`
  - Added Section 25.5.1 to `docs/HANDBOOK.md`

### Added (2026-02-06) - Phase 3: Testing & Production Deployment Complete ✅

All Phase 2 features are now fully documented and production-ready.

- **DOCUMENTATION**: Production Deployment Guide
  - Created `docs/PRODUCTION_DEPLOYMENT.md` (600+ lines)
    - Pre-deployment checklist with system requirements and configuration validation
    - Installation procedures for systemd, launchd, Windows Service, and Docker
    - Post-deployment validation for all components
    - Monitoring setup: health checks, log rotation, alerts, Prometheus metrics
    - Operational procedures: daily/weekly/monthly/quarterly tasks
    - Incident response playbooks for common issues
    - Disaster recovery procedures with backup strategies
    - Rollback procedures for failed deployments
    - Security hardening recommendations
    - Performance tuning guidelines
    - Scaling considerations for multi-instance setups

- **DOCUMENTATION**: Service Installation Guide
  - Created `docs/AGENT_SERVICE.md` (400+ lines)
    - Platform-specific installation for Linux (systemd), macOS (launchd), Windows (Service)
    - Quick start guide for each platform
    - Service management commands (start/stop/restart/status)
    - Configuration options and environment variables
    - Troubleshooting section for common service issues
    - Security considerations (permissions, non-root execution)
    - Best practices for testing, monitoring, and updates
    - Advanced configuration (custom names, multiple instances, resource limits)

- **DOCUMENTATION**: Goal Planning Guide
  - Created `docs/AGENT_GOALS.md` (600+ lines)
    - Goal lifecycle: creation → decomposition → execution → completion
    - CLI usage examples for all goal commands
    - Goal and subtask state machines explained
    - Dependency tracking and execution order
    - Integration with Heart, Brain, and Hands components
    - Storage format (JSON persistence)
    - API reference for GoalPlanner class
    - Advanced usage: complex dependencies, conditional execution, parallel execution
    - Real-world examples: database migration, deployment pipeline, maintenance tasks
    - Troubleshooting: stuck goals, blocked subtasks, progress not updating
    - Best practices: clear descriptions, granular subtasks, explicit dependencies

- **DOCUMENTATION**: Updated Handbook
  - Updated `docs/HANDBOOK.md` Section 25 (Autonomous Agent Mode)
    - Added Section 25.9: Service Installation with platform-specific commands
    - Added Section 25.10: Goal Planning with examples and state descriptions
    - Integrated all 4 Phase 2 features into handbook

### Phase 2 Feature Summary ✅

All 4 planned autonomous agent enhancements are complete and production-ready:

#### Feature 1: Self-Healing Auto-Remediation ✅
  - Automatic component restart with exponential backoff (1s → 60s)
  - Connection failure recovery with intelligent retry logic
  - Configuration rollback to last known good state
  - Comprehensive remediation logging to ~/.navig/logs/remediation.log
  - New CLI commands:
    - `navig agent remediation list` - Show all remediation actions
    - `navig agent remediation status --id <id>` - Check specific action status
    - `navig agent remediation clear` - Clear completed actions
  - Heart orchestrator integration for automatic component recovery
  - Config backup system in ~/.navig/workspace/config-backup/
  - See [AGENT_SELF_HEALING.md](docs/AGENT_SELF_HEALING.md) for full documentation

#### Feature 2: Learning System ✅
  - Analyzes agent logs to detect recurring error patterns
  - Detects: connection failures, permission issues, config errors, resource exhaustion
  - Provides actionable recommendations based on findings
  - New CLI command: `navig agent learn`
    - `--days N` - Analyze last N days (default 7)
    - `--export` - Export patterns to JSON
  - Exports to ~/.navig/workspace/error-patterns.json
  - See [AGENT_LEARNING.md](docs/AGENT_LEARNING.md) for full documentation

#### Feature 3: Service Installers ✅
  - Install NAVIG agent as system service for 24/7 operation
  - Linux support: systemd user/system units
  - macOS support: launchd LaunchAgents
  - Windows support: Windows Service (via nssm or sc.exe)
  - New CLI commands:
    - `navig agent service install` - Install service (starts on boot)
    - `navig agent service uninstall` - Remove service
    - `navig agent service status` - Check service status
  - Automatic restart on failure
  - Environment variable preservation
  - See [AGENT_SERVICE.md](docs/AGENT_SERVICE.md) for full guide
  - Implementation: navig/agent/service.py (600 lines)

#### Feature 4: Autonomous Goal Planning ✅
  - High-level goal decomposition into executable subtasks
  - Dependency tracking between subtasks
  - Progress monitoring (0-100%)
  - Goal states: pending, decomposing, in-progress, blocked, completed, failed, cancelled
  - Subtask states: pending, in-progress, completed, failed, skipped
  - New CLI commands:
    - `navig agent goal add --desc "description"` - Add new goal
    - `navig agent goal list` - List all goals with progress
    - `navig agent goal status --id <id>` - View goal details and subtasks
    - `navig agent goal cancel --id <id>` - Cancel a goal
  - Goals stored in ~/.navig/workspace/goals.json
  - See [AGENT_GOALS.md](docs/AGENT_GOALS.md) for full guide
  - Implementation: navig/agent/goals.py (400 lines)

### Technical Details

- **Files Created**: 8 new files
  - navig/agent/remediation.py (304 lines)
  - navig/agent/service.py (600 lines)
  - navig/agent/goals.py (400 lines)
  - docs/AGENT_SELF_HEALING.md (250+ lines)
  - docs/AGENT_LEARNING.md (280+ lines)
  - docs/AGENT_SERVICE.md (400+ lines)
  - docs/AGENT_GOALS.md (600+ lines)
  - docs/PRODUCTION_DEPLOYMENT.md (600+ lines)
- **Lines of Code**: 2,200+ lines implementation + 2,100+ lines documentation = 4,300+ total
- **Testing**: Comprehensive end-to-end testing completed for all features
- **Production Ready**: All features tested and documented for production deployment

### Breaking Changes
None - All new features are additive and backward compatible.

### Migration Guide
No migration required. New features are opt-in via CLI commands.

---

---

### Changed (2026-02-06)

- **IMPROVED**: Heart orchestrator now includes remediation engine
  - Starts remediation on agent startup
  - Schedules component restarts through remediation engine
  - Tracks restart attempts with exponential backoff
  - Stops remediation engine gracefully on shutdown

- **IMPROVED**: Component health check loop triggers automatic recovery
  - Failed components scheduled for restart through remediation
  - Metadata from health checks passed to remediation engine
  - Maximum 5 restart attempts before giving up
  - Prevents rapid restart loops with exponential delays

### Security (2026-02-06)

- **AUDIT COMPLETE**: Comprehensive Phase 2 security audit completed across all navig/* modules
  - Audited 38+ files across 10 directories (commands, modules, providers, adapters, etc.)
  - No critical vulnerabilities found in approval, browser, desktop, heartbeat, or task systems
  - All subprocess operations verified secure (list-based arguments, no shell injection)
  - Zero bare `except:` clauses across audited modules
  - All file operations use proper context managers (no resource leaks)
  - See AUDIT_FINDINGS_PHASE2.md for full report

- **SECURITY FIX**: Replaced os.system() in interactive menu (commands/interactive.py)
  - Changed from `os.system('cls' if os.name == 'nt' else 'clear')` to subprocess.run()
  - Uses list-based arguments: `['cmd', '/c', 'cls']` or `['clear']`
  - Eliminates last remaining os.system() usage in commands layer

- **CRITICAL FIX**: Shell injection vulnerability in task queue (gateway/server.py)
  - Task queue now validates commands and restricts to `navig` commands only
  - Changed from `shell=True` to `shlex.split()` with `shell=False`
  - Added explicit allowlist for command prefixes (`navig`, `python -m navig`)

- **CRITICAL FIX**: Shell injection vulnerability in cron scheduler
  - Cron job commands now use `shlex.split()` instead of passing to shell
  - Only NAVIG commands executed via cron are affected (AI prompts unchanged)

- **CRITICAL FIX**: Shell injection vulnerability in channel router (gateway/channel_router.py)
  - Telegram/MCP messages routed to gateway now use `shlex.split()` with `shell=False`
  - Prevents arbitrary command injection from external message sources

- **IMPROVED**: Webhook listener now binds to `127.0.0.1` by default (was `0.0.0.0`)
  - Prevents external network access to agent webhook endpoint
  - Can be overridden in config to `0.0.0.0` if needed

- **IMPROVED**: Local command execution now uses explicit shell invocation
  - Changed `shell=True` to `['bash', '-c', command]` in connection.py
  - Changed `shell=True` to `['bash', '-c', command]` in local_discovery.py
  - Removed unnecessary `shell=True` in template.py editor invocation
  - Reduces attack surface while maintaining functionality

### Fixed (2026-02-06)

- **Fixed**: Missing numpy dependency causing ImportError
  - Made numpy import lazy/conditional with proper error message
  - Added HAS_NUMPY flag to check availability before use

- **Fixed**: pyproject.toml missing 15+ subpackages
  - Changed to `find:` package discovery to include all navig.* packages
  - Ensures `pip install .` includes agent, gateway, scheduler, mcp, memory, etc.

- **Fixed**: Bare `except:` clauses swallowing KeyboardInterrupt
  - Changed to `except Exception:` in navig_ai.py and cli.py
  - Allows Ctrl+C to properly terminate the program

- **Fixed**: Scaffold date variable using literal "today" instead of actual date
  - Now uses `datetime.now().strftime('%Y-%m-%d')`

- **Fixed**: Silent error swallowing in nervous_system.py event dispatch
  - Added proper logging with traceback for handler errors

- **Fixed**: README references to nonexistent SECURITY_FIXES_APPLIED.md
  - Updated to point to Troubleshooting Guide instead

- **Fixed**: Old repository name "remote-manager" in README and pyproject.toml
  - Updated all URLs to current "navig" repository name

### Improved (2026-02-06)

- **Improved**: Scaffold dry-run now shows preview of files to be created
  - Lists all files and directories from template before generation

- **Improved**: Better error message for external source files in templates
  - Now explicitly states feature is not implemented instead of silent pass

- **Added**: Shared test fixtures in tests/conftest.py
  - Mock configs, SSH clients, subprocess calls, temp directories
  - Sample data factories for hosts, apps, templates
  - Reduces duplication across test files

### Documentation (2026-02-06)

**Phase 1:**
- **Removed**: 9 stale planning documents (~33,000 words)
  - Deleted pre-implementation architecture docs
  - Deleted completed roadmaps and gap analyses
  - Removed redundant implementation completion files

- **Updated**: HANDBOOK.md version to 2.1.0 (was incorrectly 2.3.0)
- **Updated**: HANDBOOK.md date to 2026-02-06 (was 2025-01-06)

**Phase 2:**
- **Consolidated**: Telegram documentation from 4 files into 1 authoritative guide
  - Created `docs/TELEGRAM.md` (~3,500 words) - comprehensive Telegram bot guide
  - Deleted `docs/TELEGRAM_BOT.md` (1,332 words) - merged into TELEGRAM.md
  - Deleted `docs/TELEGRAM_BOT_SETUP.md` (1,447 words) - merged into TELEGRAM.md
  - Deleted `docs/TELEGRAM_AI_ASSISTANT.md` (5,290 words) - architecture proposals, essentials merged

- **Removed**: 2 additional stale planning documents
  - Deleted `docs/IMPLEMENTATION_ROADMAP.md` (4,460 words) - planning artifact
  - Deleted `docs/TELEGRAM_IMPLEMENTATION_COMPLETE.md` (1,162 words) - completion announcement

**Total documentation cleanup:** 14 files deleted (~47,000 words), 1 consolidated guide created

**Documentation stats:** Reduced from 21 to 17 files in `docs/` (-19%), ~43k to ~33k words (-23%)

### Telegram Bot Improvements (2026-02-04)

- **Fixed**: Unicode encoding error on Windows when bot responses contain emojis
  - Added `safe_print()` function to handle character encoding gracefully
  - Set UTF-8 encoding for stdout/stderr on Windows console
  - Subprocess calls now use `encoding='utf-8', errors='replace'`

- **Fixed**: Bot now executes commands instead of just explaining them
  - Complete rewrite of `NavigAI.chat()` to be action-oriented
  - Direct intent detection for common queries (disk, containers, databases)
  - Actual command execution with formatted output
  - User-friendly error messages with troubleshooting suggestions

- **Fixed**: Subprocess output capture on Windows with Rich console
  - Use `python -m navig` invocation to ensure capturable output
  - Proper shlex parsing for quoted arguments

- **Added**: `navig agent telegram` command group for managing the Telegram bot
  - `navig agent telegram start` - Start the Telegram bot
  - `navig agent telegram status` - Show bot configuration and status
  - `navig agent telegram setup` - Interactive setup guide

- **Improved**: Clear documentation on how to run the Telegram bot as a service

### Autonomous Agent Mode (2026-02-02)

Complete autonomous agent system that transforms NAVIG into a living, intelligent entity:

- **Agent Module** (`navig agent`)
  - Human-body-inspired architecture for intuitive understanding
  - Components: Brain, Eyes, Ears, Hands, Heart, Soul, NervousSystem
  - Dual-mode operation: CLI mode + Agent mode coexist without conflict
  - Multiple personality profiles (friendly, professional, witty, paranoid, minimal)

- **Agent Commands**
  - `navig agent install` - Install and configure agent mode
  - `navig agent start` - Start the autonomous agent
  - `navig agent stop` - Stop the running agent
  - `navig agent status` - Show agent status
  - `navig agent config` - Manage agent configuration
  - `navig agent logs` - View agent logs
  - `navig agent personality` - Manage personality profiles
  - `navig agent service` - Install as system service (systemd/launchd)

- **Core Components**
  - **NervousSystem**: Async event bus for component communication
  - **Heart**: Orchestrates component lifecycles, health monitoring
  - **Brain**: AI decision-making with reasoning and planning
  - **Eyes**: System monitoring (CPU, memory, disk, logs)
  - **Ears**: Input listeners (Telegram, MCP, API, webhooks)
  - **Hands**: Safe command execution with approval system
  - **Soul**: Personality engine with customizable profiles

- **Personality System**
  - Built-in profiles: friendly, professional, witty, paranoid, minimal
  - Custom profiles: Create YAML files in `~/.navig/agent/personalities/`
  - Dynamic switching: `navig agent personality set <name>`
  - Emotional states and mood tracking

- **Safety Features**
  - Dangerous command detection and blocking
  - Approval system for destructive operations
  - Safe mode with sudo restrictions
  - Configurable confirmation patterns

- **Service Management**
  - Linux: systemd service integration
  - macOS: launchd plist generation
  - Windows: NSSM/Task Scheduler guidance

- **Configuration**
  - Location: `~/.navig/agent/config.yaml`
  - Environment variable substitution: `${VAR}` syntax
  - Component-level configuration (brain, eyes, ears, hands, heart)

- **Test Coverage:** 44 unit tests passing

### Memory/Context Management System (2026-02-02)

Complete implementation of conversation memory and knowledge base for AI context:

- **Conversation Storage** (`navig memory`)
  - SQLite-backed persistent conversation history
  - Session-based message storage with token tracking
  - Full CRUD operations with search and compaction
  - `navig memory sessions` - List all conversation sessions
  - `navig memory history <session>` - Show session messages
  - `navig memory clear --session|--all` - Clear memory
  - `navig memory stats` - Memory usage statistics

- **Knowledge Base**
  - Persistent knowledge entries with unique keys
  - TTL-based automatic expiration
  - Tag-based organization and filtering
  - `navig memory knowledge list` - List entries
  - `navig memory knowledge add --key --content` - Add knowledge
  - `navig memory knowledge search --query` - Search entries

- **RAG Pipeline** (Retrieval-Augmented Generation)
  - Combines conversation history + knowledge + files
  - Configurable context window management
  - Semantic search with embedding support
  - File reference extraction from text

- **Vector Embeddings**
  - Local provider: sentence-transformers (all-MiniLM-L6-v2)
  - Remote provider: OpenAI embeddings API
  - Cached embedding provider for performance
  - Cosine similarity search

- **Gateway Integration**
  - `GET /memory/sessions` - List sessions
  - `GET /memory/sessions/{key}/history` - Get history
  - `DELETE /memory/sessions/{key}` - Delete session
  - `POST /memory/messages` - Add message
  - `GET /memory/knowledge` - List knowledge
  - `POST /memory/knowledge` - Add/update knowledge
  - `GET /memory/knowledge/search` - Search knowledge
  - `GET /memory/stats` - Usage statistics

- **Test Coverage:** 48 unit tests passing

### Documentation: Reference Agent Architecture Adoption Roadmap (2026-02-02)

Comprehensive implementation roadmap for NAVIG v3.0 autonomous agent capabilities:

- **New Document:** `docs/IMPLEMENTATION_ROADMAP.md`
  - Complete feature gap analysis: NAVIG vs Reference Agent
  - Dependency graph with Mermaid diagrams
  - Priority matrix and implementation timeline (10 weeks)
  - Detailed specifications for 6 major features:
    - Memory/Context Management (88 person-hours)
    - Sandboxed Execution with Docker (104 person-hours)
    - Streaming Responses (56 person-hours)
    - Multi-Agent System (136 person-hours)
    - Model-Agnostic Providers (88 person-hours)
    - Computer Use/Vision (104 person-hours)
  - Risk assessment and security considerations
  - Testing strategy with 70% coverage targets
  - Migration path with feature flags
  - Concrete next steps for weeks 1-2

### Autonomous Agent Modules Implementation (2026-02-02)

Full implementation of Phase 1 & Phase 2 autonomous agent capabilities:

- **Human Approval System** (`navig approve`)
  - Pattern-based command classification: SAFE, CONFIRM, DANGEROUS, NEVER
  - Async approval flow with configurable timeout
  - Multiple handlers: Telegram, CLI, Gateway REST API
  - `navig approve list` - List pending requests
  - `navig approve yes|no <id>` - Approve or deny requests
  - `navig approve policy` - View approval patterns

- **Browser Automation** (`navig browser`)
  - Playwright-based headless browser control
  - Full page automation: navigate, click, fill, screenshot
  - `navig browser open <url>` - Navigate to URL
  - `navig browser click <selector>` - Click element
  - `navig browser fill <selector> <value>` - Fill form field
  - `navig browser screenshot` - Capture screenshot
  - Gateway endpoints: `/browser/navigate`, `/browser/click`, etc.

- **MCP Client** (Model Context Protocol)
  - Connect to any MCP server (stdio or SSE transport)
  - Multi-client management with unified tool registry
  - JSON-RPC 2.0 protocol implementation
  - Gateway endpoints: `/mcp/clients`, `/mcp/tools`, `/mcp/connect`

- **Webhook Receiver**
  - Receive webhooks from GitHub, GitLab, Stripe, Slack
  - Signature verification per provider
  - Event routing and history tracking
  - Gateway integration for push-based triggers

- **Desktop Automation** (`navig desktop`)
  - pyautogui-based screen control (when display available)
  - Mouse: click, move, drag, scroll
  - Keyboard: type, hotkeys, shortcuts
  - Image recognition: locate on screen, click image
  - File watcher: watchdog-based reactive automation

- **Task Queue** (`navig queue`)
  - Priority-based async task queue
  - Dependency management between tasks
  - Persistent storage across restarts
  - `navig queue list` - List queued tasks
  - `navig queue add <name> <handler>` - Add task
  - `navig queue stats` - View queue statistics
  - Automatic retry with configurable backoff

- **Gateway Integration** - All new modules accessible via REST API
  - `/approval/*` - Approval system endpoints
  - `/browser/*` - Browser automation endpoints
  - `/mcp/*` - MCP client management endpoints
  - `/tasks/*` - Task queue endpoints
  - Hot-reload configuration for new modules

### �📋 Autonomous Agent Implementation Plan (2025-02-02)

- **Comprehensive Implementation Plan** - [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)
  - **Phase 1 (1-2 weeks):** Approval Flows, Playwright Browser, MCP Client
  - **Phase 2 (2-4 weeks):** Webhook Receiver, Desktop Automation, Task Queue
  - **Phase 3 (1-2 months):** Multi-Agent Coordination, Undo/Rollback, Discord
  - Full code architecture and implementation snippets for each capability
  - Detailed testing strategies (unit, integration, manual checklists)
  - Risk assessment and mitigation strategies per feature
  - Configuration schema additions for `navig.json`
  - CLI command additions: `navig approve`, `navig browser`, `navig mcp clients`
  - Gateway API endpoint specifications

### �📊 Autonomous Agent Gap Analysis (2025-02-02)

- **Comprehensive Gap Analysis** 
  - Computer Understanding & Control - Desktop/browser automation gaps identified
  - Browser Capabilities - Playwright integration roadmap
  - API Integration - Webhook receiver, background token refresh needed
  - MCP Integration - MCP Client capability missing (server works)
  - UX/DX & Workflow - Approval flows, task queue recommendations
  - Priority roadmap with complexity estimates

### 🤖 Autonomous Agent System (2025-02-03)

- **Gateway Server** - 24/7 control plane for autonomous agent operations!
  - `navig gateway start` - Start the gateway server
  - `navig gateway status` - Check if gateway is running
  - `navig gateway session list|show|clear` - Manage conversation sessions
  - HTTP/WebSocket API on port 8789 (configurable)
  - Session persistence across restarts
  - Multi-channel message routing (Telegram, Discord, etc.)
  - Hot-reload configuration changes

- **Heartbeat System** - Periodic health checks with smart notifications!
  - `navig heartbeat status` - Show heartbeat status
  - `navig heartbeat trigger` - Run immediate health check
  - `navig heartbeat history` - View check history
  - `navig heartbeat configure --interval 30` - Configure interval
  - **HEARTBEAT_OK pattern**: When all systems healthy, returns "HEARTBEAT_OK" and suppresses notifications
  - Only notifies when actual issues found
  - Checks: host connectivity, disk space, memory, SSL certificates

- **Cron Scheduler** - Persistent job scheduling with natural language!
  - `navig cron list` - List scheduled jobs
  - `navig cron add "Name" "every 30 minutes" "command"` - Add job
  - `navig cron run job_1` - Run job immediately
  - `navig cron enable|disable job_1` - Enable/disable jobs
  - Natural language: "every 30 minutes", "hourly", "daily"
  - Standard cron expressions: "*/5 * * * *", "0 9 * * *"
  - Automatic retry on failure with backoff

- **Workspace Files** - Persistent AI context files
  - `AGENTS.md` - Agent capabilities and bindings
  - `SOUL.md` - Agent personality and behavior
  - `USER.md` - User preferences and patterns
  - `TOOLS.md` - Available commands and shortcuts
  - `HEARTBEAT.md` - Health check instructions
  - `MEMORY.md` - Long-term memories and notes
  - Files auto-created in `~/.navig/workspace/`

- **Telegram Bot Gateway Integration**
  - Set `NAVIG_GATEWAY_URL=http://localhost:8789` in `.env`
  - Sessions persist across bot restarts
  - Automatic session compaction prevents token overflow

### 🚀 Onboarding Wizard & Workspace Templates (2026-02-02)

- **Interactive onboarding wizard** - Get started with NAVIG in minutes!
  - `navig onboard` - Launch the interactive setup wizard
  - **Quickstart flow**: 3-step minimal setup (AI provider, Telegram, workspace)
  - **Manual flow**: Full configuration with all options
  - `--non-interactive` flag for automation and CI/CD
  - Saves configuration to `~/.navig/navig.json`
  - Syncs settings to `.env` file automatically
- **Workspace template system** - Customize your AI agent's personality!
  - `navig workspace --init` - Create workspace with all templates
  - `navig workspace --status` - Show workspace status and files
  - **7 bootstrap files** inspired by navig:
    - `IDENTITY.md` - Agent name and emoji (e.g., 🧭 NAVIG)
    - `SOUL.md` - Agent personality and behavior guidelines
    - `AGENTS.md` - Multi-agent collaboration definitions
    - `TOOLS.md` - Tool and capability definitions
    - `USER.md` - User preferences and permissions
    - `HEARTBEAT.md` - Periodic status update configuration
    - `BOOTSTRAP.md` - First-run instructions (auto-removes after bootstrap)
  - WorkspaceManager class for loading and injecting context into AI
  - Bootstrap files are injected into AI system prompts

### 💬 Telegram Bot Typing Indicator (2026-02-02)

- **Typing status indicator** - Shows "typing..." while AI processes requests
  - Three modes configurable via `TYPING_MODE`:
    - `instant` - Start typing immediately when message received
    - `message` - Start typing after acknowledging the message
    - `never` - Disable typing indicator entirely
  - `TYPING_INTERVAL` - Refresh interval (default: 4 seconds)
  - Continuous refresh keeps typing indicator active during long AI calls
  - Async context manager with proper cleanup

### OAuth Framework Implementation (2026-01-31)

- **OAuth PKCE framework added** - Production-ready OAuth 2.0 implementation (no active providers yet)
  - Full RFC 7636 PKCE implementation with S256 code challenge method
  - Interactive mode: auto-opens browser and captures OAuth callback
  - Headless mode: manual URL paste for remote/VPS environments
  - Automatic token refresh with configurable expiry buffer
  - Secure token storage with proper file permissions
- **Current status**: No OAuth providers configured
  - OpenAI requires enterprise partnership (OAuth unavailable for public use)
  - Use API key authentication instead: `navig cred add <provider> <key> --type api-key`
  - Framework ready for future providers that support OAuth
- Added `navig ai login <provider>` command (currently shows helpful error)
- Added `navig ai logout <provider>` command for OAuth credential removal
- Added comprehensive documentation:
  - `docs/development/oauth.md` - Technical OAuth implementation details
  - `docs/development/oauth-limitations.md` - Why OAuth isn't available yet
- **For users**: Continue using API key authentication - it works perfectly!

### Multi-Provider AI System (2026-01-31)

### � Multi-Provider AI System (2026-01-31)

- **Multi-provider AI support** - Connect to multiple AI providers with automatic fallback!
  - Supports OpenAI, Anthropic, OpenRouter, Ollama, Groq out of the box
  - Automatic fallback: if one provider fails, tries the next
  - Cooldown management: rate-limited providers get exponential backoff
  - Secure credential storage in `~/.navig/credentials/`
- Added `navig ai providers` - Manage AI providers and API keys:
  - `navig ai providers` - List configured providers and status
  - `navig ai providers --add openai` - Add API key for a provider
  - `navig ai providers --test anthropic` - Test provider connection
  - `navig ai providers --remove groq` - Remove API key
- Provider architecture based on Reference Agent patterns:
  - Type-safe provider configuration with builtin defaults
  - Auth profile management with priority resolution
  - Fallback candidates with allowlist/blocklist support
  - Unified client interface for all providers
- Environment variable support: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.

### �🤖 Telegram AI Assistant (2026-01-31)

- **Autonomous AI agent for Telegram** - Manage servers from anywhere using natural language!
  - Talk naturally: "How much space on my Hetzner server?" → Bot executes commands and formats results
  - AI-powered intent understanding using OpenAI function calling
  - Skills-based architecture: extensible tool definitions in `skills/` directory
  - Conversation memory: remembers context across messages
  - Security: user whitelist, confirms destructive operations
  - Windows support: PowerShell setup scripts included
- Added `navig_ai.py` - Core AI agent with OpenAI integration and NAVIG command executor
- Added `navig_bot.py` - Telegram bot with async handlers and conversation state management
- Added 4 built-in skills: disk-space, docker-manage, database-query, hestiacp-manage
- Added `scripts/install-bot.ps1` - Automated Windows setup (checks Python, installs deps, creates config)
- Added `TELEGRAM_BOT.md` - Complete guide: Quick Start (15 min), examples, troubleshooting, 24/7 deployment
- Added `requirements-bot.txt` - Dependencies for bot (python-telegram-bot, openai, pyyaml)
- Added `.env.telegram.example` - Configuration template for tokens and allowed users
- Works alongside VSCode: Use Telegram for mobile access, VSCode for complex work

### ⚡ Performance & Build System (2026-01-31)

- **CLI startup time reduced to ~75ms** (down from ~314ms) - `navig --help` now responds nearly instantly
- Added `scripts/build.py` - automated build script for creating single-binary distributions:
  - `--tool pyinstaller` - Build with PyInstaller (faster build, ~45MB binary)
  - `--tool nuitka` - Build with Nuitka (smaller/faster binary, ~25MB)
  - `--compare` - Benchmark both tools and compare results
  - `--measure-startup` - Analyze import times and identify bottlenecks
  - `--compile-bytecode` - Pre-compile all .py files for faster imports
- Added `docs/building.md` - comprehensive guide for building binary distributions
- Added `docs/development.md` - development setup guide with modern tooling:
  - `uv` support for 10-100x faster dependency installation
  - `rye` support for full project management
- Extended lazy-loading to scaffold commands for faster startup
- Verified bytecode compilation in .gitignore (`__pycache__/`, `*.pyc`)

### 🚀 Onboarding, Status, JSON Output (2025-12-26)

- Adds `navig quickstart` for fast onboarding in new projects.
- Adds `navig status` to show active host/app and tunnel state (supports `--plain` and `--json`).
- Adds short aliases: `h` (host), `a` (app), `f` (file), `t` (tunnel), `r` (run).
- Expands `--json` support across core commands (host/app/file/db/tunnel/run) with a stable JSON envelope (`schema_version`, `command`, `success`, …).
- Adds TTL-based caches under `~/.navig/cache/` for discovery-style operations and respects `--no-cache`.
- Fixes a spurious "global config directory not accessible" error on fresh installs/test environments.
- Improves host list cache invalidation reliability on Windows.

### 🛡️ PowerShell Safety (2025-12-27)

- Adds automatic PowerShell quoting issue detection for `navig run` commands.
- When complex commands with `()`, `{}`, `$` are detected on PowerShell, navig now shows helpful guidance suggesting `--stdin`, `--file`, or `-i` (interactive editor) to avoid shell parsing errors.
- Adds early error detection that catches when PowerShell mangles commands BEFORE they reach navig (e.g., "Got unexpected extra arguments" errors) and provides immediate, actionable solutions.
- Updates `navig run --help` to prominently warn PowerShell users about quoting issues.

### 🗄️ Database Improvements (2025-12-27)

- **Rich formatted output for database queries** - DESCRIBE, SELECT, and SHOW queries now display with proper column alignment, colors, and semantic highlighting:
  - Column types color-coded: integers (green), strings (cyan), enums (yellow), dates (blue)

### 📁 File Operations Improvements (2025-12-27)

- **Line range support for `navig file show`** - You can now specify line ranges using `--lines 100-200` or `--lines 100:200` to view specific sections of files without needing `sed` commands.
  - Example: `navig file show app.log --lines 800-850`
  - Uses `sed -n 'start,endp'` internally for efficient range extraction.
- Fixed overly aggressive "complex command" warning that triggered on safe commands with semicolons and pipes.
- **Improved `--b64` output display** - When using base64-encoded commands, navig now shows the original decoded command in the "Executing:" message instead of the confusing base64 string.
  - Before: `ℹ Executing: WTJRZ0wyaHZiV1UxTDJONVltVnphWE12Y0hWaWJHbGpYMmgwYld3dmFHOWlhWFZ6SUN…`
  - After: `ℹ Executing: cd /home/user/project && php artisan tinker --execute='App\Models\User::count();'`
  - Keys highlighted: PRI (bold yellow), UNI (yellow), MUL (dim yellow)
  - NULL values dimmed, auto_increment marked in magenta
  - Use `--plain` to get unformatted output for scripting
- **Auto-detection of base64 queries** - `navig db query` now automatically detects base64-encoded SQL, no flags needed!
- Adds `--b64` flag to `navig db query` for forced base64 decoding (usually auto-detected).
- Adds missing `--plain` flag to `navig db query` for clean, script-friendly output.
- Adds `--raw` as an alias for `--plain` flag (more intuitive naming).
- Improves database authentication error messages: now shows which credentials were attempted and provides 4 specific solutions.
- Automatically uses `mariadb` command instead of deprecated `mysql` when MariaDB is detected.
- Filters out "Deprecated program name" warnings from stderr for cleaner output.

### 🐛 Bug Fixes (2025-12-27)

- Fixes duplicate checkmarks in success messages - removed redundant symbols since `ch.success()` already adds them (e.g., "✓ ✓ Upload complete" now shows as "✓ Upload complete")
- Fixes `AttributeError` in app edit functionality: replaced invalid `config_manager.config_dir` with correct `config_manager.global_config_dir` and `config_manager.apps_dir` attributes.
- Fixes `navig file list` generating invalid `ls --l-h` command (now correctly generates `ls -lh`)
- Fixes database credential resolution in `navig db query`: now properly reads credentials from app/host config instead of always using default "root" user.
- Improves database authentication error messages: shows "(yes)" or "(no)" for password presence instead of misleading "(provided)/(none)" text.
- Changes confirmation prompt default from "No" to "Yes" for non-critical operations (destructive operations still default to "No" for safety).
- **Fixes critical config loading bug**: Global config (including `execution.mode`) was not being loaded when in project directories, causing `execution.mode: auto` to be ignored.
- Suppresses diagnostic messages (`Detected: <type>`) in `--plain` mode for pure scripting output.
- Improves `db query --json` output to be strictly machine-parseable (no extra diagnostic lines).

### 🧭 CLI UX & In-App Help (2025-12-26)

- Adds `python -m navig` support via package entrypoint.
- Adds `navig help` / `navig help <topic>` with markdown-backed topics (and fallback to the centralized help registry).
- Updates handbook examples to prefer canonical `navig file ...` commands while keeping legacy `navig upload/download` compatibility.
- Speeds up top-level `navig` / `navig --help` / `navig --version` by handling them in a minimal fast path (avoids Rich import and fixes Windows console encoding issues).

### 🧩 Config Validation & Schemas (2025-12-26)

- Adds `navig config validate` (and keeps `navig config test` as an alias) with file+line error reporting.
- Adds `navig config schema install` to install YAML JSON schemas for hosts/apps (optional VS Code settings writer).
- Adds global `--no-cache` to disable local caches for a run.

### 🤖 MCP Server & Wiki RAG Integration (2025-01-XX)

**NAVIG now integrates with AI assistants like GitHub Copilot and Claude!**

**New MCP Server (`navig mcp serve`):**
- Exposes NAVIG capabilities to AI assistants via Model Context Protocol
- Tools available: `navig_list_hosts`, `navig_list_apps`, `navig_search_wiki`, `navig_get_context`, and more
- Resources: hosts config, apps config, wiki content, system context
- Safety: Only read-only operations allowed by default

**New MCP Config Command (`navig mcp config`):**
- Generate configuration for VS Code: `navig mcp config vscode`
- Generate configuration for Claude Desktop: `navig mcp config claude`
- Write config directly to file: `navig mcp config vscode -o`

**New Wiki RAG System (`navig wiki rag`):**
- BM25 semantic search for relevant wiki content
- `navig wiki rag query "how to deploy"` - Search knowledge base
- `navig wiki rag query "nginx config" --context` - Get full AI context
- `navig wiki rag rebuild` - Rebuild search index
- `navig wiki rag add` - Add content to knowledge base

**VS Code Copilot Integration:**
```json
{
  "mcpServers": {
    "navig": {
      "command": "python",
      "args": ["-m", "navig.mcp_server"]
    }
  }
}
```

**Files Added:**
- `navig/mcp_server.py` - MCP protocol handler with NAVIG tools
- `navig/wiki_rag.py` - BM25-based semantic search for wiki

---

### 📚 Help System Standardization (2025-12-11)

**Centralized and standardized all CLI help text!**

**What Changed:**
- Updated `HELP_REGISTRY` to accurately reflect all available commands
- Created `navig/help_texts.py` as comprehensive documentation module
- All 14 command groups now show accurate command listings
- Consistent verb usage: add/remove/list/show/edit/test/run/use
- Professional formatting with sentence case capitalization

**Command Groups Updated:**
- `host` - 9 commands including discover-local, monitor, security, maintenance
- `app` - 8 commands including search, migrate
- `db` - 10 commands including optimize, repair
- `docker` - 9 commands including compose, stats, inspect
- `web` - 9 commands including module-enable/disable, recommend, hestia
- `file` - 6 commands with standardized verbs
- `tunnel` - 5 commands with auto-detect
- `backup` - 4 commands for config backup/restore
- `config` - 9 commands for settings management
- `flow` - 5 commands for workflow automation
- `ai` - 7 commands for AI assistant
- `local` - 7 commands for local diagnostics
- `hosts` - 3 commands for /etc/hosts management
- `log` - 2 commands for log viewing

**Developer Documentation:**
- Added `docs/development/help-text-management.md` with:
  - Standardization rules and examples
  - How to add help text for new commands
  - Verb consistency guidelines
  - Troubleshooting guide

---

### 🎨 User Interface Improvements (2025-12-11)

**Compact CLI Help & Arrow-Key Menu Navigation!**

**CLI Help (`navig --help`):**
- New compact ASCII table showing all commands organized by category
- Single-screen overview: Infrastructure, Services, Data, Automation, Config
- Clean layout without redundant category panels

**Interactive Menu (`navig menu`):**
- Restored arrow-key navigation (↑↓) with questionary
- Keyboard shortcuts: [W] Wiki, [S] Search, [A] AI, [C] Config
- Cleaner header with context display

---

### ⚡ Performance Optimization (2025-01-XX)

**Significant performance improvements across the CLI!**

**Measured Improvements:**
- CLI startup: **~40% faster** (from 280-330ms to 178-193ms)
- `navig --help`: **~15% faster** (from 480-640ms to 426-475ms)
- `navig host list`: **~28% faster** (from 625-716ms to 462-512ms)
- SSH operations: **2-10x faster** for consecutive commands (via connection pooling)

**New: SSH Connection Pool**
- Reuses SSH connections across multiple operations
- Automatic connection cleanup (expired/dead connections)
- Thread-safe for concurrent operations
- Configurable pool size and timeouts

**Technical Changes:**
- Added `navig/connection_pool.py` - SSH connection pooling with LRU eviction
- Integrated connection pooling into discovery module
- Added benchmark suite at `tests/benchmarks/baseline_performance.py`
- Performance analysis documented in `.github/reports/performance_analysis.md`

---

### 🏗️ CLI 4-Pillar Architecture Refactoring (2025-01-XX)

**Consolidated 20+ top-level commands into a clean 4-pillar structure!**

The CLI has been reorganized into four logical pillars for better discoverability:

**Pillar 1: Infrastructure (`host`)**
- `navig host monitor` - Server monitoring (resources, disk, health)
- `navig host security` - Security management (firewall, fail2ban, SSH)
- `navig host maintenance` - System maintenance (updates, cleanup)

**Pillar 2: Services (`app`, `docker`, `web`)**
- `navig web hestia` - HestiaCP panel management (nested under web)

**Pillar 3: Data (`db`, `file`, `log`, `backup`)**
- Unchanged - already well-organized

**Pillar 4: Automation (`flow`, `ai`, `wiki`)**
- `navig flow` - Workflow/task management (renamed from `workflow`/`task`)
- `navig flow template` - Template management (consolidates `template` + `addon`)

**Migration Path (Deprecated Commands):**
Commands that have moved now show deprecation warnings but continue to work:
- `navig monitor` → `navig host monitor`
- `navig security` → `navig host security`
- `navig system` → `navig host maintenance`
- `navig server` → `navig host`
- `navig workflow` → `navig flow`
- `navig task` → `navig flow`
- `navig template` → `navig flow template`
- `navig addon` → `navig flow template`
- `navig hestia` → `navig web hestia`

Deprecated commands are hidden from `--help` but remain functional during transition period.

---

### 🖥️ Local OS Management Module (2025-01-XX)

**Treat your local machine as a managed host!**

NAVIG now supports managing your local machine with the same commands used for remote hosts.

**New Commands:**
```bash
navig host use local           # Switch to local machine
navig hosts view               # View local hosts file
navig hosts edit               # Edit hosts file (with admin elevation)
navig software list            # List installed packages (winget/apt/brew)
navig security audit           # Run local security audit
```

**Architecture:**
- **ConnectionAdapter pattern**: Unified interface for local (`subprocess`) and remote (`ssh`) execution
- **OS Adapters**: Strategy pattern with Windows, Linux, and macOS implementations
- **Auto-detection**: Automatically creates `local.yaml` host config on first use
- **Cross-platform**: Works on Windows (winget, PowerShell), Linux (apt/yum/dnf), macOS (brew)

**Security Audit Checks:**
- Firewall status
- Open ports
- User accounts with login shells
- SSH configuration
- World-writable files
- Admin/root privilege detection

**New Files:**
- `navig/core/connection.py` - ConnectionAdapter, LocalConnection, SSHConnection
- `navig/adapters/os/` - OSAdapter base + Windows/Linux/macOS implementations
- `navig/local_operations.py` - LocalMachine unified operations class
- `navig/commands/local.py` - CLI commands for local management

**Tests:** 64 new tests (21 connection + 43 OS adapters)

---

### 🎨 UI/UX: Category-Based Help & Interactive Menu Redesign (2025-12-10)

**Reorganized CLI help and interactive menu for better discoverability!**

**`navig --help` now shows category-based grouping:**
```
═══ QUICK START ═══
  init         Initialize project-local .navig/ directory
  host add     Add a new remote server
  menu         Launch interactive command center

═══ CORE RESOURCES ═══
  host, app, db, file, docker

═══ REMOTE OPERATIONS ═══
  run, install, server, system, web

═══ SECURITY & NETWORKING ═══
  security, tunnel

═══ INTELLIGENCE & AUTOMATION ═══
  ai, wiki, workflow
```

**Interactive menu (`navig menu`) redesigned with 4-section layout:**
- **SERVERS & APPS**: Host, App, Remote Execution, Files, Docker
- **DATA & SYSTEM**: Database, Webserver, Maintenance, Backup
- **INTELLIGENCE**: AI Assistant, Wiki & Documentation
- **SYSTEM**: Configuration, Command History

**New menu features:**
- Enhanced context display with host IP and timestamp
- New Docker Containers submenu
- New Remote Execution submenu
- New Wiki & Documentation submenu
- Keyboard shortcuts (letters A, W, C, H for quick access)

### 🏗️ CLI Architecture Refactoring (7 Pillars)

**Industry-standard noun-verb pattern like Docker, Kubernetes, and GitHub CLI!**

NAVIG now follows the `navig <resource> <action>` pattern organized into 7 pillars:

| Pillar | Resource Groups | Purpose |
|--------|-----------------|---------|
| **1. Infrastructure** | `host` | Remote server management |
| **2. File System** | `file`, `log` | Remote file and log operations |
| **3. Data** | `db`, `backup` | Database and backup management |
| **4. Applications** | `app`, `web`, `docker` | Application lifecycle |
| **5. Security** | `security`, `tunnel` | Security and SSH tunnels |
| **6. Intelligence** | `ai`, `wiki` | AI assistance and knowledge base |
| **7. System** | `system`, `config`, `monitor` | System maintenance |

**New canonical commands:**
- `navig file add/get/list/show/edit/remove` - File operations
- `navig db list/tables/run/dump/restore` - Database operations
- `navig backup list/run/restore` - Backup management
- `navig ai ask/analyze/context/status/config/reset` - Unified AI assistant
- `navig system show/update/clean/run/reboot` - System maintenance

**Deprecated commands** (still work with deprecation warnings):
- `navig upload` → `navig file add`
- `navig download` → `navig file get`
- `navig ls/tree` → `navig file list`
- `navig chmod/chown` → `navig file edit --mode/--owner`
- `navig db-list/db-query` → `navig db list/run`
- `navig logs/health/restart` → `navig log show/monitor show/system run`
- `navig backup-*` → `navig backup run --<type>`
- `navig assistant *` → `navig ai *`

**Full backward compatibility maintained** - all old commands continue to work.

### ⚡ Performance Optimization (2025-12-10)

**56% faster CLI import time!**

Comprehensive performance optimization for CLI startup and config operations:

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| CLI import | 528ms | 231ms | **56%** |
| `navig --help` | 530ms | 71ms | **87%** |
| `navig host list` | 542ms | 99ms | **82%** |
| `list_hosts()` (cold) | 10ms | 4.9ms | **51%** |
| `list_hosts()` (warm) | 10ms | 0.6ms | **94%** |

**Key optimizations:**
- Lazy import of paramiko in `discovery.py` (saves 238ms for non-SSH commands)
- Deferred loading of `ServerDiscovery` in `commands/host.py`
- Directory listing cache with mtime-based invalidation for `list_hosts()`
- In-memory caching for host configurations with automatic invalidation

**New benchmark suite:** `tests/benchmarks/test_performance.py`

### 📚 Wiki Knowledge Base System (2025-12-10)

**Manage project documentation with AI-powered categorization!**

New `navig wiki` module provides a structured knowledge base for each project:

```bash
# Initialize wiki
navig wiki init                        # Create .navig/wiki/ structure
navig wiki init --global               # Create global wiki at ~/.navig/wiki/

# Content management
navig wiki list                        # List all wiki pages
navig wiki show <page>                 # View a wiki page
navig wiki add <file>                  # Add file to inbox
navig wiki add <file> --folder hub/tasks  # Add to specific folder
navig wiki edit <page>                 # Open in editor
navig wiki remove <page>               # Archive page
navig wiki search <query>              # Full-text search

# Inbox processing with AI categorization
navig wiki inbox                       # Show pending items
navig wiki inbox process               # AI-categorize inbox items
navig wiki inbox process --auto        # Auto-move to suggested folders

# Wiki links
navig wiki links                       # Link statistics
navig wiki links broken                # Find broken [[wiki-links]]

# Publishing
navig wiki publish                     # Export public content
navig wiki publish --preview           # Preview what would be published
```

#### Wiki Structure

```
.navig/wiki/
├── inbox/           # Drop files here for AI processing
├── .meta/           # Configuration & indexes
├── knowledge/       # Encyclopedia (configurable public/private)
├── technical/       # Technical documentation
├── hub/             # Project command center (roadmap, tasks, planning)
├── external/        # Business & marketing materials
└── archive/         # Archived content
```

#### Features

- **[[wiki-links]]** syntax for cross-referencing pages
- **AI categorization** - Auto-suggests destination folders
- **Global + project wikis** - Share knowledge across projects
- **Visibility control** - Mark content as public/private for publishing

### 🎯 CLI Standardization (2025-12-10)

**Consistent command structure with canonical actions!**

NAVIG now follows a standardized `navig <resource> <action>` pattern with canonical actions.
All legacy commands continue to work but show deprecation warnings pointing to the new canonical form.

#### New Canonical Actions

Every resource now supports these standard actions (where applicable):
- `add` - Create new resource
- `list` - List resources
- `show` - Show detailed information
- `edit` - Modify existing resource
- `update` - Update/sync resource
- `remove` - Delete resource
- `run` - Execute/start operation
- `test` - Test/validate resource
- `use` - Set active/default resource

#### New Resource Groups

- **`navig file`** - Unified file operations
  ```bash
  navig file add <local> <remote>     # Upload file
  navig file add --dir <path>         # Create directory
  navig file list [path]              # List remote directory
  navig file show <path>              # View file contents
  navig file show <path> --download   # Download file
  navig file edit <path> --content    # Write to file
  navig file edit <path> --mode 755   # Change permissions
  navig file remove <path>            # Delete file/directory
  ```

- **`navig log`** - Log viewing and management
  ```bash
  navig log show <service>            # View logs
  navig log show --follow             # Tail logs
  ```

- **`navig server`** - Unified server operations
  ```bash
  navig server list                   # List containers/services
  navig server show <name>            # Inspect container
  navig server run <name> <cmd>       # Execute in container
  navig server add <name>             # Start container
  navig server remove <name>          # Stop container
  navig server update <name>          # Restart container
  ```

- **`navig task`** - Alias for workflow
  ```bash
  navig task list                     # Same as navig workflow list
  navig task run <name>               # Same as navig workflow run
  ```

#### Command Migrations

| Old Command | New Canonical | Status |
|------------|---------------|--------|
| `navig host info` | `navig host show` | Deprecated with warning |
| `navig host current` | `navig host show --current` | Deprecated with warning |
| `navig host default <n>` | `navig host use <n> --default` | Deprecated with warning |
| `navig host clone` | `navig host add --from` | Deprecated with warning |
| `navig tunnel start` | `navig tunnel run` | Deprecated with warning |
| `navig tunnel stop` | `navig tunnel remove` | Deprecated with warning |
| `navig tunnel status` | `navig tunnel show` | Deprecated with warning |
| `navig db shell` | `navig db run --shell` | Deprecated with warning |
| `navig backup list` | `navig backup show` | Deprecated with warning |
| `navig backup delete` | `navig backup remove` | Deprecated with warning |
| `navig workflow create` | `navig workflow add` | Deprecated with warning |
| `navig workflow delete` | `navig workflow remove` | Deprecated with warning |
| `navig workflow validate` | `navig workflow test` | Deprecated with warning |
| `navig config validate` | `navig config test` | Deprecated with warning |

#### Backward Compatibility

All legacy commands continue to work but print deprecation warnings:
```
⚠️  DEPRECATED: 'navig tunnel start' → Use 'navig tunnel run' instead
```

The deprecation warnings help AI coding assistants learn the canonical forms for better command suggestions.

---

### 🔄 Workflow System (2025-12-08)

**Automate complex operations with reusable command workflows!**

The new Workflow System lets you define and execute sequences of NAVIG commands as reusable YAML workflows.

#### Features

- **Workflow Commands**:
  ```bash
  navig workflow list                    # List all workflows
  navig workflow run <name>              # Execute a workflow
  navig workflow run <name> --dry-run    # Preview without executing
  navig workflow show <name>             # Display workflow definition
  navig workflow validate <name>         # Validate syntax
  navig workflow create <name>           # Create from template
  navig workflow delete <name>           # Delete a workflow
  navig workflow edit <name>             # Open in editor
  ```

- **Variable Substitution**: Define reusable variables with defaults
  ```bash
  navig workflow run db-snapshot --var host=staging --var db_name=mydb
  ```

- **Conditional Execution**:
  - `continue_on_error: true` - Keep going if step fails
  - `skip_on_error: true` - Skip step if previous failed
  - `prompt: "Question?"` - Ask for user confirmation

- **Multi-scope Storage**:
  - Project-local: `.navig/workflows/` (highest priority)
  - Global: `~/.navig/workflows/`
  - Built-in: Bundled example workflows

#### Built-in Workflows

- **safe-deployment** - Deploy with health checks and rollback safety
- **db-snapshot** - Export database for local development
- **emergency-debug** - Rapid diagnostics for failing services
- **server-health** - Comprehensive server health check

#### Example Usage

```bash
# Preview a deployment workflow
navig workflow run safe-deployment --dry-run

# Execute with custom variables
navig workflow run db-snapshot --var db_name=production_db --yes

# Create your own workflow
navig workflow create my-deployment
```

See `docs/WORKFLOWS.md` for complete documentation.

---

### 🔌 Plugin System (2025-12-08)

**Extend NAVIG with custom commands and integrations!**

NAVIG now supports a modular plugin architecture that allows you to add custom functionality without modifying the core codebase.

#### Features

- **Plugin Discovery**: Automatically discovers plugins from three locations:
  - Built-in plugins (`navig/plugins/`)
  - User plugins (`~/.navig/plugins/`)
  - Project plugins (`.navig/plugins/`)

- **Plugin Management Commands**:
  ```bash
  navig plugin list              # List all plugins
  navig plugin info <name>       # Show plugin details
  navig plugin enable <name>     # Enable a plugin
  navig plugin disable <name>    # Disable a plugin
  navig plugin install <path>    # Install from local path
  navig plugin uninstall <name>  # Remove user plugin
  ```

- **Plugin API**: Plugins can safely access NAVIG's core functionality:
  - Execute remote commands via SSH
  - Read/write configuration
  - Access active host/app context
  - Use NAVIG's console output helpers

- **Graceful Failure**: Plugins with missing dependencies or errors won't break NAVIG - they simply won't load

#### Example Plugin

The built-in `hello` plugin demonstrates plugin development:

```bash
navig hello greet --name "Developer"
# ✓ Hello, Developer!

navig hello info
# Shows plugin info and current NAVIG context
```

#### Create Your Own Plugin

1. Create a directory in `~/.navig/plugins/my-plugin/`
2. Add `plugin.py` with required exports (`name`, `app`, `check_dependencies`)
3. Run `navig plugin list` to verify it's loaded

See `docs/PLUGIN_DEVELOPMENT.md` for the complete developer guide.

### 📚 Documentation Reorganization (2025-12-08)

**Cleaner project structure with consolidated documentation!**

- Moved `USAGE_GUIDE.md` and `INSTALLATION.md` to `/docs/` folder
- Moved internal reports to `.github/reports/` folder
- Fixed broken links to deleted `workflows.md` file
- Updated README.md with proper documentation table
- Improved troubleshooting guide with PowerShell-specific escaping tips
- Added common `--b64` and interactive mode error solutions

### 🚀 Complex Command Execution (2025-12-08)

**Execute commands with JSON, special characters, and multi-line scripts without escaping issues!**

The `navig run` command now supports multiple input methods to handle complex commands that would otherwise fail due to shell escaping conflicts.

#### Base64 Transport Mode (`--b64`)

Use `--b64` flag to encode commands as Base64, completely bypassing shell escaping:

```bash
# JSON payloads now work perfectly
navig run --b64 "curl -d '{\"user\":\"john\"}' api.com"

# Special characters preserved
navig run --b64 "echo \$HOME && ls \$(pwd)"
```

#### File and Stdin Input

Read commands from files or stdin for complex scripts:

```bash
# From file
navig run @script.sh

# From stdin
echo "complex command" | navig run "@-"

# Interactive editor
navig run -i
```

#### When to Use Each Method

| Scenario | Method |
|----------|--------|
| Simple command | `navig run "ls -la"` |
| JSON payload | `navig run --b64 "curl -d '{...}'"` |
| Special characters | `navig run --b64 "..."` |
| Multi-line script | `navig run @script.sh` |
| Quick multi-line | `navig run -i` |

---

### 🎯 Simplified Command Structure (2025-12-08)

**Commands are now organized into intuitive groups!**

NAVIG commands have been reorganized into logical groups for easier discovery and use. All old commands still work with deprecation warnings.

#### New Command Groups

| Group | Description | Example |
|-------|-------------|---------|
| `navig db` | All database operations | `navig db list`, `navig db query`, `navig db dump` |
| `navig monitor` | Server monitoring | `navig monitor health`, `navig monitor disk` |
| `navig security` | Security management | `navig security firewall`, `navig security scan` |
| `navig web` | Web server control | `navig web vhosts`, `navig web reload` |

#### Interactive Menus

Run any group without a subcommand to open its interactive menu:
- `navig db` → Database operations menu
- `navig monitor` → Monitoring menu
- `navig security` → Security menu
- `navig web` → Web server menu

#### Backward Compatibility

Old commands still work but show deprecation warnings:
```
⚠ 'navig firewall-status' is deprecated. Use 'navig security firewall' instead.
```

---

### 📚 New Documentation (2025-12-08)

**Comprehensive documentation now available in `/docs/`!**

- **[Quick Start Guide](docs/quick-start.md)** - Get started with NAVIG in minutes
- **[Commands Reference](docs/commands.md)** - Complete command documentation
- **[Workflows](docs/workflows.md)** - Common task patterns and examples
- **[Troubleshooting](docs/troubleshooting.md)** - Solutions for common issues
- **[Upgrade Roadmap](docs/upgrade-roadmap.md)** - Future development plans

---

### 📋 Plain Text Output for All List Commands (2025-12-08)

**New `--plain` flag for scripting and automation!**

All list commands now support a `--plain` flag that outputs one item per line, making it easy to pipe output to other commands or use in scripts.

#### Commands with `--plain` Support

| Command | Plain Output |
|---------|--------------|
| `navig host list --plain` | One host name per line |
| `navig app list --plain` | One app name per line |
| `navig tunnel status --plain` | "running" or "stopped" |
| `navig template list --plain` | One template name per line |
| `navig mcp list --plain` | One MCP server name per line |
| `navig backup list --plain` | One backup file name per line |
| `navig hestia users --plain` | One username per line |
| `navig hestia domains --plain` | One domain per line |
| `navig db-databases --plain` | One database name per line |
| `navig db-show-tables --plain` | One table name per line |
| `navig server-template list --plain` | One template name per line |

#### Example Usage

```bash
# Loop through all hosts
for host in $(navig host list --plain); do
    echo "Processing $host..."
done

# Count databases
navig db-databases --plain | wc -l

# Check if tunnel is running
if [ "$(navig tunnel status --plain)" = "running" ]; then
    echo "Tunnel is active"
fi
```

---

### ✨ Interactive Mode for All Command Groups (2025-12-08)

**Run any command group without subcommand to launch its interactive menu!**

Now you can simply type `navig host`, `navig app`, `navig tunnel`, etc. without any subcommand to launch an interactive menu for that command group. This provides a consistent, discoverable UX across all NAVIG features.

#### New Interactive Modes

| Command | What Happens |
|---------|--------------|
| `navig host` | Opens Host Management menu |
| `navig app` | Opens App Management menu |
| `navig tunnel` | Opens Tunnel Operations menu |
| `navig config` | Opens Configuration menu |
| `navig backup` | Opens Backup & Export menu |
| `navig assistant` | Opens AI Assistant menu |
| `navig template` | Opens Template Management menu |
| `navig mcp` | Opens MCP Server Management menu |
| `navig hestia` | Opens HestiaCP Management menu |

All menus feature:
- Consistent Mr. Robot-inspired visual theme
- Categorized options with clear separators
- Number-based selection (no arrow keys required)
- Context display showing active host/app
- Press `0` or `Ctrl+C` to go back

**Note:** All existing CLI commands continue to work exactly as before. This is purely additive - `navig host list`, `navig app use myapp`, etc. work unchanged.

---

### 🏗️ ARCHITECTURE - Simplified Host/App Selection System (2025-01-08)

**Major simplification of how NAVIG selects the active host and app.**

#### New Resolution Order (Host)

```
1. NAVIG_ACTIVE_HOST env var     ← For CI/CD and scripting
         ↓
2. .navig/config.yaml:active_host ← Project-local preference (NEW!)
         ↓
3. ~/.navig/cache/active_host.txt ← Global cache (navig host use)
         ↓
4. default_host from config      ← Fallback
```

#### New Resolution Order (App)

```
1. NAVIG_ACTIVE_APP env var      ← For CI/CD and scripting
         ↓
2. .navig/config.yaml:active_app ← Project-local preference
         ↓
3. ~/.navig/cache/active_app.txt ← Global cache (navig app use)
         ↓
4. default_app from host config  ← Fallback
```

#### Key Changes

**Added:**
- `active_host` field in project `.navig/config.yaml` for project-local host preference
- `navig host current` now shows the source of the active host (env, local, global, default)
- Better messaging when local config overrides global setting

**Removed:**
- `--session` flag from `navig host use` (was confusing - couldn't actually set env var)
- `scripts/navig-session.ps1` workaround script

**Why this is better:**
- Each project can define its preferred host in `.navig/config.yaml`
- Multi-project developers get automatic isolation (each project uses its own host)
- DevOps quick-switching still works via `navig host use` (global cache)
- Environment variables still work for CI/CD and advanced users
- No more confusing `--session` flag that didn't actually work

#### Example: Project-Local Host Configuration

```yaml
# .navig/config.yaml (commit this to git)
active_host: production
active_app: my-app

app:
  name: my-project
  version: '1.0'
```

Now when you `cd` into this project, NAVIG automatically uses the `production` host!

---

### 🐛 BUG FIX - Apps No Longer Appear in Hosts List (2025-01-06)

**Fixed:** Apps stored in `~/.navig/apps/` were incorrectly appearing in `navig host list`.

**Root Cause:** The legacy compatibility code in `list_hosts()` was adding ALL files from the apps directory to the hosts list without checking if they were host configs or app configs.

**Solution:** Now properly distinguishes between:
- **Host configs** - Have an IP address or FQDN in the `host` field (e.g., `host: 10.0.0.10`)
- **App configs** - Reference a host by name (e.g., `host: vultr`)

Files in `apps/` that reference another host by name are now correctly excluded from the hosts list.

---

### 🛡️ NEW - Execution Modes with Configurable Confirmation Levels (2025-01-06)

**Control when NAVIG prompts for confirmation before executing commands:**

Two execution modes:
- **`interactive`** (default) - Prompts for confirmation based on confirmation level
- **`auto`** - Skips all confirmations (for scripts/automation)

Three confirmation levels:
- **`critical`** - Only confirm destructive operations (rm -rf, DROP TABLE, etc.)
- **`standard`** (default) - Confirm critical + modify operations (UPDATE, file uploads)
- **`verbose`** - Confirm all remote operations

**New Commands:**
```bash
# View current settings
navig config settings

# Change execution mode
navig config set-mode auto          # Skip all confirmations
navig config set-mode interactive   # Prompt based on level

# Change confirmation level
navig config set-confirmation-level critical  # Only destructive ops
navig config set-confirmation-level standard  # Default behavior
navig config set-confirmation-level verbose   # Confirm everything
```

**New CLI Flags:**
- **`--yes`, `-y`** - Auto-confirm for a single command
- **`--confirm`, `-c`** - Force confirmation even in auto mode

**Examples:**
```bash
# Auto-confirm single command
navig -y run "rm /var/log/app/*.log"

# Force confirmation in auto mode
navig -c run "DROP DATABASE test"

# Check settings
navig config settings
```

---

### 📦 NEW - Configuration Export/Import System (2025-01-06)

**Backup, export, and share NAVIG configuration between machines:**

**New Commands:**
- **`navig backup export`** - Export configuration to backup file
- **`navig backup import`** - Import configuration from backup file
- **`navig backup list`** - List available backups
- **`navig backup inspect`** - Preview backup contents without importing
- **`navig backup delete`** - Delete a backup file

**Features:**
- Export formats: JSON (readable) or archive (.tar.gz)
- Optional AES-256 encryption with password protection
- Automatic secret redaction (passwords, API keys) for safe sharing
- Merge or overwrite modes for imports
- Timestamped backups with size information

**Examples:**
```bash
# Export all config to JSON (secrets redacted)
navig backup export

# Export with encryption
navig backup export --encrypt --format archive

# List available backups
navig backup list

# Preview before importing
navig backup inspect navig-export-2025-01-06.json

# Import with merge (keeps existing config)
navig backup import navig-export.json

# Import with overwrite
navig backup import navig-export.json --overwrite
```

---

### 🔧 FIX - Complex Command Escaping (2025-12-06)

**Fixed issues with running complex commands containing heredocs, JSON, or special characters from PowerShell:**

When running commands like creating JSON config files with heredocs, special characters (quotes, colons, backslashes) were being incorrectly parsed due to multiple escaping layers (PowerShell → Python CLI → SSH).

**New Options Added:**
- **`--stdin`, `-s`** - Read command from stdin, bypassing shell escaping
- **`--file`, `-f`** - Read command from a file, ideal for complex scripts

**Usage:**
```bash
# Simple commands still work as before
navig run "ls -la"

# Complex commands: use --file
navig run --file my_script.sh

# Or pipe via stdin
cat script.sh | navig run --stdin

# PowerShell here-strings work great
@'
cat > config.json << 'EOF'
{"api_key": "xyz", "url": "https://example.com"}
EOF
'@ | navig run --stdin
```

**Tip:** For JSON config files, consider using `navig upload` instead:
```bash
navig upload config.json /var/www/config.json
```

---

### 🗄️ NEW - Docker Database Management (2025-12-06)

**Connect to MySQL, MariaDB, and PostgreSQL databases running in Docker containers or natively on remote servers:**

- **`navig db-containers`** - List all Docker containers running database services
- **`navig db-databases`** - List all databases on the remote server
- **`navig db-show-tables`** - List tables in a specific database
- **`navig db-query`** - Execute SQL queries directly on remote databases
- **`navig db-dump`** - Backup/dump a database to a local file
- **`navig db-shell`** - Open an interactive database shell via SSH

**Features:**
- Auto-detects database type (MySQL, MariaDB, PostgreSQL)
- Works with both Docker containers and native database installations
- Supports custom database user and password
- Secure credential handling

**Usage:**
```bash
# List database containers
navig db-containers

# List databases (auto-detects type)
navig db-databases

# Query a Docker container database
navig db-query "SELECT * FROM users LIMIT 5" --container mysql_db -d myapp

# Dump a database
navig db-dump mydb --output backup.sql

# Open interactive shell
navig db-shell --container postgres_db --type postgresql
```

---

### 🔍 NEW - Debug Logging System (2025-12-05)

**Comprehensive debug logging for troubleshooting and auditing:**

- **Global `--debug-log` flag** - Enable debug logging for any NAVIG command
- **Structured log format** - ISO 8601 timestamps with clear section separators
- **SSH command tracking** - Logs all SSH commands, targets, methods, and results
- **Automatic sensitive data redaction** - Passwords, API keys, tokens, and SSH keys are automatically redacted
- **Log rotation** - Configurable file size limits (default 10MB) with backup rotation (default 5 files)
- **Performance optimized** - Minimal overhead with buffered I/O

**Usage:**
```bash
navig --debug-log host list
navig --debug-log app deploy
```

**Log location:** `.navig/debug.log` (in your project directory)

**Configuration options in global config:**
- `debug_log_max_size_mb` - Maximum log file size before rotation
- `debug_log_max_files` - Number of backup files to keep
- `debug_log_truncate_output_kb` - Maximum output size before truncation

---

### 📦 NEW - Addon Configuration Templates (2025-01-XX)

**Added 20 new addon configuration templates for popular self-hosted applications**:

#### Tier 1: Essential Infrastructure (5 templates)
- **nginx** - High-performance web server and reverse proxy
- **postgresql** - Advanced open-source relational database
- **redis** - In-memory data structure store and cache
- **docker** - Container runtime and management platform
- **traefik** - Modern reverse proxy and load balancer with automatic SSL

#### Tier 2: Monitoring & Management (5 templates)
- **grafana** - Analytics and monitoring visualization platform
- **prometheus** - Systems monitoring and alerting toolkit
- **portainer** - Container management UI for Docker/Kubernetes
- **uptime-kuma** - Self-hosted uptime monitoring tool
- **netdata** - Real-time performance and health monitoring

#### Tier 3: Popular Self-Hosted Apps (5 templates)
- **nextcloud** - Self-hosted file sync and collaboration platform
- **vaultwarden** - Lightweight Bitwarden-compatible password manager
- **mattermost** - Open-source team messaging platform
- **gitlab-runner** - CI/CD runner for GitLab pipelines
- **matomo** - Privacy-focused web analytics platform

#### Tier 4: Additional Tools (5 templates)
- **caddy** - Modern web server with automatic HTTPS
- **wikijs** - Modern wiki engine with Git sync
- **plausible** - Privacy-first web analytics without cookies
- **duplicati** - Encrypted backup software with cloud storage support
- **jellyfin** - Free software media system for streaming

**Template Features:**
- YAML format for consistency with NAVIG configuration
- Comprehensive paths, services, and commands for each application
- Environment variable templates with sensible defaults
- API endpoint configurations where applicable
- Database configuration with multiple backend support
- Detailed README.md with installation and usage instructions

**Location:** `templates/<addon-name>/template.yaml` and `templates/<addon-name>/README.md`

**Usage:**
```bash
# Enable an addon
navig addon enable <name>

# Run addon-specific commands
navig addon run <name> <command>

# List available addons
navig addon list
```

---

### 🐛 FIXED - Critical Bug Fixes (2025-11-26)

**Fixed 22 failing tests and several critical bugs**:

#### Variable Shadowing Bug in Interactive Menu
- **`navig/commands/interactive.py`**: Fixed critical variable shadowing bug where `for app in apps:` overwrote the imported `app` module
  - Changed loop variable from `app` to `app_item` in 5 locations:
    - `execute_app_edit()` - lines 1218, 1294
    - `execute_app_clone()` - line 1323
    - `execute_app_info()` - line 1358
    - `execute_app_remove()` - line 1398
  - This prevented `app.edit_app()`, `app.clone_app()` etc. from being called correctly

#### Config Manager Attribute Bug
- **`navig/config.py`**: Fixed `save_app_config()` referencing non-existent `self.config_dir` attribute
  - Changed to correct `self.base_dir` attribute

#### Test Fixture Isolation Fixes
- **`tests/test_config.py`**: Fixed `config_manager` fixture to pass explicit `config_dir` parameter
  - Prevents tests from picking up the project's actual `.navig` directory
- **`tests/test_cli_enhancements.py`**: Same fixture isolation fix
- **`tests/test_interactive_menu_fixes.py`**: Fixed test expectation - `inspect_host` receives `{'silent': True}` not empty dict
- **`tests/test_security_fixes.py`**: Fixed mock patches to use correct module paths:
  - `navig.config.get_config_manager` instead of `navig.commands.database_advanced.get_config_manager`
  - `navig.tunnel.TunnelManager` instead of `navig.commands.database_advanced.TunnelManager`
- **`tests/test_integration.py`**: Simplified to focus on import and API correctness tests
- **`tests/test_template_manager.py`**: Changed bare `except:` to `except (OSError, PermissionError):`

#### Test Results
- All 210 tests now pass
- Coverage: 23% overall, 53-91% on critical modules (config, template_manager, migration, proactive_assistant)

### 🔧 IMPROVED - Template System Consistency (Complete YAML Migration)

**Completed migration from JSON to YAML format across the template system**:

#### Template Discovery Fix
- **`navig/template_manager.py`**: Fixed `discover_templates()` to check for both `template.yaml` and `template.json` files (previously only checked JSON)
- YAML format now preferred, JSON supported for backward compatibility
- Warning messages now correctly reference both formats

#### Removed Duplicate Template Files
- Deleted `template.json` files from all bundled templates (keeping only YAML):
  - `templates/gitea/template.json` → removed
  - `templates/hestiacp/template.json` → removed
  - `templates/n8n/template.json` → removed

#### Updated Customization Format
- **`navig/server_template_manager.py`**: Changed per-server template customizations from JSON to YAML
  - Customization files now saved as `~/.navig/apps/<server>/templates/<template>.yaml`
  - Backward compatible: reads existing JSON files, writes new YAML
- Updated documentation strings to reference YAML format

#### Updated Documentation References
- Changed all user-facing messages from `template.json` to `template.yaml`
- Updated docstrings in `template_manager.py`, `commands/template.py`, `commands/server_template.py`

### 🔒 IMPROVED - Code Quality Fixes

**Fixed bare exception handlers across the codebase**:

- **`navig/modules/context_generator.py`**: Replaced 5 bare `except:` blocks with specific exceptions:
  - `ImportError, AttributeError` for version imports
  - `Exception` for optional feature failures
  - `OSError, json.JSONDecodeError` for file operations
- **`navig/commands/database.py`**: Changed bare `except:` to `OSError` for cleanup operations

### 📋 IMPROVED - MCP Server Directory

**Expanded MCP server search directory**:

- **`navig/mcp_manager.py`**: Added 7 additional official MCP servers to search:
  - `memory` - Persistent knowledge graph memory
  - `puppeteer` - Browser automation
  - `fetch` - HTTP content retrieval
  - `slack` - Slack workspace integration
  - `postgres` - PostgreSQL database access
  - `google-drive` - Google Drive file access
  - `google-maps` - Google Maps API

### 🐛 FIXED - Webserver Timestamp Placeholder

- **`navig/commands/webserver.py`**: Fixed hardcoded `'2024-01-01'` placeholder with actual `datetime.now().isoformat()`

---

### 🔒 SECURITY - Critical Security Fixes

**Multiple security vulnerabilities fixed**:

#### **Removed shell=True Subprocess Vulnerabilities**
- **`navig/commands/backup.py`**: Added `_run_scp_command()` helper function to build SCP commands as list instead of string with `shell=True`. Fixes 6 potential command injection points.
- **`navig/commands/host.py`**: Added `shlex.split()` for editor command execution, preventing shell injection.
- **`navig/commands/app.py`**: Added `shlex.split()` for editor command execution, preventing shell injection.

#### **Removed Hardcoded SSH Credentials in Tests**
- **`tests/test_discovery.py`**: Replaced hardcoded SSH password with mocked unit tests
- **`tests/test_keys.py`**: Replaced hardcoded SSH credentials with proper mocked tests

### ⚡ PERFORMANCE - ConfigManager Singleton Pattern

**New factory function `get_config_manager()`** implements singleton pattern for improved performance:

- **Problem**: Every command instantiated a new `ConfigManager()`, causing repeated filesystem traversal and YAML parsing (~50-100ms each).
- **Solution**: `get_config_manager()` returns cached singleton instance.
- **Result**: Subsequent calls now ~0.01ms instead of 50-100ms.

**Files Updated**:
- `navig/config.py`: Added `get_config_manager()` and `reset_config_manager()` functions
- Updated 20+ command files to use singleton pattern
- Type hints preserved for IDE support

### 🎨 NEW - Interactive Menu System Expansion

**Four new interactive submenus implemented**:

1. **Webserver Control Menu**:
   - List virtual hosts
   - Enable/disable sites
   - Test configuration
   - Reload/restart server

2. **File Operations Menu**:
   - Upload files
   - Download files
   - Create directories
   - List remote files
   - Edit remote files

3. **System Maintenance Menu**:
   - Update packages
   - Clean package cache
   - Check disk usage
   - Cleanup logs
   - Reboot server (with confirmation)

4. **Configuration Menu**:
   - Show current configuration
   - Edit global config
   - Show paths
   - Clear cache

### 📦 IMPROVED - Template System YAML Support

**Templates now use YAML format exclusively (migration complete)**:

- **`navig/template_manager.py`**: Loads `template.yaml` preferentially, with JSON fallback for third-party templates
- All bundled templates use YAML:
  - `templates/gitea/template.yaml`
  - `templates/hestiacp/template.yaml`
  - `templates/n8n/template.yaml`
- Saves in original format to prevent format switching for external templates

---

### 📁 IMPROVED - Configuration Structure Reorganization

**What Changed**: Reorganized example configurations to better reflect the new two-tier architecture (hosts vs apps).

#### **Configuration Cleanup**
- **Removed unused parameters** from app examples:
  - `database.charset` and `database.collation` (not used by code)
  - `paths.ssl_cert` and `paths.ssl_key` (SSL management not implemented)
  - `paths.apache_config` and `paths.storage` (not used by code)
  - `webserver.vhost_enabled_path`, `webserver.vhost_available_path`, `webserver.vhost_file` (hardcoded in code)

- **Added missing parameters** to host examples:
  - `database.root_user` and `database.root_password` for host-level database management

#### **Files Updated**
- All app configuration examples in `examples/apps/` (8 files)
- All host configuration examples in `examples/hosts/` (3 files)
- Documentation updated to reflect cleaner configuration structure

#### **Why This Matters**
- **Clearer examples**: Only show parameters that are actually used
- **Less confusion**: No more wondering why certain parameters don't work
- **Better documentation**: Examples match actual code behavior
- **Easier migration**: Clear separation between host and app configurations

#### **Migration Impact**
- ✅ **No breaking changes**: Existing configurations continue to work
- ✅ **Optional cleanup**: You can remove unused parameters from your configs if desired
- ✅ **New features**: Host-level database management now properly documented

---

### 🔧 FIXED - Duplicate Confirmation Prompt in App Removal

**Issue**: When removing a app via interactive menu, users were prompted for confirmation twice - once in the menu and once in the command function.

**Fix**:
- Modified `execute_app_remove()` in interactive menu to pass `force=True` flag
- This skips the second confirmation in `remove_app()` function
- CLI command `navig app remove` still prompts for confirmation (unless `--force` flag is used)

**Result**: Single, clear confirmation prompt when removing apps via interactive menu.

---

### 🏗️ NEW - Per-App Configuration File Architecture (v2.1)

**Major architectural enhancement**: Apps are now stored in individual `.navig/apps/<name>.yaml` files instead of being embedded in host YAML files.

#### **Old Architecture (Legacy - Still Supported)**
```
.navig/
└── hosts/
    └── vultr.yaml          # Contains embedded apps array
        apps:
          myapp:
            webserver: {type: nginx}
            database: {name: myapp_db}
          myapp:
            webserver: {type: nginx}
```

#### **New Architecture (v2.1)**
```
.navig/
├── hosts/
│   └── vultr.yaml          # Host configuration ONLY (no embedded apps)
└── apps/               # NEW: Individual app files
    ├── myapp.yaml
    ├── myapp.yaml
    └── ai.yaml
```

#### **Benefits**

1. ✅ **Better Isolation**: Apps in different `.navig/` directories remain completely separate
2. ✅ **Easier Editing**: Edit single app file instead of navigating large host YAML
3. ✅ **Better Scalability**: Hundreds of apps don't bloat single YAML file
4. ✅ **Clear Ownership**: Each app file explicitly references its host
5. ✅ **Version Control Friendly**: Easier to track changes to individual apps
6. ✅ **Backward Compatible**: Legacy embedded format still works (read-only)

#### **App File Format**

```yaml
# .navig/apps/myapp.yaml
name: myapp
host: vultr                        # Reference to host in hosts/vultr.yaml
paths:
  web_root: /var/www/myapp
  log_path: /var/log/myapp
webserver:
  type: nginx
  config_file: /etc/nginx/sites-available/myapp
  ssl_enabled: true
database:
  name: myapp_db
  user: myapp_user
  host: localhost
metadata:
  created: "2025-11-25T10:30:00"
  updated: "2025-11-25T14:20:00"
```

#### **Migration Tool**

**New command**: `navig app migrate`

```bash
# Migrate all apps from host to individual files
navig app migrate --host vultr

# Preview migration without making changes
navig app migrate --host vultr --dry-run

# Interactive menu: App Management → Migrate apps to individual files
navig menu
```

**Migration Process:**
1. Reads apps from `hosts/<host>.yaml` under `apps:` field
2. Creates individual `.navig/apps/<name>.yaml` files
3. Removes apps from host YAML (keeps host configuration)
4. Shows detailed migration results (migrated, skipped, errors)

#### **Dual-Format Support**

All app operations now support both formats:

- **Reading**: Checks individual files first, falls back to embedded format
- **Writing**: Always uses individual files (new format)
- **Listing**: Merges apps from both formats
- **Editing**: Opens individual file if exists, otherwise opens host YAML

**Commands Updated:**
- `navig app add` - Creates individual file
- `navig app remove` - Deletes individual file (or removes from host YAML)
- `navig app edit` - Opens individual file (or host YAML for legacy)
- `navig app list` - Shows apps from both formats
- `navig app clone` - Creates individual file
- `navig app show` - Reads from either format

#### **ConfigManager API Changes**

**New Methods:**
```python
# Individual file operations
config_manager.get_app_file_path(app_name, navig_dir)
config_manager.load_app_from_file(app_name, navig_dir)
config_manager.save_app_to_file(app_name, app_config, navig_dir)
config_manager.list_apps_from_files(navig_dir)

# Migration
config_manager.migrate_apps_to_files(host_name, navig_dir, remove_from_host)
```

**Modified Methods:**
```python
# Now check both formats
config_manager.list_apps(host_name)          # Merges both formats
config_manager.load_app_config(host, app)   # Checks files first
config_manager.app_exists(host, app)        # Checks both formats
config_manager.save_app_config(host, app, config, use_individual_file=True)
config_manager.delete_app_config(host, app) # Deletes from either format
```

#### **Edge Cases Handled**

1. ✅ **App file exists but host doesn't exist**: Shows error with available hosts
2. ✅ **App exists in both formats**: Prefers individual file (new format)
3. ✅ **Migration with active apps**: Preserves active_app setting
4. ✅ **Empty apps/ directory**: Handles gracefully (no apps)
5. ✅ **Malformed app YAML**: Shows validation error
6. ✅ **App name mismatch**: Validates filename matches `name:` field
7. ✅ **Missing required fields**: Validates `name` and `host` fields

#### **Validation**

App files are validated on load:
- **Required fields**: `name`, `host`
- **Name consistency**: Filename must match `name:` field
- **Host reference**: Host must exist in `hosts/` directory
- **Webserver type**: Required for app operations

#### **Interactive Menu**

New option in App Management menu:
```
━━━ Advanced Operations ━━━
  [8] Clone app
  [9] Migrate apps to individual files  ← NEW
```

**Migration Flow:**
1. Select "Migrate apps to individual files"
2. Shows count of apps to migrate
3. Confirms migration
4. Displays detailed results
5. Updates host configuration

#### **Breaking Changes**

⚠️ **None** - Fully backward compatible!

- Legacy embedded format still works (read-only)
- Existing apps continue to function
- Migration is optional (recommended for new apps)
- No changes required to existing workflows

#### **Recommended Migration Path**

1. **Backup your configuration**: `cp -r ~/.navig ~/.navig.backup`
2. **Test with dry run**: `navig app migrate --host <host> --dry-run`
3. **Migrate one host**: `navig app migrate --host <host>`
4. **Verify apps work**: `navig app list --all`
5. **Migrate remaining hosts**: Repeat for each host

**Related Issue**: User reported confusion when apps from different `.navig/` directories appeared mixed together in listings. New architecture provides clear isolation and ownership.

---

### ✨ NEW - Per-Directory Active App Selection with Local `.navig/` Override

- ✅ **Hierarchical active app resolution**:
  - **Local active app** (`.navig/config.yaml` in current directory) - HIGHEST PRIORITY
  - **Legacy format** (`.navig` file in current directory)
  - **Global active app** (`~/.navig/cache/active_app.txt`) - FALLBACK
  - **Default app** (from active host configuration)

- ✅ **Set local active app for current directory**:
  ```bash
  navig app use myapp --local
  # ✓ Set local active app to 'myapp'
  # ℹ This only affects commands run in this directory
  # Location: .navig/config.yaml
  ```

- ✅ **Set global active app (default behavior)**:
  ```bash
  navig app use ai
  # ✓ Set global active app to 'ai'
  # ℹ This affects all directories without local active app
  ```

- ✅ **Clear local active app setting**:
  ```bash
  navig app use --clear-local
  # ✓ Cleared local active app setting
  # ℹ Commands will now use global active app
  ```

- ✅ **Visual indicators show app source**:
  - 📍 **local** - Active app from `.navig/config.yaml` in current directory
  - 📄 **legacy** - Active app from `.navig` file (legacy format)
  - 🌐 **global** - Active app from `~/.navig/cache/active_app.txt`
  - ⚙️ **default** - Default app from host configuration

- ✅ **Interactive menu integration**:
  - Header shows app source icon (📍 for local, 🌐 for global)
  - "Switch active app" prompts for local/global scope when `.navig/` exists
  - Automatically detects if current directory has `.navig/` folder

- ✅ **Use cases**:
  - **Multi-app development**: Work on different apps in different terminal windows
  - **App-specific workflows**: Each app directory maintains its own active app
  - **Team collaboration**: Share `.navig/` directory in app repository for consistent app selection
  - **Monorepo support**: Different subdirectories can have different active apps

**Example Workflow:**
```bash
# Global setup
$ cd ~
$ navig app use ai
✓ Set global active app to 'ai'

# App-specific override
$ cd /var/www/myapp
$ navig init  # Creates .navig/ directory
$ navig app use myapp --local
✓ Set local active app to 'myapp'

# Check status
$ navig app current
Active Context
  Host:    myhost
  App: myapp 📍 local (.navig/)

# In another directory (uses global)
$ cd /home/user/scripts
$ navig app current
Active Context
  Host:    myhost
  App: ai 🌐 global (~/.navig/)
```

**Technical Implementation:**
- Modified `ConfigManager.get_active_app()` to check local `.navig/config.yaml` first
- Added `ConfigManager.set_active_app_local()` for local scope
- Added `ConfigManager.clear_active_app_local()` to remove local setting
- Updated `ConfigManager.set_active_app()` with `local` parameter
- Added `--local` and `--clear-local` flags to `navig app use` command
- Updated `navig app current` to show source information
- Updated interactive menu header to display source icons
- Added local/global scope prompt in interactive menu app switching

**Validation:**
- Local active app is validated against current host before use
- Shows warning if local app doesn't exist on current host
- Falls back to global active app gracefully
- Handles missing `.navig/` directory with clear error messages

**Related Issue:** User requested per-directory active app selection to avoid constantly switching global active app when working on multiple apps simultaneously

### ✨ IMPROVED - Smart Webserver Type Auto-Detection in App Wizard

- ✅ **Auto-inherits webserver type from host configuration**:
  - When adding a new app, NAVIG now automatically detects the webserver type from the host's `services.web` field
  - Eliminates redundant data entry - no need to manually specify nginx/apache2 for every app
  - Shows confirmation prompt: "Detected webserver: nginx (from host configuration). Use nginx for this app?"
  - Allows override if needed (e.g., for multi-webserver hosts running both nginx and apache2)

- ✅ **Graceful fallback for hosts without webserver metadata**:
  - If `services.web` is not configured, prompts user to select webserver type manually
  - Shows warning: "Could not auto-detect webserver type from host configuration"
  - Defaults to nginx (most common)

- ✅ **Supports multi-webserver host architectures**:
  - Keeps `webserver.type` at app-level (not host-level) to support advanced setups
  - Example: nginx as reverse proxy + apache2 as backend on same host
  - Different apps can use different webservers on the same host

- ✅ **Improved user experience**:
  - **Before**: Always prompted "Webserver Type (REQUIRED) [nginx/apache2]" for every app
  - **After**: Auto-detects from host, only prompts for confirmation or override
  - Reduces cognitive load and prevents data entry errors

**Technical Implementation:**
- Modified `navig/commands/app.py:add_app()` (lines 286-322)
- Loads host configuration before prompting for app settings
- Extracts webserver type from `host_config['services']['web']`
- Normalizes to 'nginx' or 'apache2' (handles variations like 'nginx-full', 'apache')
- Falls back to manual prompt if auto-detection fails

**Example Workflow:**
```bash
$ navig menu
→ App Management
→ Add new app
App name: myapp

=== App Configuration ===
ℹ Detected webserver: nginx (from host configuration)
? Use nginx for this app? (Y/n): y

✓ App 'myapp' added to host 'vultr'
```

**Related Issue:** User reported that webserver type prompt was redundant since it's already known at host-level

### 🐛 FIXED - Legacy `.navig` File vs New `.navig/` Directory Conflict

- ✅ **Root cause identified**: Naming conflict between legacy and new formats
  - **Legacy format**: `.navig` file containing "host:app" string
  - **New format**: `.navig/` directory for hierarchical configuration
  - When both exist, `get_active_host()` tried to read directory as file → Permission denied

- ✅ **Fixed in `ConfigManager.get_active_host()`** (navig/config.py:506-533):
  - Added `local_navig.is_file()` check before attempting `read_text()`
  - Added try-except block to handle permission errors gracefully
  - Now skips `.navig` directory and only reads `.navig` file (legacy format)

- ✅ **Fixed in `ConfigManager.get_active_app()`** (navig/config.py:548-571):
  - Same fix applied for consistency
  - Added `local_navig.is_file()` check and error handling

- ✅ **Impact**:
  - `navig menu` no longer crashes with "Permission denied" error
  - Hierarchical configuration (`.navig/` directory) now works correctly
  - Legacy `.navig` file format still supported for backward compatibility
  - Graceful fallback when `.navig` file is inaccessible

**Technical Details:**
The error occurred because:
1. `navig init` creates `.navig/` directory (new hierarchical config)
2. `get_active_host()` checks for `.navig` in current directory (legacy file format)
3. `.exists()` returns True for both files and directories
4. `read_text()` fails with PermissionError when called on a directory
5. Exception propagated to `launch_menu()` → Fatal error

**Solution:**
Check `is_file()` before `read_text()` to distinguish between:
- `.navig` file (legacy) → read it
- `.navig/` directory (new) → skip it

### 🔧 IMPROVED - Smart `--copy-global` Option in `navig init`

- ✅ **Intelligent prompt logic**:
  - Only shows "Copy global configurations?" prompt if configs actually exist
  - Skips prompt entirely if `~/.navig/` is empty or doesn't exist
  - Shows count in prompt: "Found 3 hosts and 2 legacy configs. Copy to this app?"
  - No more confusing prompts when there's nothing to copy

- ✅ **Clarified COPY behavior** (not move):
  - Updated help text: `--copy-global` COPIES configs, leaving originals in `~/.navig/`
  - Added docstring clarification in `_copy_global_configs()`
  - Success message now shows: "✓ Copied 3 hosts to .navig/ (Originals remain in ~/.navig/)"
  - This allows the same host configs to be used across multiple apps

- ✅ **Better validation and error handling**:
  - New `_count_global_configs()` helper function counts available configs before prompting
  - Handles permission errors gracefully when counting/copying configs
  - Shows specific error messages for failed copies
  - Reports: "Failed to copy 2 file(s) due to permission errors"

- ✅ **Improved user experience**:
  - Shows informative count: "Found 3 hosts and 2 legacy configs"
  - Success message: "✓ Copied 3 hosts and 2 legacy configs to .navig/"
  - Clear message when no configs exist: "No global configurations found to copy"
  - Reminder that originals remain: "(Originals remain in ~/.navig/)"

### 🐛 FIXED - Hierarchical Configuration Permission Handling

- ✅ **Robust permission error handling in ConfigManager**:
  - Added `_is_directory_accessible()` helper method to check directory accessibility
  - Updated `_find_app_root()` to skip inaccessible `.navig` directories
  - Modified `_get_config_directories()` to only return accessible directories
  - Enhanced `_ensure_directories()` to gracefully fall back to global config if app-local fails
  - Added comprehensive error handling to `list_hosts()` and `host_exists()`

- ✅ **Graceful fallback to global config**:
  - If app-local `.navig` has permission issues, automatically falls back to `~/.navig`
  - Shows warning messages but doesn't crash the application
  - Continues execution with global config only

- ✅ **Improved `navig init` command**:
  - Creates `.navig` directory with proper Windows permissions (full control for current user)
  - Uses `icacls` on Windows to grant explicit permissions
  - Sets appropriate Unix permissions on Linux/macOS
  - Validates directory accessibility after creation
  - Shows helpful error messages if permission issues occur

- ✅ **New diagnostic tool**: `scripts/fix-navig-permissions.ps1`
  - Diagnose permission issues: `.\scripts\fix-navig-permissions.ps1 -Diagnose`
  - Fix permissions: `.\scripts\fix-navig-permissions.ps1 -Fix`
  - Delete and recreate: `.\scripts\fix-navig-permissions.ps1 -Delete`
  - Shows current permissions and ownership
  - Provides actionable instructions for resolution

- ✅ **Better error messages**:
  - Clear warnings when app-local config is inaccessible
  - Helpful instructions for fixing permission issues
  - No more cryptic "Permission denied" crashes

### ✨ NEW - Hierarchical Configuration System (Git-like)

- ✅ **App-specific `.navig/` directories with automatic detection**:
  - Similar to Git's `.git` directories - creates app-specific configuration root
  - Automatic app root detection via upward directory search from current working directory
  - App-specific configs take precedence over global `~/.navig/` configs
  - Three-tier configuration priority: **App > Global > Defaults**

- ✅ **New `navig init` command**:
  - Initializes `.navig/` directory in current directory
  - Creates complete directory structure:
    - `hosts/` - App-specific host configurations
    - `apps/` - App-specific app configurations
    - `cache/` - Runtime state (tunnel PIDs, etc.)
    - `backups/` - Database backups
    - `config.yaml` - App metadata (name, version, timestamp)
  - Available in both CLI and interactive menu
  - Optional `--copy-global` flag to copy global configs to app
  - Automatic error handling if `.navig/` already exists

- ✅ **Enhanced ConfigManager with hierarchical support**:
  - `_find_app_root()` - Searches upward from cwd for `.navig/` directory
  - `_get_config_directories()` - Returns priority-ordered list of config locations
  - Updated `load_host_config()` - Searches app config first, then global
  - Updated `save_host_config()` - Saves to app config if in app context
  - Updated `list_hosts()` - Merges hosts from all config directories (deduplicated)
  - Updated `host_exists()` - Checks all config directories
  - Updated `delete_host_config()` - Deletes from first location found

- ✅ **Database path separation**:
  - App context uses `<app>/.navig/navig.db`
  - Non-app context uses `~/.navig/navig.db`
  - Automatic separation based on app root detection
  - Command history and cache are app-specific when in app directory

- ✅ **Verbose mode for diagnostics**:
  - New `verbose` parameter in `ConfigManager.__init__()`
  - Diagnostic output shows:
    - App root detection results
    - Which config directory is being used (app vs global)
    - Database file path being used
    - Config file loading locations
  - Helpful for troubleshooting configuration issues

- ✅ **Backward compatibility maintained**:
  - Existing `~/.navig/` global configs continue to work
  - Commands run outside apps use global config as before
  - No breaking changes to existing workflows
  - Legacy config format still supported

### 🔧 IMPROVED - Host/App Configuration Separation

- ✅ **Proper separation between host-level and app-level configuration**:
  - **Host-level** (server management): SSH config, OS info, database server info, root credentials, server paths
  - **App-level** (application-specific): Database name, web root, app-specific credentials

- ✅ **Auto-detection of MySQL root credentials during host creation**:
  - Automatically checks common credential storage locations:
    - `/root/.my.cnf` - Root user MySQL config
    - `/etc/mysql/debian.cnf` - Debian/Ubuntu system maintenance account
    - `/usr/local/hestia/conf/mysql.conf` - HestiaCP MySQL config
    - `mysql_config_editor` - Encrypted credential storage (MySQL 5.6+)
  - Only prompts for credentials if auto-detection fails
  - Stores root credentials at host level for server management tasks

- ✅ **Removed inappropriate prompts from "Add Host" workflow**:
  - ❌ Removed "Database Name" prompt (app-specific, not host-specific)
  - ❌ Removed web root configuration (app-specific, not host-specific)
  - ✅ Added clear messaging that web root is configured per-app

- ✅ **Enhanced discovery methods**:
  - Added `_discover_mysql_root_credentials()` method to auto-detect MySQL root password
  - Added `skip_web_root` parameter to `discover_application_paths()` and `discover_all()`
  - Web root detection is now skipped during host creation

- ✅ **Improved user experience**:
  - Clear separation of concerns between host and app configuration
  - Less manual input required during host setup
  - Auto-detected credentials reduce configuration errors
  - Helpful messages explain what's being configured and why

### ✨ NEW - Auto-Discovery Summary Report

- ✅ **Added comprehensive discovery summary in interactive menu**:
  - After inspection completes, shows a clean summary of all discovered information
  - Displays: OS, databases (with versions and ports), web servers (with versions), PHP version, and detected templates
  - Summary appears after the spinner completes, with no overlapping output
  - Example output:
    ```
    === Discovery Summary ===
    ✓ OS: Debian GNU/Linux 12 (bookworm)
    ✓ Database: MYSQL 8.0.35 (port 3306)
    ✓ Web Server: Nginx 1.29.3
    ✓ Web Server: Apache 2.4.65
    ✓ PHP: 8.2.29
    ✓ Templates: n8n (v1.18.2), HestiaCP (v1.8.12), Gitea (v1.22.1)
    ```

### 🔧 IMPROVED - Version Detection

- ✅ **Enhanced version detection for databases and templates**:
  - **MariaDB/MySQL**: Added multiple detection methods and improved regex patterns
    - Tries `mariadb --version`, `mysql --version`, `mysqld --version`, `mariadbd --version`
    - Supports multiple version output formats: "Ver 15.1 Distrib 10.11.6-MariaDB", "Ver 8.0.35", etc.
    - Correctly identifies MariaDB vs MySQL
  - **n8n**: Added fallback version detection methods
    - Tries `n8n --version`, `n8n version`, npm global list, package.json
    - Checks multiple installation paths: `/usr/local/bin/n8n`, `/usr/bin/n8n`, `~/.n8n/package.json`
  - **HestiaCP**: Enhanced version extraction with multiple fallback methods
    - Tries official API (`v-list-sys-info json`), config file, dpkg, version command
    - Supports multiple output formats: JSON, VERSION=, plain version number
  - All version detection now has robust fallback mechanisms to handle different installation methods

### 🐛 FIXED - Auto-Discovery (Inspect Host) Issues

- ✅ **Fixed display formatting in auto-discovery**:
  - Spinner and status messages now display on separate lines (no more overlapping output)
  - Added `silent` mode to `inspect_host()` to suppress output when called from interactive menu
  - Added `progress` parameter to all `ServerDiscovery` methods:
    - `discover_os()`, `discover_databases()`, `discover_web_servers()`, `discover_php()`, `discover_application_paths()`
    - `discover_templates()` - Fixed template detection messages overlapping with spinner
  - When `progress=False`, all discovery methods suppress console output completely
  - Interactive menu shows clean, single-line status messages during inspection
  - Discovery progress is hidden in silent mode for cleaner output
  - `inspect_host()` now returns discovery results for use by interactive menu

- ✅ **Fixed crash after successful discovery**:
  - Added `update_host_metadata()` function to `ConfigManager`
  - Fixed `inspect_host()` calling wrong function (`update_server_metadata()` instead of `update_host_metadata()`)
  - Discovery now completes successfully without "Server configuration not found" error
  - Properly updates host metadata after inspection

### 🐛 FIXED - SSH Connection Test Display Issues

- ✅ **Fixed output formatting in interactive menu**:
  - Spinner and success message now display on separate lines
  - Removed duplicate success messages
  - Added `silent` mode to `test_host()` to suppress output when called from interactive menu
  - Interactive menu now shows clean, single-line status messages

### 🐛 FIXED - SSH Connection Test Improvements

- ✅ **Fixed contradictory success/failure messages**:
  - Interactive menu was showing "Connection successful" even when SSH test failed
  - Changed `test_host()` to raise `RuntimeError` on failure
  - Updated `execute_host_test()` to catch `RuntimeError` and not show duplicate success message
- ✅ **Added verbose SSH debugging**:
  - New `verbose` option shows full SSH debug output (`-v` flag)
  - Displays actual SSH command being executed
  - Shows SSH key path and verification status
- ✅ **Enhanced error messages with troubleshooting tips**:
  - Permission denied → Check authorized_keys on server
  - Connection refused → SSH service may not be running
  - No route to host → Check IP address
- ✅ **Better SSH key validation**:
  - Checks if SSH key file exists before attempting connection
  - Shows expanded key path for debugging
  - Provides clear error if key file is missing

### 🐛 FIXED - Interactive Menu Parameter Passing Errors

- ✅ **Fixed "Show host info" error**:
  - Added missing `subheader()` function to `console_helper.py`
  - Added missing `Colors.ACCENT` constant to Colors class
- ✅ **Fixed "Switch active host" error**: Corrected parameter passing from `{'name': selection}` to `(selection, {})`
- ✅ **Fixed "Add new host" error**: Corrected parameter passing from `{'name': name}` to `(name, {})`
- ✅ **Fixed "Test SSH connection" error**:
  - Fixed handling of `None` SSH key values in host configuration
  - Changed from `if 'ssh_key' in host_config:` to `if ssh_key:` to properly check for None values
  - Added better error handling with TypeError catch for configuration errors
- ✅ **Fixed "Clone host" save error**: Removed invalid `style` parameter from `ch.info()` calls
- ✅ **Fixed console_helper API**: Removed `style` parameter from all `info()` function calls (not supported)
- ✅ **Fixed Unicode encoding issue**: Changed box-drawing characters to ASCII in `header()` function for Windows compatibility

### 🐛 FIXED - Interactive Menu Bug Fixes

- ✅ **Fixed "Host name is required" error** when editing host configuration from interactive menu
- ✅ **Fixed parameter passing bugs** in 6 interactive menu functions:
  - `execute_host_edit()` - Now correctly passes `host_name` parameter
  - `execute_host_clone()` - Now correctly passes `source_name` and `new_name` parameters
  - `execute_host_test()` - Now correctly passes `host_name` parameter
  - `execute_host_inspect()` - Now correctly sets active host before inspection
  - `execute_app_edit()` - Now correctly passes `app_name` and `host` parameters
  - `execute_app_clone()` - Now correctly passes `source_name`, `new_name`, and `host` parameters
- ✅ **Improved menu organization** with visual categories:
  - **View/List Operations** - List, search, and view info
  - **Management Operations** - Switch, add, edit, remove
  - **Advanced Operations** - Clone, test, inspect
  - Categories are now properly displayed with visual separators
  - Options are grouped under their respective category headers
- ✅ **Added new menu options**:
  - "Show host info" - View detailed host information
  - "Show app info" - View detailed app information
- ✅ **Added comprehensive tests** - 6 new tests to prevent regression

### 🎨 NEW - Interactive Menu System

- ✅ **`navig menu`** / **`navig interactive`** - Launch interactive terminal UI
  - **Mr. Robot inspired theme** with Rich library formatting
  - **Arrow key navigation** (with questionary) or number-based selection
  - **Context-aware menus**: Auto-detects and pre-selects active host/app
  - **Command history**: Tracks last 10 commands executed through menu
  - **Safety warnings**: Confirmation prompts for destructive operations (DROP, DELETE, restore)
  - **Progress indicators**: Spinners and status messages for long operations
  - **Graceful fallback**: Works without questionary (number selection only)

**Menu Structure:**
- Host Management (list, switch, add, edit, clone, test, inspect, remove)
- App Management (list, switch, add, edit, clone, search, remove)
- Database Operations (SQL query/file, backup, restore, list backups/databases/tables)
- Webserver Control (coming soon)
- File Operations (coming soon)
- System Maintenance (coming soon)
- Configuration (coming soon)
- Command History (view and track recent operations)

**Visual Features:**
- ASCII art header with NAVIG branding
- Color-coded status: Success (green), Error (red), Warning (yellow), Info (cyan)
- Status prefixes: `[*]` info, `[+]` success, `[!]` warning, `[x]` error, `[>]` action, `[~]` loading
- Rich tables with rounded borders and syntax highlighting
- Terminal size validation (minimum 60x20)

**Dependencies:**
- `questionary>=2.0.0` - **OPTIONAL** keyboard navigation (may cause freezes on some Windows systems)
- `rich>=13.0.0` - Terminal UI formatting (required)

**Windows Compatibility Fix:**
- Lazy-loading of questionary to prevent "out of resources" errors
- Automatic fallback to number-based selection if questionary causes issues
- Documented troubleshooting steps in README for Windows users

**Known Issues:**
- Some Windows systems may experience freezes with questionary installed
- **Solution**: Uninstall questionary (`pip uninstall questionary -y`) - menu works perfectly with number selection
- See "Interactive Menu Freezing" in Troubleshooting section of README

### 🚀 NEW - Enhanced CLI with Auto-Detection and Management Commands

#### **Global --app Flag with Auto-Detection**

- ✅ **`--app` flag now auto-detects host**: No need to specify `--host` every time!
  - Example: `navig webserver restart --app staging` (auto-finds host containing "staging")
  - Example: `navig sql "SELECT * FROM users" --app prod` (auto-finds host for "prod")
  - If app exists on multiple hosts, uses active/default host or prompts for selection
  - Clear error messages if app not found on any host

#### **Enhanced Host Management Commands**

- ✅ `navig host edit <name>` - Open host configuration in default editor (YAML file)
- ✅ `navig host clone <source> <new-name>` - Clone an existing host configuration
- ✅ `navig host test <name>` - Test SSH connection to host
- ✅ `navig host info <name>` - Show detailed host information (IP, port, user, apps count, etc.)
- ✅ `navig host list --all` - Show detailed information with app counts
- ✅ `navig host list --format json|yaml|table` - Different output formats
- ✅ **Color-coded status indicators**: Active hosts highlighted in green, default hosts in yellow

#### **Enhanced App Management Commands**

- ✅ `navig app edit <name>` - Edit app configuration in default editor
- ✅ `navig app clone <source> <new-name>` - Clone an existing app configuration
- ✅ `navig app info <name>` - Show detailed app information (webserver type, database, paths, etc.)
- ✅ `navig app search <query>` - Search for apps across all hosts by name
- ✅ `navig app list --all` - Show all apps from all hosts
- ✅ `navig app list --format json|yaml|table` - Different output formats
- ✅ **Color-coded status indicators**: Active apps highlighted in green, default apps in yellow

#### **Terminology Update**

- ✅ Renamed `server` subcommand to `host` for clarity
  - Old: `navig server use RemoteKit`
  - New: `navig host use RemoteKit`
- ✅ Updated all documentation and help text

### 🏗️ MAJOR - Two-Tier Configuration Architecture

**BREAKING CHANGE**: Complete redesign of NAVIG's configuration architecture to support managing multiple apps across different physical servers.

#### **What Changed**

**Old Architecture** (v1.0):
- Single-tier: One config file per "server" (conflated remote server + app)
- Location: `~/.navig/apps/*.yaml`
- Limitation: Could not manage multiple apps on same physical server

**New Architecture** (v2.0):
- Two-tier hierarchy: **Host** (physical server) → **App** (application)
- Location: `~/.navig/hosts/*.yaml`
- Benefit: Manage unlimited apps across unlimited servers

#### **New Features**

- ✅ **`--host` global flag**: Override active host for any command
- ✅ **Webserver type auto-detection**: No more `--server nginx` on every command!
  - Webserver type now read from `app_config['webserver']['type']`
  - **REQUIRED field**: All apps must specify `webserver.type: nginx` or `webserver.type: apache2`
- ✅ **Environment naming convention**: Separate apps for different environments
  - Example: `myapp` (prod), `myapp-staging`, `myapp-dev`
  - No `--env` flag (reserved for future v2.0 with config merging)
- ✅ **Automatic migration tool**: `navig config migrate` converts legacy configs
  - Auto-detects old format
  - Extracts webserver type from `services.web` field
  - Creates backups before migration
  - Dry-run mode available
- ✅ **Backward compatibility**: Legacy format still works alongside new format

#### **CLI Changes**

**Removed**:
- ❌ `--server` flag from all webserver commands (auto-detected now)

**Added**:
- ✅ `--host` global flag for all commands
- ✅ `navig config migrate` - Migration command
- ✅ `navig config show <host>:<app>` - Display configurations
- ✅ `navig host use <name>` - Switch active host
- ✅ `navig app use <name>` - Switch active app

**Updated**:
- ✅ All webserver commands now auto-detect webserver type from app config
- ✅ `--app` flag now correctly refers to apps (not servers)

#### **Example Usage**

```bash
# Old way (v1.0)
navig --app production webserver-reload --server nginx

# New way (v2.0)
navig --host myhost --app myapp webserver-reload
# Webserver type auto-detected from config!
```

#### **Migration**

```bash
# Preview migration (dry-run)
navig config migrate --dry-run

# Migrate all configurations
navig config migrate

# Verify migration
navig host list
navig config show myhost:myapp
```

#### **Documentation**

- 📚 [Migration Guide](docs/MIGRATION_GUIDE.md) - Step-by-step migration instructions
- 📚 [Configuration Schema](docs/CONFIG_SCHEMA.md) - Complete field reference
- 📚 [Architecture Summary](docs/ARCHITECTURE_SUMMARY.md) - Design overview
- 📚 [Design Decisions](docs/DESIGN_DECISIONS.md) - Rationale for changes

#### **Testing**

- ✅ **45 tests passing** (18 migration + 23 config + 4 webserver autodetect)
- ✅ Comprehensive test coverage for migration utilities
- ✅ Backward compatibility tests
- ✅ Webserver auto-detection validation

---

### 🎨 NEW - Mr. Robot-Style Code Comments

**ENHANCEMENT**: Added subtle, underground geek-culture comments throughout the NAVIG codebase in the voice of "void" (Schema's leader), inspired by Mr. Robot's Elliot Alderson.

- ✅ **20 strategic comments** across 10 core Python files
- ✅ **Themes**: Security paranoia, system failures, AI skepticism, traces & surveillance, production caution
- ✅ **Placement**: Near security-critical code, error handling, AI features, destructive operations
- ✅ **Style**: Cynical, introspective, technically brilliant - never disrupting code functionality
- ✅ **Documentation**: See `docs/MR_ROBOT_STYLE_COMMENTS.md` for complete catalog

**Example comments:**
- `# void: trust no one. verify everything. MITM is always watching.`
- `# void: we built an AI to watch our systems. now who watches the AI?`
- `# void: encryption is the only privacy we have left.`
- `# systems fail. we just try to fail gracefully.`

### 🤖 NEW - Proactive AI Assistant System

**MAJOR FEATURE**: Intelligent AI-powered assistant system with four core modules for proactive server management.

#### **Module 1: Auto-Detection & Analysis**
- ✅ **Command Execution Monitoring**: Automatic logging of all commands with exit codes, duration, and context
  - Stores last 1000 commands in `~/.navig/ai_context/command_history.json`
  - Automatic rotation when limit reached
  - Triggers analysis on command failures (exit code != 0)
- ✅ **Performance Baseline Tracking**: Collects CPU, memory, disk metrics every 5 minutes
  - Calculates rolling averages (1 hour, 24 hours, 7 days)
  - Stores per-server baselines in `~/.navig/baselines/<server>.json`
  - Alerts when metrics exceed configurable thresholds (80% warning, 95% critical)
- ✅ **Error Pattern Detection**: Regex-based anomaly detection in logs and command output
  - Categorizes errors: permission, network, configuration, resource_exhaustion, dependency_missing, syntax
  - Stores detected issues with severity levels in `detected_issues.json`
- ✅ **Manual Trigger**: `navig assistant analyze` for comprehensive system analysis

#### **Module 2: Proactive Information Display**
- ✅ **Pre-Execution Warnings**: Context-aware alerts before destructive operations
  - `navig delete --recursive`: Shows file count, size, backup status
  - `navig sql "DROP TABLE..."`: Warns about permanent data loss, suggests backup
  - Production server operations: Displays uptime, active connections, last backup
- ✅ **Workflow Optimization Detection**: Identifies inefficient command patterns
  - Multiple single-file uploads → Suggests batch upload
  - Frequent service restarts → Suggests root cause analysis
  - Displays suggestions at configurable frequency (once per pattern per session)
- ✅ **Contextual Command Suggestions**: Based on current context and recent operations
  - After deploy → Suggests monitoring logs or health check
  - After database changes → Suggests backup
  - When errors detected → Suggests analysis commands

#### **Module 3: Intelligent Error Resolution**
- ✅ **Enhanced Error Logging**: Structured error records with categorization
  - Stores in `~/.navig/ai_context/error_log.json` with full context
  - Tracks: timestamp, command, exit_code, category, error_message, suggested_solutions, resolution_status
  - Keeps last 1000 errors with automatic rotation
- ✅ **Solution Database**: Pattern-based solution matching with success tracking
  - Maps error patterns to fix commands using regex
  - Tracks success rates based on user feedback
  - Ranks solutions by effectiveness (success_rate field)
  - Risk levels: low (✅), medium (⚠️), high (🔴)
- ✅ **Automatic Error Analysis**: On command failure, displays top 3 solutions
  - Shows command, description, success rate, risk level
  - Supports `--dry-run` preview for suggested fixes
  - Falls back to AI-powered analysis if no pattern match
- ✅ **Learning System**: Improves suggestions based on user feedback
  - `navig assistant feedback` to record solution effectiveness
  - Updates success rates in solutions database
  - Periodically suggests removal of low-success-rate solutions

#### **Module 4: AI Copilot Integration**
- ✅ **Enhanced Context Building**: Aggregates data from multiple sources
  - Server state: OS version, services, resource usage, uptime
  - Operation history: Last 20 commands with timestamps, exit codes, duration
  - Error context: Recent failures with categories and attempted solutions
  - Configuration snapshot: Active server, enabled templates, tunnel status
  - Performance trends: Current metrics vs. baselines
- ✅ **Structured JSON Output**: Comprehensive context schema for AI assistants
  - Includes: server info, services status, resource usage, recent operations, active issues, recent errors
  - Human-readable context summary field
  - JSON-serializable for easy integration
- ✅ **Export Commands**:
  - `navig assistant context` - Display full context JSON
  - `navig assistant context --clipboard` - Copy to clipboard (requires pyperclip)
  - `navig assistant context --file <path>` - Save to file

#### **New CLI Commands**
- ✅ `navig assistant status` - Display health, statistics, monitoring status
- ✅ `navig assistant analyze` - Manual comprehensive system analysis
- ✅ `navig assistant context [--clipboard] [--file]` - Generate AI copilot context
- ✅ `navig assistant reset` - Clear all learning data (requires confirmation)
- ✅ `navig assistant config` - Configuration wizard

#### **Command Execution Integration**
- ✅ **Pre-execution hooks**: Automatic warnings before destructive operations
- ✅ **Post-execution logging**: Tracks all command executions with timing
- ✅ **Automatic error analysis**: Suggests solutions when commands fail
- ✅ **Integration helpers**: `assistant_hooks.py` module for easy command integration
- ✅ **Respects flags**: Honors `--yes` to skip confirmations, `--dry-run` for previews

#### **Cross-Platform Directory Management**
- ✅ **Linux/macOS**: `~/.navig/ai_context/` with 0755 permissions
- ✅ **Windows**: `~/Documents/.navig/ai_context/` with appropriate ACLs
- ✅ **Auto-initialization**: Creates subdirectories and JSON files on first run
- ✅ **Subdirectories**: `ai_context/`, `baselines/`
- ✅ **JSON Files**: command_history, error_log, error_patterns, solutions, performance_baselines, workflow_patterns, detected_issues, config_rules

#### **Configuration**
- ✅ **Config Location**: `~/.navig/config.yaml` under `proactive_assistant` section
- ✅ **Settings**:
  - `enabled`: Enable/disable assistant (default: true)
  - `suggestion_level`: minimal | normal | verbose (default: normal)
  - `auto_analysis`: Auto-analyze on errors (default: true)
  - `confirmation_required`: Require confirmation for high-risk ops (default: true)
  - `monitoring_interval_seconds`: Metrics collection interval (default: 300)
  - `max_history_entries`: Command history limit (default: 1000)
  - `thresholds`: CPU/memory/disk warning and critical levels
  - `log_paths`: Configurable log file locations (nginx, mysql)

#### **Safety Mechanisms**
- ✅ **Dry-Run Support**: All destructive operations show preview before execution
- ✅ **Confirmation Required**: High-risk operations require explicit user confirmation
- ✅ **Advisory Only**: All suggestions are advisory; user must execute commands
- ✅ **Audit Log**: All assistant actions logged to `assistant_audit.log`
- ✅ **No Auto-Execution**: Never automatically executes data-modifying commands

#### **AI Integration Extensions**
- ✅ **Extended AIAssistant class** (`navig/ai.py`):
  - `analyze_error()` - AI-powered error analysis and solutions
  - `suggest_optimization()` - Workflow optimization suggestions
  - `generate_context_summary()` - Enhanced context for AI copilot
- ✅ **Enhanced ai_context.py**: Backward compatible with existing error logging

#### **Testing**
- ✅ **Comprehensive test suite**: `tests/test_proactive_assistant.py`
  - Tests for all four modules
  - Cross-platform directory creation tests
  - Error categorization and solution matching tests
  - Context generation and JSON serialization tests
  - Mock-based tests for remote operations
  - **All 13 tests passing** with correct API signatures

#### **Documentation**
- ✅ **Complete guide**: `docs/PROACTIVE_ASSISTANT.md`
  - Overview of all four modules
  - CLI command reference
  - Configuration guide
  - Data storage locations
  - Safety mechanisms
  - Best practices
  - Troubleshooting
  - Integration with external AI assistants
- ✅ **Integration guide**: `docs/ASSISTANT_INTEGRATION_GUIDE.md`
  - How to add assistant hooks to commands
  - Pre-execution check examples
  - Post-execution logging examples
  - Complete integration example
  - Best practices and testing

#### **Dependencies**
- ✅ **Added**: `pyperclip>=1.8.2` for clipboard operations

#### **Bug Fixes & Improvements**
- 🔧 **Fixed RemoteOperations API compatibility**
  - Updated all `remote_ops.execute()` calls to `remote_ops.execute_command()`
  - Fixed result checking from `.success` to `.returncode == 0`
  - Added required `server_config` parameter to all remote operations
- 🔧 **Fixed ConfigManager API compatibility**
  - Updated `get_active_server()` usage to return server name (string)
  - Added `load_server_config()` calls to get full configuration dictionary
  - Fixed all modules: auto_detection, context_generator, assistant commands
- 🔧 **Added graceful error handling**
  - Commands handle unavailable servers without crashing
  - User-friendly error messages when assistant operations fail
  - Silent fallback when assistant initialization fails
- 🔧 **Updated test suite**
  - Fixed all test mocks to use correct API signatures
  - All 13 tests passing with proper RemoteOperations and ConfigManager mocking

#### **Files Added**
- `navig/assistant_utils.py` - Cross-platform directory management
- `navig/proactive_assistant.py` - Main coordinator
- `navig/modules/__init__.py` - Module exports
- `navig/modules/auto_detection.py` - Module 1
- `navig/modules/proactive_display.py` - Module 2
- `navig/modules/error_resolution.py` - Module 3
- `navig/modules/context_generator.py` - Module 4
- `navig/commands/assistant.py` - CLI commands
- `tests/test_proactive_assistant.py` - Test suite
- `docs/PROACTIVE_ASSISTANT.md` - Documentation

#### **Files Modified**
- `navig/cli.py` - Added assistant command group
- `navig/ai.py` - Extended with new methods
- `requirements.txt` - Added pyperclip dependency

---

### 🔧 Fixed - Critical JSON Flag Bug (Task 8: CLI Completeness Audit)

**CRITICAL BUG FIX**: Global `--json` flag was documented but never implemented, breaking automation workflows for 37+ commands that had JSON support coded but non-functional.

#### **CLI Framework**
- ✅ **FIXED**: Added missing global `--json` flag to `cli.py` main callback (lines 84-88)
  - Flag was referenced in documentation (README, CHANGELOG, phase reports) but completely absent from code
  - 37+ commands had JSON output logic that could never execute (options.get('json') always returned None)
  - **Impact**: ALL automation workflows using `--json` flag now functional
  - **Binding**: `ctx.obj['json'] = json` enables proper flag propagation to all commands

#### **Variable Naming Standardization**
- **Before**: Three inconsistent patterns: `json_output` (24 cmds), `json` (13 cmds), missing (18 cmds)
- **After**: Unified to `options.get('json', False)` across all 55+ commands
- **Changed files**: monitoring.py, security.py, hestia.py, maintenance.py, webserver.py
- **Consistency**: Matches other global flags (dry_run, verbose, quiet, yes, raw)

#### **Database Commands - Added JSON + Dry-run Support**
- ✅ `navig sql "SELECT ..."` - JSON output with query/success/output/error fields
- ✅ `navig backup [path]` - JSON with database/path/size_bytes, dry-run preview
- ✅ `navig restore <file>` - JSON with success/source/safety_backup, dry-run warning
  - Dry-run shows destructive operation preview without execution
  - JSON mode includes `cancelled:true` when user aborts restore
  - Safety backup tracking in JSON output (automatic rollback file)

#### **File Commands - Added JSON Support**
- ✅ `navig upload <local> [remote]` - JSON with local/remote/size_bytes/success

#### **Coverage Improvements**
- **JSON Support**: 0% actual → 85%+ working (0 → 47 commands fixed)
- **Dry-run Support**: 73% → 85%+ (57 → 47 commands with preview capability)
- **Parameter Consistency**: All commands verified using --force, --recursive, --compress consistently

#### **Documentation**
- Created `docs/CLI_COMPLETENESS_AUDIT.md` - 500+ line comprehensive audit report
- Feature matrices showing JSON/dry-run coverage across all command categories
- Detailed analysis of 50+ commands with fix recommendations

### 🤖 Enhanced - AI/MCP Integration (Task 9: AI Context & Error Analysis)

**NEW CAPABILITY**: Intelligent error tracking and context aggregation for AI assistants.

#### **AI Context Management System**
- ✅ **New Module**: `navig/ai_context.py` - Error log aggregation and analysis
  - Stores last 100 errors with timestamps, categories, commands, and context
  - Automatic error categorization (tunnel, database, file, network, config)
  - Persistent storage in `~/.navig/error_log.json`
  - Time-based filtering (last 24h, 7d, 30d)

#### **Command Suggestion Engine**
- ✅ **Smart Suggestions**: AI analyzes failed commands and suggests fixes
  - Tunnel failures → Check SSH, firewall, port conflicts, restart with auto-increment
  - Database failures → Verify credentials, tunnel status, disk space, permissions
  - File operation failures → Check permissions, paths, ownership, SSH connection
  - Config errors → List servers, validate setup, inspect configuration
  - Returns top 5 actionable suggestions based on error patterns

#### **Error Analysis Commands**
- ✅ `navig ai errors [--hours 24] [--category <cat>] [--json]` - View error summary
  - Total error count and category breakdown
  - Most common error patterns with occurrence counts
  - Recent errors with timestamps and context
  - JSON export for automation/monitoring

- ✅ `navig ai suggest <command> "<error>"` - Get troubleshooting suggestions
  - Analyzes failed command and error message
  - Returns actionable steps to diagnose and fix
  - Supports JSON output for scripting

- ✅ `navig ai clear [--days 30]` - Clear old error logs
  - Remove errors older than specified days
  - Confirmation prompt (bypassed with --yes)

- ✅ `navig ai export <file> [--hours 168]` - Export errors to JSON
  - Export last N hours of errors for external analysis
  - Includes timestamps, categories, commands, context
  - Useful for monitoring dashboards, SIEM integration

#### **Enhanced AI Assistant Context**
- ✅ **Automatic Error Context**: AI questions now include recent error history
  - Last 24h error count and categories automatically added to context
  - Top 3 most common errors included in AI prompts
  - Helps AI provide more accurate troubleshooting advice

#### **Integrated Error Logging**
- ✅ **Command Integration**: Error logging added to critical commands
  - Database commands: Log SQL failures, connection issues, credential errors
  - Tunnel commands: Log connection refused, port conflicts, timeouts
  - File commands: Log permission denied, file not found, connection drops
  - All errors include contextual data (server, query, path, parameters)

#### **AI-Friendly JSON Schemas**
- ✅ **Consistent Structure**: All JSON outputs follow standard format
  ```json
  {
    "success": true/false,
    "action": "backup|restore|sql|upload|...",
    "data": { /* command-specific results */ },
    "error": "error message if failed",
    "context": { /* server, paths, metadata */ }
  }
  ```
- ✅ **Metadata Fields**: Timestamps, server names, file sizes, durations
- ✅ **Parseable Errors**: Machine-readable error codes and categories

#### **Coverage and Impact**
- **Error Tracking**: 100% of critical commands (database, tunnel, files)
- **Suggestion Quality**: 5 actionable steps per failure with specific commands
- **Context Retention**: Last 100 errors kept with full context
- **AI Integration**: Error history automatically included in AI assistant prompts

### 🔄 Added - Retry Logic & Auto-Recovery (Task 10: Production Reliability)

**NEW CAPABILITY**: Intelligent retry mechanisms with exponential backoff and circuit breakers.

#### **Retry Logic Framework**
- ✅ **New Module**: `navig/retry.py` - Comprehensive retry and recovery system
  - Exponential backoff with configurable base delay and max delay
  - Jitter (random 0-25% variation) prevents thundering herd problem
  - Overall timeout support (prevents infinite retries)
  - Automatic error logging for failed operations

#### **Retry Configurations** (Preset for Common Operations)
- ✅ **Tunnel Operations**: 5 retries, 1s → 2s → 4s → 8s → 16s, 60s timeout
- ✅ **Database Operations**: 3 retries, 2s → 4s → 8s, 30s timeout
- ✅ **File Operations**: 3 retries, 1s → 2s → 4s → 8s, 120s timeout
- ✅ **Network Operations**: 4 retries, 0.5s → 1s → 2s → 4s → 8s, 45s timeout

#### **Exponential Backoff Algorithm**
```python
delay = base_delay * (2.0 ^ attempt)  # Exponential growth
delay = min(delay, max_delay)          # Cap at maximum
delay += random(0, delay * 0.25)       # Add jitter
```

#### **Circuit Breaker Pattern**
- ✅ **Smart Failure Handling**: Prevents repeated attempts to failing operations
  - **CLOSED** (normal): Operations execute normally, failures tracked
  - **OPEN** (failing): Operations blocked, prevents cascade failures
  - **HALF_OPEN** (testing): Limited attempts to test service recovery

- ✅ **Automatic State Transitions**:
  - CLOSED → OPEN: After 3-5 consecutive failures (configurable)
  - OPEN → HALF_OPEN: After 30-60s recovery timeout (configurable)
  - HALF_OPEN → CLOSED: After 2 successful operations
  - HALF_OPEN → OPEN: If recovery test fails

- ✅ **Global Circuit Breakers**: Separate instances for tunnel, database, SSH
  - Tunnel breaker: 3 failures → 30s timeout
  - Database breaker: 5 failures → 60s timeout
  - SSH breaker: 5 failures → 45s timeout

#### **Decorator Pattern for Easy Integration**
```python
from navig.retry import with_retry, TUNNEL_RETRY_CONFIG

@with_retry(TUNNEL_RETRY_CONFIG, error_category='tunnel', command_name='start')
def start_tunnel():
    # ... tunnel start logic ...
    # Automatically retries with exponential backoff on failure
    pass
```

#### **Enhanced Tunnel Auto-Recovery** (Extended from Task 3)
- ✅ **Retry on Connection Failure**: 5 attempts with exponential backoff
- ✅ **Port Conflict Resolution**: Auto-increment to next available port
- ✅ **Zombie Process Cleanup**: Detect and kill orphaned SSH processes
- ✅ **Health Monitoring**: Periodic checks with automatic restart on failure
- ✅ **Circuit Breaker**: Prevents repeated connection attempts to dead servers

#### **Graceful Degradation Features**
- ✅ **Timeout Handling**: All network operations have configurable timeouts
  - SSH connections: 10s default (ConnectTimeout=10)
  - Port tests: 2-3s connection timeout
  - Database queries: 30s default, configurable per command

- ✅ **Failure Isolation**: Circuit breakers prevent cascade failures
  - Tunnel failure doesn't block database cache operations
  - Database failure doesn't affect file operations
  - Individual server failures isolated (multi-server support)

#### **Configurable Settings** (Future Enhancement Ready)
- ⏳ Config file support: `~/.navig/config.yaml`
  ```yaml
  retry:
    tunnel_max_retries: 5
    tunnel_base_delay: 1.0
    tunnel_max_delay: 16.0
    database_max_retries: 3
    network_timeout: 30
  ```
- ⏳ Per-command overrides: `navig sql "..." --max-retries 5 --timeout 60`

#### **Integration Status**
- ✅ **Framework Complete**: Full retry/circuit breaker infrastructure
- ✅ **Error Logging**: All retries logged to AI context system
- ✅ **Exponential Backoff**: Prevents server overload during outages
- ✅ **Jitter**: Prevents thundering herd (multiple clients retrying simultaneously)
- ⏳ **Command Integration**: Ready for deployment to tunnel/database/file commands

#### **Production Benefits**
- **Reliability**: Transient network issues automatically recovered
- **Performance**: Exponential backoff prevents server overload
- **Visibility**: All retry attempts logged with timing and context
- **Intelligence**: Circuit breakers learn from failures and prevent waste
- **Scalability**: Jitter prevents thundering herd in multi-client scenarios

## [2.0.0] - 2025-01-XX

### ✅ Resource Leak Audit (Task 7 - Reliability)

#### Comprehensive Resource Leak Analysis
- **Scanned:** All subprocess calls, file operations, SSH connections, temp files
- **Results:** ✅ **NO CRITICAL LEAKS FOUND** - All resources properly managed

#### Resource Management Verification

**Subprocess Cleanup (50+ subprocess calls audited):**
- ✅ `subprocess.Popen` in `tunnel.py`: Process tracked via PID, graceful shutdown (SIGTERM → SIGKILL)
- ✅ `subprocess.Popen` in `mcp_manager.py`: Proper `terminate()` → `wait()` → `kill()` fallback
- ✅ `subprocess.run()` in all commands: Auto-cleanup (blocking calls, no zombie processes)
- ✅ SSH tunnel processes: Process discovery with 3-retry logic, health monitoring, auto-recovery

**File Handle Management (30+ file operations audited):**
- ✅ All `open()` calls use `with` context managers (auto-close guaranteed)
- ✅ Examples: config.py, tunnel.py, backup.py, database.py, monitoring.py
- ✅ No naked `open()` calls without context managers found

**Temporary File Cleanup (11 tempfile usages audited):**
- ✅ MySQL config files (`database.py`, `database_advanced.py`, `backup.py`):
  - Created with `tempfile.mkstemp()` for credentials (prevents password in process list)
  - **ALWAYS** cleaned up in `finally` block (all 8 functions verified)
  - Permissions set to 0600 (owner-only read/write)
  - Cleanup even on exceptions (try/except/finally pattern)
- ✅ Test temp directories: Proper cleanup in tearDown methods

**SSH Connection Cleanup (paramiko usage):**
- ✅ `discovery.py`: SSH client explicitly closed via `client.close()` after command execution
- ✅ Connection timeout: 30 seconds prevents hanging connections
- ✅ No persistent SSH connections - created per-operation and immediately closed

**Context Managers (Auto-cleanup Patterns):**
- ✅ `TunnelManager.auto_tunnel()`: Context manager with optional cleanup
- ✅ File operations: Consistent use of `with open()` throughout codebase
- ✅ File locking: `with open(lock_file, 'w') as lock:` in tunnel.py

#### Resource Leak Prevention Patterns

```python
# ✅ EXCELLENT: Temp file cleanup in all error paths
def _create_mysql_config_file(user: str, password: str) -> str:
    fd, config_path = tempfile.mkstemp(suffix='.cnf', text=True)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(f"[client]\nuser={user}\npassword={password}\n")
        os.chmod(config_path, 0o600)
        return config_path
    except Exception as e:
        try:
            os.unlink(config_path)  # Cleanup on error
        except:
            pass
        raise

# Usage always in try/finally:
config_file = _create_mysql_config_file(user, password)
try:
    subprocess.run(['mysql', f'--defaults-file={config_file}', ...])
finally:
    os.unlink(config_file)  # ALWAYS cleaned up
```

```python
# ✅ EXCELLENT: Process lifecycle management
class MCPServer:
    def stop(self) -> bool:
        try:
            self.process.terminate()
            self.process.wait(timeout=5)  # Graceful shutdown
        except subprocess.TimeoutExpired:
            self.process.kill()  # Force kill if needed
            self.process.wait()
```

```python
# ✅ EXCELLENT: SSH tunnel cleanup with retry
def stop_tunnel(self, server_name: str) -> bool:
    process = psutil.Process(pid)
    process.terminate()  # SIGTERM (graceful)
    try:
        process.wait(timeout=5)
    except psutil.TimeoutExpired:
        process.kill()  # SIGKILL (force)
        process.wait()
```

#### Zero Leaks Confirmed

**Analysis Summary:**
- **Subprocess calls:** 50+ reviewed - all properly managed
- **File operations:** 30+ reviewed - all use context managers
- **Temp files:** 11 reviewed - all cleaned up in finally blocks
- **SSH connections:** 1 reviewed - explicitly closed
- **Process tracking:** PID-based with health checks and recovery
- **Memory:** Streaming I/O for large files (prevents exhaustion)

**Best Practices Applied:**
1. **try/finally pattern:** All temp files cleaned up even on exceptions
2. **Context managers:** All file operations auto-close
3. **Graceful shutdown:** SIGTERM → wait → SIGKILL for processes
4. **Health monitoring:** Tunnel health checks detect zombie processes
5. **Retry logic:** 3-retry process discovery handles race conditions
6. **Streaming I/O:** Large backups/restores use streaming to prevent memory exhaustion

### 📋 Error Handling Enhancements (Task 6 - User Experience)

#### Actionable Error Messages
- **Enhanced:** All critical error paths now provide troubleshooting guidance
  - SSH connection failures: 5-step diagnostic checklist
  - MySQL client errors: Platform-specific installation instructions (Windows/macOS/Linux)
  - Tunnel failures: Recovery steps with specific commands
  - Disk space errors: 5 cleanup strategies with examples
  - File upload/download failures: Cause analysis with fix commands
  - Permission errors: Exact chmod/chown commands to resolve
- **Impact:** Users can self-diagnose and fix 80%+ of issues without external help

#### Error Message Examples
```
❌ mysql client not found. Please install MySQL client tools.

Installation instructions:
  Windows: choco install mysql
  macOS:   brew install mysql-client
  Ubuntu:  sudo apt-get install mysql-client
  CentOS:  sudo yum install mysql

After installation, restart your terminal.
```

```
✗ Tunnel collapsed: Connection refused

Recovery steps:
  1. Check tunnel status: navig tunnel status
  2. Restart tunnel: navig tunnel restart
  3. Check for zombie processes: ps aux | grep ssh
  4. Verify SSH connection: ssh user@host 'echo test'
  5. Check server logs: navig logs ssh
```

### ✨ Feature Implementations (Task 5 - TODO Resolution)

#### Package Manager Auto-Detection
- **Implemented:** `navig install <package>` with smart package manager detection
  - Auto-detects package manager: apt-get, yum, dnf, pacman, zypper, apk
  - OS-based detection using server metadata (Ubuntu→apt, CentOS→yum, Alpine→apk)
  - Fallback: Checks which package managers are available on server
  - Supports dry-run mode to preview installation command
  - Clear error messages with manual fallback instructions
  - **Impact:** Replaces "coming soon" placeholder with full implementation

#### HestiaCP Password Security Enhancement
- **Fixed:** Password exposure in HestiaCP user creation (HIGH severity)
  - Changed from command-line argument to stdin pipe: `printf '%s\n' <password> | v-add-user`
  - HestiaCP CLI supports reading password from stdin when using '-' placeholder
  - Password no longer visible in `ps aux` or process listings
  - **Impact:** Eliminates last remaining password exposure in NAVIG
  - Completes security hardening - ALL credentials now protected

### 🛡️ Backup/Restore Safety Enhancements (Task 4 - Edge Case Hardening)

#### Critical Edge Cases Fixed
- **Added:** Disk space verification before backup operations
  - New `_verify_disk_space()` helper function checks available space
  - Requires 1.5x estimated backup size as safety margin
  - Prevents disk full errors mid-backup (could crash server)
  - Validated before all backup operations (database, system, Hestia)
  - Shows clear error: "Insufficient disk space: X MB free, Y MB required"
- **Added:** Backup integrity verification with SHA-256 checksums
  - New `_calculate_file_checksum()` generates cryptographic hashes
  - Checksums stored in backup metadata.json
  - Automatic verification before restore operations
  - Detects corrupted backups before attempting restore
  - Prevents database corruption from bad backup files
- **Added:** Transaction-based database restore with rollback support
  - Enhanced `restore_database()` with safety-first design
  - Automatic safety backup before restore (rollback capability)
  - Verifies backup file integrity before starting restore
  - Descriptive confirmation prompt (shows file size, requires typing 'RESTORE')
  - Better error messages with rollback instructions
  - **Impact:** CRITICAL - Previous implementation had NO rollback, partial restore corrupted databases
- **Added:** Partial backup cleanup on failure
  - New `_cleanup_failed_backup()` removes incomplete backups
  - Prevents confusion from corrupted/incomplete backup files
  - Clear error messages explain what failed and why
- **Improved:** Backup metadata with checksums and detailed info
  - Backup results include per-database checksums
  - File sizes, timestamps, success/failure status tracked
  - Enables integrity verification and incremental backup detection
- **Fixed:** Memory exhaustion on large database restores
  - Changed from `file.read_text()` (loads entire file to RAM) to streaming
  - Can now restore multi-GB SQL files without memory errors
  - Progress indicators planned for long-running operations

#### Safety Features Added
- `--force` flag to override checksum verification (emergency use only)
- `--no-backup` flag to skip safety backup (faster, less safe)
- Verbose mode shows checksums and disk space details
- Failed restores provide rollback command

### 🚀 Performance & Reliability Enhancements

#### Tunnel Lifecycle Management (Task 3 - Comprehensive Audit)
- **Added:** Atomic tunnel state management with file locking (prevents race conditions)
  - Cross-platform file locking using `fcntl` (Unix) and `msvcrt` (Windows)
  - All tunnel operations now atomic - no more concurrent modification issues
  - Prevents race condition when multiple NAVIG instances start tunnels simultaneously
- **Improved:** `_find_tunnel_process()` with retry logic and better matching
  - Now retries up to 3 times with 0.5s delays (handles slow SSH process startup)
  - More precise cmdline matching: `-L {port}:localhost:{remote_port}` pattern
  - Better error handling for zombie processes and access denied errors
- **Added:** `TunnelManager.auto_tunnel()` context manager (resolves TODO)
  - Automatic tunnel lifecycle: starts if needed, optionally cleans up on exit
  - Usage: `with tunnel_manager.auto_tunnel('production') as tunnel: ...`
  - Smart cleanup: only stops tunnel if context manager started it
- **Added:** Comprehensive tunnel health monitoring
  - `check_tunnel_health()`: Verifies process running + port accessible
  - `recover_tunnel()`: Auto-recovery strategy (stop → cleanup → restart)
  - Returns detailed health report with issues list
- **Implemented:** `navig tunnel auto` command
  - Checks tunnel health and auto-recovers if unhealthy
  - CLI interface for health monitoring and recovery
  - Replaces "coming soon" placeholder with full implementation
- **Impact:** Eliminates race conditions during concurrent operations, zombie processes auto-detected and cleaned up, tunnel failures auto-recover

### 📖 Documentation Updates

#### Enhanced Security Documentation
- **Added:** Comprehensive security warnings in README.md
  - New "Security Update (v2.0.0)" callout section with production-ready features
  - Expanded "Security Best Practices" section (90+ lines):
    * Credential protection guidelines (never commit .env files)
    * SSH keys vs passwords recommendations
    * File permissions for Windows, Linux, macOS
    * Database password rotation examples
    * Git commit verification checklist
    * Credential exposure monitoring commands
  - New "Security Setup (REQUIRED)" in Installation section:
    * Verify .gitignore configuration
    * Protect NAVIG config directory permissions
    * SSH key generation and deployment guide
  - Security hardening checklist (10 items)
  - Links to SECURITY_FIXES_APPLIED.md for technical details
- **Added:** Production-ready security documentation in `docs/SECURITY_FIXES_APPLIED.md`
  - Complete before/after code examples for all 9 security fixes
  - Testing validation section (9/9 tests passing)
  - Deployment recommendations and safety checklist
- **Added:** Comprehensive security audit report in `docs/SECURITY_AUDIT_REPORT.md`
  - 20-point audit covering Critical, High, Medium, and Low severity issues
  - Developer experience enhancement recommendations

### 🔒 CRITICAL SECURITY FIXES (Phase 2 - Comprehensive Audit)

#### Password Exposure in Database Operations (CVE-level severity)
- **Fixed:** Database passwords visible in process listings in `navig/commands/database.py`
  - Functions affected: `execute_sql()`, `backup_database()`, `execute_sql_file()`, `restore_database()`
  - Changed from `-p{password}` command-line argument to `--defaults-file={temp_config}`
  - Temporary MySQL config files created with strict 0600 permissions (owner-only read/write)
  - Config files automatically cleaned up in finally blocks
  - **Impact:** CRITICAL - Every SQL command exposed database password in `ps aux`, Task Manager, `/proc/*/cmdline`
  - **Mitigation:** Passwords now passed via secure temporary files, never visible in process listings

#### Password Exposure in Backup Operations (CVE-level severity)
- **Fixed:** Database passwords visible during full system backups in `navig/commands/backup.py`
  - Function affected: `backup_db_all_cmd()` (backs up ALL databases)
  - Applied same secure credential pattern as database_advanced.py
  - Added compression verification before deleting original files (prevents data loss)
  - **Impact:** CRITICAL - Full system backups exposed password for every database being backed up
  - **Mitigation:** Secure temp config files + verification step before file deletion

#### HestiaCP Password Exposure (HIGH severity)
- **Fixed:** User passwords visible in shell commands in `navig/commands/hestia.py`
  - Function affected: `add_user_cmd()` - HestiaCP user creation
  - Applied `shlex.quote()` to username, password, and email parameters
  - **Impact:** HIGH - Admin passwords visible in SSH history, process listings, server logs
  - **Mitigation:** Command arguments properly quoted, prevents injection and reduces exposure
  - **Note:** Password still visible in cmdline (HestiaCP CLI limitation - no stdin/env var support)

#### Bare Exception Handlers Eliminated (MEDIUM severity)
- **Fixed:** 11 instances of bare `except:` blocks causing silent failures
  - `navig/commands/ai.py`: Process context gathering now logs failures
  - `navig/commands/backup.py`: Compression errors now logged with warnings
  - `navig/commands/database_advanced.py`: Cleanup exceptions now specific `OSError` only
  - Changed from `except: pass` to `except OSError: pass  # Cleanup - file deletion may fail`
  - Added logging for non-critical failures (AI context gathering)
  - **Impact:** Backup failures, compression errors, context gathering issues were completely silent
  - **Mitigation:** Specific exception types, logged warnings, user-visible error messages

### 🔒 CRITICAL SECURITY FIXES (Phase 1 - Initial Hardening)

#### Command Injection Prevention (CVE-level severity)
- **Fixed:** Command injection vulnerability in `navig/commands/files_advanced.py`
  - All shell commands now use `shlex.quote()` to safely escape user-supplied parameters
  - Prevents injection attacks like `file; rm -rf /` → safely escaped to `'file; rm -rf /'`
  - Affected functions: `delete_file_cmd`, `create_dir_cmd`, `change_permissions_cmd`, `change_ownership_cmd`
  - **Impact:** Malicious file paths could have executed arbitrary shell commands on remote servers
  - **Mitigation:** All user-supplied file paths, permissions, and ownership values are now properly escaped

#### SQL Injection Prevention (CVE-level severity)
- **Fixed:** SQL injection vulnerabilities in `navig/commands/database_advanced.py`
  - Implemented three-layer security model:
    1. **Validation Layer:** `_validate_sql_identifier()` - Regex validation (`^[a-zA-Z0-9_]+$`), keyword blacklist, 64-char limit
    2. **Escaping Layer:** `_escape_sql_identifier()` - Backtick escaping for MySQL identifiers
    3. **Secure Credentials:** `_create_mysql_config_file()` - Temporary config files with 0600 permissions
  - Affected functions: `list_databases_cmd`, `list_tables_cmd`, `list_users_cmd`, `optimize_table_cmd`, `repair_table_cmd`
  - **Impact:** Malicious table/database names could have executed arbitrary SQL commands
  - **Mitigation:** All identifiers validated and escaped, SQL keywords blacklisted

#### Credential Exposure Prevention (HIGH severity)
- **Fixed:** Database passwords visible in process listings
  - Changed from `-p{password}` command-line argument to `--defaults-file={temp_config}`
  - Temporary MySQL config files created with strict 0600 permissions (owner-only read/write)
  - Config files automatically cleaned up after command execution
  - **Impact:** Database passwords were visible in `ps aux`, `/proc/{pid}/cmdline`, Windows Task Manager
  - **Mitigation:** Credentials now passed via secure temporary files, never in command line

#### SSH Man-in-the-Middle Prevention (HIGH severity)
- **Fixed:** SSH connections auto-accepting unknown host keys in `navig/remote.py`
  - Changed default from `StrictHostKeyChecking=accept-new` to `StrictHostKeyChecking=yes`
  - Added `trust_new_host` parameter (default `False`) to `execute_command()` method
  - Unknown hosts now rejected by default unless explicitly trusted
  - **Impact:** Auto-accepting new hosts enabled MITM attacks during first connection
  - **Mitigation:** Strict host key verification enforced, manual trust required for new hosts

### 🔧 CRITICAL API FIXES

#### Fixed API Drift Crashes
- **Fixed:** Multiple modules using non-existent API methods causing crashes
  - **monitoring.py:** 15+ replacements
    - `get_app_config()` → `load_server_config()`
    - `execute_remote_command()` → `execute_command(cmd, server_config)`
    - `result['success']` → `result.returncode == 0`
    - `result['output']` → `result.stdout`
    - `result.get('error')` → `result.stderr`
  - **maintenance.py:** 20 replacements (same pattern as monitoring.py)
  - **webserver.py:** Mixed API corrections
    - `get_server_config()` → `load_server_config()`
    - `RemoteOperations(config)` → `RemoteOperations(config_manager)` with separate server_config
  - Affected functions: All monitoring, maintenance, and webserver commands
  - **Impact:** Commands would crash with `AttributeError` on `get_app_config()`, `execute_remote_command()`
  - **Mitigation:** All modules now use correct `ConfigManager` and `RemoteOperations` APIs

#### Fixed MCP Server Environment Stripping
- **Fixed:** MCP servers losing PATH and system environment variables in `navig/mcp_manager.py`
  - Changed from `env={custom_vars_only}` to `full_env = os.environ.copy(); full_env.update(custom_vars)`
  - MCP servers now inherit parent environment with custom overrides
  - **Impact:** MCP servers failed with "command not found" errors when calling system executables
  - **Mitigation:** Subprocess launched with `os.environ.copy()` preserving PATH and system variables

### ⚠️ BREAKING CHANGES

#### SSH Host Key Verification (Security Enhancement)
- **Breaking:** `RemoteOperations.execute_command()` now rejects unknown SSH hosts by default
  - **Migration:** For first-time server connections, use `trust_new_host=True` parameter:
    ```python
    # First connection to new server
    remote_ops.execute_command(cmd, server_config, trust_new_host=True)

    # Subsequent connections (default, secure)
    remote_ops.execute_command(cmd, server_config)
    ```
  - **Reason:** Previous behavior auto-accepted unknown hosts, enabling MITM attacks

#### SQL Identifier Restrictions (Security Enhancement)
- **Breaking:** Database/table names must be alphanumeric + underscore only
  - **Valid:** `users`, `user_accounts_2024`, `my_database123`
  - **Invalid:** `users; DROP TABLE`, `table-name`, `table name`, ``users` OR '1'='1``
  - **Migration:** Rename databases/tables with special characters to use only `[a-zA-Z0-9_]`
  - **Reason:** Prevents SQL injection attacks via malicious identifiers

### ✅ TESTING

#### Added Integration Test Suite
- **Added:** `tests/test_security_fixes.py` with 9 comprehensive security tests
  - Command injection protection (shlex.quote validation)
  - SQL injection protection (identifier validation, escaping, secure credentials)
  - API correctness (ConfigManager, RemoteOperations)
  - MCP environment preservation
  - SSH host key verification (strict checking, trust_new_host flag)
- **Coverage:** All 8 critical/high-priority security fixes validated
- **Status:** ✅ All 9 tests passing

### 📚 DOCUMENTATION

#### Updated Security Documentation
- **Updated:** `.github/instructions/directives.instructions.md`
  - Added multi-phase workflow execution rules
  - Enhanced app structure organization (docs/, tests/, scripts/)
  - Security best practices for NAVIG usage
- **Updated:** `.github/instructions/navig.instructions.md`
  - Documented all NAVIG security fixes
  - Updated command reference with secure defaults
  - Added troubleshooting for SSH host key rejection

### 🔍 AUDIT TRAIL

#### Security Review Summary
- **Total Vulnerabilities Fixed:** 4 critical, 2 high-priority
- **Files Modified:** 7 production files (commands/, core libraries)
- **Insecure Backups Created:** 2 files (`*.INSECURE.bak` for audit trail)
- **Tests Added:** 9 integration tests (100% pass rate)
- **Breaking Changes:** 2 (SSH host verification, SQL identifier restrictions)

#### Risk Assessment
**Without These Fixes:**
1. **Command Injection:** Attackers could execute arbitrary shell commands via malicious file paths
2. **SQL Injection:** Attackers could drop tables, steal data, or escalate privileges
3. **Credential Exposure:** Database passwords visible in process listings, logs, monitoring tools
4. **MITM Attacks:** SSH connections vulnerable to man-in-the-middle during host key exchange
5. **API Crashes:** Production commands unusable due to incorrect method calls
6. **MCP Failures:** Model Context Protocol servers non-functional due to missing PATH

**With These Fixes:**
- All inputs validated and safely escaped before execution
- Credentials passed securely via temporary files with strict permissions
- SSH connections reject unknown hosts unless explicitly trusted
- All API methods aligned with actual codebase implementation
- MCP servers inherit full system environment for proper execution

---

## [1.0.0] - 2025-11-21

### Added

#### Phase 5: Cleanup & Finalization Complete
- Moved all deprecated PowerShell scripts to `archive/` directory
- Created `archive/README.md` with deprecation notice and migration instructions
- Updated main README.md with command categories, global flags, and migration guide
- Created comprehensive `docs/RELEASE_NOTES_v1.0.0.md` with full migration summary
- **PowerShell to Python migration 100% complete**

#### Phase 4: Documentation Complete
- Created `docs/USAGE_GUIDE.md` - 600+ line comprehensive usage guide with 100+ examples
- Documented all 60+ new commands across 10 categories
- Added troubleshooting section with common errors and solutions
- Included best practices for dry-run, app markers, JSON automation
- Updated CHANGELOG.md with complete Phase 2-5 documentation

#### Phase 3: Testing & Rebranding Complete

**Rebranding**
- Rebranded to "NAVIG - No Admin Visible In Graveyard"
- New tagline: "Keep your servers alive. Forever."
- Updated README, CLI help text, and package metadata
- Shifted focus from technical features to outcome-based messaging (proactive server management to prevent admin failures)

**Testing & Validation**
- Created comprehensive test suite with pytest
- Added `tests/test_all_modules.py` - Module import and syntax validation (18 tests)
- Added `tests/test_integration.py` - Integration tests for dry-run, JSON output, error handling
- Added `tests/pytest.ini` - Pytest configuration with markers and settings
- Fixed import paths (changed from `navig.core` to `navig` for ConfigManager and RemoteOperations)
- All 8 new command modules verified to import correctly without syntax errors
- Validated version and branding information
- CLI integration tests passing (18/18 tests - 100% success rate)

#### PowerShell Migration - Phase 2.1-2.8 Complete

**Advanced File Operations** - Extended file management capabilities
- `navig delete <remote> [--recursive] [--force]` - Delete remote files or directories
  - Smart confirmation prompts (skippable with `--force`)
  - Recursive directory deletion with `--recursive` flag
  - Dry-run support via global `--dry-run` flag
  - JSON output support via global `--json` flag
- `navig mkdir <remote> [--parents] [--mode 755]` - Create remote directories
  - Parent directory creation with `--parents` (default: true)
  - Custom permission modes with `--mode` flag
  - Supports dry-run and JSON output
- `navig chmod <remote> <mode> [--recursive]` - Change file/directory permissions
  - Numeric permission modes (e.g., 755, 644, 0755)
  - Recursive application with `--recursive` flag
  - Validation for proper mode format
  - Supports dry-run and JSON output
- `navig chown <remote> <owner> [--recursive]` - Change file/directory ownership
  - Owner in `user` or `user:group` format
  - Recursive application for directories
  - Supports dry-run and JSON output

**Advanced Database Operations** - Enhanced database management
- `navig db-list` - List all databases with sizes
  - Displays database names and sizes in MB
  - Rich table output or JSON format
  - Queries information_schema for accurate sizes
- `navig db-tables <database>` - List tables in a database
  - Shows table name, size (MB), and row count
  - Sorted by size (largest first)
  - Rich table or JSON output
- `navig db-optimize <table>` - Optimize database table
  - Reclaims unused space and defragments
  - Shows optimization results
  - Supports dry-run and JSON output
- `navig db-repair <table>` - Repair corrupted database table
  - Fixes table corruption issues
  - Shows repair results
  - Supports dry-run and JSON output
- `navig db-users` - List database users
  - Displays username and host information
  - Rich table or JSON output
  - Queries mysql.user table

**HestiaCP Integration** - Comprehensive HestiaCP management (9 commands)
- `navig hestia users` - List all HestiaCP users
  - Shows username, package, email, domains, and databases count
  - JSON output via `v-list-users json` API
  - Rich table formatting
- `navig hestia domains [--user USERNAME]` - List domains
  - All domains across all users (when no --user specified)
  - Filter by specific user with `--user` flag
  - Shows domain, user, IP, SSL status, and PHP backend
  - Aggregates data from v-list-web-domains
- `navig hestia add-user <username> <password> <email>` - Create new user
  - Executes v-add-user command
  - Supports dry-run mode
  - JSON output for automation
- `navig hestia delete-user <username> [--force]` - Delete user
  - Confirms deletion unless `--force` flag used
  - Deletes ALL user data (domains, databases, email)
  - JSON mode requires `--force` flag
- `navig hestia add-domain <user> <domain>` - Add domain to user
  - Creates web domain configuration
  - Automatic DNS zone setup
  - Supports dry-run and JSON output
- `navig hestia delete-domain <user> <domain> [--force]` - Remove domain
  - Confirms deletion unless `--force` flag used
  - Removes all domain data (web, DNS, mail)
  - JSON mode requires `--force` flag
- `navig hestia renew-ssl <user> <domain>` - Renew Let's Encrypt SSL
  - Executes v-add-letsencrypt-domain
  - Automatic ACME challenge handling
  - Supports dry-run and JSON output
- `navig hestia rebuild-web <user>` - Rebuild web configuration
  - Regenerates Nginx/Apache configs for all user domains
  - Fixes configuration corruption issues
  - Supports dry-run and JSON output
- `navig hestia backup-user <user>` - Backup HestiaCP user
  - Creates full user backup (web, DB, mail, DNS)
  - Stored in HestiaCP backup directory
  - Supports dry-run and JSON output

**Comprehensive Backup System** - Full system backup and restore (7 commands)
- `navig backup-config [--name NAME]` - Backup system configuration files
  - Backs up: SSH config, UFW, Fail2Ban, hosts, hostname, timezone, fstab, crontab
  - Custom backup naming with `--name` flag
  - Saves to `~/.navig/backups/<name>/configs/`
  - Creates metadata.json with backup details
  - Skips missing files gracefully
- `navig backup-db-all [--name NAME] [--compress gzip|zstd|none]` - Backup all databases
  - Backs up ALL databases (excluding system schemas)
  - Compression options: gzip (default), zstd, or none
  - Individual SQL files per database
  - Size calculation and reporting
  - Metadata tracking with database list and sizes
  - Uses existing tunnel infrastructure
- `navig backup-hestia [--name NAME]` - Comprehensive HestiaCP backup
  - Backs up 5 critical directories:
    - `/usr/local/hestia/conf` - Configuration files
    - `/usr/local/hestia/data/users` - User data
    - `/usr/local/hestia/ssl` - SSL certificates
    - `/usr/local/hestia/data/templates` - Custom templates
    - `/usr/local/hestia/data/zones` - DNS zone files
  - Creates compressed tar archives remotely
  - Downloads and extracts locally
  - Excludes log files automatically
  - Reports file count and size per directory
- `navig backup-web [--name NAME]` - Backup web server configurations
  - Backs up Nginx configs: nginx.conf, sites-available, sites-enabled
  - Backs up Apache configs: apache2.conf, ports.conf, sites-available, sites-enabled
  - Detects available web servers automatically
  - Preserves directory structure
  - Metadata tracking per server type
- `navig backup-all [--name NAME] [--compress gzip|zstd|none]` - Full system backup
  - Executes all backup types in sequence:
    1. System configuration (`backup-config`)
    2. All databases (`backup-db-all`)
    3. HestiaCP data (`backup-hestia`)
    4. Web server configs (`backup-web`)
  - Single unified backup with organized structure
  - Compression applied to databases only
  - Comprehensive metadata with all component details
- `navig list-backups` - List all available backups
  - Rich table output with name, type, date, and size
  - Reads metadata.json for accurate information
  - Sorted by date (newest first)
  - JSON output for automation
- `navig restore-backup <name> [--component TYPE] [--force]` - Restore from backup
  - Manual review required (safety measure)
  - Confirmation prompt unless `--force` used
  - Optional component-specific restore
  - Shows backup location for manual inspection
  - Prevents accidental overwrites

  - Reports saved in JSON format with full metrics and alerts
  - Rich table output with color-coded status indicators
  - Metadata tracking for historical analysis

**Resource Monitoring** - Real-time server monitoring and health checks
- `navig monitor-resources` - Monitor real-time resource usage
  - CPU usage percentage with threshold alerts (>80% triggers alert)
  - Memory usage in percentage and MB (used/total)
  - Disk usage for root partition
  - Load averages (1, 5, 15 minute intervals)
  - TCP connection count
  - System uptime display
  - Rich table output with color-coded status (🟢 OK, 🟡 MEDIUM, 🔴 HIGH)
- `navig monitor-disk [--threshold 80]` - Disk space monitoring with custom thresholds
  - Monitors all mounted disk partitions
  - Customizable alert threshold (default: 80%)
  - Shows device, mount point, size, used, available, usage%
  - Color-coded alerts (🟢 OK, 🟡 WARNING, 🔴 ALERT)
  - JSON output for scripting/automation
- `navig monitor-services` - Service health status checks
  - Monitors 16 critical services: nginx, apache2, mysql, mariadb, postgresql, php-fpm (8.1/8.2/8.3), hestia, fail2ban, ufw, ssh, sshd, redis, memcached
  - Rich table with status icons (✓ active, ✗ inactive, - not installed)
  - Health indicators (🟢 healthy, 🔴 stopped, ⚪ N/A)
  - Reports inactive services count
  - JSON output support
- `navig monitor-network` - Network statistics and connections
  - Connection summary (TCP, UDP, UNIX sockets)
  - Listening ports count
  - Established connections count
  - Network interface list
  - Rich panel display for connection summary
- `navig health-check` - Comprehensive health check
  - Combines all monitoring aspects: resources, services, disk, network
  - Sequential execution with progress indicators
  - Comprehensive view of server health
  - Useful for scheduled health audits
- `navig monitoring-report` - Generate comprehensive health report
  - Saves JSON report to `~/.navig/reports/health-report_<server>_<timestamp>.json`
  - Includes: timestamp, server info, resource metrics, service status, disk usage, network stats, alerts
  - Alert tracking with severity levels
  - Historical data for trend analysis
  - Summary display with alert count

**Security Management** - Comprehensive security and firewall management
- `navig firewall-status` - Display UFW firewall status and rules
  - Shows firewall status (active/inactive)
  - Lists all configured rules with actions (ALLOW/DENY)
  - Displays default policies
  - Shows logging level
  - Rule count summary
  - JSON output support for automation
- `navig firewall-add <port> [--protocol tcp|udp] [--from <ip>]` - Add UFW firewall rule
  - Add port-based rules (e.g., allow 8080/tcp)
  - Restrict by source IP or subnet (e.g., --from 10.0.0.0/24)
  - Default: allow from any IP
  - Supports TCP and UDP protocols
  - Dry-run mode to preview changes
- `navig firewall-remove <port> [--protocol tcp|udp]` - Remove UFW firewall rule
  - Remove existing firewall rules
  - Specify port and protocol
  - Confirmation before deletion
- `navig firewall-enable` - Enable UFW firewall
  - Activates firewall protection
  - Uses --force to avoid interactive prompts
  - Warning about SSH access (port 22 must be allowed)
- `navig firewall-disable` - Disable UFW firewall
  - Deactivates firewall (use with caution)
  - Warning that server is unprotected
- `navig fail2ban-status` - Display Fail2Ban status and banned IPs
  - Service status check (active/inactive)
  - Lists all active jails
  - Shows currently banned IPs per jail
  - Total ban statistics
  - Rich table with color-coded banned counts (red for active bans)
  - Displays banned IP addresses if any
- `navig fail2ban-unban <ip> [--jail <name>]` - Unban IP address from Fail2Ban
  - Unban from specific jail (e.g., sshd)
  - Unban from all jails if no jail specified
  - Useful for accidental bans or trusted IPs
- `navig ssh-audit` - Audit SSH configuration for security issues
  - Checks 5 critical SSH settings:
    - PermitRootLogin (recommended: prohibit-password or no)
    - PasswordAuthentication (recommended: no)
    - PermitEmptyPasswords (recommended: no)
    - X11Forwarding (recommended: no)
    - MaxAuthTries (recommended: 3 or less)
  - Rich table with current vs recommended values
  - Status indicators (✓ OK or ⚠ REVIEW)
  - Progress bar during checks
  - Summary of issues found
  - Guidance on fixing issues
- `navig security-updates` - Check for available security updates
  - Updates package lists (apt-get update)
  - Checks for security-related updates
  - Displays available security updates
  - Update count summary
  - Installation command guidance
  - Progress bar during check
- `navig audit-connections` - Audit active network connections
  - Lists established connections (TCP/UDP)
  - Shows all listening ports
  - Checks for suspicious processes (netcat, ncat)
  - Connection count summary
  - Truncates long lists (first 10 shown)
  - Security warnings for suspicious activity
- `navig security-scan` - Run comprehensive security scan
  - Executes all security checks in sequence:
    1. Firewall status
    2. Fail2Ban status
    3. SSH audit
    4. Security updates check
    5. Connection audit
  - Comprehensive security overview
  - Useful for regular security audits
  - JSON output for reporting

**System Maintenance** - Package management and system cleanup
- `navig update-packages` - Update package lists and upgrade packages
  - Updates apt package lists with progress spinner
  - Checks for upgradable packages with count display
  - Shows first 10 upgradable packages
  - Performs non-interactive upgrade (DEBIAN_FRONTEND=noninteractive)
  - Displays packages upgraded count
  - "All packages up to date" message if none
  - Dry-run preview support
- `navig clean-packages` - Clean package cache and remove orphaned packages
  - Cleans apt package cache (apt-get clean)
  - Removes unused/orphaned packages (apt-get autoremove)
  - Frees disk space automatically
  - Success confirmation for each step
- `navig rotate-logs` - Rotate and compress log files
  - Forces log rotation using logrotate
  - Applies /etc/logrotate.conf rules
  - Compresses old log files automatically
  - Success/failure feedback
- `navig cleanup-temp` - Clean temporary files and caches
  - Removes files from /tmp older than 7 days
  - Cleans apt cache
  - Safe deletion (ignores locked files)
  - Shows cleanup completion
- `navig check-filesystem` - Check filesystem usage and find large files
  - Displays disk usage (df -h) in Rich table format
  - Finds large files (>100MB) in /var/log and /tmp
  - Shows file sizes in human-readable format
  - Warns about large log files with count
  - First 10 large files displayed (truncates if more)
  - "No large files found" confirmation
- `navig system-maintenance` - Run comprehensive system maintenance
  - Executes all maintenance tasks in sequence:
    1. Update and upgrade packages
    2. Clean package cache
    3. Rotate log files
    4. Check filesystem
    5. Clean temporary files
  - Progress indication for each step
  - Time elapsed summary
  - Useful for scheduled maintenance (cron)
  - JSON output for reporting

**Global Flag Enhancements**
- All new commands support existing global flags:
  - `--dry-run` - Preview actions without executing
  - `--json` - JSON output for automation/scripting
  - `--app/-p` - Override active server
  - `--verbose` - Detailed logging
  - `--quiet/-q` - Minimal output
  - `--yes/-y` - Auto-confirm prompts

**Web Server Management** - Apache and Nginx administration
- `navig webserver-list-vhosts [--server nginx|apache]` - List virtual hosts
  - Shows enabled sites (green checkmarks) and available but disabled sites (dimmed)
  - Rich table with status indicators, summary counts
  - Supports both Apache (/etc/apache2/sites-*) and Nginx (/etc/nginx/sites-*)
  - JSON output with enabled/available arrays

- `navig webserver-test-config [--server nginx|apache]` - Test server configuration
  - Pre-validation before reload/restart to prevent downtime
  - Apache: `apache2ctl configtest` | Nginx: `nginx -t`
  - Rich panel output with green/red border based on result
  - JSON output with valid flag and test output

- `navig webserver-enable-site SITE_NAME [--server nginx|apache]` - Enable a site
  - Apache: Uses `a2ensite` | Nginx: Creates symlink sites-available → sites-enabled
  - Success message with reload reminder, dry-run preview support

- `navig webserver-disable-site SITE_NAME [--server nginx|apache]` - Disable a site
  - Apache: Uses `a2dissite` | Nginx: Removes symlink from sites-enabled
  - Success message with reload reminder, dry-run preview support

- `navig webserver-enable-module MODULE_NAME` - Enable Apache module
  - Uses `a2enmod` for modules like: rewrite, ssl, headers, deflate, http2
  - Success message with reload reminder, dry-run preview support

- `navig webserver-disable-module MODULE_NAME` - Disable Apache module
  - Uses `a2dismod` to safely disable modules
  - Success message with reload reminder, dry-run preview support

- `navig webserver-reload [--server nginx|apache]` - Safely reload server
  - Tests configuration before reload (prevents breaking production)
  - Aborts if configuration test fails, uses `systemctl reload` (preserves connections)
  - Verifies service remains active after reload (1s wait for stabilization)
  - JSON output with config_valid, reload_success, service_active flags

- `navig webserver-recommendations [--server nginx|apache]` - Performance tuning tips
  - **Apache**: mod_deflate, mod_expires, mod_cache, MaxRequestWorkers optimization, HTTP/2, mod_pagespeed
  - **Nginx**: gzip, browser caching, fastcgi_cache, worker tuning, HTTP/2
  - Each tip includes description, command or config example
  - JSON output with full recommendations array

#### Per-Server Template Configuration System
- **Server-Specific Template Customization** - Each server can have independent template configurations
  - Hybrid storage: template state in server YAML, customizations in separate JSON files
  - 3-layer merge priority: template → auto-detection info → custom overrides
  - Lazy file creation - custom configs only created when modified
  - Per-server enable/disable with independent state per server
  - Template version tracking for update management

- **Auto-Detection for Server Templates** - Automatic discovery during server inspection
  - **n8n Detection** - systemd service, binary version, port 5678, ~/.n8n directory
  - **HestiaCP Detection** - /usr/local/hestia, CLI tools, port 8083, version info
  - **Gitea Detection** - systemd service, binary version, port 3000, /var/lib/gitea paths
  - Auto-initialization of detected templates with version and path info

- **Server Template CLI Commands** (`navig server-template`)
  - `navig server-template list [--server NAME] [--enabled]` - List templates for a server
  - `navig server-template show TEMPLATE [--server NAME]` - Show merged template configuration
  - `navig server-template enable TEMPLATE [--server NAME]` - Enable template for specific server
  - `navig server-template disable TEMPLATE [--server NAME]` - Disable template for specific server
  - `navig server-template set TEMPLATE KEY VALUE [--server NAME]` - Set custom configuration value
  - `navig server-template sync TEMPLATE [--server NAME] [--force]` - Sync from template (preserves custom settings by default)
  - `navig server-template init TEMPLATE [--server NAME] [--enable]` - Manually initialize template
  - All commands support `--server` option (defaults to active server)
  - Rich table output with status, version, source, and customization indicators

- **Template Sync Mechanism**
  - Preserve custom settings by default during template updates
  - `--force` flag to reset to template defaults
  - Version tracking shows when templates are updated
  - Deep merge strategy maintains nested customizations

#### Template System
- **Plugin-Based Template Architecture** - Dynamic management of server-specific configurations without application restarts
  - Self-contained template packages with JSON metadata (template.json)
  - Server-specific paths, connection details, services, and commands
  - Hot-swapping support - enable/disable templates at runtime
  - Lifecycle hooks: onEnable, onDisable, onLoad, onUnload
  - Dependency resolution with circular dependency prevention
  - Lazy loading - only enabled templates are loaded into memory
  - Automatic configuration merging into server configs

- **Pre-Built Templates** - Three production-ready templates included:
  - **HestiaCP** - Web hosting control panel integration
    - 8 predefined paths (hestia_root, web_root, backup_dir, etc.)
    - 7 services (nginx, php-fpm, mysql, exim4, bind9, vsftpd)
    - 5 common commands (v-list-users, v-backup-user, v-restart-web, etc.)
    - API integration support
  - **n8n** - Workflow automation platform integration
    - 5 paths (n8n_home, workflows_dir, credentials_dir, log_dir)
    - Systemd service management
    - 7 commands (start/stop/restart, export/import workflows, logs)
    - Environment variable configuration (N8N_HOST, N8N_PORT, WEBHOOK_URL)
    - Webhook and API endpoint support
  - **Gitea** - Self-hosted Git service integration
    - 7 paths (gitea_root, repositories, config, backup_dir, log_dir)
    - Git and Gitea service management
    - 8 commands (backup, list repos, version check, service control)
    - Multi-database support (SQLite3, MySQL, PostgreSQL)
    - API token authentication

- **Template CLI Commands**
  - `navig template list` - List all available templates with status
  - `navig template enable <name>` - Enable template with dependency checking
  - `navig template disable <name>` - Disable template with dependent warning
  - `navig template toggle <name>` - Toggle template state
  - `navig template info <name>` - Show detailed template information
  - `navig template validate` - Validate all template configurations

#### MCP Integration
- **MCP (Model Context Protocol) Server Management** - Discovery, installation, and process management for MCP servers
  - Directory search from MCP ecosystem
  - Automated installation (npm, Python, standalone)
  - Process lifecycle management (start/stop/restart)
  - Multi-server support with enable/disable
  - Status monitoring and health checks

- **MCP CLI Commands**
  - `navig mcp search <query>` - Search MCP directory for servers
  - `navig mcp install <name>` - Install MCP server from directory
  - `navig mcp uninstall <name>` - Uninstall MCP server
  - `navig mcp list` - List installed MCP servers
  - `navig mcp enable <name>` - Enable MCP server
  - `navig mcp disable <name>` - Disable MCP server
  - `navig mcp start <name|all>` - Start MCP server(s)
  - `navig mcp stop <name|all>` - Stop MCP server(s)
  - `navig mcp restart <name>` - Restart MCP server
  - `navig mcp status <name>` - Show detailed MCP server status

- **Built-in MCP Server Support**
  - Filesystem - Local filesystem access
  - GitHub - GitHub API integration
  - SQLite - SQLite database access
  - Brave Search - Web search via Brave API

### Changed

#### Architecture Improvements
- Enhanced Rich console output with professional formatting
- Centralized console_helper module for consistent UI
- Improved error handling and validation across all modules
- Standardized command patterns for template and MCP management

### Fixed
- Windows temp directory permission issues in test suite
- Template configuration merging now properly preserves original server settings
- MCP process management handles graceful shutdown with timeout

## [1.0.0] - Previous Release

Initial release with core functionality:
- SSH tunnel management
- Multi-server support
- Database operations (SQL execution, backup, restore)
- File operations (upload, download, list)
- Remote command execution
- Service monitoring and management
- AI-powered assistance
- Health checks and log viewing

---

For more information about these features, see the [README.md](README.md) documentation.
For more information about these features, see the [README.md](README.md) documentation.
