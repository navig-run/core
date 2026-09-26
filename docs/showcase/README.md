# NAVIG in action

Every recording on this page is a real `navig` session, captured with [VHS](https://github.com/charmbracelet/vhs)
from the tapes in [`tools/showcase/tapes/`](../../tools/showcase/tapes). Nothing is typed into the output and
nothing is edited out — see [How these were recorded](#how-these-were-recorded).

Videos (MP4 + WebM per scene, plus a 90-second trailer) are attached to the
[`showcase-2026-09-26` release](https://github.com/navig-run/core/releases/tag/showcase-2026-09-26).

## The 20-second pitch

`navig host list` → a real `navig run` over SSH → `navig apply safe-deployment --dry-run`.

![hero](hero.gif)

## Kraken dashboard

`navig dashboard` — services, hosts, tunnels and history in one live screen. `q` to leave.

![dashboard](dashboard.gif)

## Hosts, and what navig refuses to do

`navig host test` proves the SSH path. A second agent session (`NAVIG_SESSION_ID=agent-b`) is refused while the
first one holds the host. A destructive command asks before it runs — and `n` means no.

![hosts-and-safety](hosts-and-safety.gif)

## Spaces — a folder becomes a workshop

`navig space init --dry-run` shows the plan, `navig space init` scaffolds it, `navig space doctor` tells you
what is still missing and what to run.

![spaces](spaces.gif)

## Blocks — outcomes with receipts

`navig block show safe-deployment` then `navig apply safe-deployment --dry-run`: every step carries its risk,
destructive ones need an explicit `--approve`, and a real apply ends in a verified receipt.

![blocks](blocks.gif)

## Skills

`navig skill tree` lists what the agent knows how to do; `navig skill lint` checks a `SKILL.md` against the
authoring standard.

![skills](skills.gif)

## The operations ledger, and undo

Every command lands on a hash-chained ledger. `navig ledger verify` proves the chain is intact;
`navig undo --list` shows what can be rolled back.

![ledger-and-undo](ledger-and-undo.gif)

## Store and doctor

`navig store status` — what is wired, what is not. `navig doctor` — the install, section by section, with the
fix next to each finding.

![store-and-doctor](store-and-doctor.gif)

## Your node's identity

`navig whoami` — the GENESIS RECORD: a sigil derived from the node's seed, its machine name and live latency.

![whoami](whoami.gif)

## The gateway boots, and stops cleanly

`navig gateway start` — the boot story, phase by phase. `Ctrl+C` — every subsystem drains and reports.

![gateway-boot](gateway-boot.gif)

## Built for agents too

`navig --schema` exposes every command as JSON; any listing takes `--json`. Pipe into `jq`, feed to a model.

![schema-for-agents](schema-for-agents.gif)

## How these were recorded

- **Real commands, real output.** Each scene is a VHS tape (`tools/showcase/tapes/*.tape`) run against an
  isolated navig home (`tools/showcase/sandbox.sh`: `NAVIG_CONFIG_DIR` and friends point at a throwaway
  directory). Nothing that appears on screen was pasted, mocked or retouched.
- **The lab hosts are real SSH targets.** `prod-01.lab`, `staging-01.lab` and `db-01.lab` resolve to a
  local `sshd` on port 2222 (`tools/showcase/setup-wsl.sh`) — so `navig run 'df -h /'` is a genuine SSH
  round-trip, and the numbers you see are that machine's. They are labels for a lab box, not production
  servers.
- **Provenance.** [`MANIFEST.json`](MANIFEST.json) records the navig version and commit, the VHS / ffmpeg /
  Chrome builds, the font, and a SHA-256 per asset. `npm run showcase:check` verifies the committed GIFs
  against it.
- **Regenerate.** `npm run showcase:setup` (WSL Ubuntu: VHS, ttyd, ffmpeg, Chrome, the Nerd Font, the lab
  sshd) then `npm run showcase:record`. GIFs land here; MP4/WebM land in `core/.dev/showcase/video/` for the
  release upload.
