# NAVIG Ecosphere — Master Execution Plan 2026–2027+

> **Strategic source of truth** for the entire ecosphere.
> Wired to `ROADMAP.md` as the canonical strategy document.
> Surface in navig-deck as a first-class dashboard view — every checkbox resolves to a Mission in `schemas/mission.schema.json`, tracked through the full ExecutionReceipt lifecycle from creation through archival.

---

## How to Read This Plan

- Each phase is a changelog block, segmented by month and hard deadline.
- `[ ]` = not started · `[~]` = in progress · `[x]` = done
- Every item maps to a real component in the current codebase or a precisely defined gap.
- Each checkbox is a **Mission** in `schemas/mission.schema.json`, tracked through the full ExecutionReceipt lifecycle.

---

## 2026 — Foundation, NavigOS & Public Launch

---

### January 2026 — Stabilisation Sprint
**Deadline: 2026-01-31**

- [x] Audit all 586 indexed files for dead imports and broken references
- [x] Stabilise storage engine — resolve write-batcher race conditions in `storage/write_batcher.py`
- [x] Ensure all tests in `tests/` pass clean on CI (`pytest.ini` baseline)
- [x] Harden daemon supervisor restart logic to crash-proof standard — `daemon/supervisor.py`
- [x] Conduct vault encryption audit — `vault/encryption.py` and `vault/validators.py`
- [x] Freeze core contract schemas: Node, Mission, ExecutionReceipt (`schemas/*.schema.json`)
- [x] Auto-generate internal API surface documentation — `tools/api_schema.py`
- [x] Merge `_rust_legacy` into archived branch — Python stack is canonical

---

### February 2026 — Mesh Hardening + NavigOS Core Identity
**Deadline: 2026-02-28**

#### Mesh & Agent
- [~] Complete Flux Mesh Phase 2 — multi-hop routing in `mesh/router.py`
- [~] Stabilise mesh discovery across NAT — hole-punching in `mesh/discovery.py`
- [~] Achieve MCP client/server round-trip reliability — `mcp/client.py` + `mcp/transport.py`
- [~] Reach agent config loader unit coverage ≥ 90% — `agent_config_loader.py`
- [~] Test LLM router fallback chain end-to-end — `llm_router.py` + `providers/fallback.py`
- [~] Benchmark memory RAG pipeline — `memory/rag.py` + `benchmarks/bench_sqlite_engine.py`
- [~] Harden safety guard — `safety_guard.py` + `test_safety_pipeline.py` all green
- [~] Review approval policy layer — `approval/policies.py` + `approval/manager.py`

#### NavigOS Identity Foundation (NAV-001, 002, 003, 011)
- [ ] Implement `navig/commands/init.py` — scaffold `.navig/` on any path with `userdata/` structure
- [ ] Write `userdata/identity.json` on init — schema: `name, tone, os_name, version, language, timezone, created_at, node_id`
- [ ] Implement Ed25519 keypair generation — `navig/vault/crypto.py`; write `userdata/passport.json`
- [ ] Implement `navig passport seal / verify / reseal` commands
- [ ] Implement `.blackbox/` AES-256 encrypted vault — `navig/vault/` module
- [ ] Implement `navig vault set/get/lock/unlock` CLI; `${BLACKBOX:key}` runtime secret resolution
- [ ] Implement identity path migration — move flat-root `identity.json` → `userdata/`; detect legacy with deprecation warning
- [ ] Implement `navig.isolation: true/false` flag — disables all outbound calls when on

#### Blueprint Plans Written (this session)
- [x] `DECISIONS_LOG.md` — 11 decisions logged, Q-001 resolved
- [x] All 15 plan files generated in `.navig/plans/`
- [x] All 65 task files generated in `.navig/tasks/`

---

### March 2026 — NavigOS Nervous System: Inbox Router, Settings, Memory
**Deadline: 2026-03-31**

#### Inbox Neuron Router (NAV-013, blueprint Sections 1–11)
- [ ] Cross-platform filesystem watcher — `navig/inbox/watcher.py` using `watchfiles >= 0.21`
- [ ] SQLite inbox event schema — `inbox_events` + `routing_decisions` tables; use `storage/engine.py`
- [ ] LLM-based inbox classifier — `navig/inbox/classifier.py`; keyword BM25 fallback for offline
- [ ] Router dispatch — `navig/inbox/router.py`; COPY / MOVE / LINK modes; conflict resolution
- [ ] Pre/post routing hook system — `navig/inbox/hooks.py`; hooks defined per `skills.json`
- [ ] `navig inbox ui` — TUI review panel (`rich` / `textual`) showing file → destination + confidence
- [ ] `navig inbox add <url>` — fetch, classify, and route web content (article, PDF, YouTube)
- [ ] `navig inbox stats` — routing summary + `--json` flag for Deck widget
- [ ] Telegram → inbox pipeline — forwarded messages/files land in global `inbox/`

#### Layered Settings System
- [ ] VSCode-style layered resolver — `navig/settings/resolver.py`; chain: global → layer → project → local
- [ ] `${BLACKBOX:key}` secret reference resolution at runtime in all config loaders
- [ ] `navig profile set/show/export` — `userdata/profile.json` lifecycle
- [ ] Write default global `settings.json` with all `navig.*` keys from blueprint; document in HANDBOOK.md

#### 3-Tier LanceDB Memory (NAV-004, 014, 015)
- [ ] `navig/memory/lancedb_engine.py` — Global Brain / Layer / Project tiers, LanceDB embedded
- [ ] `memory.add(text, context_tier)` + `memory.search(query, tiers=[])` + `memory.forget(id)`
- [ ] ChromaDB sync adapter for cross-device global-tier sync — `navig/memory/chroma_sync.py`
- [ ] BM25 keyword fallback — `navig/memory/bm25_fallback.py` using `rank_bm25`
- [ ] `navig memory add / search / forget` CLI with `--tier` flag (NAV-015)
- [ ] Skills inheritance — `navig skills list / tree` with source path annotation (NAV-022/023)

#### Universal Drive Mounting (NAV-012)
- [ ] `navig/commands/mount.py` — `add / list / remove / verify / sync`
- [ ] Junction registry at `%USERPROFILE%\.navig\registry\drives.json`
- [ ] Auto-verify on daemon start; Telegram notification on dead mount
- [ ] Auto-regenerate `mount-drive.ps1` helper on `navig mount sync`

---

### April 2026 — NavigOS Layers: Scaffold + Context Tree + Open Source Preparation
**Deadline: 2026-04-30**

#### NavigOS Layer Scaffold (all 12 layers)
- [ ] Implement `navig init --role layer --name <LayerOS>` — creates `.navig/` with domain skills, memory, inbox, tools, links
- [ ] Cold start script `navig-core/scripts/cold-start.ps1` — full idempotent Windows scaffold for all 12 layers
- [ ] All 12 layer directories with specific subfolders (see `plan-layer-os.md`):
  - `HumanOS` (inc. `Discipline\` subfolder — merged MilitaryOS)
  - `CompanyOS` · `SocietyOS` · `KnowledgeOS` · `Archive`
  - `SpiritualOS` (inc. `Contemplations\`)
  - `CreativeOS` (inc. `AlterEgos\CyberAlchemist\`, `Sensei\`, `DoctorSoma\`)
  - `WealthOS` · `CyberIntelOS` · `SovereignOS`
  - `GovernmentOS` (inc. `Laws\`, `HumanRights\`, `Governance\`, `Civics\`, `Policy\`, `Jurisdictions\`)
  - `MatrixOS` (inc. `Deprogramming\`, `Frameworks\`, `RedPills\`, `Systems\`, `Protocols\`, `Signals\`) — `isolated: true`
- [ ] Inter-layer `links/*.json` written for all dependency relationships
- [ ] CLI thin-wrapper command groups: `spiritual / creative / wealth / cyber / intel / sovereign / gov / matrix / human`
- [ ] `navig context tree --json` outputs full hierarchy for Deck/Forge rendering
- [ ] `navig layer list / show` commands with `--json` output
- [ ] Update mesh registry to discover all 12 layer `.navig` nodes

#### Context Loader (NAV-001)
- [ ] `navig/context/loader.py` — on CLI startup: walk `.navig/` hierarchy from CWD upward, load active context
- [ ] `navig context show` command
- [ ] Register all layer contexts in `registry/contexts.json`

#### Open Source Preparation
- [ ] Complete legal review — finalise `TRADEMARK.md`, `GOVERNANCE.md`, `SECURITY.md`
- [ ] Update `CONTRIBUTING.md` with full PR and review process
- [ ] Define reproducible build pipeline in `OFFICIAL_BUILDS.md`
- [ ] Eliminate all hardcoded keys and secrets — enforce `vault/secret_str.py` everywhere
- [ ] Write public changelog from git history
- [ ] Apply licence headers to all source files
- [ ] Confirm GitHub Actions CI pipeline passes on clean clone
- [ ] Complete `pyproject.toml` metadata for PyPI publish
- [ ] Rewrite `README.md` for public audience
- [ ] Activate security disclosure process — `SECURITY.md` live

#### navig doctor & .gitignore Enforcer (NAV-033, 035)
- [ ] `navig init` appends all protected `.gitignore` patterns (memory/, vault/, userdata/, *.enc, *.key, .blackbox/)
- [ ] `navig doctor --check gitignore` — detect tracked protected paths via `git ls-files`
- [ ] `navig doctor --fix gitignore` — `git rm --cached` + prompt commit
- [ ] `navig doctor` — all 11 check categories (identity, skills integrity, broken links, git secrets, memory integrity, orphaned agents, passport age, dead junctions, ghost memory, vault lock, SovereignOS policy)
- [ ] `navig doctor --json` flag for Deck health indicator

---

### May 2026 — 🚀 Open Source Public Release
**Deadline: 2026-05-15**

- [ ] Tag `v1.0.0` on navig-core
- [ ] Publish to PyPI: `pip install navig`
- [ ] Publish navig-bridge to VS Code Marketplace
- [ ] Launch navig-www public site — `navig-www/app/` deployed via Cloudflare Pages
- [ ] Activate onboarding flow — `navig-www/app/onboarding/page.tsx`
  - Multi-step wizard: choose worlds (3 foundation / full 12-layer), name first soul, Telegram connect
  - Downloads customised cold start PowerShell script with Operator's name/Telegram/wallet substituted
- [ ] Activate capabilities page — `navig-www/app/capabilities/page.tsx`
- [ ] navig-www schema surface — GovernmentOS law browser, layer explorer
- [ ] Execute press and community announcement: HN, Reddit, X, LinkedIn
- [ ] Open Discord/Matrix community server
- [ ] Launch docs site from `help/` markdown files

---

### June 2026 — Dashboard, Forge Integration & Operator Experience
**Deadline: 2026-06-30**

#### navig-deck v1.0 Pages
- [ ] Ship stable `NodesPage` and `MissionsPage` — `navig-deck/src/pages/`
- [ ] `LayersPage` — card grid for all 12 OS layers; data from `navig layer list --json` (NAV-020)
- [ ] `ContextTreePage` — D3 force-directed graph; nodes = contexts; edges = links; click → details sidebar (NAV-031)
- [ ] `InboxPage` — pending items + routing history; Accept / Re-route / Skip buttons (NAV-013)
- [ ] `IdentitySwitcher` component — souls + ghosts + alter egos dropdown (NAV-021)
- [ ] `HealthPage` — all 11 doctor checks; "Run Doctor" + "Fix All" buttons; auto-refresh 60s

#### navig-bridge Integration
- [ ] Context Explorer TreeView — `src/ui/contextExplorerProvider.ts`; global → layer → project nodes (NAV-017)
- [ ] `/ctx` slash commands in `commandRouter.ts` — `/ctx show`, `/ctx switch`, `/ctx memory search`, `/ctx soul use`, `/ctx checkin` (NAV-018)
- [ ] Passport/Identity WebviewPanel — `src/panels/passportPanel.ts`; shows identity fields, soul badge, "Reseal" button (NAV-019)
- [ ] `ContextBadge` status bar item — `[🧠 CyberAlchemist | KnowledgeOS]`; updates on switch (NAV-034)
- [ ] Context-aware Quick Actions bar — `/ctx show`, `/checkin`, `/ctx memory search` buttons in chat panel

#### Telegram Production
- [ ] Publish Telegram worker production configuration guide — `daemon/telegram_worker.py`
- [ ] All Telegram bot commands live: `/start`, `/checkin`, `/kpi`, `/status`, `/inbox add`, `/soul use`, `/doctor`, `/memory search`
- [ ] Telegram scheduled crons: daily check-in reminder, weekly review prompt, publishing reminder, dead mount alert
- [ ] Telegram OTP/2FA commands — `/otp <service>` returns TOTP code in ephemeral message (NAV-036 partial)

#### Infrastructure
- [ ] Sign and distribute navig-inbox desktop build
- [ ] Harden webhook receiver with signature verification — `webhooks/signatures.py`
- [ ] Expose cron service in deck UI — `scheduler/cron_service.py`
- [ ] Surface task queue visibility in dashboard — `tasks/queue.py` + `tasks/worker.py`
- [ ] Document voice pipeline opt-in — `voice/stt.py` + `voice/tts.py`
- [ ] Tune proactive assistant — `proactive_assistant.py` + `modules/proactive_display.py`

---

### July 2026 — NavigOS Soul System, Digital Society Schema Private Beta
**Deadline: 2026-07-31**

#### Soul & Persona System (NAV-010, 010B, 016, 021, 029)
- [ ] AI soul system — `navig/agent/soul.py`; souls stored in `userdata/souls/`; `navig soul use/show/list`
- [ ] Default souls bootstrapped: NAVIG (system), CyberAlchemist, Sensei, DoctorSoma
- [ ] Ghost person system — `navig/agent/ghost.py`; `navig ghost create/use/feed`
- [ ] Echo Chamber — `navig echo add/list/run`; council quorum modes (majority / unanimous / first-wins)
- [ ] Voice synthesis per soul — `navig/voice/soul_voice.py`; ElevenLabs or local TTS from `soul.json` config
- [ ] Soul active on all LLM prompts via system prompt prefix injection

#### GovernmentOS & MatrixOS Activation
- [ ] `navig/commands/gov.py` — `law add`, `rights query`, `policy track`, `jurisdiction show`
- [ ] `navig/commands/matrix.py` — `deprogram add`, `redpill add`, `signal log`, `protocol run`
- [ ] MatrixOS memory confirmed `isolated: true` — no auto-cascade; tagged export only
- [ ] GovernmentOS linked from SovereignOS as readonly legal reference

#### Digital Society Schema v0.1
- [ ] Define Digital Society Schema v0.1 — extends `schemas/node.schema.json` + `schemas/mission.schema.json`
- [ ] Harden identity layer for multi-user operation — `identity/models.py` + `identity/store.py`
- [ ] Enforce workspace ownership model — `workspace_ownership.py`
- [ ] Define multi-operator mesh concept: each operator is a node in the mesh
- [ ] Publish formation schema — `resources/workflows/` as schema templates
- [ ] Stabilise council orchestration — `formations/council`
- [ ] Implement schema reverse-mapping: user inputs life state → schema surfaces goal path
- [ ] Launch private beta with 10 invited operators

#### Wiki Module
- [ ] `navig wiki init` — create `.navig/wiki/` structure with all subdirectories
- [ ] `navig wiki add / list / search / inbox process` commands
- [ ] Global wiki at `~/.navig/wiki/` + project wiki at `.navig/wiki/` — `navig wiki sync`
- [ ] AI auto-sort via `navig wiki inbox process` — categorises content into correct subdirectories

---

### August 2026 — Formation & Council Layer + CRM / Prosperity Suite
**Deadline: 2026-08-31**

#### Formations & Council
- [ ] Bring formation loader to production-ready state
- [ ] `navig formation init` scaffold — creates `.navig/` in CWD; registers in `registry/contexts.json`
- [ ] `formation.json` council schema — `id, name, vision, agents[], capabilities[], skills[], context_path`
- [ ] `navig formation council add/list/remove/show`
- [ ] Deliver end-to-end council orchestration with multi-agent task delegation
- [ ] Integrate `formations/` with task queue — `tasks/queue.py`
- [ ] Enforce full mission lifecycle: create → assign → execute → receipt → archive
- [ ] Enforce ExecutionReceipt schema — `schemas/execution_receipt.schema.json`
- [ ] Define Pulse concept: atomic real-time signal unit within a formation
- [ ] Route pulses via mesh — `mesh/router.py` pulse channel
- [ ] Stabilise formations panel in navig-bridge

#### CRM / KPI / Prosperity Suite (NAV-024–028)
- [ ] CRM SQLite schema — `contacts`, `interactions` tables; `navig crm add/show/edit/list/search/log`
- [ ] Publishing playbook — `navig publish plan/log/stats`; weekly schedule + engagement metrics
- [ ] Daily check-in system — `navig checkin`; sleep/energy/mood/movement/nutrition/stress/priority; streaks; `/checkin` Telegram shortcut
- [ ] Weekly review system — `navig review week`; aggregate 7 check-ins; markdown summary report; Sunday cron push
- [ ] Prosperity KPI tracker — `navig kpi set/log/show`; metrics: revenue, pipeline, net_worth, savings_rate, content_published, fitness_consistency

#### navig-deck Extended
- [ ] KPI dashboard widget — sparklines from `kpi.db`; trend arrows ↑/↓/→
- [ ] CRM quick-view panel — relationship heat map; "last touch" aging indicator
- [ ] Formations panel — active council status, task delegation queue

---

### September 2026 — Passport v1, Chrome Dock, Network Layer Preparation
**Deadline: 2026-09-30**

#### Passport & Network
- [ ] Issue operator identity as cryptographic identity card — extends `identity/` + `vault/`
- [ ] Activate node trust scoring — `contracts/TrustScore`
- [ ] Harden SSH key management — `ssh_keys.py`
- [ ] Stabilise tunnel infrastructure — `tunnel.py` + `help/tunnel.md`
- [ ] Test multi-region mesh: EU + US + APAC nodes
- [ ] Document peer discovery protocol
- [ ] Define "passport" as verified operator identity node in the network formation model
- [ ] Onboard first 100 operator nodes into the mesh

#### Chrome Dock Extension — navig-dock (NAV-037)
- [ ] `content.js` — injected into pages; shows active soul badge, context indicator, quick-action pill
- [ ] `service-worker.js` — background; bridges extension to navig CLI via native messaging
- [ ] `options.js` — widget configuration UI
- [ ] Native messaging host `navig.bridge` — `navig/commands/bridge.py` stdio host; Windows registry entry
- [ ] Marketplace catalog — `marketplace/catalog.json`; default widgets: QuickInbox, ContextSwitcher, MiniCheckin, OTPWidget, KPI sparkline, Memory search
- [ ] Manifest V3 compliance; CSP clean

#### navig-deck v2 Features
- [ ] System Health Page with real-time doctor integration
- [ ] Mount registry viewer — drive junction health, reconnect status
- [ ] Soul/Ghost gallery page
- [ ] Layer inter-link graph visualisation (D3, same pattern as ContextTreePage)

---

### October 2026 — Revenue & Investor Foundation
**Deadline: 2026-10-31**

- [ ] Define pricing model: Free / Pro / Operator / Enterprise tiers
- [ ] Launch pricing page on navig-www
- [ ] Integrate Stripe payments behind feature flags
- [ ] Implement opt-in, GDPR-compliant usage telemetry — `telemetry_auditor` tool
- [ ] Acquire first paid operators — target: 50 paying users
- [ ] Build pitch deck v1.0
- [ ] Begin angel investor outreach
- [ ] Establish €50k ARR signal before approaching VCs
- [ ] `navig evolve` — AI self-improvement loop; identifies repeated failures and proposes skill/command upgrades
- [ ] `navig skills benchmark` — measures skill execution accuracy; feeds into evolve loop

---

### November 2026 — Digital Society Schema Public Beta
**Deadline: 2026-11-30**

- [ ] Publish Digital Society Schema v1.0 openly
- [ ] Launch schema browser on navig-www — operators explore life goal paths
- [ ] Ship "reversed help" engine: user describes situation → schema returns goal candidates
- [ ] Release formation templates across life domains: health / finance / relationships / creativity
- [ ] Open community schema contribution workflow
- [ ] Reach 500 active operator nodes
- [ ] Execute press narrative: "the schema that maps human goals"
- [ ] MatrixOS private content policy published — community guidelines for responsible use
- [ ] GovernmentOS law database seeded with EU + Estonian + international frameworks

---

### December 2026 — Year-End Consolidation
**Deadline: 2026-12-31**

- [ ] Conduct full ecosphere audit — version-align all packages
- [ ] Commission external security audit
- [ ] Review and update `SOUL.default.md` + `PERSONA.md`
- [ ] Publish performance benchmarks — `benchmarks/baseline_performance.py`
- [ ] Write and publish 2026 retrospective
- [ ] Lock 2027 plan and commit to repository
- [ ] Verify all 12 NavigOS layers are operational with live memory and soul assignments
- [ ] navig doctor runs completely clean on a full 12-layer setup
- [ ] **Target metrics:** 1,000 GitHub stars · 500 operators · €100k ARR pipeline

---

## 2027 — Scale, Society & Recognition

---

### January–February 2027 — Company Formation
**Deadline: 2027-02-28**

- [ ] Incorporate legal entity (jurisdiction under evaluation: Estonia / UK / UAE / Singapore)
- [ ] Update `FUNDING.md` with official company details
- [ ] Define cap table structure
- [ ] Assign all IP from personal to company
- [ ] Make first employee or contractor hire
- [ ] Prepare seed round materials: deck, data room, traction metrics
- [ ] **Target:** €500k seed raise closing Q1/Q2 2027

---

### March–April 2027 — navig-os Launch
**Deadline: 2027-04-30**

- [ ] Ship navig-os v1.0 — full web OS for operator life management
- [ ] Complete navig-os build pipeline — `postcss.config.mjs` + `tsconfig.json`
- [ ] Integrate navig-os with Digital Society Schema
- [ ] Implement Mission OS concept: every life domain has a mission board
- [ ] Integrate 12 NavigOS layers as navig-os sidebar navigation (HumanOS → MatrixOS)
- [ ] Launch Pulse feed: real-time stream of operator and network activity
- [ ] Deliver mobile-responsive design
- [ ] Grant early access to first 50 navig-os operators

---

### May–June 2027 — Investor Round & Team
**Deadline: 2027-06-30**

- [ ] Close seed round (target: €500k–€1.5M)
- [ ] Build core team: 3–5 people across backend, frontend, growth, and operations
- [ ] Define office or distributed team structure
- [ ] **Revenue target:** €500k ARR
- [ ] Reach 5,000 registered operator nodes
- [ ] Secure 3 enterprise pilots using NAVIG for internal orchestration

---

### July–August 2027 — Network After Passports
**Deadline: 2027-08-31**

- [ ] Ship Operator Passport v1.0: verified identity + reputation score (on-chain or in vault)
- [ ] Implement network formation protocol — operators form trusted meshes
- [ ] Enable cross-operator mission delegation: send a mission to a trusted peer node
- [ ] Launch formation marketplace — operators publish reusable formations
- [ ] Lock Pulse OS concept definition: parallel OS layer above navig-os (full build deferred)
- [ ] Instrument network effect metrics: average node connections and mission delegation rate
- [ ] MatrixOS cross-operator signal sharing — optional opt-in for verified operator mesh

---

### September–October 2027 — Fame & Brand
**Deadline: 2027-10-31**

- [ ] Secure 3+ speaking engagements across AI, DevOps, LifeOS, and Futurism tracks
- [ ] Earn 3+ features in major technology publications
- [ ] Establish YouTube and podcast presence — "Building the Operating System for Human Life"
- [ ] Publish 5 operator success case studies
- [ ] Lock NAVIG brand identity: logo, colour system, voice (aligned with `PERSONA.md`)
- [ ] Reach 10,000 GitHub stars
- [ ] Establish Sergey as "the architect of the human ecosphere" across all public channels

---

### November–December 2027 — Series A Preparation & Ecosphere Maturity
**Deadline: 2027-12-31**

- [ ] Reach Series A readiness — target raise: €3M–€10M
- [ ] **Revenue target:** €2M ARR
- [ ] Reach 20,000 operator nodes
- [ ] Achieve navig-shared SDK adoption by 3+ third-party developers
- [ ] Grow plugin ecosystem to 50+ community plugins
- [ ] Secure Digital Society Schema adoption by 2+ external organisations
- [ ] Position NAVIG as the category-defining operator intelligence platform
- [ ] Document Sergey's net worth trajectory: €1M+ growth path
- [ ] Year-end state: ecosphere is self-sustaining, investor-backed, and publicly recognised

---

## 2028+ — Horizon Targets

- [ ] Series B — €20M+ — global expansion
- [ ] NAVIG operating as infrastructure for AI-native organisations
- [ ] Digital Society Schema ratified as an open standard — the HTML of human goals
- [ ] Pulse OS: full parallel OS layer for formation-native computing
- [ ] 100,000 operator nodes globally
- [ ] NAVIG ranked among the top 10 most impactful AI platforms worldwide
- [ ] Sergey: recognised founder, speaker, and definitive architect of the human ecosphere

---

## Implementation Directive

This plan is the strategic source of truth for the entire ecosphere. It lives at:

```
navig-core/ROADMAP_MASTER.md
```

Wire it to the existing `ROADMAP.md` as the canonical strategy document. Surface in navig-deck as a first-class dashboard view — every checkbox resolves to a Mission in `schemas/mission.schema.json`, tracked through the full ExecutionReceipt lifecycle from creation through archival.

The daemon stays open. `navig/daemon/` — encompassing `supervisor.py`, `service_manager.py`, and `telegram_worker.py` — is live infrastructure and the backbone of autonomous operation. Closing it before a replacement persistent orchestration layer is in production would sever the system's continuity. It remains active.

---

## Gap Register — Items Added vs. Original Plan

The following items were **absent from the original plan** and added during analysis:

| Domain | Items Added | First Appears |
|---|---|---|
| NavigOS Identity (NAV-001–003, 011) | Ed25519 passport, vault/blackbox, isolation flag, path migration | Feb 2026 |
| Inbox Neuron Router (NAV-013) | Watcher, classifier, router, hooks, TUI, URL ingest, Telegram→inbox | Mar 2026 |
| Layered Settings + Secret Refs | Resolver, BLACKBOX refs, profile lifecycle, default settings.json | Mar 2026 |
| 3-Tier LanceDB Memory (NAV-004, 014, 015) | Tier engine, ChromaDB sync, BM25 fallback, memory CLI | Mar 2026 |
| Universal Drive Mounting (NAV-012) | Junction registry, CLI, auto-verify, Telegram dead-mount alert | Mar 2026 |
| All 12 NavigOS Layer Scaffold | Full cold start script, 12 layers, inter-layer links, CLI groups | Apr 2026 |
| Context Loader (NAV-001) | `.navig/` hierarchy walker, `navig context show` | Apr 2026 |
| navig doctor + .gitignore enforcer (NAV-033, 035) | 11 check categories, --fix, --json, SovereignOS policy | Apr 2026 |
| navig-www onboarding with layer selection (NAV-030) | Wizard with layer choice, personalised cold start download | May 2026 |
| Forge context integration (NAV-017–019, 034) | TreeView, /ctx commands, passport panel, ContextBadge | Jun 2026 |
| navig-deck layer pages (NAV-020, 021, 031) | LayersPage, IdentitySwitcher, ContextTreePage, InboxPage, HealthPage | Jun 2026 |
| Telegram full integration (NAV-036) | All bot commands, crons, OTP/TOTP, Telegram Mini App (deferred) | Jun 2026 |
| Soul / Ghost / Echo Chamber (NAV-010, 010B, 016) | Soul system, Ghost persons, Echo Chamber council, voice | Jul 2026 |
| GovernmentOS (NAV-043, D-010) | Laws, HumanRights, Governance, Civics, Policy, Jurisdictions | Jul 2026 |
| MatrixOS (NAV-045, D-011) | Deprogramming, Frameworks, RedPills, Systems, Protocols, Signals | Jul 2026 |
| Wiki Module | `navig wiki` commands, inbox→wiki pipeline, sync | Jul 2026 |
| CRM / KPI / Checkin / Publishing (NAV-024–028) | All 5 prosperity systems, SQLite schemas, Telegram shortcuts | Aug 2026 |
| Chrome Dock Extension (NAV-037) | navig-dock, native messaging bridge, marketplace widgets | Sep 2026 |
| navig evolve | AI self-improvement loop, skill benchmarking | Oct 2026 |

---

*Last updated: 2026-02-21 by NAVIG autonomous architect.*
