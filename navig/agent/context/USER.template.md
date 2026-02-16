---
summary: "User profile and preferences template"
scope: user
editable: true
auto_update: true
---
# USER.md — Your Profile & Operating Parameters

This file teaches NAVIG who you are, how you work, and what "good help" looks like for you.  
Fill in only what you're comfortable sharing — everything is optional and editable anytime.

---

## 1. Identity

- **Name**: (Your full name or alias)
- **Preferred Name**: (How NAVIG should address you)
- **Pronouns**: (Optional: e.g. he/him, she/her, they/them)
- **Timezone**: (e.g. Europe/Paris, America/New_York)
- **Primary Location**: (City / region, optional)

---

## 2. Work Patterns & Energy

Helps NAVIG time notifications, deep‑work blocks, and maintenance windows.

- **Work Hours**: (e.g. 09:00–18:00, Monday–Friday)
- **Do Not Disturb**: (e.g. 23:00–07:00 - avoid non‑urgent pings)
- **Peak Focus Windows**:
  - Morning: (e.g. 09:00–12:00)
  - Afternoon: (e.g. 14:00–16:00)
- **Low‑Energy Times**:
  - (e.g. post‑lunch, late evenings)

- **Preferred Cadence**:
  - Status summaries: daily / weekly / only on request
  - Check‑ins on big goals: weekly / bi‑weekly / monthly

---

## 3. Communication Preferences

Tell NAVIG how to talk to you and how much detail you want.

- **Verbosity**: normal  
  - `minimal`  = Short, command‑style answers  
  - `normal`   = Balanced, with brief explanations  
  - `detailed` = Deep dives, trade‑offs, background

- **Tone**: (e.g. direct, technical, casual, friendly, formal, playful)

- **Language(s)**:
  - Primary: (e.g. English, French)
  - Secondary: (other languages NAVIG may use)

- **Confirm Destructive Actions**: yes  
  - `yes`  = Always ask before risky/irreversible changes  
  - `no`   = I explicitly accept higher automation risk

- **Notification Channels** (if configured):
  - Telegram: (handle / chat id)
  - Email: (address)
  - Other: (ntfy topic, Matrix room, etc.)

---

## 4. Technical Profile

Helps NAVIG choose tools, commands, and examples that match your stack.

- **Primary Languages**: (e.g. Python, TypeScript, JavaScript)
- **Other Languages**: (e.g. Bash, SQL, Rust, Go, C++)
- **Preferred Editor/IDE**: (e.g. VS Code, Neovim, Cursor, JetBrains)
- **OS / Platforms**: (e.g. Windows, WSL, Linux, macOS, servers)
- **Cloud / Hosting Preference**: (e.g. self‑hosted, AWS, GCP, Azure, hybrid)
- **Shells in Use**: (e.g. PowerShell, bash, zsh, fish)
- **Package Managers**: (e.g. pipx, uv, pnpm, npm, cargo, chocolatey, winget)
- **NAVIG Features**:
  - Proactive Assistance: Enabled / Disabled (Calendar/Email)
  - Daily Logs: Enabled / Disabled
  - Voice/TTS: Enabled / Disabled (Multi-provider)

- **Risk Tolerance for Automation**:
  - `low`    = Prefer read‑only checks and suggestions  
  - `medium` = Safe defaults, occasional auto‑fixes with logs  
  - `high`   = Aggressive automation once patterns are verified

---

## 5. Life‑OS & Priorities

This guides NAVIG's "life ops" and swarm planning.

- **Top 3 Current Goals**:
  1. …
  2. …
  3. …

- **Health & Longevity Focus** (high / medium / low):
  - (e.g. "Improve sleep", "Regular exercise", "Reduce stress")

- **Wealth & Work Focus** (high / medium / low):
  - (e.g. "Grow recurring income", "Stabilize freelancing", "Invest in skills")

- **Learning & Skill Targets**:
  - (e.g. "Learn Rust", "Deepen Kubernetes", "Music theory")

---

## 6. Personal Context

Add any facts that help NAVIG reason about your constraints and preferences.

### Professional Identity

**Your Roles**: (e.g. Founder, Developer, Designer, Writer, Musician, etc.)

Brief introduction or mission statement about your work and what drives you technically and creatively.

### Background

Describe your professional journey, expertise areas, and what you're building.

### What You're Working On

- **Projects**: (List current major projects or focus areas)
- **Specializations**: (Key technical domains you excel in)
- **Philosophy**: (Your approach to building, creating, or problem-solving)

### About You

Describe your multifaceted interests, methodologies, and what makes your approach unique.

### Social Media & Content

List your public channels, content platforms, or communities:

- **Platform Name**: [Link](URL) — Description
- **Platform Name**: [Link](URL) — Description

### Additional Context

```text
# Examples (customize to your situation):
# - Building an automation platform around NAVIG
# - Prefer solutions I can self-host and inspect
# - Multi-disciplinary approach: code, design, music, writing
# - Strong focus on specific technical area (e.g. cybersecurity, DevOps)
# - Passionate about open source and knowledge sharing
# - Have ADHD / low tolerance for noisy alerts
```

---

## 7. Interests & Enjoyment

Used for examples, metaphors, and low‑friction ideas you're more likely to enjoy.

```text
# Examples:
# - Music production / beatmaking / specific genres
# - Gaming (list genres/titles you like)
# - Open source & hacking on tools
# - Crypto / on‑chain experiments
# - Coffee rituals / tea / cooking
# - Writing / blogging / content creation
# - Hardware / electronics / IoT
# - Art / design / creative coding
```

---

## 8. Automation & Boundaries

Tell NAVIG what it may do on its own vs what always needs a "yes".

**NAVIG May Act Autonomously On:**

- Read‑only checks (health, logs, metrics)
- Non‑destructive maintenance (cache cleanup, log rotation)
- Drafting messages, docs, or code for review

**NAVIG Must Always Ask Before:**

- Server restarts / deployments / schema migrations
- Deleting data, backups, or repos
- Financial actions, purchases, or subscriptions
- Sending messages to other humans on your behalf

**Add any extra rules:**

```text
# Examples:
# - Never reboot production servers automatically
# - Staging can be auto-deployed after tests pass
# - Don't schedule meetings without my explicit approval
```

---

## 9. Custom Notes

Anything else NAVIG should keep in mind:

```text
# Add your own notes, quirks, or reminders here.
# This is a good place for:
# - "If I sound stressed, prioritize simplification over new ideas"
# - "Remind me once a month to review long-running subscriptions"
# - "I prefer visual explanations when dealing with complex architecture"
# - "Always suggest the self-hosted option first"
```

---

**Remember**: The more you share, the better NAVIG can assist. But share only what feels right — this is your space, and everything here is optional and under your control.
