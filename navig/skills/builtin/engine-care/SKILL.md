---
id: engine-care
name: NAVIG Engine Care
version: 1.0.0
category: system
tags: [navig, daemon, restart, heal, doctor, telegram, health]
platforms: [linux, macos, windows]
tools: [bash_exec]
safety: elevated
user_invocable: true
description: >
  Check, heal and — only when needed — restart the NAVIG engine itself (the daemon that
  runs the Telegram bot, the gateway and the automations). Use when the user says "is
  navig working", "the bot isn't answering", "navig is broken", "restart navig", "fix
  navig", or a NAVIG notification stopped arriving. Looks before it acts: status and the
  health check first, heal second, restart last and only with a stated reason.
---

# NAVIG Engine Care

The NAVIG engine is a supervised daemon: it owns the Telegram bot and the gateway. Every
surface controls it through the same seams — the CLI below, NAVIG OS (System → Engine),
the Telegram Mini App (Home → Engine) and the bot (`/doctor`, `/restart`, `/os`) — so the
answer is the same wherever the user asks.

## 1. Look first (read-only)

```bash
navig service status        # is the daemon running, and is autostart installed?
navig doctor                # the health check: ✓ verified · ⚠ could not verify · ✗ failing
navig doctor --json         # the same, machine-readable
```

- A ⚠ row means "could not verify", never "fine". Treat it as an unknown to investigate.
- Logs: `navig paths` shows where they live (the OS log dir, never `~/.navig`). Check every
  timestamp before calling an error current — the daemon log routinely holds weeks-old
  errors.

## 2. Heal the smallest thing

```bash
navig doctor --heal --dry-run   # what it would do
navig doctor --heal             # do it
```

Heal starts a stopped daemon and repairs what the health check found. It never restarts a
running engine; anything it cannot fix comes back with the exact command to run.

## 3. Restart — last, and say why

```bash
navig service restart
```

- A restart interrupts every running chat and job for ~30 seconds. State the reason first
  ("the bot stopped polling after a network change and heal did not recover it").
- Never kill navig processes by name or `taskkill`/`pkill` them: the supervisor relaunches
  children, and a pattern match can take down another install's daemon. Go through
  `navig service …` only.
- From inside NAVIG (the bot, the deck, an agent tool) the restart is handed to an
  out-of-tree task on Windows. If it answers that the restart task is missing, NAVIG did
  NOT restart — run `navig service install` once, then retry.

## 4. Report honestly

Say what you checked, what you changed, and what is still failing. A restart that was
refused did not happen; a ⚠ that you could not verify is not a pass.
