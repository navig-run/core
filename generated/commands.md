# NAVIG Command Reference

_Generated 2026-10-19T22:29:39.517021+00:00_

## `navig action add`
**Status:** `stable` · **Since:** 
Add a new quick action.
**Examples:**
```sh
navig action add
```

## `navig action list`
**Status:** `stable` · **Since:** 
List all quick actions.
**Examples:**
```sh
navig action list
```

## `navig action remove`
**Status:** `stable` · **Since:** 
Remove a quick action from the user actions file.
**Examples:**
```sh
navig action remove
```

## `navig action run`
**Status:** `stable` · **Since:** 
Run a quick action by name.
**Examples:**
```sh
navig action run
```

## `navig action show`
**Status:** `stable` · **Since:** 
Show details of a specific action.
**Examples:**
```sh
navig action show
```

## `navig agent`
**Status:** `stable` · **Since:** 
Run navig agent
**Examples:**
```sh
navig agent
```

## `navig agent config`
**Status:** `stable` · **Since:** 
Manage agent configuration.
**Examples:**
```sh
navig agent config
```

## `navig agent context`
**Status:** `stable` · **Since:** 
Audit what actually reaches the model.
**Examples:**
```sh
navig agent context
```

## `navig agent continuation continue`
**Status:** `stable` · **Since:** 
Enable continuation policy for local runtime state.
**Examples:**
```sh
navig agent continuation continue
```

## `navig agent continuation pause`
**Status:** `stable` · **Since:** 
Pause continuation policy for local runtime state.
**Examples:**
```sh
navig agent continuation pause
```

## `navig agent continuation skip`
**Status:** `stable` · **Since:** 
Skip the next continuation trigger for local runtime state.
**Examples:**
```sh
navig agent continuation skip
```

## `navig agent continuation start`
**Status:** `stable` · **Since:** 
Enable continuation policy (alias for `continuation continue`).
**Examples:**
```sh
navig agent continuation start
```

## `navig agent continuation status`
**Status:** `stable` · **Since:** 
Show continuation policy for local runtime state.
**Examples:**
```sh
navig agent continuation status
```

## `navig agent install`
**Status:** `stable` · **Since:** 
Install and configure agent mode.
**Examples:**
```sh
navig agent install
```

## `navig agent learn`
**Status:** `stable` · **Since:** 
Analyze agent logs and learn from error patterns.
**Examples:**
```sh
navig agent learn
```

## `navig agent logs`
**Status:** `stable` · **Since:** 
View agent logs.
**Examples:**
```sh
navig agent logs
```

## `navig agent personality`
**Status:** `stable` · **Since:** 
Manage personality profiles.
**Examples:**
```sh
navig agent personality
```

## `navig agent plan`
**Status:** `stable` · **Since:** 
Run a two-tier autonomous plan-execute agent cycle.
**Examples:**
```sh
navig agent plan
```

## `navig agent remediation`
**Status:** `stable` · **Since:** 
View and manage automatic remediation actions.
**Examples:**
```sh
navig agent remediation
```

## `navig agent run`
**Status:** `stable` · **Since:** 
Run a single formation agent on a task.
**Examples:**
```sh
navig agent run
```

## `navig agent service`
**Status:** `stable` · **Since:** 
Manage NAVIG agent as a system service.
**Examples:**
```sh
navig agent service
```

## `navig agent soul`
**Status:** `stable` · **Since:** 
Manage agent personality via SOUL.md.
**Examples:**
```sh
navig agent soul
```

## `navig agent start`
**Status:** `stable` · **Since:** 
Start the autonomous agent.
**Examples:**
```sh
navig agent start
```

## `navig agent status`
**Status:** `stable` · **Since:** 
Show agent status.
**Examples:**
```sh
navig agent status
```

## `navig agent stop`
**Status:** `stable` · **Since:** 
Stop the running agent.
**Examples:**
```sh
navig agent stop
```

## `navig agent telegram setup`
**Status:** `stable` · **Since:** 
Interactive setup for Telegram bot.
**Examples:**
```sh
navig agent telegram setup
```

## `navig agent telegram start`
**Status:** `stable` · **Since:** 
Start the Telegram bot.
**Examples:**
```sh
navig agent telegram start
```

## `navig agent telegram status`
**Status:** `stable` · **Since:** 
Show Telegram bot status and configuration.
**Examples:**
```sh
navig agent telegram status
```

## `navig agent transcribe`
**Status:** `stable` · **Since:** 
Transcribe an audio file to text using the best available backend.
**Examples:**
```sh
navig agent transcribe
```

## `navig agentctl ack`
**Status:** `stable` · **Since:** 
Acknowledge a failed run so it stops lighting the Dock indicator.
**Examples:**
```sh
navig agentctl ack
```

## `navig agentctl bind`
**Status:** `stable` · **Since:** 
Claim a task for a session. Rebinding MOVES the owner (one claim per task).
**Examples:**
```sh
navig agentctl bind
```

## `navig agentctl bindings`
**Status:** `stable` · **Since:** 
Active task <-> session claims (one claim per task — no duplicate execution).
**Examples:**
```sh
navig agentctl bindings
```

## `navig agentctl hooks install`
**Status:** `stable` · **Since:** 
Add the Agent Monitor hooks to Claude Code settings (shows the diff first).
**Examples:**
```sh
navig agentctl hooks install
```

## `navig agentctl hooks status`
**Status:** `stable` · **Since:** 
Which Claude Code settings files carry the monitor hooks.
**Examples:**
```sh
navig agentctl hooks status
```

## `navig agentctl hooks uninstall`
**Status:** `stable` · **Since:** 
Remove only the Agent Monitor hooks; every other hook is left as it was.
**Examples:**
```sh
navig agentctl hooks uninstall
```

## `navig agentctl run`
**Status:** `stable` · **Since:** 
Run a command as a monitored, verified process:  navig agentctl run -- <command…>
**Examples:**
```sh
navig agentctl run
```

## `navig agentctl runs`
**Status:** `stable` · **Since:** 
Agent runs: sessions, subagents, commands and lands, live and historical.
**Examples:**
```sh
navig agentctl runs
```

## `navig agentctl sessions`
**Status:** `stable` · **Since:** 
Live agent sessions (who is working where, idle or active).
**Examples:**
```sh
navig agentctl sessions
```

## `navig agentctl show`
**Status:** `stable` · **Since:** 
One run in detail: state, stages, history, and optionally its log tail.
**Examples:**
```sh
navig agentctl show
```

## `navig agentctl status`
**Status:** `stable` · **Since:** 
One-line overview: sessions, worktrees (cwd repo), tasks.
**Examples:**
```sh
navig agentctl status
```

## `navig agentctl tasks`
**Status:** `stable` · **Since:** 
Unified tasks from the board and the work queue.
**Examples:**
```sh
navig agentctl tasks
```

## `navig agentctl unbind`
**Status:** `stable` · **Since:** 
Release a task claim (the ledger keeps the history).
**Examples:**
```sh
navig agentctl unbind
```

## `navig agentctl worktrees`
**Status:** `stable` · **Since:** 
Git worktrees of a repo: branch, ahead/behind, dirty, last commit.
**Examples:**
```sh
navig agentctl worktrees
```

## `navig ahk activate`
**Status:** `stable` · **Since:** 
Activate (focus) window.
**Examples:**
```sh
navig ahk activate
```

## `navig ahk active-window`
**Status:** `stable` · **Since:** 
Get currently active window.
**Examples:**
```sh
navig ahk active-window
```

## `navig ahk automate`
**Status:** `stable` · **Since:** 
AI-powered automation - generate and execute script for goal.
**Examples:**
```sh
navig ahk automate
```

## `navig ahk click`
**Status:** `stable` · **Since:** 
Click at screen coordinates.
**Examples:**
```sh
navig ahk click
```

## `navig ahk clipboard`
**Status:** `stable` · **Since:** 
Get or set clipboard content.
**Examples:**
```sh
navig ahk clipboard
```

## `navig ahk close`
**Status:** `stable` · **Since:** 
Close window by selector.
**Examples:**
```sh
navig ahk close
```

## `navig ahk dashboard`
**Status:** `stable` · **Since:** 
Live window manager dashboard.
**Examples:**
```sh
navig ahk dashboard
```

## `navig ahk doctor`
**Status:** `stable` · **Since:** 
Diagnose AutoHotkey integration issues.
**Examples:**
```sh
navig ahk doctor
```

## `navig ahk evolve`
**Status:** `stable` · **Since:** 
Auto-generate, test, and evolve AHK scripts until they work.
**Examples:**
```sh
navig ahk evolve
```

## `navig ahk exec`
**Status:** `stable` · **Since:** 
Execute inline AHK code.
**Examples:**
```sh
navig ahk exec
```

## `navig ahk find`
**Status:** `stable` · **Since:** 
Find windows matching criteria.
**Examples:**
```sh
navig ahk find
```

## `navig ahk generate`
**Status:** `stable` · **Since:** 
Generate AHK script from natural language using AI.
**Examples:**
```sh
navig ahk generate
```

## `navig ahk install`
**Status:** `stable` · **Since:** 
Detect or install AutoHotkey v2.
**Examples:**
```sh
navig ahk install
```

## `navig ahk is-muted`
**Status:** `stable` · **Since:** 
Check if system audio is muted.
**Examples:**
```sh
navig ahk is-muted
```

## `navig ahk kill`
**Status:** `stable` · **Since:** 
Kill a process by name or PID.
**Examples:**
```sh
navig ahk kill
```

## `navig ahk layout list`
**Status:** `stable` · **Since:** 
List saved layouts.
**Examples:**
```sh
navig ahk layout list
```

## `navig ahk layout restore`
**Status:** `stable` · **Since:** 
Restore a saved window layout.
**Examples:**
```sh
navig ahk layout restore
```

## `navig ahk layout save`
**Status:** `stable` · **Since:** 
Save current window positions and sizes.
**Examples:**
```sh
navig ahk layout save
```

## `navig ahk library list`
**Status:** `stable` · **Since:** 
List saved automation scripts.
**Examples:**
```sh
navig ahk library list
```

## `navig ahk library show`
**Status:** `stable` · **Since:** 
Show content of a saved script.
**Examples:**
```sh
navig ahk library show
```

## `navig ahk listen`
**Status:** `stable` · **Since:** 
Register a global hotkey to run a command.
**Examples:**
```sh
navig ahk listen
```

## `navig ahk listener-edit`
**Status:** `stable` · **Since:** 
Open listener script in default editor.
**Examples:**
```sh
navig ahk listener-edit
```

## `navig ahk listener-start`
**Status:** `stable` · **Since:** 
Start or restart the persistent listener script.
**Examples:**
```sh
navig ahk listener-start
```

## `navig ahk maximize`
**Status:** `stable` · **Since:** 
Maximize window.
**Examples:**
```sh
navig ahk maximize
```

## `navig ahk minimize`
**Status:** `stable` · **Since:** 
Minimize window.
**Examples:**
```sh
navig ahk minimize
```

## `navig ahk monitors`
**Status:** `stable` · **Since:** 
List all connected monitors.
**Examples:**
```sh
navig ahk monitors
```

## `navig ahk mouse-move`
**Status:** `stable` · **Since:** 
Move mouse cursor.
**Examples:**
```sh
navig ahk mouse-move
```

## `navig ahk move`
**Status:** `stable` · **Since:** 
Move and optionally resize window.
**Examples:**
```sh
navig ahk move
```

## `navig ahk move-to-monitor`
**Status:** `stable` · **Since:** 
Move window to specific monitor.
**Examples:**
```sh
navig ahk move-to-monitor
```

## `navig ahk mute`
**Status:** `stable` · **Since:** 
Mute or unmute system audio.
**Examples:**
```sh
navig ahk mute
```

## `navig ahk notify`
**Status:** `stable` · **Since:** 
Show Windows notification.
**Examples:**
```sh
navig ahk notify
```

## `navig ahk ocr`
**Status:** `stable` · **Since:** 
Extract text from screen or image (requires pytesseract).
**Examples:**
```sh
navig ahk ocr
```

## `navig ahk open`
**Status:** `stable` · **Since:** 
Open application or URL.
**Examples:**
```sh
navig ahk open
```

## `navig ahk pin`
**Status:** `stable` · **Since:** 
Toggle Always-On-Top (Pin) status.
**Examples:**
```sh
navig ahk pin
```

## `navig ahk processes`
**Status:** `stable` · **Since:** 
List all running processes.
**Examples:**
```sh
navig ahk processes
```

## `navig ahk read-text`
**Status:** `stable` · **Since:** 
Read text from window or control.
**Examples:**
```sh
navig ahk read-text
```

## `navig ahk resize`
**Status:** `stable` · **Since:** 
Resize window.
**Examples:**
```sh
navig ahk resize
```

## `navig ahk run`
**Status:** `stable` · **Since:** 
Execute an AHK script file.
**Examples:**
```sh
navig ahk run
```

## `navig ahk screenshot`
**Status:** `stable` · **Since:** 
Take a screenshot.
**Examples:**
```sh
navig ahk screenshot
```

## `navig ahk send`
**Status:** `stable` · **Since:** 
Send key sequence.
**Examples:**
```sh
navig ahk send
```

## `navig ahk snap`
**Status:** `stable` · **Since:** 
Snap window to screen edge/corner.
**Examples:**
```sh
navig ahk snap
```

## `navig ahk start`
**Status:** `stable` · **Since:** 
Start a new process.
**Examples:**
```sh
navig ahk start
```

## `navig ahk status`
**Status:** `stable` · **Since:** 
Show AutoHotkey status and capabilities.
**Examples:**
```sh
navig ahk status
```

## `navig ahk transparency`
**Status:** `stable` · **Since:** 
Set window transparency.
**Examples:**
```sh
navig ahk transparency
```

## `navig ahk type`
**Status:** `stable` · **Since:** 
Type text using keyboard.
**Examples:**
```sh
navig ahk type
```

## `navig ahk volume`
**Status:** `stable` · **Since:** 
Get or set system volume.
**Examples:**
```sh
navig ahk volume
```

## `navig ahk window-state`
**Status:** `stable` · **Since:** 
Get detailed window state.
**Examples:**
```sh
navig ahk window-state
```

## `navig ahk windows`
**Status:** `stable` · **Since:** 
List all visible windows.
**Examples:**
```sh
navig ahk windows
```

## `navig ahk workflow list`
**Status:** `stable` · **Since:** 
List available workflows.
**Examples:**
```sh
navig ahk workflow list
```

## `navig ahk workflow run`
**Status:** `stable` · **Since:** 
Run a cross-platform workflow.
**Examples:**
```sh
navig ahk workflow run
```

## `navig ai`
**Status:** `stable` · **Since:** 
AI Assistant — ask anything, or run a subcommand.
**Examples:**
```sh
navig ai
```

## `navig ai airllm`
**Status:** `stable` · **Since:** 
Configure and manage AirLLM local inference provider.
**Examples:**
```sh
navig ai airllm
```

## `navig ai ask`
**Status:** `stable` · **Since:** 
[DEPRECATED: Use 'navig ask'] Ask AI about server/configuration.
**Examples:**
```sh
navig ai ask
```

## `navig ai diagnose`
**Status:** `stable` · **Since:** 
[DEPRECATED: Use 'navig ask'] AI-powered issue diagnosis based on system state.
**Examples:**
```sh
navig ai diagnose
```

## `navig ai edit`
**Status:** `stable` · **Since:** 
Configure AI assistant settings (interactive wizard).
**Examples:**
```sh
navig ai edit
```

## `navig ai explain`
**Status:** `stable` · **Since:** 
[DEPRECATED: Use 'navig ask'] Explain a log file, error output, or shell command.
**Examples:**
```sh
navig ai explain
```

## `navig ai fallback history`
**Status:** `stable` · **Since:** 
Recent model switches and recoveries (newest first).
**Examples:**
```sh
navig ai fallback history
```

## `navig ai fallback set`
**Status:** `stable` · **Since:** 
Set (or --clear) the model a mode falls back to.
**Examples:**
```sh
navig ai fallback set
```

## `navig ai fallback status`
**Status:** `stable` · **Since:** 
Per mode: primary, its health, the fallback in use or ready, last switch.
**Examples:**
```sh
navig ai fallback status
```

## `navig ai login`
**Status:** `stable` · **Since:** 
Login to an AI provider using OAuth.
**Examples:**
```sh
navig ai login
```

## `navig ai logout`
**Status:** `stable` · **Since:** 
Remove OAuth credentials for a provider.
**Examples:**
```sh
navig ai logout
```

## `navig ai memory add`
**Status:** `stable` · **Since:** 
Add a note to NAVIG's memory about you.
**Examples:**
```sh
navig ai memory add
```

## `navig ai memory clear`
**Status:** `stable` · **Since:** 
Clear all memory (requires --confirm).
**Examples:**
```sh
navig ai memory clear
```

## `navig ai memory edit`
**Status:** `stable` · **Since:** 
Open user profile in your default editor.
**Examples:**
```sh
navig ai memory edit
```

## `navig ai memory search`
**Status:** `stable` · **Since:** 
Search NAVIG's memory about you.
**Examples:**
```sh
navig ai memory search
```

## `navig ai memory set`
**Status:** `stable` · **Since:** 
Set a specific profile field.
**Examples:**
```sh
navig ai memory set
```

## `navig ai memory show`
**Status:** `stable` · **Since:** 
Display what NAVIG knows about you.
**Examples:**
```sh
navig ai memory show
```

## `navig ai models`
**Status:** `stable` · **Since:** 
List available AI models from all providers.
**Examples:**
```sh
navig ai models
```

## `navig ai providers`
**Status:** `stable` · **Since:** 
Manage AI providers and API keys.
**Examples:**
```sh
navig ai providers
```

## `navig ai run`
**Status:** `stable` · **Since:** 
Run AI operations (canonical command).
**Examples:**
```sh
navig ai run
```

## `navig ai show`
**Status:** `stable` · **Since:** 
Show AI assistant information (canonical command).
**Examples:**
```sh
navig ai show
```

## `navig ai suggest`
**Status:** `stable` · **Since:** 
[DEPRECATED: Use 'navig ask'] Get AI-powered optimization suggestions.
**Examples:**
```sh
navig ai suggest
```

## `navig android connect`
**Status:** `stable` · **Since:** 
adb connect over TCP/IP.
**Examples:**
```sh
navig android connect
```

## `navig android fastboot`
**Status:** `stable` · **Since:** 
Pass arguments straight through to the fastboot binary (device in bootloader).
**Examples:**
```sh
navig android fastboot
```

## `navig android logcat`
**Status:** `stable` · **Since:** 
Show recent Android logcat lines.
**Examples:**
```sh
navig android logcat
```

## `navig android reboot`
**Status:** `stable` · **Since:** 
Reboot the device (into the bootloader/recovery for rooting flows).
**Examples:**
```sh
navig android reboot
```

## `navig android sideload`
**Status:** `stable` · **Since:** 
adb sideload a zip (device must be in recovery). Consent + --yes required.
**Examples:**
```sh
navig android sideload
```

## `navig antivirus extensions`
**Status:** `stable` · **Since:** 
🔎 Scan every installed extension across all profiles for malware/PUP/proxyware signatures.
**Examples:**
```sh
navig antivirus extensions
```

## `navig antivirus recover-profiles`
**Status:** `stable` · **Since:** 
🩹 Rebuild a collapsed Chrome profile list (picker shows only 'Default' but the folders exist).
**Examples:**
```sh
navig antivirus recover-profiles
```

## `navig antivirus registry`
**Status:** `stable` · **Since:** 
🗝  Audit registry-forced Chrome extensions + install policies for PUP signatures (Windows).
**Examples:**
```sh
navig antivirus registry
```

## `navig antivirus report`
**Status:** `stable` · **Since:** 
📋 One-pass read-only health report: extensions + registry + AV engine status.
**Examples:**
```sh
navig antivirus report
```

## `navig antivirus system`
**Status:** `stable` · **Since:** 
🦠 Kick off an on-demand system malware scan via Windows Defender.
**Examples:**
```sh
navig antivirus system
```

## `navig app`
**Status:** `stable` · **Since:** 
App management - run without subcommand for help.
**Examples:**
```sh
navig app
```

## `navig app add`
**Status:** `stable` · **Since:** 
Add new app to a host (or clone from existing).
**Examples:**
```sh
navig app add
```

## `navig app edit`
**Status:** `stable` · **Since:** 
Edit app configuration in default editor.
**Examples:**
```sh
navig app edit
```

## `navig app list`
**Status:** `stable` · **Since:** 
List all apps on a host.
**Examples:**
```sh
navig app list
```

## `navig app migrate`
**Status:** `stable` · **Since:** 
Migrate apps from legacy embedded format to individual files.
**Examples:**
```sh
navig app migrate
```

## `navig app remove`
**Status:** `stable` · **Since:** 
Remove app from a host.
**Examples:**
```sh
navig app remove
```

## `navig app search`
**Status:** `stable` · **Since:** 
Search for apps across all hosts by name or configuration.
**Examples:**
```sh
navig app search
```

## `navig app show`
**Status:** `stable` · **Since:** 
Show detailed app configuration (canonical command).
**Examples:**
```sh
navig app show
```

## `navig app use`
**Status:** `stable` · **Since:** 
Set active app (global or local scope).
**Examples:**
```sh
navig app use
```

## `navig apply`
**Status:** `stable` · **Since:** 
Apply a block — run its outcome end-to-end and write a receipt.
**Examples:**
```sh
navig apply
```

## `navig approve`
**Status:** `stable` · **Since:** 
Approval management - run without subcommand to list pending.
**Examples:**
```sh
navig approve
```

## `navig approve level`
**Status:** `stable` · **Since:** 
Set the security level — one knob, three postures, like a router firewall.
**Examples:**
```sh
navig approve level
```

## `navig approve list`
**Status:** `stable` · **Since:** 
List pending approval requests.
**Examples:**
```sh
navig approve list
```

## `navig approve no`
**Status:** `stable` · **Since:** 
Deny a pending request.
**Examples:**
```sh
navig approve no
```

## `navig approve policy`
**Status:** `stable` · **Since:** 
Show approval policy (patterns and levels).
**Examples:**
```sh
navig approve policy
```

## `navig approve status`
**Status:** `stable` · **Since:** 
Show the security level and what it means — configured vs. what the gateway runs.
**Examples:**
```sh
navig approve status
```

## `navig approve yes`
**Status:** `stable` · **Since:** 
Approve a pending request.
**Examples:**
```sh
navig approve yes
```

## `navig ask`
**Status:** `stable` · **Since:** 
Ask AI about server/configuration.
**Examples:**
```sh
navig ask
```

## `navig audio ambience`
**Status:** `stable` · **Since:** 
🌧 Rain, static or city weather under a cut — deterministic, seeded by kind.
**Examples:**
```sh
navig audio ambience
```

## `navig audio beat analyse`
**Status:** `stable` · **Since:** 
🔍 What is this beat — tempo, key, structure, spectrum, and a style brief derived from them.
**Examples:**
```sh
navig audio beat analyse
```

## `navig audio beat demo`
**Status:** `stable` · **Since:** 
🎤 A 30-second guide-vocal demo: the hook spoken in character voices on its bars. GUIDE ONLY.
**Examples:**
```sh
navig audio beat demo
```

## `navig audio beat fit`
**Status:** `stable` · **Since:** 
📏 Does a lyric sit on the beat? (alias of `navig text lyrics fit`)
**Examples:**
```sh
navig audio beat fit
```

## `navig audio beat gen`
**Status:** `stable` · **Since:** 
🎛  Render beats from a style — bar-exact sections, no vocals, tempo and key checked afterwards.
**Examples:**
```sh
navig audio beat gen
```

## `navig audio beat grid`
**Status:** `stable` · **Since:** 
📐 Beat times for a bed — the grid a cut lands on, cached per (file, offset, length).
**Examples:**
```sh
navig audio beat grid
```

## `navig audio beat layer`
**Status:** `stable` · **Since:** 
🎚  Layer sound effects onto a beat — on bars, with fades. The dry beat is never touched.
**Examples:**
```sh
navig audio beat layer
```

## `navig audio beat plan`
**Status:** `stable` · **Since:** 
🧮 The arrangement a render would use — section by section, in seconds. Spends nothing.
**Examples:**
```sh
navig audio beat plan
```

## `navig audio beat styles`
**Status:** `stable` · **Since:** 
📚 The beat styles available — built-in genres plus any file you pass.
**Examples:**
```sh
navig audio beat styles
```

## `navig audio bed assign`
**Status:** `stable` · **Since:** 
🎼 (Re)use the episode's track — stable across renders, unique per episode.
**Examples:**
```sh
navig audio bed assign
```

## `navig audio check`
**Status:** `stable` · **Since:** 
Show whether AI audio generation is configured (a provider key resolves).
**Examples:**
```sh
navig audio check
```

## `navig audio clean-video`
**Status:** `stable` · **Since:** 
🎬 Clean the dialogue in a video and put it back in sync (picture copied untouched).
**Examples:**
```sh
navig audio clean-video
```

## `navig audio clone`
**Status:** `stable` · **Since:** 
👤 Clone a voice from your own recordings and print its id.
**Examples:**
```sh
navig audio clone
```

## `navig audio delivery`
**Status:** `stable` · **Since:** 
📡 Check loudness against a platform's delivery spec — and fix it to fit.
**Examples:**
```sh
navig audio delivery
```

## `navig audio draft`
**Status:** `stable` · **Since:** 
📝 Turn rough notes into a strict scenario you can review before rendering.
**Examples:**
```sh
navig audio draft
```

## `navig audio dub`
**Status:** `stable` · **Since:** 
🌍 Dub into other languages — the same voices, the timing kept.
**Examples:**
```sh
navig audio dub
```

## `navig audio enhance`
**Status:** `stable` · **Since:** 
🎛 Restore and master a recording — measured, explained, verified, never overwritten.
**Examples:**
```sh
navig audio enhance
```

## `navig audio enhance-presets`
**Status:** `stable` · **Since:** 
🎚 The enhancement presets (Preserve Original, Clean SFX, Studio Polish, …).
**Examples:**
```sh
navig audio enhance-presets
```

## `navig audio gen`
**Status:** `stable` · **Since:** 
Generate audio from a text prompt and save it locally.
**Examples:**
```sh
navig audio gen
```

## `navig audio isolate`
**Status:** `stable` · **Since:** 
🎤 Strip background noise, keep the voice — the ElevenLabs voice isolator.
**Examples:**
```sh
navig audio isolate
```

## `navig audio loop`
**Status:** `stable` · **Since:** 
🌧 A seamless, loopable ambience bed at any length (rain, room tone, server room, …).
**Examples:**
```sh
navig audio loop
```

## `navig audio loudness`
**Status:** `stable` · **Since:** 
📏 Integrated loudness (LUFS), loudness range and true peak — how a master is judged.
**Examples:**
```sh
navig audio loudness
```

## `navig audio master`
**Status:** `stable` · **Since:** 
🎚️ Mix a vocal over a beat and master it — a finished song at a measured loudness.
**Examples:**
```sh
navig audio master
```

## `navig audio match`
**Status:** `stable` · **Since:** 
🎚 Match a reference's tonal balance and loudness (dynamics kept unless asked).
**Examples:**
```sh
navig audio match
```

## `navig audio plan`
**Status:** `stable` · **Since:** 
🧮 What this episode will cost, before it costs it. Spends nothing.
**Examples:**
```sh
navig audio plan
```

## `navig audio podcast-edit`
**Status:** `stable` · **Since:** 
✂️ Find fillers, long pauses and repeats — review the cut list, then apply it.
**Examples:**
```sh
navig audio podcast-edit
```

## `navig audio publish`
**Status:** `stable` · **Since:** 
📤 Publish a rendered episode to NobiCast — plan it first, --upload to send.
**Examples:**
```sh
navig audio publish
```

## `navig audio read`
**Status:** `stable` · **Since:** 
📖 Read a text aloud, like a book — numbered ten-minute parts, quiet and unhurried.
**Examples:**
```sh
navig audio read
```

## `navig audio regenerate`
**Status:** `stable` · **Since:** 
🧪 Make NEW audio from a recording with AI — new voice, variations, or a replaced stem.
**Examples:**
```sh
navig audio regenerate
```

## `navig audio render`
**Status:** `stable` · **Since:** 
🎧 Build the episode — one mp3 per track, plus subtitles, transcript and a master.
**Examples:**
```sh
navig audio render
```

## `navig audio score`
**Status:** `stable` · **Since:** 
🎼 Compose music that follows the cuts of a video, mixed under its dialogue.
**Examples:**
```sh
navig audio score
```

## `navig audio similar`
**Status:** `stable` · **Since:** 
🔎 Find sounds that sound like this one — local fingerprints, cached.
**Examples:**
```sh
navig audio similar
```

## `navig audio slowed`
**Status:** `stable` · **Since:** 
🐢 The "slowed" sound — pitch drops with the speed, plus optional reverb.
**Examples:**
```sh
navig audio slowed
```

## `navig audio songs card`
**Status:** `stable` · **Since:** 
🪪 Create or refresh a song card (README.md front matter + facts read from the folder).
**Examples:**
```sh
navig audio songs card
```

## `navig audio songs catalog`
**Status:** `stable` · **Since:** 
📚 Build the catalog from every song card under ROOT.
**Examples:**
```sh
navig audio songs catalog
```

## `navig audio songs check`
**Status:** `stable` · **Since:** 
🩺 Duplicate ids, unknown status, a recorded song with no audio. Exit 1 when anything is wrong.
**Examples:**
```sh
navig audio songs check
```

## `navig audio speed`
**Status:** `stable` · **Since:** 
⏩ Change speed, keeping pitch — a lecture at 1.5x still sounds like the speaker.
**Examples:**
```sh
navig audio speed
```

## `navig audio spoken`
**Status:** `stable` · **Since:** 
🔢 Display text → the words a voice should actually speak (cases, ordinals, years).
**Examples:**
```sh
navig audio spoken
```

## `navig audio stems`
**Status:** `stable` · **Since:** 
🎛 Split a song into parts once, then mute / solo / re-balance them locally.
**Examples:**
```sh
navig audio stems
```

## `navig audio translate`
**Status:** `stable` · **Since:** 
🌍 Translate the spoken lines, keeping the album structure identical.
**Examples:**
```sh
navig audio translate
```

## `navig audio voice-line`
**Status:** `stable` · **Since:** 
🗣 One narration line with word timings — the kinetic renderer reads the same cache.
**Examples:**
```sh
navig audio voice-line
```

## `navig audio voice-stats`
**Status:** `stable` · **Since:** 
📊 What the shared voice cache holds — counts, size, orphans, age.
**Examples:**
```sh
navig audio voice-stats
```

## `navig audio voices`
**Status:** `stable` · **Since:** 
🗣  List the voices this key can speak with — your clones first.
**Examples:**
```sh
navig audio voices
```

## `navig audit`
**Status:** `stable` · **Since:** 
Privileged-action audit trail (gateway policy + approval decisions).
**Examples:**
```sh
navig audit
```

## `navig audit tail`
**Status:** `stable` · **Since:** 
Show recent privileged-action audit records from the gateway audit log.
**Examples:**
```sh
navig audit tail
```

## `navig auto click`
**Status:** `stable` · **Since:** 
Click at screen coordinates.
**Examples:**
```sh
navig auto click
```

## `navig auto clipboard`
**Status:** `stable` · **Since:** 
Get or set clipboard content.
**Examples:**
```sh
navig auto clipboard
```

## `navig auto focus`
**Status:** `stable` · **Since:** 
Get currently focused window.
**Examples:**
```sh
navig auto focus
```

## `navig auto open`
**Status:** `stable` · **Since:** 
Open application.
**Examples:**
```sh
navig auto open
```

## `navig auto snap`
**Status:** `stable` · **Since:** 
Snap window to screen position.
**Examples:**
```sh
navig auto snap
```

## `navig auto status`
**Status:** `stable` · **Since:** 
Check automation system status.
**Examples:**
```sh
navig auto status
```

## `navig auto type`
**Status:** `stable` · **Since:** 
Type text.
**Examples:**
```sh
navig auto type
```

## `navig auto windows`
**Status:** `stable` · **Since:** 
List all windows.
**Examples:**
```sh
navig auto windows
```

## `navig backup`
**Status:** `stable` · **Since:** 
Backup management - run without subcommand for help.
**Examples:**
```sh
navig backup
```

## `navig backup config`
**Status:** `stable` · **Since:** 
Backup/export NAVIG configuration (hosts, apps, settings).
**Examples:**
```sh
navig backup config
```

## `navig backup export`
**Status:** `stable` · **Since:** 
Export NAVIG configuration to a backup file.
**Examples:**
```sh
navig backup export
```

## `navig backup import`
**Status:** `stable` · **Since:** 
Import NAVIG configuration from a backup file.
**Examples:**
```sh
navig backup import
```

## `navig backup remove`
**Status:** `stable` · **Since:** 
Remove/delete a backup file (canonical command).
**Examples:**
```sh
navig backup remove
```

## `navig backup restore`
**Status:** `stable` · **Since:** 
Report what a backup contains (restore itself is not implemented).
**Examples:**
```sh
navig backup restore
```

## `navig backup run`
**Status:** `stable` · **Since:** 
Run server backup/restore operations (system config, DBs, Hestia, web).
**Examples:**
```sh
navig backup run
```

## `navig backup show`
**Status:** `stable` · **Since:** 
Show backup details or list all backups (canonical command).
**Examples:**
```sh
navig backup show
```

## `navig backup watch`
**Status:** `stable` · **Since:** 
Is the newest backup in PATH fresh? Exit 0 fresh, 1 stale/unreachable.
**Examples:**
```sh
navig backup watch
```

## `navig benchmark run`
**Status:** `stable` · **Since:** 
Run benchmarks and show timing results.
**Examples:**
```sh
navig benchmark run
```

## `navig blackbox bundle create`
**Status:** `stable` · **Since:** 
Create a .navbox bundle from recent events and logs.
**Examples:**
```sh
navig blackbox bundle create
```

## `navig blackbox bundle export`
**Status:** `stable` · **Since:** 
Re-export an existing .navbox bundle, optionally with encryption.
**Examples:**
```sh
navig blackbox bundle export
```

## `navig blackbox bundle inspect`
**Status:** `stable` · **Since:** 
Inspect the contents of a .navbox bundle.
**Examples:**
```sh
navig blackbox bundle inspect
```

## `navig blackbox capture`
**Status:** `stable` · **Since:** 
Capture a session snapshot to a .navbox bundle.
**Examples:**
```sh
navig blackbox capture
```

## `navig blackbox clear`
**Status:** `stable` · **Since:** 
Delete all recorded blackbox events (irreversible).
**Examples:**
```sh
navig blackbox clear
```

## `navig blackbox crashes`
**Status:** `stable` · **Since:** 
List recent crash reports.
**Examples:**
```sh
navig blackbox crashes
```

## `navig blackbox disable`
**Status:** `stable` · **Since:** 
Pause event recording (events will not be written).
**Examples:**
```sh
navig blackbox disable
```

## `navig blackbox enable`
**Status:** `stable` · **Since:** 
Enable event recording.
**Examples:**
```sh
navig blackbox enable
```

## `navig blackbox record`
**Status:** `stable` · **Since:** 
Manually record an event into the blackbox.
**Examples:**
```sh
navig blackbox record
```

## `navig blackbox seal`
**Status:** `stable` · **Since:** 
Seal the blackbox (prevents recording new events until unsealed).
**Examples:**
```sh
navig blackbox seal
```

## `navig blackbox status`
**Status:** `stable` · **Since:** 
Show blackbox recorder status and storage statistics.
**Examples:**
```sh
navig blackbox status
```

## `navig blackbox tail`
**Status:** `stable` · **Since:** 
Show the most recent N events from the blackbox.
**Examples:**
```sh
navig blackbox tail
```

## `navig blackbox timeline`
**Status:** `stable` · **Since:** 
Render a Rich timeline table of recorded events.
**Examples:**
```sh
navig blackbox timeline
```

## `navig block doctor`
**Status:** `stable` · **Since:** 
Check whether a block can run **here**: declared tools/plugins present and its
**Examples:**
```sh
navig block doctor
```

## `navig block list`
**Status:** `stable` · **Since:** 
List installed/discovered blocks.
**Examples:**
```sh
navig block list
```

## `navig block new`
**Status:** `stable` · **Since:** 
Scaffold a new BLOCK.md (a valid, runnable example) under .navig/blocks/<id>/.
**Examples:**
```sh
navig block new
```

## `navig block show`
**Status:** `stable` · **Since:** 
Show a block's spec: inputs, steps (with computed risk), and verify.
**Examples:**
```sh
navig block show
```

## `navig block sign`
**Status:** `stable` · **Since:** 
Device-sign a block's manifest — writes BLOCK.sig (digest + Ed25519 signature).
**Examples:**
```sh
navig block sign
```

## `navig block verify`
**Status:** `stable` · **Since:** 
Lint a block manifest (does NOT execute it).
**Examples:**
```sh
navig block verify
```

## `navig block verify-receipt`
**Status:** `stable` · **Since:** 
Verify a receipt's device signature — proves it wasn't altered after the run.
**Examples:**
```sh
navig block verify-receipt
```

## `navig blueprint apply`
**Status:** `stable` · **Since:** 
Apply a blueprint to a directory.
**Examples:**
```sh
navig blueprint apply
```

## `navig blueprint list`
**Status:** `stable` · **Since:** 
List available blueprints.
**Examples:**
```sh
navig blueprint list
```

## `navig body`
**Status:** `stable` · **Since:** 
Show today's record when called with no subcommand.
**Examples:**
```sh
navig body
```

## `navig body cadence`
**Status:** `stable` · **Since:** 
Show — or set — how often the scheduled weigh-in asks.
**Examples:**
```sh
navig body cadence
```

## `navig body checkin`
**Status:** `stable` · **Since:** 
Preview — or send to Telegram — the weigh-in prompt or the weekly card.
**Examples:**
```sh
navig body checkin
```

## `navig body export`
**Status:** `stable` · **Since:** 
Write a dated summary to the space's out/ — what an appointment needs.
**Examples:**
```sh
navig body export
```

## `navig body log`
**Status:** `stable` · **Since:** 
Record one or more measurements for a day.
**Examples:**
```sh
navig body log
```

## `navig body today`
**Status:** `stable` · **Since:** 
Show what is recorded for a day, and where the week stands.
**Examples:**
```sh
navig body today
```

## `navig body trend`
**Status:** `stable` · **Since:** 
Show the moving average and the change against the previous window.
**Examples:**
```sh
navig body trend
```

## `navig bookmarks add`
**Status:** `stable` · **Since:** 
Save a bookmark.
**Examples:**
```sh
navig bookmarks add
```

## `navig bookmarks check`
**Status:** `stable` · **Since:** 
Find dead links and redirects (headers only; private addresses are never contacted).
**Examples:**
```sh
navig bookmarks check
```

## `navig bookmarks dedupe`
**Status:** `stable` · **Since:** 
Find bookmarks that point at the same page (tracking parameters, www., http/https ignored).
**Examples:**
```sh
navig bookmarks dedupe
```

## `navig bookmarks delete`
**Status:** `stable` · **Since:** 
Delete a bookmark.
**Examples:**
```sh
navig bookmarks delete
```

## `navig bookmarks edit`
**Status:** `stable` · **Since:** 
Change a bookmark.
**Examples:**
```sh
navig bookmarks edit
```

## `navig bookmarks export`
**Status:** `stable` · **Since:** 
Export bookmarks as Netscape HTML (with folders) or JSON.
**Examples:**
```sh
navig bookmarks export
```

## `navig bookmarks folders`
**Status:** `stable` · **Since:** 
Folders and how many bookmarks each holds.
**Examples:**
```sh
navig bookmarks folders
```

## `navig bookmarks import`
**Status:** `stable` · **Since:** 
Import bookmarks — folders are kept; URLs already saved are skipped.
**Examples:**
```sh
navig bookmarks import
```

## `navig bookmarks list`
**Status:** `stable` · **Since:** 
List bookmarks, most recently used first.
**Examples:**
```sh
navig bookmarks list
```

## `navig bookmarks migrate`
**Status:** `stable` · **Since:** 
Copy bookmarks from navig's old `links` store (also done automatically; safe to repeat).
**Examples:**
```sh
navig bookmarks migrate
```

## `navig bookmarks open`
**Status:** `stable` · **Since:** 
Open a bookmark. With a vault credential and navig installed, the login is filled in.
**Examples:**
```sh
navig bookmarks open
```

## `navig bookmarks organize`
**Status:** `stable` · **Since:** 
Plan a clean folder tree with your AI. Dry run unless --apply.
**Examples:**
```sh
navig bookmarks organize
```

## `navig bookmarks rules add`
**Status:** `stable` · **Since:** 
Add a rule (applies to new bookmarks; run `rules apply` for existing ones).
**Examples:**
```sh
navig bookmarks rules add
```

## `navig bookmarks rules apply`
**Status:** `stable` · **Since:** 
Re-tag every bookmark from the current rules (only rule tags are replaced).
**Examples:**
```sh
navig bookmarks rules apply
```

## `navig bookmarks rules list`
**Status:** `stable` · **Since:** 
List tagging rules.
**Examples:**
```sh
navig bookmarks rules list
```

## `navig bookmarks rules remove`
**Status:** `stable` · **Since:** 
Remove a rule (its tags stay until `rules apply`).
**Examples:**
```sh
navig bookmarks rules remove
```

## `navig bookmarks search`
**Status:** `stable` · **Since:** 
Full-text search.
**Examples:**
```sh
navig bookmarks search
```

## `navig bookmarks show`
**Status:** `stable` · **Since:** 
Everything about one bookmark.
**Examples:**
```sh
navig bookmarks show
```

## `navig bookmarks tag`
**Status:** `stable` · **Since:** 
Add or remove tags — or let the AI tag (it only ever replaces its own tags).
**Examples:**
```sh
navig bookmarks tag
```

## `navig bookmarks tags`
**Status:** `stable` · **Since:** 
Tags and how often each is used.
**Examples:**
```sh
navig bookmarks tags
```

## `navig bookmarks version`
**Status:** `stable` · **Since:** 
Print the plugin version.
**Examples:**
```sh
navig bookmarks version
```

## `navig bot`
**Status:** `stable` · **Since:** 
Bot commands - run without subcommand to start bot.
**Examples:**
```sh
navig bot
```

## `navig bot start`
**Status:** `stable` · **Since:** 
Start the NAVIG Telegram bot.
**Examples:**
```sh
navig bot start
```

## `navig bot status`
**Status:** `stable` · **Since:** 
Check whether OUR bot/gateway (this config dir) is running.
**Examples:**
```sh
navig bot status
```

## `navig bot stop`
**Status:** `stable` · **Since:** 
Stop OUR running NAVIG bot/gateway processes (this config dir only).
**Examples:**
```sh
navig bot stop
```

## `navig brain prompts get`
**Status:** `stable` · **Since:** 
Output prompt content — intended for programmatic consumption by navig-bridge/CLI.
**Examples:**
```sh
navig brain prompts get
```

## `navig brain prompts list`
**Status:** `stable` · **Since:** 
List all available prompt slugs (project-local + global, merged).
**Examples:**
```sh
navig brain prompts list
```

## `navig brain prompts reload`
**Status:** `stable` · **Since:** 
Signal the NAVIG daemon to flush its prompt cache (no-op if daemon is not running).
**Examples:**
```sh
navig brain prompts reload
```

## `navig brain prompts set`
**Status:** `stable` · **Since:** 
Write or overwrite a prompt file.
**Examples:**
```sh
navig brain prompts set
```

## `navig bridge connect`
**Status:** `stable` · **Since:** 
Set up Bridge ↔ Daemon connection in one step.
**Examples:**
```sh
navig bridge connect
```

## `navig bridge rotate-token`
**Status:** `stable` · **Since:** 
Rotate the gateway auth token.
**Examples:**
```sh
navig bridge rotate-token
```

## `navig bridge status`
**Status:** `stable` · **Since:** 
Show current Bridge connection configuration.
**Examples:**
```sh
navig bridge status
```

## `navig build`
**Status:** `stable` · **Since:** 
Run navig build
**Examples:**
```sh
navig build
```

## `navig cabinet add`
**Status:** `stable` · **Since:** 
Encrypt files into the cabinet (originals are kept unless --move).
**Examples:**
```sh
navig cabinet add
```

## `navig cabinet backup`
**Status:** `stable` · **Since:** 
Write one encrypted backup file that restores on any computer, with its own passphrase.
**Examples:**
```sh
navig cabinet backup
```

## `navig cabinet close`
**Status:** `stable` · **Since:** 
Remove every decrypted copy left by `open` right now.
**Examples:**
```sh
navig cabinet close
```

## `navig cabinet dates`
**Status:** `stable` · **Since:** 
Find expiry dates inside documents that have none — passports, ID cards, policies.
**Examples:**
```sh
navig cabinet dates
```

## `navig cabinet edit`
**Status:** `stable` · **Since:** 
Change an item's title, category, tags, expiry, issuer or notes.
**Examples:**
```sh
navig cabinet edit
```

## `navig cabinet expiring`
**Status:** `stable` · **Since:** 
Documents that have expired or expire soon — passports, ID cards, insurance.
**Examples:**
```sh
navig cabinet expiring
```

## `navig cabinet export`
**Status:** `stable` · **Since:** 
Write decrypted copies with their original names (verified; never overwrites).
**Examples:**
```sh
navig cabinet export
```

## `navig cabinet import-paperwork`
**Status:** `stable` · **Since:** 
Encrypt the ID and medical documents a `navig paperwork` scan set aside (originals stay put).
**Examples:**
```sh
navig cabinet import-paperwork
```

## `navig cabinet list`
**Status:** `stable` · **Since:** 
Everything in the cabinet.
**Examples:**
```sh
navig cabinet list
```

## `navig cabinet open`
**Status:** `stable` · **Since:** 
Open an item in its usual app (a temporary decrypted copy).
**Examples:**
```sh
navig cabinet open
```

## `navig cabinet passphrase clear`
**Status:** `stable` · **Since:** 
Go back to the machine key (no passphrase).
**Examples:**
```sh
navig cabinet passphrase clear
```

## `navig cabinet passphrase set`
**Status:** `stable` · **Since:** 
Lock the cabinet with a passphrase (asked for on every command).
**Examples:**
```sh
navig cabinet passphrase set
```

## `navig cabinet remind`
**Status:** `stable` · **Since:** 
Send the expiry reminders that are due now (the daemon also does this daily).
**Examples:**
```sh
navig cabinet remind
```

## `navig cabinet remove`
**Status:** `stable` · **Since:** 
Move an item to the trash (or --purge it for good).
**Examples:**
```sh
navig cabinet remove
```

## `navig cabinet restore`
**Status:** `stable` · **Since:** 
Add everything from a backup that the cabinet does not already hold.
**Examples:**
```sh
navig cabinet restore
```

## `navig cabinet search`
**Status:** `stable` · **Since:** 
Find items by title, tag, or any word inside the document.
**Examples:**
```sh
navig cabinet search
```

## `navig cabinet show`
**Status:** `stable` · **Since:** 
Details of one item.
**Examples:**
```sh
navig cabinet show
```

## `navig cabinet status`
**Status:** `stable` · **Since:** 
What is in the cabinet, how it is locked, and what needs attention.
**Examples:**
```sh
navig cabinet status
```

## `navig cabinet undelete`
**Status:** `stable` · **Since:** 
Bring an item back from the trash.
**Examples:**
```sh
navig cabinet undelete
```

## `navig cabinet verify`
**Status:** `stable` · **Since:** 
Decrypt every item and check it against its stored checksum.
**Examples:**
```sh
navig cabinet verify
```

## `navig calendar add`
**Status:** `stable` · **Since:** 
Add a new calendar event.
**Examples:**
```sh
navig calendar add
```

## `navig calendar auth`
**Status:** `stable` · **Since:** 
Authenticate with a calendar provider.
**Examples:**
```sh
navig calendar auth
```

## `navig calendar list`
**Status:** `stable` · **Since:** 
List upcoming calendar events.
**Examples:**
```sh
navig calendar list
```

## `navig calendar sync`
**Status:** `stable` · **Since:** 
Nothing to sync: events are read live from your provider on every `list`.
**Examples:**
```sh
navig calendar sync
```

## `navig card add`
**Status:** `stable` · **Since:** 
Create a task card (orchestrator only — assignment is one session's job).
**Examples:**
```sh
navig card add
```

## `navig card claim`
**Status:** `stable` · **Since:** 
Take exclusive ownership of a card (atomic CAS — one holder wins).
**Examples:**
```sh
navig card claim
```

## `navig card deps add`
**Status:** `stable` · **Since:** 
Declare a dependency (orchestrator only; cycles are rejected by the store).
**Examples:**
```sh
navig card deps add
```

## `navig card deps remove`
**Status:** `stable` · **Since:** 
Drop a dependency (orchestrator only).
**Examples:**
```sh
navig card deps remove
```

## `navig card list`
**Status:** `stable` · **Since:** 
List task cards — their real lanes, gate and holder.
**Examples:**
```sh
navig card list
```

## `navig card move`
**Status:** `stable` · **Since:** 
Move a card between lifecycle stages — allowed only inside the caller's role matrix.
**Examples:**
```sh
navig card move
```

## `navig card release`
**Status:** `stable` · **Since:** 
Release a claim (your own — or, with --force, an abandoned one).
**Examples:**
```sh
navig card release
```

## `navig card show`
**Status:** `stable` · **Since:** 
One card in full: record pointer, dependencies, checklist, stage history.
**Examples:**
```sh
navig card show
```

## `navig card subtask add`
**Status:** `stable` · **Since:** 
Add an acceptance criterion (orchestrator seeds the checklist).
**Examples:**
```sh
navig card subtask add
```

## `navig card subtask done`
**Status:** `stable` · **Since:** 
Tick one acceptance criterion (never a reviewer — its write surface is the receipt).
**Examples:**
```sh
navig card subtask done
```

## `navig cdp click`
**Status:** `stable` · **Since:** 
Click by a11y ref or at coordinates.
**Examples:**
```sh
navig cdp click
```

## `navig cdp detach`
**Status:** `stable` · **Since:** 
Disconnect NAVIG's session but LEAVE the browser open (port stays on).
**Examples:**
```sh
navig cdp detach
```

## `navig cdp eval`
**Status:** `stable` · **Since:** 
Evaluate JavaScript in the page (gated — JS can change page state).
**Examples:**
```sh
navig cdp eval
```

## `navig cdp inject`
**Status:** `stable` · **Since:** 
Inject a persistent userscript (runs at document-start on every navigation).
**Examples:**
```sh
navig cdp inject
```

## `navig cdp key`
**Status:** `stable` · **Since:** 
Press a key or key combination.
**Examples:**
```sh
navig cdp key
```

## `navig cdp launch`
**Status:** `stable` · **Since:** 
Launch an app with a debug port so CDP can attach.
**Examples:**
```sh
navig cdp launch
```

## `navig cdp launched`
**Status:** `stable` · **Since:** 
List debug browsers NAVIG launched (and whether each is still live).
**Examples:**
```sh
navig cdp launched
```

## `navig cdp login`
**Status:** `stable` · **Since:** 
Auto-login on the attached page from a vaulted website credential.
**Examples:**
```sh
navig cdp login
```

## `navig cdp move`
**Status:** `stable` · **Since:** 
Move the mouse to viewport coordinates.
**Examples:**
```sh
navig cdp move
```

## `navig cdp nav`
**Status:** `stable` · **Since:** 
Navigate a page to a URL (optionally choose which tab first).
**Examples:**
```sh
navig cdp nav
```

## `navig cdp new`
**Status:** `stable` · **Since:** 
Open a COMPLETELY FRESH, isolated browser (own profile + own debug port).
**Examples:**
```sh
navig cdp new
```

## `navig cdp open`
**Status:** `stable` · **Since:** 
Open (or REUSE if already running) a named profile's browser on its stable port.
**Examples:**
```sh
navig cdp open
```

## `navig cdp profile close`
**Status:** `stable` · **Since:** 
Close a running profile's browser (leaves the profile registered).
**Examples:**
```sh
navig cdp profile close
```

## `navig cdp profile export`
**Status:** `stable` · **Since:** 
Export a profile's coherent persona (+ optional session) as a portable capsule.
**Examples:**
```sh
navig cdp profile export
```

## `navig cdp profile import`
**Status:** `stable` · **Since:** 
Import a persona capsule → (re)create the profile, its proxy, and its session.
**Examples:**
```sh
navig cdp profile import
```

## `navig cdp profile list`
**Status:** `stable` · **Since:** 
List named automation profiles (and optionally your real browser profiles).
**Examples:**
```sh
navig cdp profile list
```

## `navig cdp profile new`
**Status:** `stable` · **Since:** 
Create a named browser profile (a fresh automation profile by default).
**Examples:**
```sh
navig cdp profile new
```

## `navig cdp profile open`
**Status:** `stable` · **Since:** 
Open (or reuse) a profile — same as `navig cdp open <name>`.
**Examples:**
```sh
navig cdp profile open
```

## `navig cdp profile proxy`
**Status:** `stable` · **Since:** 
Assign a per-profile proxy (overrides the shared browser.proxies pool).
**Examples:**
```sh
navig cdp profile proxy
```

## `navig cdp profile prune`
**Status:** `stable` · **Since:** 
Reclaim disk from browser profiles (throwaway sessions, or a profile you name).
**Examples:**
```sh
navig cdp profile prune
```

## `navig cdp profile remove`
**Status:** `stable` · **Since:** 
Remove a profile from the registry (closing it first).
**Examples:**
```sh
navig cdp profile remove
```

## `navig cdp profile usage`
**Status:** `stable` · **Since:** 
Show how much disk each browser profile is using.
**Examples:**
```sh
navig cdp profile usage
```

## `navig cdp profile use`
**Status:** `stable` · **Since:** 
Set the active profile (navig do / cdp default to it).
**Examples:**
```sh
navig cdp profile use
```

## `navig cdp profile vacuum`
**Status:** `stable` · **Since:** 
Reclaim disk from a profile WITHOUT losing its logins: delete only what Chrome rebuilds.
**Examples:**
```sh
navig cdp profile vacuum
```

## `navig cdp record`
**Status:** `stable` · **Since:** 
Record the attached page to an mp4 (screencast → video).
**Examples:**
```sh
navig cdp record
```

## `navig cdp screenshot`
**Status:** `stable` · **Since:** 
Capture a screenshot of the attached page.
**Examples:**
```sh
navig cdp screenshot
```

## `navig cdp scroll`
**Status:** `stable` · **Since:** 
Scroll the page by a wheel delta (positive = down).
**Examples:**
```sh
navig cdp scroll
```

## `navig cdp snapshot`
**Status:** `stable` · **Since:** 
Accessibility snapshot with numeric refs (feed to `cdp click --ref`).
**Examples:**
```sh
navig cdp snapshot
```

## `navig cdp status`
**Status:** `stable` · **Since:** 
Show the platform, launchable apps, live CDP targets, and any LEAKED browsers.
**Examples:**
```sh
navig cdp status
```

## `navig cdp stop`
**Status:** `stable` · **Since:** 
Close a NAVIG-launched debug browser (turns the debug port OFF).
**Examples:**
```sh
navig cdp stop
```

## `navig cdp switch`
**Status:** `stable` · **Since:** 
Make a specific open tab the active target (agent/daemon sessions).
**Examples:**
```sh
navig cdp switch
```

## `navig cdp tabs`
**Status:** `stable` · **Since:** 
List EVERY open page in the browser (full inventory from raw CDP).
**Examples:**
```sh
navig cdp tabs
```

## `navig cdp targets`
**Status:** `stable` · **Since:** 
List live CDP endpoints on localhost.
**Examples:**
```sh
navig cdp targets
```

## `navig cdp type`
**Status:** `stable` · **Since:** 
Type text into the focused element.
**Examples:**
```sh
navig cdp type
```

## `navig chats`
**Status:** `stable` · **Since:** 
Run navig chats
**Examples:**
```sh
navig chats
```

## `navig chats export`
**Status:** `stable` · **Since:** 
Export chats — readable without NAVIG. Several chats go into one .zip with a manifest.
**Examples:**
```sh
navig chats export
```

## `navig chats folders add`
**Status:** `stable` · **Since:** 
Create a folder.
**Examples:**
```sh
navig chats folders add
```

## `navig chats folders list`
**Status:** `stable` · **Since:** 
Every folder, as a tree.
**Examples:**
```sh
navig chats folders list
```

## `navig chats folders rm`
**Status:** `stable` · **Since:** 
Delete a folder. Its chats stay in the library; subfolders move up a level.
**Examples:**
```sh
navig chats folders rm
```

## `navig chats handoff`
**Status:** `stable` · **Since:** 
Continue this work — a context pack (decisions, open items, code, sources) for another agent.
**Examples:**
```sh
navig chats handoff
```

## `navig chats import`
**Status:** `stable` · **Since:** 
Import a ChatGPT account export. Safe to re-run: unchanged chats are skipped.
**Examples:**
```sh
navig chats import
```

## `navig chats list`
**Status:** `stable` · **Since:** 
List chats in the library.
**Examples:**
```sh
navig chats list
```

## `navig chats move`
**Status:** `stable` · **Since:** 
Put chats in a NAVIG folder (a local change — nothing moves in ChatGPT).
**Examples:**
```sh
navig chats move
```

## `navig chats reindex`
**Status:** `stable` · **Since:** 
Rebuild the search index from the stored messages.
**Examples:**
```sh
navig chats reindex
```

## `navig chats restore`
**Status:** `stable` · **Since:** 
Restore chats from a NAVIG export into the library (not into ChatGPT itself).
**Examples:**
```sh
navig chats restore
```

## `navig chats search`
**Status:** `stable` · **Since:** 
Search every message and title, ranked by relevance.
**Examples:**
```sh
navig chats search
```

## `navig chats share`
**Status:** `stable` · **Since:** 
Choose what NAVIG's knowledge index may read. Nothing is shared until you say so.
**Examples:**
```sh
navig chats share
```

## `navig chats shared`
**Status:** `stable` · **Since:** 
Every chat NAVIG's knowledge index may read, and at which level.
**Examples:**
```sh
navig chats shared
```

## `navig chats show`
**Status:** `stable` · **Since:** 
Read one chat.
**Examples:**
```sh
navig chats show
```

## `navig chats stats`
**Status:** `stable` · **Since:** 
How much the library holds, and where.
**Examples:**
```sh
navig chats stats
```

## `navig checkin`
**Status:** `stable` · **Since:** 
Show today's check-ins when called with no subcommand.
**Examples:**
```sh
navig checkin
```

## `navig checkin add pills`
**Status:** `stable` · **Since:** 
Add a medication check-in — one prompt per time of day.
**Examples:**
```sh
navig checkin add pills
```

## `navig checkin add water`
**Status:** `stable` · **Since:** 
Add the water check-in — a daily target and optional reminders.
**Examples:**
```sh
navig checkin add water
```

## `navig checkin list`
**Status:** `stable` · **Since:** 
Every check-in, its schedule and today's state.
**Examples:**
```sh
navig checkin list
```

## `navig checkin log`
**Status:** `stable` · **Since:** 
Record an answer by hand (the weigh-in has `navig body log --weight`).
**Examples:**
```sh
navig checkin log
```

## `navig checkin migrate`
**Status:** `stable` · **Since:** 
Move the old weigh-in jobs (habit:weigh / habit:bodyreview) onto check-ins.
**Examples:**
```sh
navig checkin migrate
```

## `navig checkin pause`
**Status:** `stable` · **Since:** 
Stop asking. Nothing is deleted; resume restores it exactly.
**Examples:**
```sh
navig checkin pause
```

## `navig checkin remove`
**Status:** `stable` · **Since:** 
Remove a check-in and its schedule. Answers already recorded are kept.
**Examples:**
```sh
navig checkin remove
```

## `navig checkin resume`
**Status:** `stable` · **Since:** 
Start asking again.
**Examples:**
```sh
navig checkin resume
```

## `navig checkin stats`
**Status:** `stable` · **Since:** 
Pill adherence and water totals over the last N days.
**Examples:**
```sh
navig checkin stats
```

## `navig cloud connect`
**Status:** `stable` · **Since:** 
Enable cloud routing on this daemon and register with the broker.
**Examples:**
```sh
navig cloud connect
```

## `navig cloud direct`
**Status:** `stable` · **Since:** 
Switch to DIRECT mode for VPS / reverse-proxy deployments.
**Examples:**
```sh
navig cloud direct
```

## `navig cloud disconnect`
**Status:** `stable` · **Since:** 
Disable cloud routing. Daemon stops cloudflared and unregisters.
**Examples:**
```sh
navig cloud disconnect
```

## `navig cloud key`
**Status:** `stable` · **Since:** 
Show / rotate the api_key the broker maps to this daemon.
**Examples:**
```sh
navig cloud key
```

## `navig cloud status`
**Status:** `stable` · **Since:** 
Show cloud + tunnel status. Works whether or not the daemon is running.
**Examples:**
```sh
navig cloud status
```

## `navig cloud tailscale`
**Status:** `stable` · **Since:** 
Self-host the Mini App via Tailscale Funnel — stable HTTPS, no domain needed.
**Examples:**
```sh
navig cloud tailscale
```

## `navig config`
**Status:** `stable` · **Since:** 
Configuration management - run without subcommand for help.
**Examples:**
```sh
navig config
```

## `navig config audit`
**Status:** `stable` · **Since:** 
Audit configuration for security issues (optionally auto-fix with --fix).
**Examples:**
```sh
navig config audit
```

## `navig config backup`
**Status:** `stable` · **Since:** 
Back up NAVIG configuration to an archive or JSON file.
**Examples:**
```sh
navig config backup
```

## `navig config edit`
**Status:** `stable` · **Since:** 
Open configuration in default editor.
**Examples:**
```sh
navig config edit
```

## `navig config get`
**Status:** `stable` · **Since:** 
Read a configuration value by dotted key (e.g. ai.default_provider).
**Examples:**
```sh
navig config get
```

## `navig config migrate`
**Status:** `stable` · **Since:** 
Migrate the global configuration to the current schema.
**Examples:**
```sh
navig config migrate
```

## `navig config schema install`
**Status:** `stable` · **Since:** 
Install NAVIG YAML JSON Schemas for editor validation/autocomplete.
**Examples:**
```sh
navig config schema install
```

## `navig config set`
**Status:** `stable` · **Since:** 
Set a global configuration value.
**Examples:**
```sh
navig config set
```

## `navig config set-confirmation-level`
**Status:** `stable` · **Since:** 
Set the confirmation level: critical, standard, or verbose.
**Examples:**
```sh
navig config set-confirmation-level
```

## `navig config set-mode`
**Status:** `stable` · **Since:** 
Set the execution mode: interactive or auto.
**Examples:**
```sh
navig config set-mode
```

## `navig config settings`
**Status:** `stable` · **Since:** 
Display current NAVIG settings including execution mode and confirmation level.
**Examples:**
```sh
navig config settings
```

## `navig config show`
**Status:** `stable` · **Since:** 
Show the current configuration (global scope or a host).
**Examples:**
```sh
navig config show
```

## `navig config test`
**Status:** `stable` · **Since:** 
Alias for: navig config validate.
**Examples:**
```sh
navig config test
```

## `navig config validate`
**Status:** `stable` · **Since:** 
Validate configuration for a host, the project, the global scope, or all.
**Examples:**
```sh
navig config validate
```

## `navig connect`
**Status:** `stable` · **Since:** 
List connections when invoked with no subcommand.
**Examples:**
```sh
navig connect
```

## `navig connect add`
**Status:** `stable` · **Since:** 
Add a connection. The API key is read via hidden prompt or --stdin (never argv).
**Examples:**
```sh
navig connect add
```

## `navig connect default`
**Status:** `stable` · **Since:** 
Set the global default connection.
**Examples:**
```sh
navig connect default
```

## `navig connect detect`
**Status:** `stable` · **Since:** 
Detect official coding-agent runtimes (Claude Code, Codex, Copilot) on this machine.
**Examples:**
```sh
navig connect detect
```

## `navig connect doctor`
**Status:** `stable` · **Since:** 
Print a redacted, paste-safe diagnostics report (no secrets).
**Examples:**
```sh
navig connect doctor
```

## `navig connect list`
**Status:** `stable` · **Since:** 
List configured connections and their status.
**Examples:**
```sh
navig connect list
```

## `navig connect login`
**Status:** `stable` · **Since:** 
Log in to a subscription via OAuth — `claude-max` (Claude Pro/Max) or `chatgpt`
**Examples:**
```sh
navig connect login
```

## `navig connect remove`
**Status:** `stable` · **Since:** 
Disconnect & remove a connection (local config + vault material only).
**Examples:**
```sh
navig connect remove
```

## `navig connect templates`
**Status:** `stable` · **Since:** 
List the available connection templates, grouped.
**Examples:**
```sh
navig connect templates
```

## `navig connect test`
**Status:** `stable` · **Since:** 
Re-validate connection(s) and persist the fresh status.
**Examples:**
```sh
navig connect test
```

## `navig connector catalog`
**Status:** `stable` · **Since:** 
Every service NAVIG knows about, ranked into batches of 25.
**Examples:**
```sh
navig connector catalog
```

## `navig connector connect`
**Status:** `stable` · **Since:** 
Authenticate and connect a service connector.
**Examples:**
```sh
navig connector connect
```

## `navig connector disconnect`
**Status:** `stable` · **Since:** 
Disconnect a service connector.
**Examples:**
```sh
navig connector disconnect
```

## `navig connector fetch`
**Status:** `stable` · **Since:** 
Fetch a single resource by connector:resource_id.
**Examples:**
```sh
navig connector fetch
```

## `navig connector health`
**Status:** `stable` · **Since:** 
Run health checks on connected connectors.
**Examples:**
```sh
navig connector health
```

## `navig connector list`
**Status:** `stable` · **Since:** 
List all registered connectors.
**Examples:**
```sh
navig connector list
```

## `navig connector search`
**Status:** `stable` · **Since:** 
Search across connected connectors.
**Examples:**
```sh
navig connector search
```

## `navig connector status`
**Status:** `stable` · **Since:** 
Show status and health of connected connectors.
**Examples:**
```sh
navig connector status
```

## `navig contacts add`
**Status:** `stable` · **Since:** 
Add a contact by hand.
**Examples:**
```sh
navig contacts add
```

## `navig contacts doctor`
**Status:** `stable` · **Since:** 
Check that the book says the same thing in both of its halves.
**Examples:**
```sh
navig contacts doctor
```

## `navig contacts export`
**Status:** `stable` · **Since:** 
Export contacts as CSV.
**Examples:**
```sh
navig contacts export
```

## `navig contacts flag`
**Status:** `stable` · **Since:** 
Record whether a handle still resolves, so exports can skip dead ones.
**Examples:**
```sh
navig contacts flag
```

## `navig contacts import`
**Status:** `stable` · **Since:** 
Import an address book, merging duplicates into one contact per person.
**Examples:**
```sh
navig contacts import
```

## `navig contacts list`
**Status:** `stable` · **Since:** 
List contacts.
**Examples:**
```sh
navig contacts list
```

## `navig contacts liveness`
**Status:** `stable` · **Since:** 
What the book knows about which routes still resolve.
**Examples:**
```sh
navig contacts liveness
```

## `navig contacts merge`
**Status:** `stable` · **Since:** 
Fold one contact into another, reversibly.
**Examples:**
```sh
navig contacts merge
```

## `navig contacts merges`
**Status:** `stable` · **Since:** 
Every merge and split applied, and whether it was undone.
**Examples:**
```sh
navig contacts merges
```

## `navig contacts migrate`
**Status:** `stable` · **Since:** 
Bring a pre-merge contact book onto the shared schema.
**Examples:**
```sh
navig contacts migrate
```

## `navig contacts remove`
**Status:** `stable` · **Since:** 
Remove a contact.
**Examples:**
```sh
navig contacts remove
```

## `navig contacts resolve`
**Status:** `stable` · **Since:** 
Ask Telegram which of these handles still reach a person.
**Examples:**
```sh
navig contacts resolve
```

## `navig contacts review`
**Status:** `stable` · **Since:** 
Record that a flagged merge was looked at and found correct.
**Examples:**
```sh
navig contacts review
```

## `navig contacts route`
**Status:** `stable` · **Since:** 
Add or remove a route for a contact.
**Examples:**
```sh
navig contacts route
```

## `navig contacts search`
**Status:** `stable` · **Since:** 
Find someone by anything you remember about them.
**Examples:**
```sh
navig contacts search
```

## `navig contacts show`
**Status:** `stable` · **Since:** 
Everything known about one contact, and where it came from.
**Examples:**
```sh
navig contacts show
```

## `navig contacts split`
**Status:** `stable` · **Since:** 
Move one identifier onto a contact of its own.
**Examples:**
```sh
navig contacts split
```

## `navig contacts stats`
**Status:** `stable` · **Since:** 
How many people, at which tiers, and how much evidence backs them.
**Examples:**
```sh
navig contacts stats
```

## `navig contacts undo`
**Status:** `stable` · **Since:** 
Reverse a merge, restoring the absorbed contact.
**Examples:**
```sh
navig contacts undo
```

## `navig content`
**Status:** `stable` · **Since:** 
Run navig content
**Examples:**
```sh
navig content
```

## `navig content captions`
**Status:** `stable` · **Since:** 
✍️  Transcribe a track to an .srt — with word-level timings.
**Examples:**
```sh
navig content captions
```

## `navig content cast`
**Status:** `stable` · **Since:** 
🎬 Capture a REAL command's output for the Reel Stage — once, replayable at any size.
**Examples:**
```sh
navig content cast
```

## `navig content clip`
**Status:** `stable` · **Since:** 
🎵 Build a vertical clip cut to a track you already have — no narration.
**Examples:**
```sh
navig content clip
```

## `navig content episode build`
**Status:** `stable` · **Since:** 
🎬 Build an episode end to end — and stop at the first stage that fails.
**Examples:**
```sh
navig content episode build
```

## `navig content episode draft`
**Status:** `stable` · **Since:** 
📝 Draft an episode from an article — hook, beats, narration, posting copy.
**Examples:**
```sh
navig content episode draft
```

## `navig content episode plan`
**Status:** `stable` · **Since:** 
🧭 Reconstruct a narration plan (rebuild.json) from a legacy episode's voice + cuts.
**Examples:**
```sh
navig content episode plan
```

## `navig content episode stats record`
**Status:** `stable` · **Since:** 
Store a snapshot of a posted episode's numbers (from TikTok's insights screen).
**Examples:**
```sh
navig content episode stats record
```

## `navig content episode stats show`
**Status:** `stable` · **Since:** 
Every episode side by side: what the cut is (hook, cuts, pace) and how it did.
**Examples:**
```sh
navig content episode stats show
```

## `navig content hooks`
**Status:** `stable` · **Since:** 
🎣 Rank the passages of a track worth cutting a short clip from.
**Examples:**
```sh
navig content hooks
```

## `navig content localize`
**Status:** `stable` · **Since:** 
🌍 The same picture in another language — captures untouched.
**Examples:**
```sh
navig content localize
```

## `navig content publish`
**Status:** `stable` · **Since:** 
📦 A render → a ready-to-post pack: spec check, cover, caption, hashtags.
**Examples:**
```sh
navig content publish
```

## `navig content qa`
**Status:** `stable` · **Since:** 
🔎 Catch a video's design bugs before (and after) rendering it.
**Examples:**
```sh
navig content qa
```

## `navig content recipe apply`
**Status:** `stable` · **Since:** 
Unpack a recipe into an episode folder — hash-checked, never half-applied.
**Examples:**
```sh
navig content recipe apply
```

## `navig content recipe export`
**Status:** `stable` · **Since:** 
Pack an episode's irreplaceable sources — scenes, shotlists, narration plan, casts.
**Examples:**
```sh
navig content recipe export
```

## `navig content reel`
**Status:** `stable` · **Since:** 
🎬 Build a vertical reel per language — capture, narrate, caption, score.
**Examples:**
```sh
navig content reel
```

## `navig content run`
**Status:** `stable` · **Since:** 
Run the assembly line from a topic (or a source clip) to a fanned-out post.
**Examples:**
```sh
navig content run
```

## `navig content schedule`
**Status:** `stable` · **Since:** 
Autopilot — run + publish the assembly line on a recurring cadence.
**Examples:**
```sh
navig content schedule
```

## `navig content stage`
**Status:** `stable` · **Since:** 
📱 Compile a scene into a self-contained, phone-sized page a reel or clip can film.
**Examples:**
```sh
navig content stage
```

## `navig content status`
**Status:** `stable` · **Since:** 
Show which assembly-line stages are wired (their plugin is installed).
**Examples:**
```sh
navig content status
```

## `navig content telegram-import`
**Status:** `stable` · **Since:** 
Import a Telegram channel's media into the local library, then optionally delete the
**Examples:**
```sh
navig content telegram-import
```

## `navig context`
**Status:** `stable` · **Since:** 
Context management - shows current context if no subcommand.
**Examples:**
```sh
navig context
```

## `navig context clear`
**Status:** `stable` · **Since:** 
Clear project-local context.
**Examples:**
```sh
navig context clear
```

## `navig context init`
**Status:** `stable` · **Since:** 
Initialize .navig directory in current project.
**Examples:**
```sh
navig context init
```

## `navig context set`
**Status:** `stable` · **Since:** 
Set project-local context in .navig/config.yaml.
**Examples:**
```sh
navig context set
```

## `navig context show`
**Status:** `stable` · **Since:** 
Show current context resolution.
**Examples:**
```sh
navig context show
```

## `navig continuation continue`
**Status:** `stable` · **Since:** 
Enable continuation policy for local runtime state.
**Examples:**
```sh
navig continuation continue
```

## `navig continuation pause`
**Status:** `stable` · **Since:** 
Pause continuation policy for local runtime state.
**Examples:**
```sh
navig continuation pause
```

## `navig continuation skip`
**Status:** `stable` · **Since:** 
Skip the next continuation trigger for local runtime state.
**Examples:**
```sh
navig continuation skip
```

## `navig continuation start`
**Status:** `stable` · **Since:** 
Enable continuation policy (alias for `continuation continue`).
**Examples:**
```sh
navig continuation start
```

## `navig continuation status`
**Status:** `stable` · **Since:** 
Show continuation policy for local runtime state.
**Examples:**
```sh
navig continuation status
```

## `navig contribute scan`
**Status:** `stable` · **Since:** 
Scan local NAVIG source, review findings, and (optionally) submit a PR.
**Examples:**
```sh
navig contribute scan
```

## `navig contribute status`
**Status:** `stable` · **Since:** 
Show Contribution Mode configuration and fork status.
**Examples:**
```sh
navig contribute status
```

## `navig copilot ask`
**Status:** `stable` · **Since:** 
Ask Copilot a question and get a response.
**Examples:**
```sh
navig copilot ask
```

## `navig copilot explain`
**Status:** `stable` · **Since:** 
Explain a log file, error, or code snippet using Copilot.
**Examples:**
```sh
navig copilot explain
```

## `navig copilot review`
**Status:** `stable` · **Since:** 
Code review a file using Copilot.
**Examples:**
```sh
navig copilot review
```

## `navig copilot sessions delete`
**Status:** `stable` · **Since:** 
Delete one or more chat sessions.
**Examples:**
```sh
navig copilot sessions delete
```

## `navig copilot sessions export`
**Status:** `stable` · **Since:** 
Export sessions to JSON, Markdown, or CSV.
**Examples:**
```sh
navig copilot sessions export
```

## `navig copilot sessions list`
**Status:** `stable` · **Since:** 
List all Copilot chat sessions.
**Examples:**
```sh
navig copilot sessions list
```

## `navig copilot sessions search`
**Status:** `stable` · **Since:** 
Search across all chat sessions for a query string.
**Examples:**
```sh
navig copilot sessions search
```

## `navig copilot sessions stats`
**Status:** `stable` · **Since:** 
Show storage statistics for all chat sessions.
**Examples:**
```sh
navig copilot sessions stats
```

## `navig copilot sessions view`
**Status:** `stable` · **Since:** 
View a chat session's content.
**Examples:**
```sh
navig copilot sessions view
```

## `navig copilot status`
**Status:** `stable` · **Since:** 
Check navig-bridge MCP connectivity and status.
**Examples:**
```sh
navig copilot status
```

## `navig copilot suggest`
**Status:** `stable` · **Since:** 
Get optimization suggestions from Copilot.
**Examples:**
```sh
navig copilot suggest
```

## `navig cortex run`
**Status:** `stable` · **Since:** 
Start the Cortex Hybrid Brain Loop on a specific goal.
**Examples:**
```sh
navig cortex run
```

## `navig cost`
**Status:** `stable` · **Since:** 
Show current session cost summary (default sub-command).
**Examples:**
```sh
navig cost
```

## `navig cost clear`
**Status:** `stable` · **Since:** 
Clear session cost history (irreversible).
**Examples:**
```sh
navig cost clear
```

## `navig cost history`
**Status:** `stable` · **Since:** 
Show recent session cost history.
**Examples:**
```sh
navig cost history
```

## `navig crash export`
**Status:** `stable` · **Since:** 
Export the latest crash report for GitHub issues.
**Examples:**
```sh
navig crash export
```

## `navig cron`
**Status:** `stable` · **Since:** 
Run navig cron
**Examples:**
```sh
navig cron
```

## `navig cron add`
**Status:** `stable` · **Since:** 
Add a new scheduled job.
**Examples:**
```sh
navig cron add
```

## `navig cron disable`
**Status:** `stable` · **Since:** 
Disable a job without removing it.
**Examples:**
```sh
navig cron disable
```

## `navig cron enable`
**Status:** `stable` · **Since:** 
Enable a disabled job.
**Examples:**
```sh
navig cron enable
```

## `navig cron list`
**Status:** `stable` · **Since:** 
List all scheduled jobs.
**Examples:**
```sh
navig cron list
```

## `navig cron remove`
**Status:** `stable` · **Since:** 
Remove a scheduled job.
**Examples:**
```sh
navig cron remove
```

## `navig cron run`
**Status:** `stable` · **Since:** 
Run a job immediately.
**Examples:**
```sh
navig cron run
```

## `navig cron status`
**Status:** `stable` · **Since:** 
Show cron service status.
**Examples:**
```sh
navig cron status
```

## `navig dashboard`
**Status:** `stable` · **Since:** 
Live operator console: identity, services, safety, hosts and activity.
**Examples:**
```sh
navig dashboard
```

## `navig db`
**Status:** `stable` · **Since:** 
Database management - run without subcommand for help.
**Examples:**
```sh
navig db
```

## `navig db dump`
**Status:** `stable` · **Since:** 
Dump/backup a database from remote server.
**Examples:**
```sh
navig db dump
```

## `navig db file`
**Status:** `stable` · **Since:** 
Execute SQL file through tunnel.
**Examples:**
```sh
navig db file
```

## `navig db list`
**Status:** `stable` · **Since:** 2.4.18
List remote databases
**Examples:**
```sh
navig db list
```
```sh
navig db list --plain
```

## `navig db local backup`
**Status:** `stable` · **Since:** 
Backup all managed SQLite databases to a destination directory.
**Examples:**
```sh
navig db local backup
```

## `navig db local cleanup`
**Status:** `stable` · **Since:** 
Remove deprecated .db.migrated files and empty legacy directories.
**Examples:**
```sh
navig db local cleanup
```

## `navig db local maintenance`
**Status:** `stable` · **Since:** 
Run maintenance on all managed stores (optimize, checkpoint, ANALYZE).
**Examples:**
```sh
navig db local maintenance
```

## `navig db local migrate`
**Status:** `stable` · **Since:** 
Run pending data migrations (legacy bot_data.db, daily_log.db → runtime.db).
**Examples:**
```sh
navig db local migrate
```

## `navig db local status`
**Status:** `stable` · **Since:** 
Show health status of all local SQLite stores.
**Examples:**
```sh
navig db local status
```

## `navig db optimize`
**Status:** `stable` · **Since:** 
Optimize database table.
**Examples:**
```sh
navig db optimize
```

## `navig db query`
**Status:** `stable` · **Since:** 2.4.18
Run an SQL query through NAVIG DB surface
**Examples:**
```sh
navig db query "SELECT 1"
```
```sh
navig db query "SHOW DATABASES" --json
```

## `navig db repair`
**Status:** `stable` · **Since:** 
Repair database table.
**Examples:**
```sh
navig db repair
```

## `navig db restore`
**Status:** `stable` · **Since:** 
Restore database from backup file.
**Examples:**
```sh
navig db restore
```

## `navig db run`
**Status:** `stable` · **Since:** 
Run SQL query/file or open shell (canonical command).
**Examples:**
```sh
navig db run
```

## `navig db show`
**Status:** `stable` · **Since:** 
Show database information (canonical command).
**Examples:**
```sh
navig db show
```

## `navig db tables`
**Status:** `stable` · **Since:** 
List tables in a database.
**Examples:**
```sh
navig db tables
```

## `navig debrief build`
**Status:** `stable` · **Since:** 
Build a visual debrief of a session (facts from the transcript, prose from story.json).
**Examples:**
```sh
navig debrief build
```

## `navig debrief list`
**Status:** `stable` · **Since:** 
List recent DSH sessions a debrief can be built from.
**Examples:**
```sh
navig debrief list
```

## `navig debrief open`
**Status:** `stable` · **Since:** 
Build (if needed) and open a session's debrief.
**Examples:**
```sh
navig debrief open
```

## `navig debrief shot`
**Status:** `stable` · **Since:** 
Capture PNGs for an existing (or freshly built) debrief.
**Examples:**
```sh
navig debrief shot
```

## `navig debug`
**Status:** `stable` · **Since:** 
Show debug log info.
**Examples:**
```sh
navig debug
```

## `navig debug clear`
**Status:** `stable` · **Since:** 
Clear the NAVIG debug log.
**Examples:**
```sh
navig debug clear
```

## `navig debug tail`
**Status:** `stable` · **Since:** 
Tail the NAVIG debug log.
**Examples:**
```sh
navig debug tail
```

## `navig decider`
**Status:** `stable` · **Since:** 
Show the decision model's status (default sub-command).
**Examples:**
```sh
navig decider
```

## `navig decider off`
**Status:** `stable` · **Since:** 
Switch the decision model off — everywhere, or for one use.
**Examples:**
```sh
navig decider off
```

## `navig decider on`
**Status:** `stable` · **Since:** 
Switch the decision model on — for everything, or for one use.
**Examples:**
```sh
navig decider on
```

## `navig decider review`
**Status:** `stable` · **Since:** 
The most recent shadow-mode DISAGREEMENTS — what to read before switching a use on.
**Examples:**
```sh
navig decider review
```

## `navig decider shadow`
**Status:** `stable` · **Since:** 
Shadow mode: Clef answers are compared with the old logic and recorded, not applied.
**Examples:**
```sh
navig decider shadow
```

## `navig decider status`
**Status:** `stable` · **Since:** 
Switches, credential, today's usage and the last call.
**Examples:**
```sh
navig decider status
```

## `navig decider test`
**Status:** `stable` · **Since:** 
One live call — works even while the decider is switched off.
**Examples:**
```sh
navig decider test
```

## `navig decider use`
**Status:** `stable` · **Since:** 
Pick the model.
**Examples:**
```sh
navig decider use
```

## `navig deck deploy`
**Status:** `stable` · **Since:** 
Deploy the Deck to YOUR own Cloudflare, pointed at your edge.
**Examples:**
```sh
navig deck deploy
```

## `navig deck dev`
**Status:** `stable` · **Since:** 
Run the deck dev server against an EXISTING daemon — never spawns a second daemon.
**Examples:**
```sh
navig deck dev
```

## `navig deck open`
**Status:** `stable` · **Since:** 
Open the control deck — the gateway already serves it (no Node, no second daemon).
**Examples:**
```sh
navig deck open
```

## `navig dedupe`
**Status:** `stable` · **Since:** 
Find, quarantine & restore duplicate photos, video, audio & files — non-destructive.
**Examples:**
```sh
navig dedupe
```

## `navig dedupe restore`
**Status:** `stable` · **Since:** 
Undo a quarantine — move files back to where they came from.
**Examples:**
```sh
navig dedupe restore
```

## `navig dedupe scan`
**Status:** `stable` · **Since:** 
Find near-duplicate photos/video/audio and exact-duplicate files.
**Examples:**
```sh
navig dedupe scan
```

## `navig deploy check`
**Status:** `stable` · **Since:** 
Validate .navig/deploy.yaml and test connectivity — no deploy executed.
**Examples:**
```sh
navig deploy check
```

## `navig deploy history`
**Status:** `stable` · **Since:** 
Show recent deploy history.
**Examples:**
```sh
navig deploy history
```

## `navig deploy init`
**Status:** `stable` · **Since:** 
Scaffold a .navig/deploy.yaml config interactively.
**Examples:**
```sh
navig deploy init
```

## `navig deploy rollback`
**Status:** `stable` · **Since:** 
Restore the previous deploy snapshot.
**Examples:**
```sh
navig deploy rollback
```

## `navig deploy run`
**Status:** `stable` · **Since:** 
Deploy the active (or specified) app to the target host.
**Examples:**
```sh
navig deploy run
```

## `navig deploy status`
**Status:** `stable` · **Since:** 
Show the status of the last deploy.
**Examples:**
```sh
navig deploy status
```

## `navig design check`
**Status:** `stable` · **Since:** 
Show whether AI design edits are ready (an AI provider is configured).
**Examples:**
```sh
navig design check
```

## `navig design edit`
**Status:** `stable` · **Since:** 
Restyle/rewrite one element per an instruction; print the revised HTML as JSON.
**Examples:**
```sh
navig design edit
```

## `navig design tokens save`
**Status:** `stable` · **Since:** 
Persist a token set as tokens.json + tokens.css (global, or a space's .navig/design).
**Examples:**
```sh
navig design tokens save
```

## `navig design tokens show`
**Status:** `stable` · **Since:** 
Show the stored design tokens (colors, type scale, spacing, radii).
**Examples:**
```sh
navig design tokens show
```

## `navig desktop ahk`
**Status:** `stable` · **Since:** 
Execute an AutoHotkey v2 script via AutoHotkey.exe.
**Examples:**
```sh
navig desktop ahk
```

## `navig desktop click`
**Status:** `stable` · **Since:** 
Click a UI element by its native window handle.
**Examples:**
```sh
navig desktop click
```

## `navig desktop crash-chrome`
**Status:** `stable` · **Since:** 
Crash all Chrome tabs + extensions to reclaim RAM — without closing the browser.
**Examples:**
```sh
navig desktop crash-chrome
```

## `navig desktop find`
**Status:** `stable` · **Since:** 
Search the UI element tree for matching elements.
**Examples:**
```sh
navig desktop find
```

## `navig desktop ping`
**Status:** `stable` · **Since:** 
Health-check the desktop agent.
**Examples:**
```sh
navig desktop ping
```

## `navig desktop set`
**Status:** `stable` · **Since:** 
Set the value of a UI element by its native window handle.
**Examples:**
```sh
navig desktop set
```

## `navig desktop tree`
**Status:** `stable` · **Since:** 
Dump the UI element tree to the specified depth.
**Examples:**
```sh
navig desktop tree
```

## `navig dev`
**Status:** `stable` · **Since:** 
Run navig dev
**Examples:**
```sh
navig dev
```

## `navig devhost add`
**Status:** `stable` · **Since:** 
Register a dev domain: hosts entry + (optional) trusted cert.
**Examples:**
```sh
navig devhost add
```

## `navig devhost doctor`
**Status:** `stable` · **Since:** 
Check prerequisites: mkcert, its CA, admin for hosts edits.
**Examples:**
```sh
navig devhost doctor
```

## `navig devhost list`
**Status:** `stable` · **Since:** 
List registered dev domains and their live status.
**Examples:**
```sh
navig devhost list
```

## `navig devhost remove`
**Status:** `stable` · **Since:** 
Remove a dev domain: hosts entry + cert + registry record.
**Examples:**
```sh
navig devhost remove
```

## `navig devhost status`
**Status:** `stable` · **Since:** 
One-line health per domain (hosts · cert · dev-up · serving).
**Examples:**
```sh
navig devhost status
```

## `navig devhost up`
**Status:** `stable` · **Since:** 
Run the HTTPS relay (foreground). Ctrl+C to stop.
**Examples:**
```sh
navig devhost up
```

## `navig devnet api`
**Status:** `stable` · **Since:** 
Discover and parse an OpenAPI/Swagger spec; list its endpoints.
**Examples:**
```sh
navig devnet api
```

## `navig devnet crawl`
**Status:** `stable` · **Since:** 
Map a site: a polite, same-origin, robots-respecting read-only crawl.
**Examples:**
```sh
navig devnet crawl
```

## `navig devnet diff`
**Status:** `stable` · **Since:** 
Compare two recorded responses (status, headers, body).
**Examples:**
```sh
navig devnet diff
```

## `navig devnet dns`
**Status:** `stable` · **Since:** 
Resolve DNS records (MX/TXT/NS/… with dnspython; A/AAAA always).
**Examples:**
```sh
navig devnet dns
```

## `navig devnet history`
**Status:** `stable` · **Since:** 
List recent repeater requests in this space.
**Examples:**
```sh
navig devnet history
```

## `navig devnet http`
**Status:** `stable` · **Since:** 
Send an HTTP request and read the full response (the repeater).
**Examples:**
```sh
navig devnet http
```

## `navig devnet ssl`
**Status:** `stable` · **Since:** 
Read a server's TLS certificate (issuer, validity, SANs, days left).
**Examples:**
```sh
navig devnet ssl
```

## `navig devnet whois`
**Status:** `stable` · **Since:** 
WHOIS registration record (bare-TCP; no system whois binary needed).
**Examples:**
```sh
navig devnet whois
```

## `navig discord bot invite`
**Status:** `stable` · **Since:** 
Open the link that adds the bot to a server you manage.
**Examples:**
```sh
navig discord bot invite
```

## `navig discord bot profile`
**Status:** `stable` · **Since:** 
Show the bot's profile, or change its name, avatar and About Me.
**Examples:**
```sh
navig discord bot profile
```

## `navig discord bot set`
**Status:** `stable` · **Since:** 
Store the bot token (Developer Portal → your app → Bot → Reset Token).
**Examples:**
```sh
navig discord bot set
```

## `navig discord channel create`
**Status:** `stable` · **Since:** 
Create a channel or category.
**Examples:**
```sh
navig discord channel create
```

## `navig discord channel delete`
**Status:** `stable` · **Since:** 
Delete a channel (its messages go with it — this cannot be undone).
**Examples:**
```sh
navig discord channel delete
```

## `navig discord channel edit`
**Status:** `stable` · **Since:** 
Rename, re-topic, move or slow down a channel.
**Examples:**
```sh
navig discord channel edit
```

## `navig discord channels`
**Status:** `stable` · **Since:** 
Channels of a server, grouped by category.
**Examples:**
```sh
navig discord channels
```

## `navig discord join`
**Status:** `stable` · **Since:** 
Preview an invite and open it in your browser to accept it (never joins by API).
**Examples:**
```sh
navig discord join
```

## `navig discord leave`
**Status:** `stable` · **Since:** 
Leave a server (as you, or remove the bot from it).
**Examples:**
```sh
navig discord leave
```

## `navig discord login`
**Status:** `stable` · **Since:** 
Opt in to account mode: store YOUR user token (list, read and leave your servers).
**Examples:**
```sh
navig discord login
```

## `navig discord logout`
**Status:** `stable` · **Since:** 
Forget your user token (the bot is untouched).
**Examples:**
```sh
navig discord logout
```

## `navig discord members`
**Status:** `stable` · **Since:** 
Members of a server the bot is in (bot only — needs the Server Members intent).
**Examples:**
```sh
navig discord members
```

## `navig discord mod ban`
**Status:** `stable` · **Since:** 
Ban a member (also works for users who already left).
**Examples:**
```sh
navig discord mod ban
```

## `navig discord mod bans`
**Status:** `stable` · **Since:** 
Everyone banned from a server, with the reason.
**Examples:**
```sh
navig discord mod bans
```

## `navig discord mod kick`
**Status:** `stable` · **Since:** 
Kick a member (they can rejoin with an invite).
**Examples:**
```sh
navig discord mod kick
```

## `navig discord mod log`
**Status:** `stable` · **Since:** 
NAVIG's own record of every moderation action it took (newest first).
**Examples:**
```sh
navig discord mod log
```

## `navig discord mod timeout`
**Status:** `stable` · **Since:** 
Time a member out: they can read but not talk, react or join voice.
**Examples:**
```sh
navig discord mod timeout
```

## `navig discord mod unban`
**Status:** `stable` · **Since:** 
Lift a ban.
**Examples:**
```sh
navig discord mod unban
```

## `navig discord mod untimeout`
**Status:** `stable` · **Since:** 
End a member's timeout early.
**Examples:**
```sh
navig discord mod untimeout
```

## `navig discord role add`
**Status:** `stable` · **Since:** 
Give a member a role.
**Examples:**
```sh
navig discord role add
```

## `navig discord role create`
**Status:** `stable` · **Since:** 
Create a role (never with permissions the bot itself lacks).
**Examples:**
```sh
navig discord role create
```

## `navig discord role delete`
**Status:** `stable` · **Since:** 
Delete a role (members lose it; this cannot be undone).
**Examples:**
```sh
navig discord role delete
```

## `navig discord role edit`
**Status:** `stable` · **Since:** 
Rename, recolour or change a role's permissions.
**Examples:**
```sh
navig discord role edit
```

## `navig discord role remove`
**Status:** `stable` · **Since:** 
Take a role from a member.
**Examples:**
```sh
navig discord role remove
```

## `navig discord roles`
**Status:** `stable` · **Since:** 
Roles of a server, highest first.
**Examples:**
```sh
navig discord roles
```

## `navig discord server`
**Status:** `stable` · **Since:** 
Details of one server.
**Examples:**
```sh
navig discord server
```

## `navig discord servers`
**Status:** `stable` · **Since:** 
Every server you are in — members, online, your role, and where the bot is.
**Examples:**
```sh
navig discord servers
```

## `navig discord settings`
**Status:** `stable` · **Since:** 
The bot's settings (discord.* in config) and what each one does.
**Examples:**
```sh
navig discord settings
```

## `navig discord setup`
**Status:** `stable` · **Since:** 
Guided setup of the NAVIG Discord bot — token, library, intents, owners, invite.
**Examples:**
```sh
navig discord setup
```

## `navig discord status`
**Status:** `stable` · **Since:** 
Which Discord identities are set up, and whether their tokens still work.
**Examples:**
```sh
navig discord status
```

## `navig dispatch send`
**Status:** `stable` · **Since:** 
Send a message through the unified messaging layer.
**Examples:**
```sh
navig dispatch send
```

## `navig dispatch status`
**Status:** `stable` · **Since:** 
Show recent delivery statuses.
**Examples:**
```sh
navig dispatch status
```

## `navig dispatch threads`
**Status:** `stable` · **Since:** 
List active conversation threads.
**Examples:**
```sh
navig dispatch threads
```

## `navig distribution adapters`
**Status:** `stable` · **Since:** 
Reviewed form maps on disk and whether each one is usable.
**Examples:**
```sh
navig distribution adapters
```

## `navig distribution analytics`
**Status:** `stable` · **Since:** 
Measured outcomes — and explicit gaps where nothing was measured.
**Examples:**
```sh
navig distribution analytics
```

## `navig distribution channel`
**Status:** `stable` · **Since:** 
Everything recorded about one channel, with its evidence.
**Examples:**
```sh
navig distribution channel
```

## `navig distribution channels`
**Status:** `stable` · **Since:** 
Search the channel database (project-scoped when --project is given).
**Examples:**
```sh
navig distribution channels
```

## `navig distribution content block`
**Status:** `stable` · **Since:** 
Replace exactly one block. Every save is a recoverable version.
**Examples:**
```sh
navig distribution content block
```

## `navig distribution content import`
**Status:** `stable` · **Since:** 
Import a written article package as editable drafts (idempotent per folder).
**Examples:**
```sh
navig distribution content import
```

## `navig distribution content new`
**Status:** `stable` · **Since:** 
Create a first draft scaffolded from the project's verified claims.
**Examples:**
```sh
navig distribution content new
```

## `navig distribution content regenerate`
**Status:** `stable` · **Since:** 
Rewrite one block with the model. The rest of the article is untouched.
**Examples:**
```sh
navig distribution content regenerate
```

## `navig distribution content review`
**Status:** `stable` · **Since:** 
Quality, canon and localisation review for one document.
**Examples:**
```sh
navig distribution content review
```

## `navig distribution content show`
**Status:** `stable` · **Since:** 
Print a document block by block, with ids so a single block can be edited.
**Examples:**
```sh
navig distribution content show
```

## `navig distribution maintain`
**Status:** `stable` · **Since:** 
Recompute eligibility, list stale verifications, and report the policy.
**Examples:**
```sh
navig distribution maintain
```

## `navig distribution plan`
**Status:** `stable` · **Since:** 
The best N places for this project, with the reasons and the mode.
**Examples:**
```sh
navig distribution plan
```

## `navig distribution projects`
**Status:** `stable` · **Since:** 
List the registered projects and what each still needs.
**Examples:**
```sh
navig distribution projects
```

## `navig distribution publish approve`
**Status:** `stable` · **Since:** 
Bind an approval to the exact content hash, identity and destination.
**Examples:**
```sh
navig distribution publish approve
```

## `navig distribution publish journal`
**Status:** `stable` · **Since:** 
The publication journal.
**Examples:**
```sh
navig distribution publish journal
```

## `navig distribution publish prepare`
**Status:** `stable` · **Since:** 
Run the gate and create the publication row (or return the existing one).
**Examples:**
```sh
navig distribution publish prepare
```

## `navig distribution publish record`
**Status:** `stable` · **Since:** 
Record the operator's own submission and verify the public URL.
**Examples:**
```sh
navig distribution publish record
```

## `navig distribution publish reject`
**Status:** `stable` · **Since:** 
Record a rejection with its reason.
**Examples:**
```sh
navig distribution publish reject
```

## `navig distribution publish run`
**Status:** `stable` · **Since:** 
Execute the publication: manual package, delegated Social post, or a refusal.
**Examples:**
```sh
navig distribution publish run
```

## `navig distribution publish verify`
**Status:** `stable` · **Since:** 
Re-check a published URL and update its status.
**Examples:**
```sh
navig distribution publish verify
```

## `navig distribution research import`
**Status:** `stable` · **Since:** 
Import researched channels; refuses unless every record is valid.
**Examples:**
```sh
navig distribution research import
```

## `navig distribution research stale`
**Status:** `stable` · **Since:** 
Channels whose verification is older than the window (or never happened).
**Examples:**
```sh
navig distribution research stale
```

## `navig distribution research validate`
**Status:** `stable` · **Since:** 
Check records against the research contract before anything is imported.
**Examples:**
```sh
navig distribution research validate
```

## `navig distribution schedule`
**Status:** `stable` · **Since:** 
Schedule re-verification and re-scoring through NAVIG's own scheduler.
**Examples:**
```sh
navig distribution schedule
```

## `navig distribution seed`
**Status:** `stable` · **Since:** 
Import the bundled channel research, campaigns and identities.
**Examples:**
```sh
navig distribution seed
```

## `navig distribution status`
**Status:** `stable` · **Since:** 
Cross-project health: channels, campaigns, publications and the policy.
**Examples:**
```sh
navig distribution status
```

## `navig do`
**Status:** `stable` · **Since:** 
Run a one-shot AI browser task on the active (or --profile) browser.
**Examples:**
```sh
navig do
```

## `navig dock status`
**Status:** `stable` · **Since:** 
Which extension is paired, and whether agents can use the browser now.
**Examples:**
```sh
navig dock status
```

## `navig dock unpair`
**Status:** `stable` · **Since:** 
Forget the paired extension. The Dock pairs again when browser control is turned on.
**Examples:**
```sh
navig dock unpair
```

## `navig docker`
**Status:** `stable` · **Since:** 
Docker management — run without subcommand for help.
**Examples:**
```sh
navig docker
```

## `navig docker compose`
**Status:** `stable` · **Since:** 
Run docker compose commands on remote host.
**Examples:**
```sh
navig docker compose
```

## `navig docker exec`
**Status:** `stable` · **Since:** 
Execute command in Docker container.
**Examples:**
```sh
navig docker exec
```

## `navig docker inspect`
**Status:** `stable` · **Since:** 
Inspect Docker container.
**Examples:**
```sh
navig docker inspect
```

## `navig docker logs`
**Status:** `stable` · **Since:** 
View Docker container logs.
**Examples:**
```sh
navig docker logs
```

## `navig docker ps`
**Status:** `stable` · **Since:** 
List Docker containers on remote host.
**Examples:**
```sh
navig docker ps
```

## `navig docker restart`
**Status:** `stable` · **Since:** 
Restart Docker container.
**Examples:**
```sh
navig docker restart
```

## `navig docker start`
**Status:** `stable` · **Since:** 
Start Docker container.
**Examples:**
```sh
navig docker start
```

## `navig docker stats`
**Status:** `stable` · **Since:** 
Show container resource usage statistics.
**Examples:**
```sh
navig docker stats
```

## `navig docker stop`
**Status:** `stable` · **Since:** 
Stop Docker container.
**Examples:**
```sh
navig docker stop
```

## `navig docs`
**Status:** `stable` · **Since:** 
Search NAVIG documentation for relevant information.
**Examples:**
```sh
navig docs
```

## `navig doctor`
**Status:** `stable` · **Since:** 
Run self-diagnostics on the NAVIG installation.
**Examples:**
```sh
navig doctor
```

## `navig doctor clean-path`
**Status:** `stable` · **Since:** 
Prune non-existent and duplicate directories from the persistent user PATH (Windows).
**Examples:**
```sh
navig doctor clean-path
```

## `navig doctor migrate-packs`
**Status:** `stable` · **Since:** 
Migrate legacy ~/.navig/packs content into the plugin format (idempotent).
**Examples:**
```sh
navig doctor migrate-packs
```

## `navig download analyse`
**Status:** `stable` · **Since:** 
AI markdown briefing of a post (description + best comments combined).
**Examples:**
```sh
navig download analyse
```

## `navig download batch`
**Status:** `stable` · **Since:** 
Download every URL in a links file (videos via yt-dlp, /photo/ posts via the browser).
**Examples:**
```sh
navig download batch
```

## `navig download comments`
**Status:** `stable` · **Since:** 
List the top comments, ranked by likes.
**Examples:**
```sh
navig download comments
```

## `navig download download`
**Status:** `stable` · **Since:** 
Download TikTok posts (organized <out>/<creator>/<id>) — concurrent, resumable.
**Examples:**
```sh
navig download download
```

## `navig download info`
**Status:** `stable` · **Since:** 
Show metadata: creator, description, stats (and image URLs for /photo/ posts).
**Examples:**
```sh
navig download info
```

## `navig download login`
**Status:** `stable` · **Since:** 
Log into TikTok once and vault the session; afterwards every command uses it automatically.
**Examples:**
```sh
navig download login
```

## `navig download logout`
**Status:** `stable` · **Since:** 
Forget the vaulted TikTok session (go back to anonymous browsing).
**Examples:**
```sh
navig download logout
```

## `navig download music-links`
**Status:** `stable` · **Since:** 
Resolve a music link to the same track on every other platform (via song.link).
**Examples:**
```sh
navig download music-links
```

## `navig download post`
**Status:** `stable` · **Since:** 
Read a whole post via the browser in ONE pass — description + stats + comments.
**Examples:**
```sh
navig download post
```

## `navig download profile`
**Status:** `stable` · **Since:** 
Download a creator's whole profile — videos, slideshows images, audio, or just metadata.
**Examples:**
```sh
navig download profile
```

## `navig ebay bulk`
**Status:** `stable` · **Since:** 
Draft (and optionally publish) every item YAML in a folder.
**Examples:**
```sh
navig ebay bulk
```

## `navig ebay doctor`
**Status:** `stable` · **Since:** 
Verify prerequisites before publishing (creds · token · location · policies).
**Examples:**
```sh
navig ebay doctor
```

## `navig ebay draft`
**Status:** `stable` · **Since:** 
Create/replace an inventory item and its (unpublished) offer from a YAML file.
**Examples:**
```sh
navig ebay draft
```

## `navig ebay edit`
**Status:** `stable` · **Since:** 
Quick-edit price and/or quantity of an existing SKU's offer.
**Examples:**
```sh
navig ebay edit
```

## `navig ebay end`
**Status:** `stable` · **Since:** 
End a live listing (withdraw the offer).
**Examples:**
```sh
navig ebay end
```

## `navig ebay env`
**Status:** `stable` · **Since:** 
Show or set the active eBay environment (sandbox is the safe default).
**Examples:**
```sh
navig ebay env
```

## `navig ebay list`
**Status:** `stable` · **Since:** 
List your inventory items.
**Examples:**
```sh
navig ebay list
```

## `navig ebay offers`
**Status:** `stable` · **Since:** 
Show listings eligible for a seller-initiated 'Offer to buyers'.
**Examples:**
```sh
navig ebay offers
```

## `navig ebay orders`
**Status:** `stable` · **Since:** 
List your recent orders.
**Examples:**
```sh
navig ebay orders
```

## `navig ebay price`
**Status:** `stable` · **Since:** 
Active-listing price proxy via the Browse API (asking prices, NOT sold).
**Examples:**
```sh
navig ebay price
```

## `navig ebay publish`
**Status:** `stable` · **Since:** 
Publish an offer → live listing.
**Examples:**
```sh
navig ebay publish
```

## `navig ebay relist`
**Status:** `stable` · **Since:** 
Relist an ended item by republishing its offer.
**Examples:**
```sh
navig ebay relist
```

## `navig ebay ship`
**Status:** `stable` · **Since:** 
Mark an order shipped with tracking (creates a shipping fulfillment).
**Examples:**
```sh
navig ebay ship
```

## `navig ebay show`
**Status:** `stable` · **Since:** 
Show an inventory item and its offers.
**Examples:**
```sh
navig ebay show
```

## `navig email`
**Status:** `stable` · **Since:** 
Gmail mailroom. `navig email --account other@gmail.com <command>` selects a linked account.
**Examples:**
```sh
navig email
```

## `navig email attachments save`
**Status:** `stable` · **Since:** 
Save the message's documents into <space>/inbox/ for paperwork to file (local only).
**Examples:**
```sh
navig email attachments save
```

## `navig email brief dismiss`
**Status:** `stable` · **Since:** 
Not interested: the item is not shown again.
**Examples:**
```sh
navig email brief dismiss
```

## `navig email brief extract`
**Status:** `stable` · **Since:** 
What the briefing extracts from one email (no model, nothing stored).
**Examples:**
```sh
navig email brief extract
```

## `navig email brief items`
**Status:** `stable` · **Since:** 
Items the briefing has extracted, newest first.
**Examples:**
```sh
navig email brief items
```

## `navig email brief restore`
**Status:** `stable` · **Since:** 
Bring a newsletter the briefing archived (or moved to Trash) back to the inbox.
**Examples:**
```sh
navig email brief restore
```

## `navig email brief run`
**Status:** `stable` · **Since:** 
Build the briefing and deliver it to Telegram (then archive the newsletters it used).
**Examples:**
```sh
navig email brief run
```

## `navig email brief save`
**Status:** `stable` · **Since:** 
Keep an item: its source email is never archived or trashed while it is saved.
**Examples:**
```sh
navig email brief save
```

## `navig email brief schedule`
**Status:** `stable` · **Since:** 
Move the weekly template digest to the briefing: one job that decides itself when it is due.
**Examples:**
```sh
navig email brief schedule
```

## `navig email connect`
**Status:** `stable` · **Since:** 
Link a Gmail account: OAuth inside navig, a Gmail app password on its own.
**Examples:**
```sh
navig email connect
```

## `navig email digest`
**Status:** `stable` · **Since:** 
Template digest for the period; `--llm` adds prose through the guarded model door.
**Examples:**
```sh
navig email digest
```

## `navig email edge deploy`
**Status:** `stable` · **Since:** 
Deploy the Worker from the plugin's edge/ folder (uses wrangler's own login).
**Examples:**
```sh
navig email edge deploy
```

## `navig email edge events`
**Status:** `stable` · **Since:** 
Recent messages the edge handled (metadata only — never bodies).
**Examples:**
```sh
navig email edge events
```

## `navig email edge secrets`
**Status:** `stable` · **Since:** 
Push the Worker's secrets from navig's own vault/config. Nothing is ever printed.
**Examples:**
```sh
navig email edge secrets
```

## `navig email edge send`
**Status:** `stable` · **Since:** 
Send AS support@<domain> (or another @domain address) through Cloudflare Email Sending.
**Examples:**
```sh
navig email edge send
```

## `navig email edge stats`
**Status:** `stable` · **Since:** 
Counts by alias / tag / day / sender domain for mail that entered at the edge.
**Examples:**
```sh
navig email edge stats
```

## `navig email edge status`
**Status:** `stable` · **Since:** 
Is the Worker up, and does it have Email Sending?
**Examples:**
```sh
navig email edge status
```

## `navig email followup`
**Status:** `stable` · **Since:** 
Draft follow-ups in the space's style for ledger threads (opt-in model; drafts only).
**Examples:**
```sh
navig email followup
```

## `navig email imap connect`
**Status:** `stable` · **Since:** 
Sign in to Gmail over IMAP with an app password, and keep it in the encrypted vault.
**Examples:**
```sh
navig email imap connect
```

## `navig email imap disconnect`
**Status:** `stable` · **Since:** 
Forget the stored app password.
**Examples:**
```sh
navig email imap disconnect
```

## `navig email imap status`
**Status:** `stable` · **Since:** 
Whether an app password is stored, and where it comes from (never shows it).
**Examples:**
```sh
navig email imap status
```

## `navig email labels ensure`
**Status:** `stable` · **Since:** 
Create a label (and its parents) if missing; print its id.
**Examples:**
```sh
navig email labels ensure
```

## `navig email labels list`
**Status:** `stable` · **Since:** 
Every label with its counts.
**Examples:**
```sh
navig email labels list
```

## `navig email list`
**Status:** `stable` · **Since:** 
List messages of a folder (newest first).
**Examples:**
```sh
navig email list
```

## `navig email organize apply`
**Status:** `stable` · **Since:** 
Add the new labels to the messages (old labels kept), create new filters. Snapshot written first.
**Examples:**
```sh
navig email organize apply
```

## `navig email organize audit`
**Status:** `stable` · **Since:** 
Labels with counts, equivalent and unused labels, conflicting / forwarding filters (read-only).
**Examples:**
```sh
navig email organize audit
```

## `navig email organize preview`
**Status:** `stable` · **Since:** 
The proposed taxonomy for THIS mailbox, with message counts. Changes nothing.
**Examples:**
```sh
navig email organize preview
```

## `navig email organize retire`
**Status:** `stable` · **Since:** 
After checking `apply`: take the OLD labels off migrated messages (labels and filters are kept).
**Examples:**
```sh
navig email organize retire
```

## `navig email organize rollback`
**Status:** `stable` · **Since:** 
Undo an apply: take off the labels it added, delete the filters it created.
**Examples:**
```sh
navig email organize rollback
```

## `navig email outbox approve`
**Status:** `stable` · **Since:** 
Approve the text you reviewed and schedule it (default: in 15 minutes — time to cancel).
**Examples:**
```sh
navig email outbox approve
```

## `navig email outbox cancel`
**Status:** `stable` · **Since:** 
Cancel a queued message. A cancelled job can never be sent.
**Examples:**
```sh
navig email outbox cancel
```

## `navig email outbox confirm-sent`
**Status:** `stable` · **Since:** 
You found a NEEDS_REVIEW message in Sent: record that it did go out.
**Examples:**
```sh
navig email outbox confirm-sent
```

## `navig email outbox delay`
**Status:** `stable` · **Since:** 
Move a scheduled send later (or to a given time).
**Examples:**
```sh
navig email outbox delay
```

## `navig email outbox dispatch`
**Status:** `stable` · **Since:** 
Run one dispatcher pass now (the daemon does this every ~45 s).
**Examples:**
```sh
navig email outbox dispatch
```

## `navig email outbox edit`
**Status:** `stable` · **Since:** 
Change the text. The approval is dropped — the new version must be approved again.
**Examples:**
```sh
navig email outbox edit
```

## `navig email outbox history`
**Status:** `stable` · **Since:** 
The mailroom audit trail: who did what, when (no message bodies).
**Examples:**
```sh
navig email outbox history
```

## `navig email outbox list`
**Status:** `stable` · **Since:** 
Queued, sent and stuck messages, newest first.
**Examples:**
```sh
navig email outbox list
```

## `navig email outbox pause`
**Status:** `stable` · **Since:** 
Emergency stop: hold automatic replies (--all: every send) until `resume`.
**Examples:**
```sh
navig email outbox pause
```

## `navig email outbox reopen`
**Status:** `stable` · **Since:** 
Put a failed or reviewed job back in front of you for approval (never straight to sending).
**Examples:**
```sh
navig email outbox reopen
```

## `navig email outbox resume`
**Status:** `stable` · **Since:** 
Lift the pause.
**Examples:**
```sh
navig email outbox resume
```

## `navig email outbox send-now`
**Status:** `stable` · **Since:** 
Send an approved, scheduled message right now (skips the rest of the wait).
**Examples:**
```sh
navig email outbox send-now
```

## `navig email outbox show`
**Status:** `stable` · **Since:** 
One job: the exact text, its approval, and its full history.
**Examples:**
```sh
navig email outbox show
```

## `navig email policy disable`
**Status:** `stable` · **Since:** 
Turn a policy off. Replies it already queued will not be sent.
**Examples:**
```sh
navig email policy disable
```

## `navig email policy enable`
**Status:** `stable` · **Since:** 
Turn a policy on for one account. Sending policies need --yes.
**Examples:**
```sh
navig email policy enable
```

## `navig email policy list`
**Status:** `stable` · **Since:** 
Every policy, its scope, and whether it is on.
**Examples:**
```sh
navig email policy list
```

## `navig email policy show`
**Status:** `stable` · **Since:** 
One policy in full.
**Examples:**
```sh
navig email policy show
```

## `navig email policy test`
**Status:** `stable` · **Since:** 
What the enabled policies would do with one real message — a dry run, no model call.
**Examples:**
```sh
navig email policy test
```

## `navig email read`
**Status:** `stable` · **Since:** 
Read one message (or its whole thread), body included.
**Examples:**
```sh
navig email read
```

## `navig email replied`
**Status:** `stable` · **Since:** 
Find every Sent reply to mail that sat in a folder (spam): ledger, corpus, label, stats.
**Examples:**
```sh
navig email replied
```

## `navig email reply`
**Status:** `stable` · **Since:** 
Draft a reply to one thread in a chosen style — to Drafts, or the outbox with --queue.
**Examples:**
```sh
navig email reply
```

## `navig email rules apply`
**Status:** `stable` · **Since:** 
Run the rules over existing mail (backfill labels). Notifications are NOT sent here.
**Examples:**
```sh
navig email rules apply
```

## `navig email rules check`
**Status:** `stable` · **Since:** 
Validate the rules file; with a message id, show which rules would fire.
**Examples:**
```sh
navig email rules check
```

## `navig email rules list`
**Status:** `stable` · **Since:** 
The rules the watch applies (space file + enabled daemon rules).
**Examples:**
```sh
navig email rules list
```

## `navig email search`
**Status:** `stable` · **Since:** 
Search with Gmail's own syntax (spam and trash included).
**Examples:**
```sh
navig email search
```

## `navig email send`
**Status:** `stable` · **Since:** 
Send (or draft) a message — through Gmail, or AS the domain via the Cloudflare edge.
**Examples:**
```sh
navig email send
```

## `navig email stats`
**Status:** `stable` · **Since:** 
Counts for the period (received/sent/spam/unread, per label & rule, top senders, per day).
**Examples:**
```sh
navig email stats
```

## `navig email status`
**Status:** `stable` · **Since:** 
Connected account, mailbox size, rules loaded, last watch.
**Examples:**
```sh
navig email status
```

## `navig email styles`
**Status:** `stable` · **Since:** 
The writing styles available to `reply` (the space's own shadow the built-in ones).
**Examples:**
```sh
navig email styles
```

## `navig email tag`
**Status:** `stable` · **Since:** 
Add/remove labels on one message or on a search result.
**Examples:**
```sh
navig email tag
```

## `navig email watch`
**Status:** `stable` · **Since:** 
One incremental pass over new mail: rules → labels / Telegram / run. For cron (every 5 min).
**Examples:**
```sh
navig email watch
```

## `navig eval run`
**Status:** `stable` · **Since:** 
Evaluate a Python expression with NAVIG context available.
**Examples:**
```sh
navig eval run
```

## `navig evolve fix`
**Status:** `stable` · **Since:** 
Attempt to fix or improve an existing file.
**Examples:**
```sh
navig evolve fix
```

## `navig evolve optimize`
**Status:** `stable` · **Since:** 
Analyze profile data and propose the next optimization target.
**Examples:**
```sh
navig evolve optimize
```

## `navig evolve pack`
**Status:** `stable` · **Since:** 
Generate a new Pack (collection of skills).
**Examples:**
```sh
navig evolve pack
```

## `navig evolve script`
**Status:** `stable` · **Since:** 
Generate a Python automation script.
**Examples:**
```sh
navig evolve script
```

## `navig evolve skill`
**Status:** `stable` · **Since:** 
Generate and refine a new skill definition (SKILL.md).
**Examples:**
```sh
navig evolve skill
```

## `navig evolve status`
**Status:** `stable` · **Since:** 
Show performance trends and regression alerts from the auto-profiler.
**Examples:**
```sh
navig evolve status
```

## `navig evolve sweep`
**Status:** `stable` · **Since:** 
Check a repo with ruff + pytest and fix what fails, using a local model.
**Examples:**
```sh
navig evolve sweep
```

## `navig evolve workflow`
**Status:** `stable` · **Since:** 
Generate and refine a new automation workflow (YAML).
**Examples:**
```sh
navig evolve workflow
```

## `navig explore`
**Status:** `stable` · **Since:** 
Explore & organize any folder in a local web UI.
**Examples:**
```sh
navig explore
```

## `navig explore audio-ingest`
**Status:** `stable` · **Since:** 
🗂️ Ingest clips into a sound library — classified, renamed, cleaned to mp3, reversible.
**Examples:**
```sh
navig explore audio-ingest
```

## `navig explore audio-ingest-undo`
**Status:** `stable` · **Since:** 
↩️  Put every ingested original back and remove the mp3s made from it.
**Examples:**
```sh
navig explore audio-ingest-undo
```

## `navig explore audio-sort`
**Status:** `stable` · **Since:** 
🎧 Sort audio by CONTENT — music / voice / sfx — from the waveform, not the filename.
**Examples:**
```sh
navig explore audio-sort
```

## `navig explore audio-undo`
**Status:** `stable` · **Since:** 
↩️  Put every file an audio-sort run moved back where it came from.
**Examples:**
```sh
navig explore audio-undo
```

## `navig explore collect`
**Status:** `stable` · **Since:** 
🎯 Pull a themed selection (e.g. --res 4K --year 2024) into an edit folder via hardlinks (no copy).
**Examples:**
```sh
navig explore collect
```

## `navig explore dedup`
**Status:** `stable` · **Since:** 
🎯 Find exact + identical-pixel (auto-trash) and near (flag-only) duplicates.
**Examples:**
```sh
navig explore dedup
```

## `navig explore gallery`
**Status:** `stable` · **Since:** 
🖼️  Build a shareable offline gallery.html — cached thumbnails, live filter, lightbox.
**Examples:**
```sh
navig explore gallery
```

## `navig explore merge-episode`
**Status:** `stable` · **Since:** 
🎬 Merge an episode's clips into one lossless RAW file (MPEG-TS concat; skips corrupt clips).
**Examples:**
```sh
navig explore merge-episode
```

## `navig explore open`
**Status:** `stable` · **Since:** 
🔎 Browse & organize a folder in the local web UI (the default for `navig explore <folder>`).
**Examples:**
```sh
navig explore open
```

## `navig explore photos arrange`
**Status:** `stable` · **Since:** 
🗂️  Folders per person, year, place or kind — as links, moving nothing.
**Examples:**
```sh
navig explore photos arrange
```

## `navig explore photos arrange-remove`
**Status:** `stable` · **Since:** 
↩️  Delete an arranged folder tree. Originals are never affected.
**Examples:**
```sh
navig explore photos arrange-remove
```

## `navig explore photos classes`
**Status:** `stable` · **Since:** 
🏷️  Re-decide what each file IS — file metadata first, guess second.
**Examples:**
```sh
navig explore photos classes
```

## `navig explore photos dates`
**Status:** `stable` · **Since:** 
📅  Recover capture dates EXIF no longer holds — and record HOW.
**Examples:**
```sh
navig explore photos dates
```

## `navig explore photos events`
**Status:** `stable` · **Since:** 
📅  Work out which occasion each photograph belongs to.
**Examples:**
```sh
navig explore photos events
```

## `navig explore photos explain`
**Status:** `stable` · **Since:** 
🔍  Show every fact the catalog holds about one photo, and where it came from.
**Examples:**
```sh
navig explore photos explain
```

## `navig explore photos faces`
**Status:** `stable` · **Since:** 
🙂  Find and embed faces — only in files that are actually photographs.
**Examples:**
```sh
navig explore photos faces
```

## `navig explore photos gallery`
**Status:** `stable` · **Since:** 
🖼️  A browsable page of the library, filtered by person, year, place and kind.
**Examples:**
```sh
navig explore photos gallery
```

## `navig explore photos index`
**Status:** `stable` · **Since:** 
🧠  Read every photo ONCE — hash, measure, embed and classify it.
**Examples:**
```sh
navig explore photos index
```

## `navig explore photos organize`
**Status:** `stable` · **Since:** 
🖼️  File photos into <date-root>/YYYY/YYYY-MM by EXIF, quarantine litter and salvage.
**Examples:**
```sh
navig explore photos organize
```

## `navig explore photos people cluster`
**Status:** `stable` · **Since:** 
Group the detected faces. Names you have already assigned are preserved.
**Examples:**
```sh
navig explore photos people cluster
```

## `navig explore photos people list`
**Status:** `stable` · **Since:** 
Show face groups, biggest first.
**Examples:**
```sh
navig explore photos people list
```

## `navig explore photos people merge`
**Status:** `stable` · **Since:** 
Fold groups together — clustering always over-splits the same person.
**Examples:**
```sh
navig explore photos people merge
```

## `navig explore photos people name`
**Status:** `stable` · **Since:** 
Name a group and lock it, so re-clustering never loses the name.
**Examples:**
```sh
navig explore photos people name
```

## `navig explore photos people sheet`
**Status:** `stable` · **Since:** 
🖼️  One row of faces per group, labelled with the id — so you can name them.
**Examples:**
```sh
navig explore photos people sheet
```

## `navig explore photos people split`
**Status:** `stable` · **Since:** 
Detach a group so the next cluster run re-derives it.
**Examples:**
```sh
navig explore photos people split
```

## `navig explore photos places`
**Status:** `stable` · **Since:** 
🌍  Work out where photos were taken — GPS, neighbours, then folder names.
**Examples:**
```sh
navig explore photos places
```

## `navig explore photos search`
**Status:** `stable` · **Since:** 
🔎  Find photos by what is IN them — plus person, place, year and kind.
**Examples:**
```sh
navig explore photos search
```

## `navig explore photos status`
**Status:** `stable` · **Since:** 
📊  What the vision catalog currently knows about this library.
**Examples:**
```sh
navig explore photos status
```

## `navig explore photos subjects`
**Status:** `stable` · **Since:** 
🏷️  Tag what photographs are OF — sunsets, food, the sea, cars, concerts.
**Examples:**
```sh
navig explore photos subjects
```

## `navig explore photos triage`
**Status:** `stable` · **Since:** 
🗂️  Plan the split: real photos vs webcam vs screenshots vs web litter.
**Examples:**
```sh
navig explore photos triage
```

## `navig explore photos triage-undo`
**Status:** `stable` · **Since:** 
↩️  Put every file a triage run moved back where it came from.
**Examples:**
```sh
navig explore photos triage-undo
```

## `navig explore photos undo`
**Status:** `stable` · **Since:** 
↩️  Put every file a photos-organize run moved back where it came from.
**Examples:**
```sh
navig explore photos undo
```

## `navig explore photos views`
**Status:** `stable` · **Since:** 
🗂️  Build the whole organised tree: year, event, person, place, screenshots.
**Examples:**
```sh
navig explore photos views
```

## `navig explore photos views-remove`
**Status:** `stable` · **Since:** 
↩️  Delete the organised tree. Your photos are never affected.
**Examples:**
```sh
navig explore photos views-remove
```

## `navig explore probe`
**Status:** `stable` · **Since:** 
📐 Extract width/height/codec/date/GPS/camera → .mediaexplorer/meta.jsonl (resumable).
**Examples:**
```sh
navig explore probe
```

## `navig explore rename apply`
**Status:** `stable` · **Since:** 
Move every file in the plan (MD5-checked), then rewrite references to them.
**Examples:**
```sh
navig explore rename apply
```

## `navig explore rename plan`
**Status:** `stable` · **Since:** 
Compute every new name and write the plan CSV. Moves NOTHING — read it first.
**Examples:**
```sh
navig explore rename plan
```

## `navig explore rename undo`
**Status:** `stable` · **Since:** 
Restore rewritten text files from their backups and move every file back.
**Examples:**
```sh
navig explore rename undo
```

## `navig explore rename verify`
**Status:** `stable` · **Since:** 
Every file where the manifest says, same MD5, nothing left behind, no old path named.
**Examples:**
```sh
navig explore rename verify
```

## `navig explore route`
**Status:** `stable` · **Since:** 
🚚 Route media to the master library. Default: PLAN (manifest only). --apply to move.
**Examples:**
```sh
navig explore route
```

## `navig explore undo`
**Status:** `stable` · **Since:** 
↩️  Put every file a quarantine / photos-organize run moved back where it was.
**Examples:**
```sh
navig explore undo
```

## `navig facebook backup`
**Status:** `stable` · **Since:** 
Download EVERY uploaded photo at full resolution + a manifest. Do this before deleting.
**Examples:**
```sh
navig facebook backup
```

## `navig facebook caption`
**Status:** `stable` · **Since:** 
Edit one photo's caption (best-effort; some photos reject edits).
**Examples:**
```sh
navig facebook caption
```

## `navig facebook check`
**Status:** `stable` · **Since:** 
Verify the token works and show which Page it controls.
**Examples:**
```sh
navig facebook check
```

## `navig facebook delete`
**Status:** `stable` · **Since:** 
Delete a single photo (permanent).
**Examples:**
```sh
navig facebook delete
```

## `navig facebook delete-all`
**Status:** `stable` · **Since:** 
Delete every uploaded photo that is already backed up (never un-backed ones).
**Examples:**
```sh
navig facebook delete-all
```

## `navig facebook info`
**Status:** `stable` · **Since:** 
Show the Page's current about / description / category / contact fields.
**Examples:**
```sh
navig facebook info
```

## `navig facebook photos`
**Status:** `stable` · **Since:** 
Count and preview the Page's uploaded photos.
**Examples:**
```sh
navig facebook photos
```

## `navig facebook set-about`
**Status:** `stable` · **Since:** 
Update the Page's about / description / category text.
**Examples:**
```sh
navig facebook set-about
```

## `navig fetch`
**Status:** `stable` · **Since:** 
Fetch and extract content from a URL.
**Examples:**
```sh
navig fetch
```

## `navig file`
**Status:** `stable` · **Since:** 
File operations - run without subcommand for help.
**Examples:**
```sh
navig file
```

## `navig file add`
**Status:** `stable` · **Since:** 
Add file/directory to remote (upload or mkdir).
**Examples:**
```sh
navig file add
```

## `navig file edit`
**Status:** `stable` · **Since:** 
Edit remote file (write content, change permissions/owner).
**Examples:**
```sh
navig file edit
```

## `navig file get`
**Status:** `stable` · **Since:** 
Download file from remote.
**Examples:**
```sh
navig file get
```

## `navig file list`
**Status:** `stable` · **Since:** 
List remote directory contents.
**Examples:**
```sh
navig file list
```

## `navig file remove`
**Status:** `stable` · **Since:** 
Remove remote file or directory.
**Examples:**
```sh
navig file remove
```

## `navig file show`
**Status:** `stable` · **Since:** 
Show remote file contents or download.
**Examples:**
```sh
navig file show
```

## `navig flow`
**Status:** `stable` · **Since:** 
Flow management - run without subcommand for help.
**Examples:**
```sh
navig flow
```

## `navig flow add`
**Status:** `stable` · **Since:** 
Create a new flow.
**Examples:**
```sh
navig flow add
```

## `navig flow edit`
**Status:** `stable` · **Since:** 
Open flow in default editor.
**Examples:**
```sh
navig flow edit
```

## `navig flow list`
**Status:** `stable` · **Since:** 
List all available flows.
**Examples:**
```sh
navig flow list
```

## `navig flow remove`
**Status:** `stable` · **Since:** 
Remove a flow.
**Examples:**
```sh
navig flow remove
```

## `navig flow run`
**Status:** `stable` · **Since:** 
Execute a flow.
**Examples:**
```sh
navig flow run
```

## `navig flow show`
**Status:** `stable` · **Since:** 
Display flow definition and steps.
**Examples:**
```sh
navig flow show
```

## `navig flow template add`
**Status:** `stable` · **Since:** 
Enable/add a template.
**Examples:**
```sh
navig flow template add
```

## `navig flow template list`
**Status:** `stable` · **Since:** 
List all available templates.
**Examples:**
```sh
navig flow template list
```

## `navig flow template remove`
**Status:** `stable` · **Since:** 
Disable/remove a template.
**Examples:**
```sh
navig flow template remove
```

## `navig flow template run`
**Status:** `stable` · **Since:** 
Deploy/run a template.
**Examples:**
```sh
navig flow template run
```

## `navig flow template show`
**Status:** `stable` · **Since:** 
Show template details.
**Examples:**
```sh
navig flow template show
```

## `navig flow test`
**Status:** `stable` · **Since:** 
Test/validate flow syntax and structure.
**Examples:**
```sh
navig flow test
```

## `navig flux`
**Status:** `stable` · **Since:** 
Run navig flux
**Examples:**
```sh
navig flux
```

## `navig flux add`
**Status:** `stable` · **Since:** 
Manually add a peer by its gateway URL.
**Examples:**
```sh
navig flux add
```

## `navig flux clear`
**Status:** `stable` · **Since:** 
Clear the routing target — commands run locally.
**Examples:**
```sh
navig flux clear
```

## `navig flux install`
**Status:** `stable` · **Since:** 
Show one-liner install commands, or push the install to a peer (--push).
**Examples:**
```sh
navig flux install
```

## `navig flux peers`
**Status:** `stable` · **Since:** 
List all known mesh peers.
**Examples:**
```sh
navig flux peers
```

## `navig flux scan`
**Status:** `stable` · **Since:** 
Trigger LAN multicast discovery and wait for responses.
**Examples:**
```sh
navig flux scan
```

## `navig flux status`
**Status:** `stable` · **Since:** 
Overall mesh health summary.
**Examples:**
```sh
navig flux status
```

## `navig flux target`
**Status:** `stable` · **Since:** 
Set the active routing target. Interactive picker if no arg given.
**Examples:**
```sh
navig flux target
```

## `navig flux token`
**Status:** `stable` · **Since:** 
Show (and optionally copy) the mesh_token.
**Examples:**
```sh
navig flux token
```

## `navig formation`
**Status:** `stable` · **Since:** 
Formation commands - run without subcommand for help.
**Examples:**
```sh
navig formation
```

## `navig formation agents`
**Status:** `stable` · **Since:** 
List agents in the active formation (from .navig/profile.json).
**Examples:**
```sh
navig formation agents
```

## `navig formation council`
**Status:** `stable` · **Since:** 
Run a council deliberation across all agents in the active formation.
**Examples:**
```sh
navig formation council
```

## `navig formation init`
**Status:** `stable` · **Since:** 
Initialize a profile for this workspace.
**Examples:**
```sh
navig formation init
```

## `navig formation list`
**Status:** `stable` · **Since:** 
List all available formations.
**Examples:**
```sh
navig formation list
```

## `navig formation run`
**Status:** `stable` · **Since:** 
Run a request across the active formation's specialists (real multi-agent).
**Examples:**
```sh
navig formation run
```

## `navig formation show`
**Status:** `stable` · **Since:** 
Show detailed information about a formation.
**Examples:**
```sh
navig formation show
```

## `navig formation sync`
**Status:** `stable` · **Since:** 
Render the active formation's agents into .navig/agents/<id>.md (Claude Code).
**Examples:**
```sh
navig formation sync
```

## `navig games art`
**Status:** `stable` · **Since:** 
🖼  Fetch/refresh Steam cover art for your unified (non-Steam) games.
**Examples:**
```sh
navig games art
```

## `navig games check`
**Status:** `stable` · **Since:** 
🔎 Show what's free right now (and, with --upcoming, what's next).
**Examples:**
```sh
navig games check
```

## `navig games claim`
**Status:** `stable` · **Since:** 
🕹  Claim the currently-free games into your account.
**Examples:**
```sh
navig games claim
```

## `navig games deals list`
**Status:** `stable` · **Since:** 
Show your manual watchlist (your wishlist is watched automatically).
**Examples:**
```sh
navig games deals list
```

## `navig games deals notify`
**Status:** `stable` · **Since:** 
🔔 Check deals and fire notifications for anything new (the scheduler target).
**Examples:**
```sh
navig games deals notify
```

## `navig games deals unwatch`
**Status:** `stable` · **Since:** 
Remove an appid from the manual watchlist.
**Examples:**
```sh
navig games deals unwatch
```

## `navig games deals watch`
**Status:** `stable` · **Since:** 
Add an appid to the manual watchlist (checked alongside your wishlist).
**Examples:**
```sh
navig games deals watch
```

## `navig games doctor`
**Status:** `stable` · **Since:** 
🩺 Full self-check across every subsystem (sourcing, browser, vault, Steam, library, art, deals).
**Examples:**
```sh
navig games doctor
```

## `navig games grab`
**Status:** `stable` · **Since:** 
Mark a free game as grabbed — it settles, so it stops nagging you.
**Examples:**
```sh
navig games grab
```

## `navig games history`
**Status:** `stable` · **Since:** 
📜 What has been claimed / attempted.
**Examples:**
```sh
navig games history
```

## `navig games library`
**Status:** `stable` · **Since:** 
🗂  List installed games across Steam, Epic, GOG and Amazon (local scan).
**Examples:**
```sh
navig games library
```

## `navig games login epic`
**Status:** `stable` · **Since:** 
🔐 Open Epic in a persistent browser, sign in once, and save the session.
**Examples:**
```sh
navig games login epic
```

## `navig games login list`
**Status:** `stable` · **Since:** 
List stored store sessions/logins.
**Examples:**
```sh
navig games login list
```

## `navig games login remove`
**Status:** `stable` · **Since:** 
Remove the saved Epic session.
**Examples:**
```sh
navig games login remove
```

## `navig games schedule disable`
**Status:** `stable` · **Since:** 
⏹ Remove a recurring job.
**Examples:**
```sh
navig games schedule disable
```

## `navig games schedule enable`
**Status:** `stable` · **Since:** 
▶ Register a recurring job (auto-claim · deal alerts · unify to Steam).
**Examples:**
```sh
navig games schedule enable
```

## `navig games schedule status`
**Status:** `stable` · **Since:** 
Show all NAVIG-games scheduled jobs.
**Examples:**
```sh
navig games schedule status
```

## `navig games status`
**Status:** `stable` · **Since:** 
ℹ️  Plugin, login, library, Steam & schedule status.
**Examples:**
```sh
navig games status
```

## `navig games steam accounts`
**Status:** `stable` · **Since:** 
List stored Steam Guard accounts (+ live codes).
**Examples:**
```sh
navig games steam accounts
```

## `navig games steam auth`
**Status:** `stable` · **Since:** 
🔐 Store a Steam Guard secret (from --shared-secret or a --mafile) in the vault.
**Examples:**
```sh
navig games steam auth
```

## `navig games steam code`
**Status:** `stable` · **Since:** 
🔑 Print the current Steam Guard code.
**Examples:**
```sh
navig games steam code
```

## `navig games steam shortcuts`
**Status:** `stable` · **Since:** 
List the non-Steam shortcuts NAVIG (or you) added to Steam.
**Examples:**
```sh
navig games steam shortcuts
```

## `navig games unify`
**Status:** `stable` · **Since:** 
➕ Add your Epic/GOG/Amazon games to the Steam library (one library for all).
**Examples:**
```sh
navig games unify
```

## `navig gateway restart`
**Status:** `stable` · **Since:** 
Restart the NAVIG daemon/gateway (alias for `navig service restart`).
**Examples:**
```sh
navig gateway restart
```

## `navig gateway session`
**Status:** `stable` · **Since:** 
Manage gateway sessions.
**Examples:**
```sh
navig gateway session
```

## `navig gateway start`
**Status:** `stable` · **Since:** 
Start the autonomous agent gateway server.
**Examples:**
```sh
navig gateway start
```

## `navig gateway status`
**Status:** `stable` · **Since:** 
Show config state and liveness for every gateway channel.
**Examples:**
```sh
navig gateway status
```

## `navig gateway stop`
**Status:** `stable` · **Since:** 
Stop the running gateway server.
**Examples:**
```sh
navig gateway stop
```

## `navig gateway test`
**Status:** `stable` · **Since:** 
Send a smoke-test message through one or all configured channels.
**Examples:**
```sh
navig gateway test
```

## `navig generate`
**Status:** `stable` · **Since:** 
Run navig generate
**Examples:**
```sh
navig generate
```

## `navig generate analyse`
**Status:** `stable` · **Since:** 
Extract a **briefing.md** from a video/audio/doc: frames + transcript + links + AI summary.
**Examples:**
```sh
navig generate analyse
```

## `navig generate analyze`
**Status:** `stable` · **Since:** 
Extract a **briefing.md** from a video/audio/doc: frames + transcript + links + AI summary.
**Examples:**
```sh
navig generate analyze
```

## `navig generate browse`
**Status:** `stable` · **Since:** 
Launch a local web gallery for any media folder (grid + preview, streams video).
**Examples:**
```sh
navig generate browse
```

## `navig generate contact-sheet`
**Status:** `stable` · **Since:** 
Montage variants into a review contact sheet (Pillow).
**Examples:**
```sh
navig generate contact-sheet
```

## `navig generate dedupe-audio`
**Status:** `stable` · **Since:** 
Find & collapse re-shared/re-encoded duplicate tracks via acoustic fingerprints.
**Examples:**
```sh
navig generate dedupe-audio
```

## `navig generate dedupe-files`
**Status:** `stable` · **Since:** 
Exact byte-identical duplicates (content SHA-256). Keeps one, quarantines the rest.
**Examples:**
```sh
navig generate dedupe-files
```

## `navig generate dedupe-images`
**Status:** `stable` · **Since:** 
Perceptual image near-dups (256-bit dHash) + redundant thumbnails. Keeps highest-res.
**Examples:**
```sh
navig generate dedupe-images
```

## `navig generate dedupe-video`
**Status:** `stable` · **Since:** 
Perceptual video near-dups via keyframe signatures (catches re-encodes/re-uploads).
**Examples:**
```sh
navig generate dedupe-video
```

## `navig generate download`
**Status:** `stable` · **Since:** 
Download a video (TikTok & friends) — same engine as `navig download`.
**Examples:**
```sh
navig generate download
```

## `navig generate edit`
**Status:** `stable` · **Since:** 
Instruction-edit an image variant (OpenAI gpt-image).
**Examples:**
```sh
navig generate edit
```

## `navig generate eleven change-voice`
**Status:** `stable` · **Since:** 
Re-speak a recording in another voice, keeping timing and delivery (costs credits).
**Examples:**
```sh
navig generate eleven change-voice
```

## `navig generate eleven fetch`
**Status:** `stable` · **Since:** 
Download one history item's audio (free — the bytes are already stored).
**Examples:**
```sh
navig generate eleven fetch
```

## `navig generate eleven history`
**Status:** `stable` · **Since:** 
List what this key has already generated (free — GET only, costs no credits).
**Examples:**
```sh
navig generate eleven history
```

## `navig generate eleven isolate`
**Status:** `stable` · **Since:** 
Remove background noise, keep the voice (costs credits).
**Examples:**
```sh
navig generate eleven isolate
```

## `navig generate eleven stems`
**Status:** `stable` · **Since:** 
Split a track into stems — returns a zip (costs credits; slow on long files).
**Examples:**
```sh
navig generate eleven stems
```

## `navig generate eleven subscription`
**Status:** `stable` · **Since:** 
Plan, credits and cloning entitlements behind the configured key (free GET).
**Examples:**
```sh
navig generate eleven subscription
```

## `navig generate eleven transcribe`
**Status:** `stable` · **Since:** 
Transcribe with ElevenLabs Scribe (costs credits; word timings included).
**Examples:**
```sh
navig generate eleven transcribe
```

## `navig generate eleven voices`
**Status:** `stable` · **Since:** 
Search the voice library — or list your own voices with `--mine` (free GETs).
**Examples:**
```sh
navig generate eleven voices
```

## `navig generate frames`
**Status:** `stable` · **Since:** 
Extract keyframes from a video (ffmpeg scene-change or interval sampling).
**Examples:**
```sh
navig generate frames
```

## `navig generate gen`
**Status:** `stable` · **Since:** 
Generate variant(s) into the active space's refs library.
**Examples:**
```sh
navig generate gen
```

## `navig generate ingest`
**Status:** `stable` · **Since:** 
Pull externally-generated files into the refs library for review (Midjourney, etc.).
**Examples:**
```sh
navig generate ingest
```

## `navig generate keep`
**Status:** `stable` · **Since:** 
Promote a staged variant to the kept library.
**Examples:**
```sh
navig generate keep
```

## `navig generate license`
**Status:** `stable` · **Since:** 
Record the license for a variant (provenance).
**Examples:**
```sh
navig generate license
```

## `navig generate list`
**Status:** `stable` · **Since:** 
List variants in THIS space, newest first (clickable). --all spans every space.
**Examples:**
```sh
navig generate list
```

## `navig generate palette`
**Status:** `stable` · **Since:** 
Extract a hex palette from reference frames.
**Examples:**
```sh
navig generate palette
```

## `navig generate probe`
**Status:** `stable` · **Since:** 
Print video metadata (duration / resolution / fps) via ffprobe.
**Examples:**
```sh
navig generate probe
```

## `navig generate process`
**Status:** `stable` · **Since:** 
LOCAL pixel pipeline → a game-ready sprite variant (Pillow).
**Examples:**
```sh
navig generate process
```

## `navig generate redesign`
**Status:** `stable` · **Since:** 
Reference-based img2img redesign (Recraft).
**Examples:**
```sh
navig generate redesign
```

## `navig generate reject`
**Status:** `stable` · **Since:** 
Reject a staged variant (retained in .rejected, never deleted).
**Examples:**
```sh
navig generate reject
```

## `navig generate rembg`
**Status:** `stable` · **Since:** 
Remove the background → transparent PNG (Recraft).
**Examples:**
```sh
navig generate rembg
```

## `navig generate statement`
**Status:** `stable` · **Since:** 
⬛ Render a STATEMENT overlay — one line, in Schema's own interface language.
**Examples:**
```sh
navig generate statement
```

## `navig generate style list`
**Status:** `stable` · **Since:** 
List every template id with its kind and title.
**Examples:**
```sh
navig generate style list
```

## `navig generate style show`
**Status:** `stable` · **Since:** 
Print one template: source, prompt, motion (side effects), layout.
**Examples:**
```sh
navig generate style show
```

## `navig generate tiktok analyse`
**Status:** `stable` · **Since:** 
AI markdown briefing of a post (description + best comments combined).
**Examples:**
```sh
navig generate tiktok analyse
```

## `navig generate tiktok batch`
**Status:** `stable` · **Since:** 
Download every URL in a links file (videos via yt-dlp, /photo/ posts via the browser).
**Examples:**
```sh
navig generate tiktok batch
```

## `navig generate tiktok comments`
**Status:** `stable` · **Since:** 
List the top comments, ranked by likes.
**Examples:**
```sh
navig generate tiktok comments
```

## `navig generate tiktok download`
**Status:** `stable` · **Since:** 
Download TikTok posts (organized <out>/<creator>/<id>) — concurrent, resumable.
**Examples:**
```sh
navig generate tiktok download
```

## `navig generate tiktok info`
**Status:** `stable` · **Since:** 
Show metadata: creator, description, stats (and image URLs for /photo/ posts).
**Examples:**
```sh
navig generate tiktok info
```

## `navig generate tiktok login`
**Status:** `stable` · **Since:** 
Log into TikTok once and vault the session; afterwards every command uses it automatically.
**Examples:**
```sh
navig generate tiktok login
```

## `navig generate tiktok logout`
**Status:** `stable` · **Since:** 
Forget the vaulted TikTok session (go back to anonymous browsing).
**Examples:**
```sh
navig generate tiktok logout
```

## `navig generate tiktok music-links`
**Status:** `stable` · **Since:** 
Resolve a music link to the same track on every other platform (via song.link).
**Examples:**
```sh
navig generate tiktok music-links
```

## `navig generate tiktok post`
**Status:** `stable` · **Since:** 
Read a whole post via the browser in ONE pass — description + stats + comments.
**Examples:**
```sh
navig generate tiktok post
```

## `navig generate tiktok profile`
**Status:** `stable` · **Since:** 
Download a creator's whole profile — videos, slideshows images, audio, or just metadata.
**Examples:**
```sh
navig generate tiktok profile
```

## `navig generate wallpaper generate`
**Status:** `stable` · **Since:** 
Generate an image, compose it for the display, and set it as the wallpaper.
**Examples:**
```sh
navig generate wallpaper generate
```

## `navig generate wallpaper list`
**Status:** `stable` · **Since:** 
What this verb has produced, newest first.
**Examples:**
```sh
navig generate wallpaper list
```

## `navig generate wallpaper make`
**Status:** `stable` · **Since:** 
Compose a wallpaper file without touching the desktop (works on any OS).
**Examples:**
```sh
navig generate wallpaper make
```

## `navig generate wallpaper restore`
**Status:** `stable` · **Since:** 
Put the wallpaper that was active before the last `set`/`generate` back.
**Examples:**
```sh
navig generate wallpaper restore
```

## `navig generate wallpaper schedule`
**Status:** `stable` · **Since:** 
Rotate the wallpaper on a cadence: set the prompt, the day and the clock.
**Examples:**
```sh
navig generate wallpaper schedule
```

## `navig generate wallpaper screens`
**Status:** `stable` · **Since:** 
List displays with the exact pixel size each wallpaper must be composed for.
**Examples:**
```sh
navig generate wallpaper screens
```

## `navig generate wallpaper set`
**Status:** `stable` · **Since:** 
Compose an existing image for the display and apply it.
**Examples:**
```sh
navig generate wallpaper set
```

## `navig generate wallpaper status`
**Status:** `stable` · **Since:** 
What is on the desktop now, and what this machine supports.
**Examples:**
```sh
navig generate wallpaper status
```

## `navig generate wallpaper style`
**Status:** `stable` · **Since:** 
Change how Windows fits the current image to the screen.
**Examples:**
```sh
navig generate wallpaper style
```

## `navig github`
**Status:** `stable` · **Since:** 
Run navig github
**Examples:**
```sh
navig github
```

## `navig github analytics`
**Status:** `stable` · **Since:** 
Analyze backup directory and generate statistics report.
**Examples:**
```sh
navig github analytics
```

## `navig github analytics-history`
**Status:** `stable` · **Since:** 
Show backup history and statistics.
**Examples:**
```sh
navig github analytics-history
```

## `navig github attachments`
**Status:** `stable` · **Since:** 
Download attachments (images, files) from issues and pull requests.
**Examples:**
```sh
navig github attachments
```

## `navig github backup`
**Status:** `stable` · **Since:** 
📦 Clone / mirror every repo for a user (or --org organisation).
**Examples:**
```sh
navig github backup
```

## `navig github clone`
**Status:** `stable` · **Since:** 
⬇️  Clone a single repository (with the full toolbox if available, else plain git).
**Examples:**
```sh
navig github clone
```

## `navig github config-delete`
**Status:** `stable` · **Since:** 
Delete a saved backup profile.
**Examples:**
```sh
navig github config-delete
```

## `navig github config-export`
**Status:** `stable` · **Since:** 
Export a profile to a file for sharing.
**Examples:**
```sh
navig github config-export
```

## `navig github config-import`
**Status:** `stable` · **Since:** 
Import a profile from a file.
**Examples:**
```sh
navig github config-import
```

## `navig github config-list`
**Status:** `stable` · **Since:** 
List all saved backup profiles.
**Examples:**
```sh
navig github config-list
```

## `navig github config-load`
**Status:** `stable` · **Since:** 
Load and display a saved backup profile.
**Examples:**
```sh
navig github config-load
```

## `navig github config-save`
**Status:** `stable` · **Since:** 
Save a backup configuration profile.
**Examples:**
```sh
navig github config-save
```

## `navig github delete`
**Status:** `stable` · **Since:** 
Delete a GitHub repository.
**Examples:**
```sh
navig github delete
```

## `navig github diff`
**Status:** `stable` · **Since:** 
Compare two backup directories and show differences.
**Examples:**
```sh
navig github diff
```

## `navig github discussions`
**Status:** `stable` · **Since:** 
Export all discussions from a GitHub repository.
**Examples:**
```sh
navig github discussions
```

## `navig github followers`
**Status:** `stable` · **Since:** 
Export followers (and optionally following) for a GitHub user.
**Examples:**
```sh
navig github followers
```

## `navig github gists`
**Status:** `stable` · **Since:** 
Backup all gists for a user.
**Examples:**
```sh
navig github gists
```

## `navig github issues`
**Status:** `stable` · **Since:** 
Export all issues from a GitHub repository.
**Examples:**
```sh
navig github issues
```

## `navig github labels`
**Status:** `stable` · **Since:** 
Export all labels from a GitHub repository.
**Examples:**
```sh
navig github labels
```

## `navig github milestones`
**Status:** `stable` · **Since:** 
Export all milestones from a GitHub repository.
**Examples:**
```sh
navig github milestones
```

## `navig github notify-status`
**Status:** `stable` · **Since:** 
Show notification configuration status.
**Examples:**
```sh
navig github notify-status
```

## `navig github notify-test`
**Status:** `stable` · **Since:** 
Test all configured notification providers.
**Examples:**
```sh
navig github notify-test
```

## `navig github org`
**Status:** `stable` · **Since:** 
Backup all repositories for a GitHub organization.
**Examples:**
```sh
navig github org
```

## `navig github profile`
**Status:** `stable` · **Since:** 
Export GitHub user profile information.
**Examples:**
```sh
navig github profile
```

## `navig github projects`
**Status:** `stable` · **Since:** 
Export GitHub Projects (v2) for a user/org or repository.
**Examples:**
```sh
navig github projects
```

## `navig github pulls`
**Status:** `stable` · **Since:** 
Export all pull requests from a GitHub repository.
**Examples:**
```sh
navig github pulls
```

## `navig github releases`
**Status:** `stable` · **Since:** 
Download all releases and assets from a GitHub repository.
**Examples:**
```sh
navig github releases
```

## `navig github repo`
**Status:** `stable` · **Since:** 
Backup a single GitHub repository with optional data exports.
**Examples:**
```sh
navig github repo
```

## `navig github restore-issues`
**Status:** `stable` · **Since:** 
Restore issues from a backup to a GitHub repository.
**Examples:**
```sh
navig github restore-issues
```

## `navig github restore-labels`
**Status:** `stable` · **Since:** 
Restore labels from a backup to a GitHub repository.
**Examples:**
```sh
navig github restore-labels
```

## `navig github restore-milestones`
**Status:** `stable` · **Since:** 
Restore milestones from a backup to a GitHub repository.
**Examples:**
```sh
navig github restore-milestones
```

## `navig github restore-releases`
**Status:** `stable` · **Since:** 
Restore releases from a backup to a GitHub repository.
**Examples:**
```sh
navig github restore-releases
```

## `navig github schedule-add`
**Status:** `stable` · **Since:** 
Add a scheduled backup.
**Examples:**
```sh
navig github schedule-add
```

## `navig github schedule-list`
**Status:** `stable` · **Since:** 
List all scheduled backups.
**Examples:**
```sh
navig github schedule-list
```

## `navig github schedule-remove`
**Status:** `stable` · **Since:** 
Remove a scheduled backup.
**Examples:**
```sh
navig github schedule-remove
```

## `navig github schedule-run`
**Status:** `stable` · **Since:** 
Run the backup scheduler daemon.
**Examples:**
```sh
navig github schedule-run
```

## `navig github search`
**Status:** `stable` · **Since:** 
🔍 Search GitHub and clone matching repositories.
**Examples:**
```sh
navig github search
```

## `navig github secrets`
**Status:** `stable` · **Since:** 
Export repository secrets (names only, not values).
**Examples:**
```sh
navig github secrets
```

## `navig github snapshot`
**Status:** `stable` · **Since:** 
Create a snapshot of the current backup state for later comparison.
**Examples:**
```sh
navig github snapshot
```

## `navig github starred`
**Status:** `stable` · **Since:** 
Mirror all repositories starred by a user.
**Examples:**
```sh
navig github starred
```

## `navig github status`
**Status:** `stable` · **Since:** 
ℹ️  Show engine installation status and token configuration.
**Examples:**
```sh
navig github status
```

## `navig github template-create`
**Status:** `stable` · **Since:** 
Create a custom backup template.
**Examples:**
```sh
navig github template-create
```

## `navig github template-delete`
**Status:** `stable` · **Since:** 
Delete a custom template (built-in templates cannot be deleted).
**Examples:**
```sh
navig github template-delete
```

## `navig github template-show`
**Status:** `stable` · **Since:** 
Show details of a backup template.
**Examples:**
```sh
navig github template-show
```

## `navig github template-use`
**Status:** `stable` · **Since:** 
Run a backup using a template.
**Examples:**
```sh
navig github template-use
```

## `navig github templates`
**Status:** `stable` · **Since:** 
List available backup templates.
**Examples:**
```sh
navig github templates
```

## `navig github token remove`
**Status:** `stable` · **Since:** 
🗑  Remove the stored GitHub token.
**Examples:**
```sh
navig github token remove
```

## `navig github token set`
**Status:** `stable` · **Since:** 
🔑 Save a GitHub token for navig github to use automatically.
**Examples:**
```sh
navig github token set
```

## `navig github token show`
**Status:** `stable` · **Since:** 
🔍 Display where the current GitHub token comes from (masked).
**Examples:**
```sh
navig github token show
```

## `navig github transfer`
**Status:** `stable` · **Since:** 
Transfer repository(ies) to a GitHub organization.
**Examples:**
```sh
navig github transfer
```

## `navig github user`
**Status:** `stable` · **Since:** 
Backup all repositories for a GitHub user.
**Examples:**
```sh
navig github user
```

## `navig github verify`
**Status:** `stable` · **Since:** 
Verify backup integrity.
**Examples:**
```sh
navig github verify
```

## `navig github watched`
**Status:** `stable` · **Since:** 
Mirror all repositories watched by a user.
**Examples:**
```sh
navig github watched
```

## `navig github webhooks`
**Status:** `stable` · **Since:** 
Export webhooks configuration from a GitHub repository.
**Examples:**
```sh
navig github webhooks
```

## `navig github wiki`
**Status:** `stable` · **Since:** 
Backup a repository's wiki (if it exists).
**Examples:**
```sh
navig github wiki
```

## `navig github workflows`
**Status:** `stable` · **Since:** 
Backup GitHub Actions workflows from a repository.
**Examples:**
```sh
navig github workflows
```

## `navig gmail compose`
**Status:** `stable` · **Since:** 
Open a pre-filled Gmail compose window (and optionally send it).
**Examples:**
```sh
navig gmail compose
```

## `navig goal add`
**Status:** `stable` · **Since:** 
Start a standing goal. Its first cycle runs at the first scheduled time.
**Examples:**
```sh
navig goal add
```

## `navig goal list`
**Status:** `stable` · **Since:** 
Your standing goals and where each one is.
**Examples:**
```sh
navig goal list
```

## `navig goal pause`
**Status:** `stable` · **Since:** 
Stop scheduling cycles, keeping the goal's history.
**Examples:**
```sh
navig goal pause
```

## `navig goal remove`
**Status:** `stable` · **Since:** 
Delete a goal and its schedule. Its mission receipts are kept.
**Examples:**
```sh
navig goal remove
```

## `navig goal resume`
**Status:** `stable` · **Since:** 
Start scheduling a paused goal again, from where it left off.
**Examples:**
```sh
navig goal resume
```

## `navig goal run`
**Status:** `stable` · **Since:** 
Run one cycle now and print what it found (counts toward the cycle limit).
**Examples:**
```sh
navig goal run
```

## `navig goal session clear`
**Status:** `stable` · **Since:** 
Remove the goal from a session. Its injected rounds stay in history.
**Examples:**
```sh
navig goal session clear
```

## `navig goal session list`
**Status:** `stable` · **Since:** 
List every NAVIG OS session that has a goal.
**Examples:**
```sh
navig goal session list
```

## `navig goal session pause`
**Status:** `stable` · **Since:** 
Stop injecting rounds; the goal stays on the session, resumable.
**Examples:**
```sh
navig goal session pause
```

## `navig goal session resume`
**Status:** `stable` · **Since:** 
Re-arm a paused goal so the session works it again.
**Examples:**
```sh
navig goal session resume
```

## `navig goal session set`
**Status:** `stable` · **Since:** 
Give a NAVIG OS session a goal; it works the rounds until done or blocked.
**Examples:**
```sh
navig goal session set
```

## `navig goal session show`
**Status:** `stable` · **Since:** 
Show a session's goal: objective, phase, rounds, loop arming.
**Examples:**
```sh
navig goal session show
```

## `navig goal show`
**Status:** `stable` · **Since:** 
Everything about one goal, including its latest report and receipt.
**Examples:**
```sh
navig goal show
```

## `navig guard install`
**Status:** `stable` · **Since:** 
Register Navig-Guard (at logon) and Navig-ProcSweep (hourly) as scheduled tasks.
**Examples:**
```sh
navig guard install
```

## `navig guard run`
**Status:** `stable` · **Since:** 
Watch memory hogs: warn with ancestry, kill past the ceiling, hold builds down.
**Examples:**
```sh
navig guard run
```

## `navig guard status`
**Status:** `stable` · **Since:** 
Show the watchdog tasks and the last guard log lines.
**Examples:**
```sh
navig guard status
```

## `navig guard sweep`
**Status:** `stable` · **Since:** 
The full hourly sweep: orphans, runaway node/python, leaked test browsers,
**Examples:**
```sh
navig guard sweep
```

## `navig guard uninstall`
**Status:** `stable` · **Since:** 
Remove the Navig-Guard / Navig-ProcSweep tasks.
**Examples:**
```sh
navig guard uninstall
```

## `navig habit`
**Status:** `stable` · **Since:** 
Run navig habit
**Examples:**
```sh
navig habit
```

## `navig habit add`
**Status:** `stable` · **Since:** 
Add a recurring habit reminder.
**Examples:**
```sh
navig habit add
```

## `navig habit checkin`
**Status:** `stable` · **Since:** 
Show — or send to Telegram — a one-tap check-in card.
**Examples:**
```sh
navig habit checkin
```

## `navig habit journal`
**Status:** `stable` · **Since:** 
Write the day's entry into the space journal.
**Examples:**
```sh
navig habit journal
```

## `navig habit list`
**Status:** `stable` · **Since:** 
List all active habit jobs.
**Examples:**
```sh
navig habit list
```

## `navig habit log`
**Status:** `stable` · **Since:** 
Log a habit into a space's habits.csv.
**Examples:**
```sh
navig habit log
```

## `navig habit pause`
**Status:** `stable` · **Since:** 
Stop habit reminders without losing anything.
**Examples:**
```sh
navig habit pause
```

## `navig habit remove`
**Status:** `stable` · **Since:** 
Remove a habit reminder.
**Examples:**
```sh
navig habit remove
```

## `navig habit resume`
**Status:** `stable` · **Since:** 
Turn habit reminders back on after a pause.
**Examples:**
```sh
navig habit resume
```

## `navig habit review`
**Status:** `stable` · **Since:** 
Build the weekly review from the tracker instead of from memory.
**Examples:**
```sh
navig habit review
```

## `navig habit spark`
**Status:** `stable` · **Since:** 
Send one short line — chosen from the day, not at random.
**Examples:**
```sh
navig habit spark
```

## `navig habit stats`
**Status:** `stable` · **Since:** 
Show the whole cycle: streaks, complete days, and which days you lose.
**Examples:**
```sh
navig habit stats
```

## `navig habit status`
**Status:** `stable` · **Since:** 
Show habit job details including last run and next fire time.
**Examples:**
```sh
navig habit status
```

## `navig habit streak`
**Status:** `stable` · **Since:** 
Show consecutive-day streaks, non-negotiables first.
**Examples:**
```sh
navig habit streak
```

## `navig habit templates`
**Status:** `stable` · **Since:** 
Show all available built-in habit templates.
**Examples:**
```sh
navig habit templates
```

## `navig habit today`
**Status:** `stable` · **Since:** 
Show what is logged for today and which non-negotiables are still open.
**Examples:**
```sh
navig habit today
```

## `navig heartbeat`
**Status:** `stable` · **Since:** 
Heartbeat commands - run without subcommand for help.
**Examples:**
```sh
navig heartbeat
```

## `navig heartbeat configure`
**Status:** `stable` · **Since:** 
Configure heartbeat settings.
**Examples:**
```sh
navig heartbeat configure
```

## `navig heartbeat history`
**Status:** `stable` · **Since:** 
Show heartbeat history.
**Examples:**
```sh
navig heartbeat history
```

## `navig heartbeat status`
**Status:** `stable` · **Since:** 
Show heartbeat status.
**Examples:**
```sh
navig heartbeat status
```

## `navig heartbeat trigger`
**Status:** `stable` · **Since:** 
Trigger an immediate heartbeat check.
**Examples:**
```sh
navig heartbeat trigger
```

## `navig help`
**Status:** `stable` · **Since:** 
In-app help system for predictable, AI-friendly help output.
**Examples:**
```sh
navig help
```

## `navig history`
**Status:** `stable` · **Since:** 
History management - shows recent history if no subcommand.
**Examples:**
```sh
navig history
```

## `navig history clear`
**Status:** `stable` · **Since:** 
Clear all operation history.
**Examples:**
```sh
navig history clear
```

## `navig history export`
**Status:** `stable` · **Since:** 
Export operation history to file.
**Examples:**
```sh
navig history export
```

## `navig history files diff`
**Status:** `stable` · **Since:** 
Show a unified diff between two stored versions of a file.
**Examples:**
```sh
navig history files diff
```

## `navig history files restore`
**Status:** `stable` · **Since:** 
Roll back a file to a previously stored version.
**Examples:**
```sh
navig history files restore
```

## `navig history files versions`
**Status:** `stable` · **Since:** 
List all stored versions of a file.
**Examples:**
```sh
navig history files versions
```

## `navig history list`
**Status:** `stable` · **Since:** 
List command history with filtering.
**Examples:**
```sh
navig history list
```

## `navig history replay`
**Status:** `stable` · **Since:** 
Replay a previous operation.
**Examples:**
```sh
navig history replay
```

## `navig history show`
**Status:** `stable` · **Since:** 
Show detailed information about an operation.
**Examples:**
```sh
navig history show
```

## `navig history stats`
**Status:** `stable` · **Since:** 
Show history statistics.
**Examples:**
```sh
navig history stats
```

## `navig history undo`
**Status:** `stable` · **Since:** 
Undo a reversible (green) operation — confirm-gated.
**Examples:**
```sh
navig history undo
```

## `navig host`
**Status:** `stable` · **Since:** 
Host management - run without subcommand for help.
**Examples:**
```sh
navig host
```

## `navig host add`
**Status:** `stable` · **Since:** 
Add new host configuration (interactive wizard or clone).
**Examples:**
```sh
navig host add
```

## `navig host all`
**Status:** `stable` · **Since:** 
Run a shell command on ALL configured hosts simultaneously.
**Examples:**
```sh
navig host all
```

## `navig host deploy`
**Status:** `stable` · **Since:** 
Install + start NAVIG on a host over SSH — idempotent (upgrades on re-run).
**Examples:**
```sh
navig host deploy
```

## `navig host discover-local`
**Status:** `stable` · **Since:** 
Discover and configure local development environment.
**Examples:**
```sh
navig host discover-local
```

## `navig host firewall`
**Status:** `stable` · **Since:** 
Show the Hetzner Cloud firewall(s) for a host.
**Examples:**
```sh
navig host firewall
```

## `navig host list`
**Status:** `stable` · **Since:** 2.4.18
List hosts from current workspace
**Examples:**
```sh
navig host list
```
```sh
navig host list --all
```

## `navig host lock acquire`
**Status:** `stable` · **Since:** 
Claim the lock for a host (also refreshes an existing claim).
**Examples:**
```sh
navig host lock acquire
```

## `navig host lock release`
**Status:** `stable` · **Since:** 
Release the lock for a host.
**Examples:**
```sh
navig host lock release
```

## `navig host lock status`
**Status:** `stable` · **Since:** 
Show who currently holds the lock for a host.
**Examples:**
```sh
navig host lock status
```

## `navig host maintenance clean`
**Status:** `stable` · **Since:** 
Clean package cache and orphans.
**Examples:**
```sh
navig host maintenance clean
```

## `navig host maintenance install`
**Status:** `stable` · **Since:** 
Install a package on the remote host.
**Examples:**
```sh
navig host maintenance install
```

## `navig host maintenance run`
**Status:** `stable` · **Since:** 
Run system maintenance operations.
**Examples:**
```sh
navig host maintenance run
```

## `navig host maintenance show`
**Status:** `stable` · **Since:** 
Show system maintenance information.
**Examples:**
```sh
navig host maintenance show
```

## `navig host maintenance update`
**Status:** `stable` · **Since:** 
Update system packages.
**Examples:**
```sh
navig host maintenance update
```

## `navig host monitor report`
**Status:** `stable` · **Since:** 
Generate comprehensive monitoring report.
**Examples:**
```sh
navig host monitor report
```

## `navig host monitor show`
**Status:** `stable` · **Since:** 
Show monitoring information.
**Examples:**
```sh
navig host monitor show
```

## `navig host remove`
**Status:** `stable` · **Since:** 
Remove a host configuration.
**Examples:**
```sh
navig host remove
```

## `navig host security edit`
**Status:** `stable` · **Since:** 
Edit security settings.
**Examples:**
```sh
navig host security edit
```

## `navig host security show`
**Status:** `stable` · **Since:** 
Show security information.
**Examples:**
```sh
navig host security show
```

## `navig host servers`
**Status:** `stable` · **Since:** 
List cloud (Hetzner) servers for hosts that have a key — beside the SSH hosts.
**Examples:**
```sh
navig host servers
```

## `navig host show`
**Status:** `stable` · **Since:** 
Show host information (canonical command).
**Examples:**
```sh
navig host show
```

## `navig host status`
**Status:** `stable` · **Since:** 
Transport-aware status — probes SSH + Hetzner and shows the optimal path.
**Examples:**
```sh
navig host status
```

## `navig host test`
**Status:** `stable` · **Since:** 
Test SSH connection to host.
**Examples:**
```sh
navig host test
```

## `navig host use`
**Status:** `stable` · **Since:** 
Switch active host context (global).
**Examples:**
```sh
navig host use
```

## `navig hosts`
**Status:** `stable` · **Since:** 
Hosts file operations - run without subcommand for help.
**Examples:**
```sh
navig hosts
```

## `navig hosts add`
**Status:** `stable` · **Since:** 
Add an entry to the hosts file (requires admin).
**Examples:**
```sh
navig hosts add
```

## `navig hosts edit`
**Status:** `stable` · **Since:** 
Open hosts file in editor (requires admin).
**Examples:**
```sh
navig hosts edit
```

## `navig hosts view`
**Status:** `stable` · **Since:** 
View the system hosts file with syntax highlighting.
**Examples:**
```sh
navig hosts view
```

## `navig import`
**Status:** `stable` · **Since:** 
Run navig import
**Examples:**
```sh
navig import
```

## `navig import list-sources`
**Status:** `stable` · **Since:** 
List all built-in import sources.
**Examples:**
```sh
navig import list-sources
```

## `navig inbox`
**Status:** `stable` · **Since:** 
Run navig inbox
**Examples:**
```sh
navig inbox
```

## `navig inbox add`
**Status:** `stable` · **Since:** 
Fetch a URL, classify it, and route it into the wiki inbox.
**Examples:**
```sh
navig inbox add
```

## `navig inbox dry-run`
**Status:** `stable` · **Since:** 
Preview routing for all inbox files (no files written or moved).
**Examples:**
```sh
navig inbox dry-run
```

## `navig inbox filter`
**Status:** `stable` · **Since:** 
Filter and normalize all .navig/**/*.md files in-place.
**Examples:**
```sh
navig inbox filter
```

## `navig inbox process-all`
**Status:** `stable` · **Since:** 
Process ALL .md files in .navig/plans/inbox/.
**Examples:**
```sh
navig inbox process-all
```

## `navig inbox process-current`
**Status:** `stable` · **Since:** 
Process a single inbox file — classify, transform, and route.
**Examples:**
```sh
navig inbox process-current
```

## `navig inbox promote`
**Status:** `stable` · **Since:** 
Promote an inbox item up the plan tiers — never deletes; appends a bullet
**Examples:**
```sh
navig inbox promote
```

## `navig inbox reroute`
**Status:** `stable` · **Since:** 
Re-evaluate routes.yaml exclude rules and redirect misplaced inbox files.
**Examples:**
```sh
navig inbox reroute
```

## `navig inbox stats`
**Status:** `stable` · **Since:** 
Show routing summary statistics from the inbox SQLite store.
**Examples:**
```sh
navig inbox stats
```

## `navig inbox ui`
**Status:** `stable` · **Since:** 
Interactive TUI review panel — inspect inbox files and approve routing.
**Examples:**
```sh
navig inbox ui
```

## `navig inbox watch`
**Status:** `stable` · **Since:** 
Watch .navig/**/*.md for changes and re-filter automatically.
**Examples:**
```sh
navig inbox watch
```

## `navig index drop`
**Status:** `stable` · **Since:** 
Drop the project index (removes SQLite database).
**Examples:**
```sh
navig index drop
```

## `navig index scan`
**Status:** `stable` · **Since:** 
Scan and index project source code for BM25 search.
**Examples:**
```sh
navig index scan
```

## `navig index search`
**Status:** `stable` · **Since:** 
Search the project index using BM25 ranking.
**Examples:**
```sh
navig index search
```

## `navig index stats`
**Status:** `stable` · **Since:** 
Show project index statistics.
**Examples:**
```sh
navig index stats
```

## `navig init`
**Status:** `stable` · **Since:** 
State-aware NAVIG setup gateway.
**Examples:**
```sh
navig init
```

## `navig init-local`
**Status:** `stable` · **Since:** 
Initialize app-specific .navig/ directory (renamed from 'init').
**Examples:**
```sh
navig init-local
```

## `navig init-rollback`
**Status:** `stable` · **Since:** 
Roll back the most recent installer run.
**Examples:**
```sh
navig init-rollback
```

## `navig insights`
**Status:** `stable` · **Since:** 
Operations insights - analytics on your command patterns.
**Examples:**
```sh
navig insights
```

## `navig insights anomalies`
**Status:** `stable` · **Since:** 
Detect unusual patterns and potential issues.
**Examples:**
```sh
navig insights anomalies
```

## `navig insights commands`
**Status:** `stable` · **Since:** 
Show most frequently used commands with success rates.
**Examples:**
```sh
navig insights commands
```

## `navig insights hosts`
**Status:** `stable` · **Since:** 
Show host health scores and trends.
**Examples:**
```sh
navig insights hosts
```

## `navig insights recommend`
**Status:** `stable` · **Since:** 
Get personalized recommendations based on your usage.
**Examples:**
```sh
navig insights recommend
```

## `navig insights report`
**Status:** `stable` · **Since:** 
Generate a full analytics report.
**Examples:**
```sh
navig insights report
```

## `navig insights show`
**Status:** `stable` · **Since:** 
Show insights summary with key metrics.
**Examples:**
```sh
navig insights show
```

## `navig insights time`
**Status:** `stable` · **Since:** 
Show time-based usage patterns.
**Examples:**
```sh
navig insights time
```

## `navig install`
**Status:** `stable` · **Since:** 
Install community assets from GitHub into store/.
**Examples:**
```sh
navig install
```

## `navig install add`
**Status:** `stable` · **Since:** 
Install an asset from GitHub.
**Examples:**
```sh
navig install add
```

## `navig install browse`
**Status:** `stable` · **Since:** 
Browse the NAVIG community registry.
**Examples:**
```sh
navig install browse
```

## `navig install freeze`
**Status:** `stable` · **Since:** 
Print installed assets as type/name==version specs.
**Examples:**
```sh
navig install freeze
```

## `navig install list`
**Status:** `stable` · **Since:** 
List installed community assets.
**Examples:**
```sh
navig install list
```

## `navig install remove`
**Status:** `stable` · **Since:** 
Remove an installed community asset.
**Examples:**
```sh
navig install remove
```

## `navig install search`
**Status:** `stable` · **Since:** 
Search the NAVIG community registry.
**Examples:**
```sh
navig install search
```

## `navig install show`
**Status:** `stable` · **Since:** 
Show details of an installed asset.
**Examples:**
```sh
navig install show
```

## `navig install status`
**Status:** `stable` · **Since:** 
Show health of all installed assets.
**Examples:**
```sh
navig install status
```

## `navig install update`
**Status:** `stable` · **Since:** 
Update one or all installed assets to latest.
**Examples:**
```sh
navig install update
```

## `navig install upgrade`
**Status:** `stable` · **Since:** 
Upgrade an asset's **capabilities only** — memory/plans/inbox/state untouched.
**Examples:**
```sh
navig install upgrade
```

## `navig instructions audit`
**Status:** `stable` · **Since:** 
Report the bytes, duplicates and broken links in the loaded instruction set.
**Examples:**
```sh
navig instructions audit
```

## `navig ios crashlogs`
**Status:** `stable` · **Since:** 
Pull (or list) iOS crash reports.
**Examples:**
```sh
navig ios crashlogs
```

## `navig ios dfu`
**Status:** `stable` · **Since:** 
Guide entering DFU mode (button timing is physical — can't be automated).
**Examples:**
```sh
navig ios dfu
```

## `navig ios instruments`
**Status:** `stable` · **Since:** 
Drive DVT instruments (proclist/applist/kill/signal/…) — passthrough to
**Examples:**
```sh
navig ios instruments
```

## `navig ios pcap`
**Status:** `stable` · **Since:** 
Capture device network traffic to a .pcap (Ctrl-C to stop).
**Examples:**
```sh
navig ios pcap
```

## `navig ios recovery`
**Status:** `stable` · **Since:** 
Enter/exit iOS Recovery mode (drives pymobiledevice3 restore enter/exit).
**Examples:**
```sh
navig ios recovery
```

## `navig ios syslog`
**Status:** `stable` · **Since:** 
Stream live iOS syslog (Ctrl-C to stop).
**Examples:**
```sh
navig ios syslog
```

## `navig kg forget`
**Status:** `stable` · **Since:** 
Delete a fact by ID.
**Examples:**
```sh
navig kg forget
```

## `navig kg recall`
**Status:** `stable` · **Since:** 
Recall all facts about a subject.
**Examples:**
```sh
navig kg recall
```

## `navig kg remember`
**Status:** `stable` · **Since:** 
Store a fact triple in the knowledge graph.
**Examples:**
```sh
navig kg remember
```

## `navig kg routines`
**Status:** `stable` · **Since:** 
List all registered routines.
**Examples:**
```sh
navig kg routines
```

## `navig kg search`
**Status:** `stable` · **Since:** 
Full-text search across all facts (subject, predicate, object).
**Examples:**
```sh
navig kg search
```

## `navig kg status`
**Status:** `stable` · **Since:** 
Show knowledge graph statistics.
**Examples:**
```sh
navig kg status
```

## `navig kill`
**Status:** `stable` · **Since:** 
Kill processes by group or name (cross-platform).
**Examples:**
```sh
navig kill
```

## `navig ledger`
**Status:** `stable` · **Since:** 
Operations-ledger integrity (tamper-evident hash chain).
**Examples:**
```sh
navig ledger
```

## `navig ledger reap`
**Status:** `stable` · **Since:** 
Record interrupted operations as `interrupted` in the ledger.
**Examples:**
```sh
navig ledger reap
```

## `navig ledger show`
**Status:** `stable` · **Since:** 
Show recent operations with chain state and reversibility labels.
**Examples:**
```sh
navig ledger show
```

## `navig ledger verify`
**Status:** `stable` · **Since:** 
Walk the operations ledger and re-check every hash-chain link.
**Examples:**
```sh
navig ledger verify
```

## `navig lens add`
**Status:** `stable` · **Since:** 
Install (or update) a Lens from a file or an https URL.
**Examples:**
```sh
navig lens add
```

## `navig lens approve`
**Status:** `stable` · **Since:** 
Apply a staged change, or turn on a Lens the agent proposed.
**Examples:**
```sh
navig lens approve
```

## `navig lens disable`
**Status:** `stable` · **Since:** 
Turn a Lens off (it stays installed).
**Examples:**
```sh
navig lens disable
```

## `navig lens enable`
**Status:** `stable` · **Since:** 
Turn a Lens on.
**Examples:**
```sh
navig lens enable
```

## `navig lens export`
**Status:** `stable` · **Since:** 
Export a Lens's original .user.js (works in Tampermonkey / Violentmonkey too).
**Examples:**
```sh
navig lens export
```

## `navig lens list`
**Status:** `stable` · **Since:** 
List installed Lenses.
**Examples:**
```sh
navig lens list
```

## `navig lens reject`
**Status:** `stable` · **Since:** 
Discard a staged change (the Lens stays as it was), or delete an agent's proposal.
**Examples:**
```sh
navig lens reject
```

## `navig lens remove`
**Status:** `stable` · **Since:** 
Delete a Lens and its cached @require files.
**Examples:**
```sh
navig lens remove
```

## `navig lens review`
**Status:** `stable` · **Since:** 
What is waiting on you: agent proposals and staged updates, with the diff.
**Examples:**
```sh
navig lens review
```

## `navig lens show`
**Status:** `stable` · **Since:** 
Show a Lens: where it runs, what it may use, warnings.
**Examples:**
```sh
navig lens show
```

## `navig lens update`
**Status:** `stable` · **Since:** 
Check install links for new versions. Stages them for review — applies nothing.
**Examples:**
```sh
navig lens update
```

## `navig license paste`
**Status:** `stable` · **Since:** 
Validate a license token and persist it to ``~/.navig/license.key``.
**Examples:**
```sh
navig license paste
```

## `navig license refresh`
**Status:** `stable` · **Since:** 
Fetch your newest licence from navig.run (renewals, Bay purchases).
**Examples:**
```sh
navig license refresh
```

## `navig license remove`
**Status:** `stable` · **Since:** 
Delete the persisted license (drops you back to Solo / Free tier).
**Examples:**
```sh
navig license remove
```

## `navig license show`
**Status:** `stable` · **Since:** 
Print the persisted license token in truncated form.
**Examples:**
```sh
navig license show
```

## `navig license status`
**Status:** `stable` · **Since:** 
Show detailed entitlement (tier, hosts, modules, subscription).
**Examples:**
```sh
navig license status
```

## `navig life`
**Status:** `stable` · **Since:** 
Run navig life
**Examples:**
```sh
navig life
```

## `navig life show`
**Status:** `stable` · **Since:** 
Show the unified life + ops dashboard.
**Examples:**
```sh
navig life show
```

## `navig lighthouse deploy`
**Status:** `stable` · **Since:** 
Deploy Lighthouse to your own Cloudflare account — no Node, no tunnel, no domain.
**Examples:**
```sh
navig lighthouse deploy
```

## `navig lighthouse disable`
**Status:** `stable` · **Since:** 
Turn off Lighthouse mode (reverts to tunnel/direct on the next gateway start).
**Examples:**
```sh
navig lighthouse disable
```

## `navig lighthouse login`
**Status:** `stable` · **Since:** 
One-click: authorize via your browser (no API token to create), then deploy.
**Examples:**
```sh
navig lighthouse login
```

## `navig lighthouse redeploy`
**Status:** `stable` · **Since:** 
Re-upload the Worker to the same account (idempotent; reuses saved config).
**Examples:**
```sh
navig lighthouse redeploy
```

## `navig lighthouse status`
**Status:** `stable` · **Since:** 
Show Lighthouse config + live uplink state (if the daemon is running).
**Examples:**
```sh
navig lighthouse status
```

## `navig lighthouse url`
**Status:** `stable` · **Since:** 
Print the one stable edge URL + every inbound hook (and where to set each).
**Examples:**
```sh
navig lighthouse url
```

## `navig lighthouse version`
**Status:** `stable` · **Since:** 
Compare the deployed edge to the version bundled in this navig release.
**Examples:**
```sh
navig lighthouse version
```

## `navig local`
**Status:** `stable` · **Since:** 
Local system management - run without subcommand for help.
**Examples:**
```sh
navig local
```

## `navig local audit`
**Status:** `stable` · **Since:** 
Run local security audit.
**Examples:**
```sh
navig local audit
```

## `navig local dns`
**Status:** `stable` · **Since:** 
Perform DNS lookup.
**Examples:**
```sh
navig local dns
```

## `navig local firewall`
**Status:** `stable` · **Since:** 
Show local firewall status.
**Examples:**
```sh
navig local firewall
```

## `navig local interfaces`
**Status:** `stable` · **Since:** 
Show network interfaces.
**Examples:**
```sh
navig local interfaces
```

## `navig local ping`
**Status:** `stable` · **Since:** 
Ping a host from local machine.
**Examples:**
```sh
navig local ping
```

## `navig local ports`
**Status:** `stable` · **Since:** 
Show open/listening ports on local machine.
**Examples:**
```sh
navig local ports
```

## `navig local show`
**Status:** `stable` · **Since:** 
Show local system information.
**Examples:**
```sh
navig local show
```

## `navig log`
**Status:** `stable` · **Since:** 
Log operations - run without subcommand for help.
**Examples:**
```sh
navig log
```

## `navig log run`
**Status:** `stable` · **Since:** 
Run log maintenance operations.
**Examples:**
```sh
navig log run
```

## `navig log show`
**Status:** `stable` · **Since:** 
Show service or container logs.
**Examples:**
```sh
navig log show
```

## `navig logs`
**Status:** `stable` · **Since:** 
Log operations - run without subcommand for help.
**Examples:**
```sh
navig logs
```

## `navig logs run`
**Status:** `stable` · **Since:** 
Run log maintenance operations.
**Examples:**
```sh
navig logs run
```

## `navig logs show`
**Status:** `stable` · **Since:** 
Show service or container logs.
**Examples:**
```sh
navig logs show
```

## `navig matrix`
**Status:** `stable` · **Since:** 
Run navig matrix
**Examples:**
```sh
navig matrix
```

## `navig matrix accounts`
**Status:** `stable` · **Since:** 
List configured Matrix accounts from the vault.
**Examples:**
```sh
navig matrix accounts
```

## `navig matrix bridge deploy`
**Status:** `stable` · **Since:** 
Pull and start all mautrix bridge containers via Docker.
**Examples:**
```sh
navig matrix bridge deploy
```

## `navig matrix bridge logs`
**Status:** `stable` · **Since:** 
Tail logs from a mautrix bridge container.
**Examples:**
```sh
navig matrix bridge logs
```

## `navig matrix bridge restart`
**Status:** `stable` · **Since:** 
Restart a mautrix bridge container.
**Examples:**
```sh
navig matrix bridge restart
```

## `navig matrix bridge setup`
**Status:** `stable` · **Since:** 
Interactive setup wizard for a mautrix bridge.
**Examples:**
```sh
navig matrix bridge setup
```

## `navig matrix bridge status`
**Status:** `stable` · **Since:** 
Show running state of all mautrix bridges.
**Examples:**
```sh
navig matrix bridge status
```

## `navig matrix e2ee blacklist`
**Status:** `stable` · **Since:** 
Blacklist a device (do not send keys to it).
**Examples:**
```sh
navig matrix e2ee blacklist
```

## `navig matrix e2ee devices`
**Status:** `stable` · **Since:** 
List devices and their trust state.
**Examples:**
```sh
navig matrix e2ee devices
```

## `navig matrix e2ee export-keys`
**Status:** `stable` · **Since:** 
Export E2EE room keys to a file (encrypted).
**Examples:**
```sh
navig matrix e2ee export-keys
```

## `navig matrix e2ee import-keys`
**Status:** `stable` · **Since:** 
Import E2EE room keys from a file.
**Examples:**
```sh
navig matrix e2ee import-keys
```

## `navig matrix e2ee keys`
**Status:** `stable` · **Since:** 
Show the bot's own device keys (for cross-verification).
**Examples:**
```sh
navig matrix e2ee keys
```

## `navig matrix e2ee status`
**Status:** `stable` · **Since:** 
Show E2EE diagnostic status.
**Examples:**
```sh
navig matrix e2ee status
```

## `navig matrix e2ee trust`
**Status:** `stable` · **Since:** 
Manually trust a device (skip SAS verification).
**Examples:**
```sh
navig matrix e2ee trust
```

## `navig matrix e2ee trust-all`
**Status:** `stable` · **Since:** 
Trust ALL known devices for a user.
**Examples:**
```sh
navig matrix e2ee trust-all
```

## `navig matrix e2ee unverify`
**Status:** `stable` · **Since:** 
Remove trust from a device.
**Examples:**
```sh
navig matrix e2ee unverify
```

## `navig matrix e2ee verify`
**Status:** `stable` · **Since:** 
Start interactive SAS (emoji) verification with a device.
**Examples:**
```sh
navig matrix e2ee verify
```

## `navig matrix features`
**Status:** `stable` · **Since:** 
Show Matrix feature toggle states.
**Examples:**
```sh
navig matrix features
```

## `navig matrix file download`
**Status:** `stable` · **Since:** 
Download a file from a Matrix content URI.
**Examples:**
```sh
navig matrix file download
```

## `navig matrix file upload`
**Status:** `stable` · **Since:** 
Upload a file to a Matrix room.
**Examples:**
```sh
navig matrix file upload
```

## `navig matrix inbox list`
**Status:** `stable` · **Since:** 
List persisted Matrix inbox messages.
**Examples:**
```sh
navig matrix inbox list
```

## `navig matrix inbox mark-read`
**Status:** `stable` · **Since:** 
Mark messages as read (one or all).
**Examples:**
```sh
navig matrix inbox mark-read
```

## `navig matrix inbox process`
**Status:** `stable` · **Since:** 
Route unread Matrix messages through InboxRouterAgent.
**Examples:**
```sh
navig matrix inbox process
```

## `navig matrix inbox purge`
**Status:** `stable` · **Since:** 
Delete all read messages from inbox.
**Examples:**
```sh
navig matrix inbox purge
```

## `navig matrix inbox unread`
**Status:** `stable` · **Since:** 
Show unread count.
**Examples:**
```sh
navig matrix inbox unread
```

## `navig matrix login`
**Status:** `stable` · **Since:** 
Authenticate with a Matrix homeserver.
**Examples:**
```sh
navig matrix login
```

## `navig matrix logout`
**Status:** `stable` · **Since:** 
End the current Matrix session.
**Examples:**
```sh
navig matrix logout
```

## `navig matrix notice`
**Status:** `stable` · **Since:** 
Send a notice (bot-style, no notification highlight).
**Examples:**
```sh
navig matrix notice
```

## `navig matrix read`
**Status:** `stable` · **Since:** 
Read recent messages from a room.
**Examples:**
```sh
navig matrix read
```

## `navig matrix rooms`
**Status:** `stable` · **Since:** 
List joined Matrix rooms.
**Examples:**
```sh
navig matrix rooms
```

## `navig matrix send`
**Status:** `stable` · **Since:** 
Send a text message to a Matrix room.
**Examples:**
```sh
navig matrix send
```

## `navig matrix status`
**Status:** `stable` · **Since:** 
Show Matrix connection status and account info.
**Examples:**
```sh
navig matrix status
```

## `navig matrix store bridges`
**Status:** `stable` · **Since:** 
List bridge configurations in the store.
**Examples:**
```sh
navig matrix store bridges
```

## `navig matrix store events`
**Status:** `stable` · **Since:** 
Show recent events for a room from the persistent store.
**Examples:**
```sh
navig matrix store events
```

## `navig matrix store prune`
**Status:** `stable` · **Since:** 
Prune old events from the store.
**Examples:**
```sh
navig matrix store prune
```

## `navig matrix store rooms`
**Status:** `stable` · **Since:** 
List rooms in the persistent store.
**Examples:**
```sh
navig matrix store rooms
```

## `navig matrix store set-purpose`
**Status:** `stable` · **Since:** 
Classify what a stored room is FOR.
**Examples:**
```sh
navig matrix store set-purpose
```

## `navig matrix store stats`
**Status:** `stable` · **Since:** 
Show persistent store statistics.
**Examples:**
```sh
navig matrix store stats
```

## `navig matrix tail`
**Status:** `stable` · **Since:** 
Live-tail messages from a room (Ctrl+C to stop).
**Examples:**
```sh
navig matrix tail
```

## `navig matrix use`
**Status:** `stable` · **Since:** 
Switch the active Matrix account.
**Examples:**
```sh
navig matrix use
```

## `navig mcp disable`
**Status:** `stable` · **Since:** 
Disable a configured MCP server.
**Examples:**
```sh
navig mcp disable
```

## `navig mcp enable`
**Status:** `stable` · **Since:** 
Enable a configured MCP server.
**Examples:**
```sh
navig mcp enable
```

## `navig mcp info`
**Status:** `stable` · **Since:** 
Show one server's details — type, command, enabled state.
**Examples:**
```sh
navig mcp info
```

## `navig mcp install`
**Status:** `stable` · **Since:** 
Install an MCP server from the directory.
**Examples:**
```sh
navig mcp install
```

## `navig mcp install-config`
**Status:** `stable` · **Since:** 
Wire an editor or agent to NAVIG's MCP server so its tools are callable there.
**Examples:**
```sh
navig mcp install-config
```

## `navig mcp list`
**Status:** `stable` · **Since:** 
List configured MCP servers.
**Examples:**
```sh
navig mcp list
```

## `navig mcp remove`
**Status:** `stable` · **Since:** 
Remove a configured MCP server.
**Examples:**
```sh
navig mcp remove
```

## `navig mcp search`
**Status:** `stable` · **Since:** 
Search the MCP directory for installable servers.
**Examples:**
```sh
navig mcp search
```

## `navig mcp serve`
**Status:** `stable` · **Since:** 
Start the NAVIG MCP server.
**Examples:**
```sh
navig mcp serve
```

## `navig mcp tools`
**Status:** `stable` · **Since:** 
List every tool the NAVIG MCP server exposes (what an editor/agent can call).
**Examples:**
```sh
navig mcp tools
```

## `navig memory approve`
**Status:** `stable` · **Since:** 
Approve a proposed fact so the agent may use it in future conversations.
**Examples:**
```sh
navig memory approve
```

## `navig memory bank`
**Status:** `stable` · **Since:** 
Show memory bank status and statistics.
**Examples:**
```sh
navig memory bank
```

## `navig memory clear`
**Status:** `stable` · **Since:** 
Clear conversation memory.
**Examples:**
```sh
navig memory clear
```

## `navig memory clear-bank`
**Status:** `stable` · **Since:** 
Clear the memory bank index (keeps original files).
**Examples:**
```sh
navig memory clear-bank
```

## `navig memory compact`
**Status:** `stable` · **Since:** 
Compress a session's history into a single AI-generated summary.
**Examples:**
```sh
navig memory compact
```

## `navig memory edit`
**Status:** `stable` · **Since:** 
Correct a saved memory in place.
**Examples:**
```sh
navig memory edit
```

## `navig memory export`
**Status:** `stable` · **Since:** 
Export your memory — portable, user-owned. JSON for re-import, Markdown to read.
**Examples:**
```sh
navig memory export
```

## `navig memory fact-stats`
**Status:** `stable` · **Since:** 
Show key facts memory statistics.
**Examples:**
```sh
navig memory fact-stats
```

## `navig memory facts`
**Status:** `stable` · **Since:** 
List stored key facts (what NAVIG remembers about you).
**Examples:**
```sh
navig memory facts
```

## `navig memory files`
**Status:** `stable` · **Since:** 
List indexed files in the memory bank.
**Examples:**
```sh
navig memory files
```

## `navig memory forget`
**Status:** `stable` · **Since:** 
Remove a key fact from memory (soft-delete).
**Examples:**
```sh
navig memory forget
```

## `navig memory history`
**Status:** `stable` · **Since:** 
Show conversation history for a session.
**Examples:**
```sh
navig memory history
```

## `navig memory import`
**Status:** `stable` · **Since:** 
Import memory from another device/agent (an export JSON). Dedups by content.
**Examples:**
```sh
navig memory import
```

## `navig memory index`
**Status:** `stable` · **Since:** 
Index files in the memory bank.
**Examples:**
```sh
navig memory index
```

## `navig memory knowledge`
**Status:** `stable` · **Since:** 
Manage knowledge base entries.
**Examples:**
```sh
navig memory knowledge
```

## `navig memory learn`
**Status:** `stable` · **Since:** 
Mine your command-usage patterns and propose them as memories (pending review).
**Examples:**
```sh
navig memory learn
```

## `navig memory pending`
**Status:** `stable` · **Since:** 
List facts the agent has PROPOSED, awaiting your approval.
**Examples:**
```sh
navig memory pending
```

## `navig memory pin`
**Status:** `stable` · **Since:** 
Pin a memory so the agent keeps it in mind in every conversation.
**Examples:**
```sh
navig memory pin
```

## `navig memory reject`
**Status:** `stable` · **Since:** 
Reject a proposed fact. It's kept for audit but never used.
**Examples:**
```sh
navig memory reject
```

## `navig memory remember`
**Status:** `stable` · **Since:** 
Manually add a key fact to memory.
**Examples:**
```sh
navig memory remember
```

## `navig memory scan-secrets`
**Status:** `stable` · **Since:** 
Find secrets stored in memory before the write filter existed.
**Examples:**
```sh
navig memory scan-secrets
```

## `navig memory search`
**Status:** `stable` · **Since:** 
Search the memory bank with hybrid search.
**Examples:**
```sh
navig memory search
```

## `navig memory sessions`
**Status:** `stable` · **Since:** 
List conversation sessions.
**Examples:**
```sh
navig memory sessions
```

## `navig memory stats`
**Status:** `stable` · **Since:** 
Show memory usage statistics.
**Examples:**
```sh
navig memory stats
```

## `navig memory summary`
**Status:** `stable` · **Since:** 
What NAVIG knows about you — a short profile written from your approved memories.
**Examples:**
```sh
navig memory summary
```

## `navig memory sync`
**Status:** `stable` · **Since:** 
Pull memory chunks from a remote NAVIG formation.
**Examples:**
```sh
navig memory sync
```

## `navig memory unpin`
**Status:** `stable` · **Since:** 
Unpin a memory (it is still recalled when relevant).
**Examples:**
```sh
navig memory unpin
```

## `navig menu`
**Status:** `stable` · **Since:** 
Open the menu (auto-builds on first run). Subcommands: build/scan/setup/doctor/run.
**Examples:**
```sh
navig menu
```

## `navig menu build`
**Status:** `stable` · **Since:** 
(Re)generate .navig/menu.json from detection (--ai to enrich).
**Examples:**
```sh
navig menu build
```

## `navig menu doctor`
**Status:** `stable` · **Since:** 
Diagnose environment + detection (--fix repairs, with a backup).
**Examples:**
```sh
navig menu doctor
```

## `navig menu import`
**Status:** `stable` · **Since:** 
Import a curated command catalog into .navig/menu.json (--write).
**Examples:**
```sh
navig menu import
```

## `navig menu organize`
**Status:** `stable` · **Since:** 
AI: describe every command + report gaps (--write).
**Examples:**
```sh
navig menu organize
```

## `navig menu run`
**Status:** `stable` · **Since:** 
Run a canonical action (dev/build/test/…) or any script id.
**Examples:**
```sh
navig menu run
```

## `navig menu scan`
**Status:** `stable` · **Since:** 
Detect + report (refresh cache), no UI.
**Examples:**
```sh
navig menu scan
```

## `navig menu setup`
**Status:** `stable` · **Since:** 
Guided config (offers the `menu` npm script).
**Examples:**
```sh
navig menu setup
```

## `navig mesh agents claim`
**Status:** `stable` · **Since:** 
Claim a role for this session (upsert — one entry per session, refreshed).
**Examples:**
```sh
navig mesh agents claim
```

## `navig mesh agents list`
**Status:** `stable` · **Since:** 
Who holds which role — the assignment board's read side.
**Examples:**
```sh
navig mesh agents list
```

## `navig mesh agents release`
**Status:** `stable` · **Since:** 
Release this session's registry claim (kept as history, status=released).
**Examples:**
```sh
navig mesh agents release
```

## `navig mesh peers`
**Status:** `stable` · **Since:** 
List all known mesh peers (alias for 'mesh status').
**Examples:**
```sh
navig mesh peers
```

## `navig mesh status`
**Status:** `stable` · **Since:** 
Show the current mesh topology and peer status.
**Examples:**
```sh
navig mesh status
```

## `navig migrate`
**Status:** `stable` · **Since:** 
Run navig migrate
**Examples:**
```sh
navig migrate
```

## `navig migrate addons`
**Status:** `stable` · **Since:** 
Migrate legacy addons/ → templates/ architecture.
**Examples:**
```sh
navig migrate addons
```

## `navig migrate all`
**Status:** `stable` · **Since:** 
Run all migration steps in dependency order.
**Examples:**
```sh
navig migrate all
```

## `navig migrate config`
**Status:** `stable` · **Since:** 
Migrate Documents\.navig → ~/.navig config layout.
**Examples:**
```sh
navig migrate config
```

## `navig migrate status`
**Status:** `stable` · **Since:** 
Show which migration steps have been completed.
**Examples:**
```sh
navig migrate status
```

## `navig mini deploy`
**Status:** `stable` · **Since:** 
[bold]Upload agent files[/bold] to a remote device via SSH and restart.
**Examples:**
```sh
navig mini deploy
```

## `navig mini list`
**Status:** `stable` · **Since:** 
[bold]List all configured NAVIG Mini agents.[/bold]
**Examples:**
```sh
navig mini list
```

## `navig mini logs`
**Status:** `stable` · **Since:** 
[bold]Tail agent or monitor logs[/bold] from the remote device via SSH.
**Examples:**
```sh
navig mini logs
```

## `navig mini restart`
**Status:** `stable` · **Since:** 
[bold]Restart the agent daemon[/bold] (or a watched service) remotely.
**Examples:**
```sh
navig mini restart
```

## `navig mini run`
**Status:** `stable` · **Since:** 
[bold]Execute an allowlisted command[/bold] on the remote agent (HMAC-signed).
**Examples:**
```sh
navig mini run
```

## `navig mini status`
**Status:** `stable` · **Since:** 
[bold]Show health and system stats[/bold] of the remote agent.
**Examples:**
```sh
navig mini status
```

## `navig miniapp deploy`
**Status:** `stable` · **Since:** 
Deploy the Deck to YOUR own Cloudflare, pointed at your edge.
**Examples:**
```sh
navig miniapp deploy
```

## `navig miniapp register`
**Status:** `stable` · **Since:** 
Auto-register the Deck URL as your bot's Mini App menu button.
**Examples:**
```sh
navig miniapp register
```

## `navig miniapp status`
**Status:** `stable` · **Since:** 
Show the bot's current Mini App menu button configuration.
**Examples:**
```sh
navig miniapp status
```

## `navig miniapp unregister`
**Status:** `stable` · **Since:** 
Revert the bot's menu button to the default (commands list).
**Examples:**
```sh
navig miniapp unregister
```

## `navig miniapp version`
**Status:** `stable` · **Since:** 
Show the deployed deck version vs the version in this navig release.
**Examples:**
```sh
navig miniapp version
```

## `navig mobile android connect`
**Status:** `stable` · **Since:** 
adb connect over TCP/IP.
**Examples:**
```sh
navig mobile android connect
```

## `navig mobile android fastboot`
**Status:** `stable` · **Since:** 
Pass arguments straight through to the fastboot binary (device in bootloader).
**Examples:**
```sh
navig mobile android fastboot
```

## `navig mobile android logcat`
**Status:** `stable` · **Since:** 
Show recent Android logcat lines.
**Examples:**
```sh
navig mobile android logcat
```

## `navig mobile android reboot`
**Status:** `stable` · **Since:** 
Reboot the device (into the bootloader/recovery for rooting flows).
**Examples:**
```sh
navig mobile android reboot
```

## `navig mobile android sideload`
**Status:** `stable` · **Since:** 
adb sideload a zip (device must be in recovery). Consent + --yes required.
**Examples:**
```sh
navig mobile android sideload
```

## `navig mobile apps`
**Status:** `stable` · **Since:** 
Installed apps: list · install <ipa/apk> · uninstall <id> · info <id> · extract <id>.
**Examples:**
```sh
navig mobile apps
```

## `navig mobile backup`
**Status:** `stable` · **Since:** 
Device backup: create a full backup, or list past backups.
**Examples:**
```sh
navig mobile backup
```

## `navig mobile battery`
**Status:** `stable` · **Since:** 
Battery level.
**Examples:**
```sh
navig mobile battery
```

## `navig mobile case`
**Status:** `stable` · **Since:** 
List investigation cases, or show one's evidence manifest.
**Examples:**
```sh
navig mobile case
```

## `navig mobile connect`
**Status:** `stable` · **Since:** 
Connect to an Android device over TCP/IP (adb connect).
**Examples:**
```sh
navig mobile connect
```

## `navig mobile consent`
**Status:** `stable` · **Since:** 
Record/show/revoke authorization to investigate a device (required before
**Examples:**
```sh
navig mobile consent
```

## `navig mobile dev`
**Status:** `stable` · **Since:** 
Developer tools — shell, enable dev-mode, frida (list/attach/spawn), objection.
**Examples:**
```sh
navig mobile dev
```

## `navig mobile devices`
**Status:** `stable` · **Since:** 
List every connected/paired Android + iOS device.
**Examples:**
```sh
navig mobile devices
```

## `navig mobile doctor`
**Status:** `stable` · **Since:** 
Toolchain health — what's installed for Android/iOS ops and how to get the rest.
**Examples:**
```sh
navig mobile doctor
```

## `navig mobile evidence`
**Status:** `stable` · **Since:** 
List a case's evidence (or --verify their integrity).
**Examples:**
```sh
navig mobile evidence
```

## `navig mobile forensics`
**Status:** `stable` · **Since:** 
Consent-gated logical acquisition + artifact parsing (iLEAPP/ALEAPP) → case dir.
**Examples:**
```sh
navig mobile forensics
```

## `navig mobile fs`
**Status:** `stable` · **Since:** 
Device filesystem: ls <remote> · pull <remote> <local> · push <local> <remote>.
**Examples:**
```sh
navig mobile fs
```

## `navig mobile info`
**Status:** `stable` · **Since:** 
Unified device info (auto-detects the connected platform).
**Examples:**
```sh
navig mobile info
```

## `navig mobile ios crashlogs`
**Status:** `stable` · **Since:** 
Pull (or list) iOS crash reports.
**Examples:**
```sh
navig mobile ios crashlogs
```

## `navig mobile ios dfu`
**Status:** `stable` · **Since:** 
Guide entering DFU mode (button timing is physical — can't be automated).
**Examples:**
```sh
navig mobile ios dfu
```

## `navig mobile ios instruments`
**Status:** `stable` · **Since:** 
Drive DVT instruments (proclist/applist/kill/signal/…) — passthrough to
**Examples:**
```sh
navig mobile ios instruments
```

## `navig mobile ios pcap`
**Status:** `stable` · **Since:** 
Capture device network traffic to a .pcap (Ctrl-C to stop).
**Examples:**
```sh
navig mobile ios pcap
```

## `navig mobile ios recovery`
**Status:** `stable` · **Since:** 
Enter/exit iOS Recovery mode (drives pymobiledevice3 restore enter/exit).
**Examples:**
```sh
navig mobile ios recovery
```

## `navig mobile ios syslog`
**Status:** `stable` · **Since:** 
Stream live iOS syslog (Ctrl-C to stop).
**Examples:**
```sh
navig mobile ios syslog
```

## `navig mobile jailbreak`
**Status:** `stable` · **Since:** 
Jailbreak assist (iOS): status (checkm8 eligibility + dev-mode) · guide (palera1n/checkra1n).
**Examples:**
```sh
navig mobile jailbreak
```

## `navig mobile logs`
**Status:** `stable` · **Since:** 
Device log — recent logcat (Android) or live syslog (iOS).
**Examples:**
```sh
navig mobile logs
```

## `navig mobile media`
**Status:** `stable` · **Since:** 
Pull the camera roll / DCIM off the device (consent-gated).
**Examples:**
```sh
navig mobile media
```

## `navig mobile mirror`
**Status:** `stable` · **Since:** 
Mirror/control the screen (scrcpy on Android).
**Examples:**
```sh
navig mobile mirror
```

## `navig mobile osint`
**Status:** `stable` · **Since:** 
Device-anchored OSINT: identity · app-privacy report · combined report.
**Examples:**
```sh
navig mobile osint
```

## `navig mobile pair`
**Status:** `stable` · **Since:** 
Pair a device — Android wireless (`adb pair host:port`), else guidance.
**Examples:**
```sh
navig mobile pair
```

## `navig mobile quest awake`
**Status:** `stable` · **Since:** 
Keep the headset awake without wearing it (for mirroring, setup, demos).
**Examples:**
```sh
navig mobile quest awake
```

## `navig mobile quest connect`
**Status:** `stable` · **Since:** 
Switch a USB-connected Quest to wireless ADB (port 5555) and connect to it.
**Examples:**
```sh
navig mobile quest connect
```

## `navig mobile quest mirror`
**Status:** `stable` · **Since:** 
Mirror the headset on this PC (scrcpy) — clean one-eye view, optional MP4.
**Examples:**
```sh
navig mobile quest mirror
```

## `navig mobile quest perf`
**Status:** `stable` · **Since:** 
Live FPS / foveation / CPU-GPU / temperature from the compositor — the proof
**Examples:**
```sh
navig mobile quest perf
```

## `navig mobile quest status`
**Status:** `stable` · **Since:** 
Model, Horizon build, battery, sleep state, storage, active tweaks.
**Examples:**
```sh
navig mobile quest status
```

## `navig mobile quest tweaks`
**Status:** `stable` · **Since:** 
Apply render/perf tweaks and verify each by read-back. Reboot clears them;
**Examples:**
```sh
navig mobile quest tweaks
```

## `navig mobile quest watch`
**Status:** `stable` · **Since:** 
Re-apply your saved tweaks whenever the headset comes back.
**Examples:**
```sh
navig mobile quest watch
```

## `navig mobile restore`
**Status:** `stable` · **Since:** 
Restore a backup — DESTRUCTIVE (overwrites device data). Consent + --yes required.
**Examples:**
```sh
navig mobile restore
```

## `navig mobile root`
**Status:** `stable` · **Since:** 
Rooting assist (Android): status · guide · unlock · flash. Never one-click —
**Examples:**
```sh
navig mobile root
```

## `navig mobile scan`
**Status:** `stable` · **Since:** 
Scan a device/backup for spyware with MVT (Pegasus/mercenary IOCs). Consent-gated.
**Examples:**
```sh
navig mobile scan
```

## `navig mobile screenshot`
**Status:** `stable` · **Since:** 
Capture the device screen to a PNG.
**Examples:**
```sh
navig mobile screenshot
```

## `navig mobile storage`
**Status:** `stable` · **Since:** 
Storage usage.
**Examples:**
```sh
navig mobile storage
```

## `navig mobile ui apps`
**Status:** `stable` · **Since:** 
List launchable apps for a platform.
**Examples:**
```sh
navig mobile ui apps
```

## `navig mobile ui assert`
**Status:** `stable` · **Since:** 
Assert the current screen shows (or --gone: hides) a text/ref — the
**Examples:**
```sh
navig mobile ui assert
```

## `navig mobile ui close`
**Status:** `stable` · **Since:** 
End the current UI session.
**Examples:**
```sh
navig mobile ui close
```

## `navig mobile ui devices`
**Status:** `stable` · **Since:** 
List app-automation targets (simulators / emulators / devices).
**Examples:**
```sh
navig mobile ui devices
```

## `navig mobile ui doctor`
**Status:** `stable` · **Since:** 
agent-device's own environment check (SDKs, drivers, permissions).
**Examples:**
```sh
navig mobile ui doctor
```

## `navig mobile ui exec`
**Status:** `stable` · **Since:** 
Escape hatch — pass raw arguments straight to agent-device.
**Examples:**
```sh
navig mobile ui exec
```

## `navig mobile ui fill`
**Status:** `stable` · **Since:** 
Fill a text field identified by its snapshot ref.
**Examples:**
```sh
navig mobile ui fill
```

## `navig mobile ui open`
**Status:** `stable` · **Since:** 
Start a UI session bound to an app — snapshot/tap/… then act on it.
**Examples:**
```sh
navig mobile ui open
```

## `navig mobile ui press`
**Status:** `stable` · **Since:** 
Press a device button / key.
**Examples:**
```sh
navig mobile ui press
```

## `navig mobile ui record`
**Status:** `stable` · **Since:** 
Record your manual flow into a replayable .ad script (interactive).
**Examples:**
```sh
navig mobile ui record
```

## `navig mobile ui replay`
**Status:** `stable` · **Since:** 
Replay a recorded .ad script (streams progress).
**Examples:**
```sh
navig mobile ui replay
```

## `navig mobile ui screenshot`
**Status:** `stable` · **Since:** 
Capture the app screen to a PNG (app-context — distinct from the raw
**Examples:**
```sh
navig mobile ui screenshot
```

## `navig mobile ui scroll`
**Status:** `stable` · **Since:** 
Scroll the current view.
**Examples:**
```sh
navig mobile ui scroll
```

## `navig mobile ui snapshot`
**Status:** `stable` · **Since:** 
Accessibility snapshot with element refs (@e1…) — the agent's eyes.
**Examples:**
```sh
navig mobile ui snapshot
```

## `navig mobile ui tap`
**Status:** `stable` · **Since:** 
Tap an element by its snapshot ref.
**Examples:**
```sh
navig mobile ui tap
```

## `navig mobile ui type`
**Status:** `stable` · **Since:** 
Type text into the currently focused field.
**Examples:**
```sh
navig mobile ui type
```

## `navig mobile ui wait`
**Status:** `stable` · **Since:** 
Wait for the UI to settle.
**Examples:**
```sh
navig mobile ui wait
```

## `navig mobile unpack audio`
**Status:** `stable` · **Since:** 
Batch-convert game audio (Wwise, XACT, FMOD, Bink…) to .ogg via vgmstream + ffmpeg.
**Examples:**
```sh
navig mobile unpack audio
```

## `navig mobile unpack carve-ogg`
**Status:** `stable` · **Since:** 
Recover every complete Ogg stream from an unknown archive (CRC-verified).
**Examples:**
```sh
navig mobile unpack carve-ogg
```

## `navig mobile unpack civ6`
**Status:** `stable` · **Since:** 
Civilization VI: rebuild the in-game database (Gathering Storm + DLC) and export readable lists.
**Examples:**
```sh
navig mobile unpack civ6
```

## `navig mobile unpack classes`
**Status:** `stable` · **Since:** 
Turn decoded Unity objects into one readable CSV per script class.
**Examples:**
```sh
navig mobile unpack classes
```

## `navig mobile unpack dds`
**Status:** `stable` · **Since:** 
Convert every .dds texture to .png.
**Examples:**
```sh
navig mobile unpack dds
```

## `navig mobile unpack detect`
**Status:** `stable` · **Since:** 
Name the engine(s) of an unpacked game folder.
**Examples:**
```sh
navig mobile unpack detect
```

## `navig mobile unpack doctor`
**Status:** `stable` · **Since:** 
Show which external tools and optional Python packages are available.
**Examples:**
```sh
navig mobile unpack doctor
```

## `navig mobile unpack game`
**Status:** `stable` · **Since:** 
Pull a game off the device and extract everything (pull + run).
**Examples:**
```sh
navig mobile unpack game
```

## `navig mobile unpack kairo`
**Status:** `stable` · **Since:** 
Unpack Kairosoft .bin archives (auto-detects the title's XOR key).
**Examples:**
```sh
navig mobile unpack kairo
```

## `navig mobile unpack pull`
**Status:** `stable` · **Since:** 
Copy APK + splits, OBB data files and Android/data content of a package off the device.
**Examples:**
```sh
navig mobile unpack pull
```

## `navig mobile unpack run`
**Status:** `stable` · **Since:** 
Detect the engine and extract all data and media from a game folder.
**Examples:**
```sh
navig mobile unpack run
```

## `navig mobile unpack setup`
**Status:** `stable` · **Since:** 
Download vgmstream and Il2CppDumper (GitHub releases) into ~/.navig/tools.
**Examples:**
```sh
navig mobile unpack setup
```

## `navig mobile unpack xnb`
**Status:** `stable` · **Since:** 
Decode .xnb tables → JSON and textures → PNG (Stardew Valley and other XNA games).
**Examples:**
```sh
navig mobile unpack xnb
```

## `navig mobile watch`
**Status:** `stable` · **Since:** 
Watch for device arrival/removal until interrupted (Ctrl-C).
**Examples:**
```sh
navig mobile watch
```

## `navig mode`
**Status:** `stable` · **Since:** 
LLM Mode Router — run without subcommand to show modes.
**Examples:**
```sh
navig mode
```

## `navig mode detect`
**Status:** `stable` · **Since:** 
Test mode detection on a piece of text.
**Examples:**
```sh
navig mode detect
```

## `navig mode doctor`
**Status:** `stable` · **Since:** 
Probe each mode's provider:model with a 1-token call — catches DEAD/EOL
**Examples:**
```sh
navig mode doctor
```

## `navig mode list`
**Status:** `stable` · **Since:** 
List available models per provider, with uncensored status.
**Examples:**
```sh
navig mode list
```

## `navig mode route set`
**Status:** `stable` · **Since:** 
Set provider/model for one hybrid routing tier slot.
**Examples:**
```sh
navig mode route set
```

## `navig mode route show`
**Status:** `stable` · **Since:** 
Show hybrid routing slots (small/big/code).
**Examples:**
```sh
navig mode route show
```

## `navig mode set`
**Status:** `stable` · **Since:** 
Update a mode's provider, model, or parameters.
**Examples:**
```sh
navig mode set
```

## `navig mode show`
**Status:** `stable` · **Since:** 
Display all LLM modes with their configuration.
**Examples:**
```sh
navig mode show
```

## `navig mount`
**Status:** `stable` · **Since:** 
Run navig mount
**Examples:**
```sh
navig mount
```

## `navig mount add`
**Status:** `stable` · **Since:** 
Register a drive junction and create it immediately.
**Examples:**
```sh
navig mount add
```

## `navig mount list`
**Status:** `stable` · **Since:** 
List all registered drive junctions.
**Examples:**
```sh
navig mount list
```

## `navig mount remove`
**Status:** `stable` · **Since:** 
Remove a registered drive junction.
**Examples:**
```sh
navig mount remove
```

## `navig mount sync`
**Status:** `stable` · **Since:** 
Verify all junctions and regenerate mount-drive.ps1 helper script.
**Examples:**
```sh
navig mount sync
```

## `navig mount verify`
**Status:** `stable` · **Since:** 
Verify all registered junctions are alive and update registry status.
**Examples:**
```sh
navig mount verify
```

## `navig move in`
**Status:** `stable` · **Since:** 
Unpack a moved NAVIG here. Whatever was here before is kept as a backup.
**Examples:**
```sh
navig move in
```

## `navig move out`
**Status:** `stable` · **Since:** 
Pack this NAVIG into one encrypted file and stand it down here.
**Examples:**
```sh
navig move out
```

## `navig move status`
**Status:** `stable` · **Since:** 
Has this NAVIG moved to another machine?
**Examples:**
```sh
navig move status
```

## `navig move undo`
**Status:** `stable` · **Since:** 
Bring a moved NAVIG back on this (old) machine.
**Examples:**
```sh
navig move undo
```

## `navig mstore auth`
**Status:** `stable` · **Since:** 
Configure the `msstore` CLI from credentials in the vault (or env).
**Examples:**
```sh
navig mstore auth
```

## `navig mstore configure`
**Status:** `stable` · **Since:** 
Write the Store identity (store/identity.json) for an app -- interactive.
**Examples:**
```sh
navig mstore configure
```

## `navig mstore creds`
**Status:** `stable` · **Since:** 
Show which Store credentials resolve, and from where (values never shown).
**Examples:**
```sh
navig mstore creds
```

## `navig mstore doctor`
**Status:** `stable` · **Since:** 
Preflight the whole Store chain: tooling, credentials, block, and (per app) readiness.
**Examples:**
```sh
navig mstore doctor
```

## `navig mstore info`
**Status:** `stable` · **Since:** 
Explain how `navig mstore` relates to the menu and the block.
**Examples:**
```sh
navig mstore info
```

## `navig mstore open`
**Status:** `stable` · **Since:** 
Open the Microsoft Partner Center dashboard in a browser.
**Examples:**
```sh
navig mstore open
```

## `navig mstore package`
**Status:** `stable` · **Since:** 
Build the MSIX by driving the app's scripts/create-msix.ps1.
**Examples:**
```sh
navig mstore package
```

## `navig mstore publish`
**Status:** `stable` · **Since:** 
Upload + commit a Store submission by applying the msstore-publish block.
**Examples:**
```sh
navig mstore publish
```

## `navig mstore sales`
**Status:** `stable` · **Since:** 
Paid sales (paid apps + add-on purchases) from Partner Center. Read-only.
**Examples:**
```sh
navig mstore sales
```

## `navig mstore show`
**Status:** `stable` · **Since:** 
Show an app's Store identity + latest built MSIX.
**Examples:**
```sh
navig mstore show
```

## `navig mstore status`
**Status:** `stable` · **Since:** 
Show the current submission status via the Microsoft Store Developer CLI.
**Examples:**
```sh
navig mstore status
```

## `navig net speedtest`
**Status:** `stable` · **Since:** 
Measure internet speed with speedtest-cli (Ookla) and/or iperf3.
**Examples:**
```sh
navig net speedtest
```

## `navig node add`
**Status:** `stable` · **Since:** 
Register a new node.
**Examples:**
```sh
navig node add
```

## `navig node list`
**Status:** `stable` · **Since:** 
List known nodes.
**Examples:**
```sh
navig node list
```

## `navig node remove`
**Status:** `stable` · **Since:** 
Remove a registered node.
**Examples:**
```sh
navig node remove
```

## `navig notes add`
**Status:** `stable` · **Since:** 
Add a note — it appears in the browser on the Dock's next sync.
**Examples:**
```sh
navig notes add
```

## `navig notes export`
**Status:** `stable` · **Since:** 
Export notes to a file the Dock's Library can import (JSON) or anyone can read (Markdown).
**Examples:**
```sh
navig notes export
```

## `navig notes list`
**Status:** `stable` · **Since:** 
Your notes, most recently edited first.
**Examples:**
```sh
navig notes list
```

## `navig notes rm`
**Status:** `stable` · **Since:** 
Delete a note everywhere (the Dock moves it to its Trash on the next sync).
**Examples:**
```sh
navig notes rm
```

## `navig notes search`
**Status:** `stable` · **Since:** 
Search every note's title, text, tags, page title and website.
**Examples:**
```sh
navig notes search
```

## `navig notes share pull`
**Status:** `stable` · **Since:** 
Read the Space's .navig/notes/*.md back — ids kept, newer revision wins.
**Examples:**
```sh
navig notes share pull
```

## `navig notes share push`
**Status:** `stable` · **Since:** 
Write every note into the Space's .navig/notes/ as .md files (ids preserved).
**Examples:**
```sh
navig notes share push
```

## `navig notes share status`
**Status:** `stable` · **Since:** 
How many notes, how many files, and which side is ahead.
**Examples:**
```sh
navig notes share status
```

## `navig notes show`
**Status:** `stable` · **Since:** 
One note in full.
**Examples:**
```sh
navig notes show
```

## `navig notes status`
**Status:** `stable` · **Since:** 
How many notes, where they live, and when the Dock last synced.
**Examples:**
```sh
navig notes status
```

## `navig notify pushover call`
**Status:** `stable` · **Since:** 
Telephone you when a Pushover emergency goes unacknowledged.
**Examples:**
```sh
navig notify pushover call
```

## `navig notify pushover emergency`
**Status:** `stable` · **Since:** 
Choose which priorities ring on silent and repeat until acknowledged.
**Examples:**
```sh
navig notify pushover emergency
```

## `navig notify pushover escalate`
**Status:** `stable` · **Since:** 
Choose when an unacknowledged emergency is escalated to SMS.
**Examples:**
```sh
navig notify pushover escalate
```

## `navig notify pushover setup`
**Status:** `stable` · **Since:** 
Validate and save your Pushover keys (to the vault). Prompts for any not given.
**Examples:**
```sh
navig notify pushover setup
```

## `navig notify pushover sound`
**Status:** `stable` · **Since:** 
Pick the Pushover sound NAVIG asks for ('default' = your device setting).
**Examples:**
```sh
navig notify pushover sound
```

## `navig notify pushover status`
**Status:** `stable` · **Since:** 
Show whether Pushover is connected, which types use it, and the emergency rule.
**Examples:**
```sh
navig notify pushover status
```

## `navig notify pushover test`
**Status:** `stable` · **Since:** 
Send a test message to your phone.
**Examples:**
```sh
navig notify pushover test
```

## `navig notify route clear`
**Status:** `stable` · **Since:** 
Send a type back to your DM.
**Examples:**
```sh
navig notify route clear
```

## `navig notify route list`
**Status:** `stable` · **Since:** 
Show where each type lands on Telegram and whether it rings.
**Examples:**
```sh
navig notify route list
```

## `navig notify route set`
**Status:** `stable` · **Since:** 
Route a type to a chat. The bot must already be in it (an admin, for a channel).
**Examples:**
```sh
navig notify route set
```

## `navig notify route sound`
**Status:** `stable` · **Since:** 
Choose whether a type rings or lands silently on Telegram.
**Examples:**
```sh
navig notify route sound
```

## `navig notify route test`
**Status:** `stable` · **Since:** 
Post a test message where this type is routed now.
**Examples:**
```sh
navig notify route test
```

## `navig notify send`
**Status:** `stable` · **Since:** 
Dispatch one notification and report which channels accepted it.
**Examples:**
```sh
navig notify send
```

## `navig notify types`
**Status:** `stable` · **Since:** 
Show every notification type and the channels it currently routes to.
**Examples:**
```sh
navig notify types
```

## `navig obs`
**Status:** `stable` · **Since:** 
Connect target: flags > NAVIG_OBS_* env > navig obs.* config > local OBS file.
**Examples:**
```sh
navig obs
```

## `navig obs clips`
**Status:** `stable` · **Since:** 
Cut the Grid Pigeon's moments from a recording into 9:16 clips, captions burned in.
**Examples:**
```sh
navig obs clips
```

## `navig obs doctor`
**Status:** `stable` · **Since:** 
Local OBS health check: install, websocket config, plugin load errors, missing files.
**Examples:**
```sh
navig obs doctor
```

## `navig obs events`
**Status:** `stable` · **Since:** 
Stream OBS events live (scene changes, output state, media, …).
**Examples:**
```sh
navig obs events
```

## `navig obs marker`
**Status:** `stable` · **Since:** 
Append a marker to the session's recording clock (OBS must be recording).
**Examples:**
```sh
navig obs marker
```

## `navig obs request`
**Status:** `stable` · **Since:** 
Send any obs-websocket v5 request and print its responseData.
**Examples:**
```sh
navig obs request
```

## `navig obs screenshot`
**Status:** `stable` · **Since:** 
Capture a source/scene image from OBS and save it locally (proof shot).
**Examples:**
```sh
navig obs screenshot
```

## `navig obs stats`
**Status:** `stable` · **Since:** 
OBS resource stats: CPU, memory, FPS, render/output frame health.
**Examples:**
```sh
navig obs stats
```

## `navig obs status`
**Status:** `stable` · **Since:** 
Live OBS state: version, current scene, stream/record/replay/vcam/studio.
**Examples:**
```sh
navig obs status
```

## `navig origin`
**Status:** `stable` · **Since:** 
Show where this NAVIG installation came from.
**Examples:**
```sh
navig origin
```

## `navig os account link`
**Status:** `stable` · **Since:** 
Link this computer to a navig.run email; nothing is linked until you click the emailed link.
**Examples:**
```sh
navig os account link
```

## `navig os account unlink`
**Status:** `stable` · **Since:** 
Unlink this computer. Paired devices keep working; new ones need a QR code again.
**Examples:**
```sh
navig os account unlink
```

## `navig os devices revoke`
**Status:** `stable` · **Since:** 
Remove a device now. Its live session ends; it must pair again to come back.
**Examples:**
```sh
navig os devices revoke
```

## `navig os link`
**Status:** `stable` · **Since:** 
Let ANOTHER NAVIG OS use this computer as its brain, through your own relay.
**Examples:**
```sh
navig os link
```

## `navig os open`
**Status:** `stable` · **Since:** 
Open NAVIG OS from this machine's local server — no password to type.
**Examples:**
```sh
navig os open
```

## `navig os pair`
**Status:** `stable` · **Since:** 
Pair a phone or browser to use NAVIG OS here, through your own relay.
**Examples:**
```sh
navig os pair
```

## `navig os requests allow`
**Status:** `stable` · **Since:** 
Let this device use NAVIG OS. Only allow a sign-in you made yourself, just now.
**Examples:**
```sh
navig os requests allow
```

## `navig os requests deny`
**Status:** `stable` · **Since:** 
Refuse this device.
**Examples:**
```sh
navig os requests deny
```

## `navig os serve`
**Status:** `stable` · **Since:** 
Serve NAVIG OS to a browser from this machine.
**Examples:**
```sh
navig os serve
```

## `navig os status`
**Status:** `stable` · **Since:** 
Is the web server up, and is it set up?
**Examples:**
```sh
navig os status
```

## `navig output-style create`
**Status:** `stable` · **Since:** 
Scaffold a new output style file.
**Examples:**
```sh
navig output-style create
```

## `navig output-style list`
**Status:** `stable` · **Since:** 
List all discovered output styles (project, user, and built-in).
**Examples:**
```sh
navig output-style list
```

## `navig output-style off`
**Status:** `stable` · **Since:** 
Clear the active output style (use default AI behaviour).
**Examples:**
```sh
navig output-style off
```

## `navig output-style show`
**Status:** `stable` · **Since:** 
Print a style's prompt text and metadata.
**Examples:**
```sh
navig output-style show
```

## `navig output-style use`
**Status:** `stable` · **Since:** 
Activate an output style by name.
**Examples:**
```sh
navig output-style use
```

## `navig paperwork apply`
**Status:** `stable` · **Since:** 
Execute the plan: copy, verify by hash, then quarantine the source.
**Examples:**
```sh
navig paperwork apply
```

## `navig paperwork echeances`
**Status:** `stable` · **Since:** 
The deadline radar: what is overdue or due soon, from filed letters and recurrent renewals.
**Examples:**
```sh
navig paperwork echeances
```

## `navig paperwork handoff`
**Status:** `stable` · **Since:** 
File the manifest's non-company documents where they belong.
**Examples:**
```sh
navig paperwork handoff
```

## `navig paperwork index`
**Status:** `stable` · **Since:** 
Rebuild the document index and the invoice register from disk.
**Examples:**
```sh
navig paperwork index
```

## `navig paperwork reply`
**Status:** `stable` · **Since:** 
Draft the reply to a filed letter (French, formal) into out/courriers/ — never sent.
**Examples:**
```sh
navig paperwork reply
```

## `navig paperwork review`
**Status:** `stable` · **Since:** 
Show what the plan intends, or just the rows that still need you.
**Examples:**
```sh
navig paperwork review
```

## `navig paperwork scan`
**Status:** `stable` · **Since:** 
Extract, classify and deduplicate documents into a reviewable plan.
**Examples:**
```sh
navig paperwork scan
```

## `navig paperwork undo`
**Status:** `stable` · **Since:** 
Reverse an apply, restoring every source from the quarantine.
**Examples:**
```sh
navig paperwork undo
```

## `navig paths`
**Status:** `stable` · **Since:** 
Show key NAVIG directory paths.
**Examples:**
```sh
navig paths
```

## `navig pentest amass`
**Status:** `stable` · **Since:** 
Attack-surface mapping (OWASP amass). GATED unless --passive.
**Examples:**
```sh
navig pentest amass
```

## `navig pentest binwalk`
**Status:** `stable` · **Since:** 
Firmware image analysis (binwalk). Local — no gate.
**Examples:**
```sh
navig pentest binwalk
```

## `navig pentest cewl`
**Status:** `stable` · **Since:** 
Generate a wordlist from a target site (cewl). Passive read of a single URL.
**Examples:**
```sh
navig pentest cewl
```

## `navig pentest crawl`
**Status:** `stable` · **Since:** 
Web crawler for endpoint discovery (katana). GATED.
**Examples:**
```sh
navig pentest crawl
```

## `navig pentest dns`
**Status:** `stable` · **Since:** 
Resolve DNS records (A/AAAA via the system resolver; others via UDP).
**Examples:**
```sh
navig pentest dns
```

## `navig pentest dnsx`
**Status:** `stable` · **Since:** 
Fast DNS record resolution (dnsx). Passive.
**Examples:**
```sh
navig pentest dnsx
```

## `navig pentest doctor`
**Status:** `stable` · **Since:** 
Tool health: detected engines by category, engagement store, and gate status.
**Examples:**
```sh
navig pentest doctor
```

## `navig pentest engage`
**Status:** `stable` · **Since:** 
Open an AUTHORIZED engagement. Refuses without a written authorization.
**Examples:**
```sh
navig pentest engage
```

## `navig pentest enum4linux`
**Status:** `stable` · **Since:** 
SMB / null-session enumeration (enum4linux). GATED — requires an engagement.
**Examples:**
```sh
navig pentest enum4linux
```

## `navig pentest exif`
**Status:** `stable` · **Since:** 
Read/write file metadata (exiftool). Local — no gate.
**Examples:**
```sh
navig pentest exif
```

## `navig pentest fingerprint`
**Status:** `stable` · **Since:** 
HTTP fingerprint — status, headers, tech-stack guesses, TLS metadata.
**Examples:**
```sh
navig pentest fingerprint
```

## `navig pentest fuzz`
**Status:** `stable` · **Since:** 
Directory/content fuzzing. GATED — requires an authorized engagement.
**Examples:**
```sh
navig pentest fuzz
```

## `navig pentest harvester`
**Status:** `stable` · **Since:** 
OSINT emails, hosts and subdomains (theHarvester). Passive.
**Examples:**
```sh
navig pentest harvester
```

## `navig pentest hashcat`
**Status:** `stable` · **Since:** 
GPU-accelerated hash cracking (hashcat). Local — no network gate.
**Examples:**
```sh
navig pentest hashcat
```

## `navig pentest hashid`
**Status:** `stable` · **Since:** 
Identify a hash type (hashid). Local — no gate.
**Examples:**
```sh
navig pentest hashid
```

## `navig pentest hydra`
**Status:** `stable` · **Since:** 
Network login brute force (hydra). GATED — very loud; confirm scope first.
**Examples:**
```sh
navig pentest hydra
```

## `navig pentest john`
**Status:** `stable` · **Since:** 
Hash cracking (John the Ripper). Local — no network gate.
**Examples:**
```sh
navig pentest john
```

## `navig pentest nikto`
**Status:** `stable` · **Since:** 
Web server vulnerability scan (nikto). GATED.
**Examples:**
```sh
navig pentest nikto
```

## `navig pentest nuclei`
**Status:** `stable` · **Since:** 
Run nuclei template checks. GATED — requires an authorized engagement.
**Examples:**
```sh
navig pentest nuclei
```

## `navig pentest osint`
**Status:** `stable` · **Since:** 
One-pass passive sweep: whois + DNS + certificate-transparency subdomains.
**Examples:**
```sh
navig pentest osint
```

## `navig pentest ports`
**Status:** `stable` · **Since:** 
TCP port scan. GATED — requires an authorized engagement covering the host.
**Examples:**
```sh
navig pentest ports
```

## `navig pentest probe`
**Status:** `stable` · **Since:** 
HTTP liveness probe over hosts (httpx-style). GATED — requires an engagement.
**Examples:**
```sh
navig pentest probe
```

## `navig pentest run`
**Status:** `stable` · **Since:** 
Run any detected Kali tool through the authorization gate.
**Examples:**
```sh
navig pentest run
```

## `navig pentest scope`
**Status:** `stable` · **Since:** 
List engagements, or check whether a target is authorized.
**Examples:**
```sh
navig pentest scope
```

## `navig pentest searchsploit`
**Status:** `stable` · **Since:** 
Exploit-DB search (searchsploit). Passive — local database.
**Examples:**
```sh
navig pentest searchsploit
```

## `navig pentest sherlock`
**Status:** `stable` · **Since:** 
Username OSINT across social networks (sherlock). Passive.
**Examples:**
```sh
navig pentest sherlock
```

## `navig pentest sqlmap`
**Status:** `stable` · **Since:** 
Automatic SQL injection (sqlmap). GATED — intrusive; confirm scope first.
**Examples:**
```sh
navig pentest sqlmap
```

## `navig pentest subdomains`
**Status:** `stable` · **Since:** 
Enumerate subdomains from certificate-transparency logs (passive).
**Examples:**
```sh
navig pentest subdomains
```

## `navig pentest subfinder`
**Status:** `stable` · **Since:** 
Passive subdomain enumeration (subfinder).
**Examples:**
```sh
navig pentest subfinder
```

## `navig pentest surface`
**Status:** `stable` · **Since:** 
Attack-surface mapping: passive subdomains → DNS resolution → optional live probe.
**Examples:**
```sh
navig pentest surface
```

## `navig pentest tools`
**Status:** `stable` · **Since:** 
List the Kali toolchain catalog with detection status and install guidance.
**Examples:**
```sh
navig pentest tools
```

## `navig pentest urls`
**Status:** `stable` · **Since:** 
Discover known URLs from archives/engines (gau + waybackurls). Passive.
**Examples:**
```sh
navig pentest urls
```

## `navig pentest whois`
**Status:** `stable` · **Since:** 
RDAP lookup (the modern whois) — public registry records.
**Examples:**
```sh
navig pentest whois
```

## `navig pentest wordlist`
**Status:** `stable` · **Since:** 
Find installed wordlists, or generate one from a target site (cewl).
**Examples:**
```sh
navig pentest wordlist
```

## `navig pentest wpscan`
**Status:** `stable` · **Since:** 
WordPress security scan (wpscan). GATED.
**Examples:**
```sh
navig pentest wpscan
```

## `navig perplexity ask`
**Status:** `stable` · **Since:** 
Delegate any task; model, reasoning mode and conversation are chosen for you.
**Examples:**
```sh
navig perplexity ask
```

## `navig perplexity attach`
**Status:** `stable` · **Since:** 
Launch the app with a debug port (or report the port it already has).
**Examples:**
```sh
navig perplexity attach
```

## `navig perplexity model`
**Status:** `stable` · **Since:** 
Show or change the model the composer will use (explicit, not routed).
**Examples:**
```sh
navig perplexity model
```

## `navig perplexity models`
**Status:** `stable` · **Since:** 
What this account can select, and what each model is good at.
**Examples:**
```sh
navig perplexity models
```

## `navig perplexity new`
**Status:** `stable` · **Since:** 
Start a fresh conversation in the app (the current one stays in the sidebar).
**Examples:**
```sh
navig perplexity new
```

## `navig perplexity open`
**Status:** `stable` · **Since:** 
Open a conversation from the app's sidebar by index.
**Examples:**
```sh
navig perplexity open
```

## `navig perplexity selftest`
**Status:** `stable` · **Since:** 
Verify the whole path: port, window, composer, models, modes, registry.
**Examples:**
```sh
navig perplexity selftest
```

## `navig perplexity status`
**Status:** `stable` · **Since:** 
Is the app attachable, what thread is open, which model, and is a composer busy?
**Examples:**
```sh
navig perplexity status
```

## `navig perplexity threads archive`
**Status:** `stable` · **Since:** 
Hide a conversation from automatic selection (its remote history is untouched).
**Examples:**
```sh
navig perplexity threads archive
```

## `navig perplexity threads list`
**Status:** `stable` · **Since:** 
Conversations this machine has delegated, newest first.
**Examples:**
```sh
navig perplexity threads list
```

## `navig perplexity threads resume`
**Status:** `stable` · **Since:** 
Reopen a registry thread in the app (and mark it active).
**Examples:**
```sh
navig perplexity threads resume
```

## `navig perplexity threads show`
**Status:** `stable` · **Since:** 
One conversation's identity, state and last result reference.
**Examples:**
```sh
navig perplexity threads show
```

## `navig perplexity threads sidebar`
**Status:** `stable` · **Since:** 
What the app's own sidebar currently shows (not the registry).
**Examples:**
```sh
navig perplexity threads sidebar
```

## `navig perplexity write`
**Status:** `stable` · **Since:** 
Write something publication-shaped: an article, launch copy, a report.
**Examples:**
```sh
navig perplexity write
```

## `navig plan list`
**Status:** `stable` · **Since:** 
List all plans in the current workspace.
**Examples:**
```sh
navig plan list
```

## `navig plan new`
**Status:** `stable` · **Since:** 
Create a new AI-guided plan from a goal statement.
**Examples:**
```sh
navig plan new
```

## `navig plan run`
**Status:** `stable` · **Since:** 
Execute a plan file via the NAVIG agent (`navig agent plan`).
**Examples:**
```sh
navig plan run
```

## `navig plan show`
**Status:** `stable` · **Since:** 
Print a stored plan.
**Examples:**
```sh
navig plan show
```

## `navig plans`
**Status:** `stable` · **Since:** 
Run navig plans
**Examples:**
```sh
navig plans
```

## `navig plans add`
**Status:** `stable` · **Since:** 
Add a goal to a plan file (defaults to DEV_PLAN.md).
**Examples:**
```sh
navig plans add
```

## `navig plans briefing`
**Status:** `stable` · **Since:** 
Print a daily briefing of active work across all spaces.
**Examples:**
```sh
navig plans briefing
```

## `navig plans next`
**Status:** `stable` · **Since:** 
Show the best next action across spaces (or a specific space).
**Examples:**
```sh
navig plans next
```

## `navig plans status`
**Status:** `stable` · **Since:** 
Show current progress by resolved space.
**Examples:**
```sh
navig plans status
```

## `navig plans summary`
**Status:** `stable` · **Since:** 
Show cross-space phase rollup table.
**Examples:**
```sh
navig plans summary
```

## `navig plans sync`
**Status:** `stable` · **Since:** 
Route .navig/plans/inbox files into the right space plans.
**Examples:**
```sh
navig plans sync
```

## `navig plans update`
**Status:** `stable` · **Since:** 
Recompute a plan file's completion from its checklist.
**Examples:**
```sh
navig plans update
```

## `navig plugin`
**Status:** `stable` · **Since:** 
Run navig plugin
**Examples:**
```sh
navig plugin
```

## `navig plugin inspect`
**Status:** `stable` · **Since:** 
Inspect a plugin/package (CC bundle + NAVIG personas/formations/spaces).
**Examples:**
```sh
navig plugin inspect
```

## `navig plugin list`
**Status:** `stable` · **Since:** 
List installed plugins across every format (package, pip, legacy).
**Examples:**
```sh
navig plugin list
```

## `navig plugin marketplace add`
**Status:** `stable` · **Since:** 
Register a marketplace (validates its marketplace.json first).
**Examples:**
```sh
navig plugin marketplace add
```

## `navig plugin marketplace list`
**Status:** `stable` · **Since:** 
List registered marketplaces and the plugins they advertise.
**Examples:**
```sh
navig plugin marketplace list
```

## `navig plugin marketplace refresh`
**Status:** `stable` · **Since:** 
Re-fetch live catalogs so the Store's AVAILABLE rows aren't stale.
**Examples:**
```sh
navig plugin marketplace refresh
```

## `navig plugin marketplace remove`
**Status:** `stable` · **Since:** 
Unregister a marketplace.
**Examples:**
```sh
navig plugin marketplace remove
```

## `navig plugin new`
**Status:** `stable` · **Since:** 
Scaffold a new plugin skeleton into ~/.navig/plugins/<name>/.
**Examples:**
```sh
navig plugin new
```

## `navig plugin show`
**Status:** `stable` · **Since:** 
Show detailed information about an installed plugin.
**Examples:**
```sh
navig plugin show
```

## `navig portable create`
**Status:** `stable` · **Since:** 
Create a portable NAVIG bundle.
**Examples:**
```sh
navig portable create
```

## `navig portable validate`
**Status:** `stable` · **Since:** 
Validate a portable NAVIG bundle.
**Examples:**
```sh
navig portable validate
```

## `navig preset default`
**Status:** `stable` · **Since:** 
Show or set the preset a tool starts with.
**Examples:**
```sh
navig preset default
```

## `navig preset duplicate`
**Status:** `stable` · **Since:** 
Copy a preset into a new one you own.
**Examples:**
```sh
navig preset duplicate
```

## `navig preset edit`
**Status:** `stable` · **Since:** 
Edit a preset. Editing a builtin saves your own override (undo with `reset`).
**Examples:**
```sh
navig preset edit
```

## `navig preset export`
**Status:** `stable` · **Since:** 
Export a preset to share. Secrets and local paths are removed; files are not included.
**Examples:**
```sh
navig preset export
```

## `navig preset fav`
**Status:** `stable` · **Since:** 
Star (or --off unstar) a preset.
**Examples:**
```sh
navig preset fav
```

## `navig preset import`
**Status:** `stable` · **Since:** 
Import a shared preset. It stays marked unreviewed until you run `navig presets trust`.
**Examples:**
```sh
navig preset import
```

## `navig preset list`
**Status:** `stable` · **Since:** 
List presets (favorites first).
**Examples:**
```sh
navig preset list
```

## `navig preset new`
**Status:** `stable` · **Since:** 
Create a preset (from flags or a file).
**Examples:**
```sh
navig preset new
```

## `navig preset rename`
**Status:** `stable` · **Since:** 
Rename a preset (the id stays stable).
**Examples:**
```sh
navig preset rename
```

## `navig preset reset`
**Status:** `stable` · **Since:** 
Discard your edits to a builtin preset.
**Examples:**
```sh
navig preset reset
```

## `navig preset resolve`
**Status:** `stable` · **Since:** 
Show exactly what a combination of presets sends to a tool — nothing is generated.
**Examples:**
```sh
navig preset resolve
```

## `navig preset rm`
**Status:** `stable` · **Since:** 
Delete a preset you created (builtins can be reset, not deleted).
**Examples:**
```sh
navig preset rm
```

## `navig preset show`
**Status:** `stable` · **Since:** 
Show one preset in full.
**Examples:**
```sh
navig preset show
```

## `navig preset trust`
**Status:** `stable` · **Since:** 
Mark an imported preset as reviewed, so its instructions apply unquoted.
**Examples:**
```sh
navig preset trust
```

## `navig presets default`
**Status:** `stable` · **Since:** 
Show or set the preset a tool starts with.
**Examples:**
```sh
navig presets default
```

## `navig presets duplicate`
**Status:** `stable` · **Since:** 
Copy a preset into a new one you own.
**Examples:**
```sh
navig presets duplicate
```

## `navig presets edit`
**Status:** `stable` · **Since:** 
Edit a preset. Editing a builtin saves your own override (undo with `reset`).
**Examples:**
```sh
navig presets edit
```

## `navig presets export`
**Status:** `stable` · **Since:** 
Export a preset to share. Secrets and local paths are removed; files are not included.
**Examples:**
```sh
navig presets export
```

## `navig presets fav`
**Status:** `stable` · **Since:** 
Star (or --off unstar) a preset.
**Examples:**
```sh
navig presets fav
```

## `navig presets import`
**Status:** `stable` · **Since:** 
Import a shared preset. It stays marked unreviewed until you run `navig presets trust`.
**Examples:**
```sh
navig presets import
```

## `navig presets list`
**Status:** `stable` · **Since:** 
List presets (favorites first).
**Examples:**
```sh
navig presets list
```

## `navig presets new`
**Status:** `stable` · **Since:** 
Create a preset (from flags or a file).
**Examples:**
```sh
navig presets new
```

## `navig presets rename`
**Status:** `stable` · **Since:** 
Rename a preset (the id stays stable).
**Examples:**
```sh
navig presets rename
```

## `navig presets reset`
**Status:** `stable` · **Since:** 
Discard your edits to a builtin preset.
**Examples:**
```sh
navig presets reset
```

## `navig presets resolve`
**Status:** `stable` · **Since:** 
Show exactly what a combination of presets sends to a tool — nothing is generated.
**Examples:**
```sh
navig presets resolve
```

## `navig presets rm`
**Status:** `stable` · **Since:** 
Delete a preset you created (builtins can be reset, not deleted).
**Examples:**
```sh
navig presets rm
```

## `navig presets show`
**Status:** `stable` · **Since:** 
Show one preset in full.
**Examples:**
```sh
navig presets show
```

## `navig presets trust`
**Status:** `stable` · **Since:** 
Mark an imported preset as reviewed, so its instructions apply unquoted.
**Examples:**
```sh
navig presets trust
```

## `navig proactive setup`
**Status:** `stable` · **Since:** 
Configure proactive assistance sources interactively.
**Examples:**
```sh
navig proactive setup
```

## `navig proactive start`
**Status:** `stable` · **Since:** 
Start the proactive agent loop.
**Examples:**
```sh
navig proactive start
```

## `navig proactive status`
**Status:** `stable` · **Since:** 
Show proactive agent status and configured sources.
**Examples:**
```sh
navig proactive status
```

## `navig proactive test`
**Status:** `stable` · **Since:** 
Test configured proactive sources.
**Examples:**
```sh
navig proactive test
```

## `navig procs`
**Status:** `stable` · **Since:** 
Show which project each process belongs to, with memory and owner (read-only).
**Examples:**
```sh
navig procs
```

## `navig profile list`
**Status:** `stable` · **Since:** 
Show all available operating profiles.
**Examples:**
```sh
navig profile list
```

## `navig profile pin-clear`
**Status:** `stable` · **Since:** 
Remove the stored PIN (disables PIN protection for all profiles).
**Examples:**
```sh
navig profile pin-clear
```

## `navig profile pin-set`
**Status:** `stable` · **Since:** 
Set or change the PIN that protects operator/architect profiles.
**Examples:**
```sh
navig profile pin-set
```

## `navig profile set`
**Status:** `stable` · **Since:** 
Switch to a different operating profile.
**Examples:**
```sh
navig profile set
```

## `navig profile show`
**Status:** `stable` · **Since:** 
Show the current operating profile and all its settings.
**Examples:**
```sh
navig profile show
```

## `navig project add`
**Status:** `stable` · **Since:** 
Register a folder as a project (reads its .navig/ immediately).
**Examples:**
```sh
navig project add
```

## `navig project analyze`
**Status:** `stable` · **Since:** 
Re-analyse a project with AI — a brief, a doc update, or new todos. Proposes; you accept.
**Examples:**
```sh
navig project analyze
```

## `navig project context`
**Status:** `stable` · **Since:** 
The project's context pack — what an agent is told about it.
**Examples:**
```sh
navig project context
```

## `navig project fabric`
**Status:** `stable` · **Since:** 
The Idea Fabric: run an AI pattern over the project — brainstorm, harvest, expand, critique, plan.
**Examples:**
```sh
navig project fabric
```

## `navig project ideas`
**Status:** `stable` · **Since:** 
Ideas not planned yet — by stage (IDEAS.md). Capture one with --add.
**Examples:**
```sh
navig project ideas
```

## `navig project list`
**Status:** `stable` · **Since:** 
Every project, pinned first, then most recently active.
**Examples:**
```sh
navig project list
```

## `navig project logs`
**Status:** `stable` · **Since:** 
Show a run's output.
**Examples:**
```sh
navig project logs
```

## `navig project ps`
**Status:** `stable` · **Since:** 
Project scripts running now (on this machine).
**Examples:**
```sh
navig project ps
```

## `navig project run`
**Status:** `stable` · **Since:** 
Start a project script in the background (it keeps running if navig restarts).
**Examples:**
```sh
navig project run
```

## `navig project scan`
**Status:** `stable` · **Since:** 
Find projects one level inside a folder.
**Examples:**
```sh
navig project scan
```

## `navig project scripts`
**Status:** `stable` · **Since:** 
Every runnable script — root and workspace packages.
**Examples:**
```sh
navig project scripts
```

## `navig project show`
**Status:** `stable` · **Since:** 
One project: phase, next action, todos, scripts, git.
**Examples:**
```sh
navig project show
```

## `navig project stop`
**Status:** `stable` · **Since:** 
Stop a run and every process it started.
**Examples:**
```sh
navig project stop
```

## `navig project todos`
**Status:** `stable` · **Since:** 
The checkboxes across the project's docs, as one list.
**Examples:**
```sh
navig project todos
```

## `navig prompts backup`
**Status:** `stable` · **Since:** 
Write the whole library out as Markdown files (re-importable).
**Examples:**
```sh
navig prompts backup
```

## `navig prompts categories`
**Status:** `stable` · **Since:** 
Domains, categories, workflows and tags in the library, with counts.
**Examples:**
```sh
navig prompts categories
```

## `navig prompts edit`
**Status:** `stable` · **Since:** 
Edit a library prompt's text in your editor (or open a prompt file).
**Examples:**
```sh
navig prompts edit
```

## `navig prompts export`
**Status:** `stable` · **Since:** 
Export a prompt as a Claude slash command (``.claude/commands/<id>.md``).
**Examples:**
```sh
navig prompts export
```

## `navig prompts fav`
**Status:** `stable` · **Since:** 
Star a prompt (favourites show first in NAVIG OS and Telegram /prompt).
**Examples:**
```sh
navig prompts fav
```

## `navig prompts import`
**Status:** `stable` · **Since:** 
Import (or re-sync) a folder of prompts. Safe to run again — your edits are kept.
**Examples:**
```sh
navig prompts import
```

## `navig prompts list`
**Status:** `stable` · **Since:** 
List your prompt library, then file prompts from every root.
**Examples:**
```sh
navig prompts list
```

## `navig prompts new`
**Status:** `stable` · **Since:** 
Add a prompt to the library.
**Examples:**
```sh
navig prompts new
```

## `navig prompts remove`
**Status:** `stable` · **Since:** 
Delete a prompt (library prompts are archived unless --purge).
**Examples:**
```sh
navig prompts remove
```

## `navig prompts restore`
**Status:** `stable` · **Since:** 
Bring back an archived library prompt.
**Examples:**
```sh
navig prompts restore
```

## `navig prompts search`
**Status:** `stable` · **Since:** 
Full-text search of the prompt library.
**Examples:**
```sh
navig prompts search
```

## `navig prompts show`
**Status:** `stable` · **Since:** 
Show a prompt from the library (or a saved prompt file).
**Examples:**
```sh
navig prompts show
```

## `navig prompts use`
**Status:** `stable` · **Since:** 
Fill a prompt's placeholders, copy it, and print it.
**Examples:**
```sh
navig prompts use
```

## `navig queue`
**Status:** `stable` · **Since:** 
Task queue - run without subcommand to list tasks.
**Examples:**
```sh
navig queue
```

## `navig queue add`
**Status:** `stable` · **Since:** 
Add a task to the queue.
**Examples:**
```sh
navig queue add
```

## `navig queue cancel`
**Status:** `stable` · **Since:** 
Cancel a pending task.
**Examples:**
```sh
navig queue cancel
```

## `navig queue list`
**Status:** `stable` · **Since:** 
List queued tasks.
**Examples:**
```sh
navig queue list
```

## `navig queue show`
**Status:** `stable` · **Since:** 
Show task details.
**Examples:**
```sh
navig queue show
```

## `navig queue stats`
**Status:** `stable` · **Since:** 
Show queue statistics.
**Examples:**
```sh
navig queue stats
```

## `navig quick`
**Status:** `stable` · **Since:** 
Quick actions - shortcuts for frequent operations.
**Examples:**
```sh
navig quick
```

## `navig quick add`
**Status:** `stable` · **Since:** 
Add a quick action shortcut.
**Examples:**
```sh
navig quick add
```

## `navig quick list`
**Status:** `stable` · **Since:** 
List all quick actions.
**Examples:**
```sh
navig quick list
```

## `navig quick remove`
**Status:** `stable` · **Since:** 
Remove a quick action.
**Examples:**
```sh
navig quick remove
```

## `navig quick run`
**Status:** `stable` · **Since:** 
Run a quick action by name.
**Examples:**
```sh
navig quick run
```

## `navig quickstart`
**Status:** `stable` · **Since:** 
Minimal onboarding to get NAVIG usable in under 5 minutes.
**Examples:**
```sh
navig quickstart
```

## `navig radar add`
**Status:** `stable` · **Since:** 
Add a keyword to radar.
**Examples:**
```sh
navig radar add
```

## `navig radar list`
**Status:** `stable` · **Since:** 
List active radar watches.
**Examples:**
```sh
navig radar list
```

## `navig recall ask`
**Status:** `stable` · **Since:** 
Have I asked this before? Earlier questions, the answers that followed, and decisions.
**Examples:**
```sh
navig recall ask
```

## `navig recall decisions`
**Status:** `stable` · **Since:** 
Decisions — your answers to structured questions, and audited decisions with reversals.
**Examples:**
```sh
navig recall decisions
```

## `navig recall disable`
**Status:** `stable` · **Since:** 
Stop Recall from reading transcripts (the existing bank is kept).
**Examples:**
```sh
navig recall disable
```

## `navig recall enable`
**Status:** `stable` · **Since:** 
Allow Recall to read local transcripts (Claude Code, Codex, session audits).
**Examples:**
```sh
navig recall enable
```

## `navig recall eval`
**Status:** `stable` · **Since:** 
Measure extraction against the session audit: recall of the audit's features/ideas per
**Examples:**
```sh
navig recall eval
```

## `navig recall excerpt`
**Status:** `stable` · **Since:** 
The original conversation around one turn — the citation behind a record.
**Examples:**
```sh
navig recall excerpt
```

## `navig recall extract`
**Status:** `stable` · **Since:** 
Extract ideas, decisions, terms, plans, questions, findings and risks with the model —
**Examples:**
```sh
navig recall extract
```

## `navig recall features`
**Status:** `stable` · **Since:** 
Features from the session audit, with their verified implementation state.
**Examples:**
```sh
navig recall features
```

## `navig recall findings`
**Status:** `stable` · **Since:** 
Research reports produced by agents during your sessions.
**Examples:**
```sh
navig recall findings
```

## `navig recall forgotten`
**Status:** `stable` · **Since:** 
Forgotten ideas — not rejected, no implementation found, oldest first.
**Examples:**
```sh
navig recall forgotten
```

## `navig recall ideas`
**Status:** `stable` · **Since:** 
Ideas — including the audit's recovered, never-built ones.
**Examples:**
```sh
navig recall ideas
```

## `navig recall ingest`
**Status:** `stable` · **Since:** 
Read new or changed conversations into the bank (incremental; safe to re-run).
**Examples:**
```sh
navig recall ingest
```

## `navig recall link`
**Status:** `stable` · **Since:** 
Link a session to a file, plan or commit by hand (kept across reindex).
**Examples:**
```sh
navig recall link
```

## `navig recall plans`
**Status:** `stable` · **Since:** 
Plans proposed in sessions, with the verdict you gave each one.
**Examples:**
```sh
navig recall plans
```

## `navig recall questions`
**Status:** `stable` · **Since:** 
Open questions and recorded conflicts.
**Examples:**
```sh
navig recall questions
```

## `navig recall reindex`
**Status:** `stable` · **Since:** 
Rebuild the search index from the archive and the vault (both are left untouched).
**Examples:**
```sh
navig recall reindex
```

## `navig recall review`
**Status:** `stable` · **Since:** 
Items that need a human: possible duplicates and model items without a verifiable quote.
**Examples:**
```sh
navig recall review
```

## `navig recall search`
**Status:** `stable` · **Since:** 
Find records (and, with --raw, the conversation turns) that match.
**Examples:**
```sh
navig recall search
```

## `navig recall sessions`
**Status:** `stable` · **Since:** 
Claude Code sessions linked to a file, a plan or a commit — or every session of a project.
**Examples:**
```sh
navig recall sessions
```

## `navig recall show`
**Status:** `stable` · **Since:** 
One record: status, summary, body, evidence with citations, timeline, links.
**Examples:**
```sh
navig recall show
```

## `navig recall sources`
**Status:** `stable` · **Since:** 
Conversation sources Recall has read, newest first.
**Examples:**
```sh
navig recall sources
```

## `navig recall status`
**Status:** `stable` · **Since:** 
What is in the bank, where it lives, and whether reading is enabled.
**Examples:**
```sh
navig recall status
```

## `navig recall tables`
**Status:** `stable` · **Since:** 
Tables from past conversations, kept verbatim (show one to copy it).
**Examples:**
```sh
navig recall tables
```

## `navig recall terms`
**Status:** `stable` · **Since:** 
Vocabulary — names and terms coined, adopted or rejected (with the reason).
**Examples:**
```sh
navig recall terms
```

## `navig recall unlink`
**Status:** `stable` · **Since:** 
Remove every link between a session and a target (recorded, so reindex keeps it removed).
**Examples:**
```sh
navig recall unlink
```

## `navig recall verify`
**Status:** `stable` · **Since:** 
Check ideas/plans against the project's code: likely · partial · not_found · unknown.
**Examples:**
```sh
navig recall verify
```

## `navig receipt list`
**Status:** `stable` · **Since:** 
List stored receipts, newest first.
**Examples:**
```sh
navig receipt list
```

## `navig receipt record`
**Status:** `stable` · **Since:** 
Record a signed receipt — refuses to persist one whose signature failed.
**Examples:**
```sh
navig receipt record
```

## `navig receipt verify`
**Status:** `stable` · **Since:** 
Verify a stored receipt's device signature — the verdict's tamper check.
**Examples:**
```sh
navig receipt verify
```

## `navig replay list`
**Status:** `stable` · **Since:** 
List recorded sessions available for replay.
**Examples:**
```sh
navig replay list
```

## `navig replay run`
**Status:** `stable` · **Since:** 
Replay a recorded session.
**Examples:**
```sh
navig replay run
```

## `navig repo conflicts`
**Status:** `stable` · **Since:** 
Simulate merges between every pair of worktrees; list conflicting files.
**Examples:**
```sh
navig repo conflicts
```

## `navig repo gates`
**Status:** `stable` · **Since:** 
Show every CI gate on this machine: lanes, runners, bottleneck, spend — or PR checks with --prs.
**Examples:**
```sh
navig repo gates
```

## `navig repo guard install`
**Status:** `stable` · **Since:** 
Install the guard hooks + Claude Code wiring into a git repo.
**Examples:**
```sh
navig repo guard install
```

## `navig repo guard status`
**Status:** `stable` · **Since:** 
Show whether the guard is installed and wired in a repo.
**Examples:**
```sh
navig repo guard status
```

## `navig repo guard uninstall`
**Status:** `stable` · **Since:** 
Remove the guard wiring (and hook scripts, when unmodified) from a repo.
**Examples:**
```sh
navig repo guard uninstall
```

## `navig repo land`
**Status:** `stable` · **Since:** 
Finish a MERGED branch completely: delete it locally, on origin, and its worktree.
**Examples:**
```sh
navig repo land
```

## `navig repo lock check`
**Status:** `stable` · **Since:** 
Exit 1 when ANOTHER live session holds the main checkout. Built for the pre-commit hook.
**Examples:**
```sh
navig repo lock check
```

## `navig repo lock release`
**Status:** `stable` · **Since:** 
Release the agent lock.
**Examples:**
```sh
navig repo lock release
```

## `navig repo lock status`
**Status:** `stable` · **Since:** 
Show who holds the main-checkout agent lock.
**Examples:**
```sh
navig repo lock status
```

## `navig repo new`
**Status:** `stable` · **Since:** 
Create an isolated worktree under .dev/worktrees/ for a parallel session.
**Examples:**
```sh
navig repo new
```

## `navig repo prune`
**Status:** `stable` · **Since:** 
Remove orphaned .dev/worktrees dirs that git no longer tracks.
**Examples:**
```sh
navig repo prune
```

## `navig repo remove`
**Status:** `stable` · **Since:** 
Reliably remove a .dev/worktrees/<slug> worktree — unregister + delete.
**Examples:**
```sh
navig repo remove
```

## `navig repo stale`
**Status:** `stable` · **Since:** 
Report leftover agent work: worktrees, unmerged branches, stashes, lock.
**Examples:**
```sh
navig repo stale
```

## `navig repo sweep`
**Status:** `stable` · **Since:** 
Delete local branches whose work is provably already on the default branch.
**Examples:**
```sh
navig repo sweep
```

## `navig repo sync`
**Status:** `stable` · **Since:** 
Return the MAIN checkout to the default branch and fast-forward it.
**Examples:**
```sh
navig repo sync
```

## `navig run`
**Status:** `stable` · **Since:** 
Execute arbitrary shell command on remote server.
**Examples:**
```sh
navig run
```

## `navig scaffold`
**Status:** `stable` · **Since:** 
Scaffold management - run without subcommand for help.
**Examples:**
```sh
navig scaffold
```

## `navig scaffold apply`
**Status:** `stable` · **Since:** 
Generate files/directories from a template.
**Examples:**
```sh
navig scaffold apply
```

## `navig scaffold validate`
**Status:** `stable` · **Since:** 
Validate a template file syntax.
**Examples:**
```sh
navig scaffold validate
```

## `navig script edit`
**Status:** `stable` · **Since:** 
Open script in editor.
**Examples:**
```sh
navig script edit
```

## `navig script list`
**Status:** `stable` · **Since:** 
List available scripts.
**Examples:**
```sh
navig script list
```

## `navig script new`
**Status:** `stable` · **Since:** 
Create a new manual script.
**Examples:**
```sh
navig script new
```

## `navig script run`
**Status:** `stable` · **Since:** 
Run a Python script.
**Examples:**
```sh
navig script run
```

## `navig search`
**Status:** `stable` · **Since:** 
Search the web for information.
**Examples:**
```sh
navig search
```

## `navig security`
**Status:** `stable` · **Since:** 
Local security operations — run without a subcommand for the help.
**Examples:**
```sh
navig security
```

## `navig security migrate-host-secrets`
**Status:** `stable` · **Since:** 
Move plaintext host passwords into the vault (dry run unless --apply).
**Examples:**
```sh
navig security migrate-host-secrets
```

## `navig server`
**Status:** `stable` · **Since:** 
Server management - DEPRECATED, use 'navig host'.
**Examples:**
```sh
navig server
```

## `navig server list`
**Status:** `stable` · **Since:** 
List server resources (vhosts, containers, etc.).
**Examples:**
```sh
navig server list
```

## `navig server run`
**Status:** `stable` · **Since:** 
Run server operations.
**Examples:**
```sh
navig server run
```

## `navig server show`
**Status:** `stable` · **Since:** 
Show server details.
**Examples:**
```sh
navig server show
```

## `navig server test`
**Status:** `stable` · **Since:** 
Test server configuration.
**Examples:**
```sh
navig server test
```

## `navig server-template`
**Status:** `stable` · **Since:** 
Per-server template configuration.
**Examples:**
```sh
navig server-template
```

## `navig server-template disable`
**Status:** `stable` · **Since:** 
Disable an template for a specific server.
**Examples:**
```sh
navig server-template disable
```

## `navig server-template enable`
**Status:** `stable` · **Since:** 
Enable an template for a specific server.
**Examples:**
```sh
navig server-template enable
```

## `navig server-template init`
**Status:** `stable` · **Since:** 
Manually initialize an template for a server.
**Examples:**
```sh
navig server-template init
```

## `navig server-template list`
**Status:** `stable` · **Since:** 
List template configurations for a server.
**Examples:**
```sh
navig server-template list
```

## `navig server-template set`
**Status:** `stable` · **Since:** 
Set a custom value for a server template configuration.
**Examples:**
```sh
navig server-template set
```

## `navig server-template show`
**Status:** `stable` · **Since:** 
Show merged configuration for a server template.
**Examples:**
```sh
navig server-template show
```

## `navig server-template sync`
**Status:** `stable` · **Since:** 
Sync template configuration from template.
**Examples:**
```sh
navig server-template sync
```

## `navig service config`
**Status:** `stable` · **Since:** 
Show or manage daemon configuration.
**Examples:**
```sh
navig service config
```

## `navig service install`
**Status:** `stable` · **Since:** 
Install NAVIG daemon as a persistent service.
**Examples:**
```sh
navig service install
```

## `navig service logs`
**Status:** `stable` · **Since:** 
Show NAVIG daemon logs.
**Examples:**
```sh
navig service logs
```

## `navig service pids`
**Status:** `stable` · **Since:** 
The pids an external process sweeper must spare — every navig-owned tree.
**Examples:**
```sh
navig service pids
```

## `navig service restart`
**Status:** `stable` · **Since:** 
Restart the NAVIG daemon.
**Examples:**
```sh
navig service restart
```

## `navig service start`
**Status:** `stable` · **Since:** 
Start the NAVIG daemon.
**Examples:**
```sh
navig service start
```

## `navig service status`
**Status:** `stable` · **Since:** 
Show NAVIG daemon status.
**Examples:**
```sh
navig service status
```

## `navig service stop`
**Status:** `stable` · **Since:** 
Stop the running NAVIG daemon.
**Examples:**
```sh
navig service stop
```

## `navig service uninstall`
**Status:** `stable` · **Since:** 
Remove NAVIG daemon service.
**Examples:**
```sh
navig service uninstall
```

## `navig settings`
**Status:** `stable` · **Since:** 
View or edit layered NAVIG settings (VSCode-style, 5 layers).
**Examples:**
```sh
navig settings
```

## `navig signals add`
**Status:** `stable` · **Since:** 
Create a signed ingest source.
**Examples:**
```sh
navig signals add
```

## `navig signals connect`
**Status:** `stable` · **Since:** 
Create the Stripe webhook endpoint for a project and store its secret — one step.
**Examples:**
```sh
navig signals connect
```

## `navig signals edit`
**Status:** `stable` · **Since:** 
Change where a source routes, its priority, or its templates (URL and secret stay).
**Examples:**
```sh
navig signals edit
```

## `navig signals list`
**Status:** `stable` · **Since:** 
List signal sources (secrets masked) with hit counts.
**Examples:**
```sh
navig signals list
```

## `navig signals presets`
**Status:** `stable` · **Since:** 
List the built-in event presets (polished title/body shapes).
**Examples:**
```sh
navig signals presets
```

## `navig signals remove`
**Status:** `stable` · **Since:** 
Delete a signal source (its URL stops accepting events immediately).
**Examples:**
```sh
navig signals remove
```

## `navig signals rotate`
**Status:** `stable` · **Since:** 
Replace a source's secret (the old one stops working immediately).
**Examples:**
```sh
navig signals rotate
```

## `navig signals test`
**Status:** `stable` · **Since:** 
Sign a sample event and POST it to the local gateway to prove the round-trip.
**Examples:**
```sh
navig signals test
```

## `navig skill`
**Status:** `stable` · **Since:** 
Skills management - run without subcommand for help.
**Examples:**
```sh
navig skill
```

## `navig skill auto`
**Status:** `stable` · **Since:** 
Scan the project, detect its stack, and auto-install matching agent skills.
**Examples:**
```sh
navig skill auto
```

## `navig skill benchmark`
**Status:** `stable` · **Since:** 
Run the behavioral eval N times across a skill library and gate on mean(with) ≥ mean(without).
**Examples:**
```sh
navig skill benchmark
```

## `navig skill create`
**Status:** `stable` · **Since:** 
Scaffold a new Claude-compatible SKILL.md skill.
**Examples:**
```sh
navig skill create
```

## `navig skill distill`
**Status:** `stable` · **Since:** 
Distill a slice of the operations ledger into a draft SKILL.md.
**Examples:**
```sh
navig skill distill
```

## `navig skill eval`
**Status:** `stable` · **Since:** 
Evaluate a skill against its ``evals.json`` (the guide's 'planned standard').
**Examples:**
```sh
navig skill eval
```

## `navig skill export`
**Status:** `stable` · **Since:** 
Export a NAVIG skill in another agent's format so it can consume it.
**Examples:**
```sh
navig skill export
```

## `navig skill install`
**Status:** `stable` · **Since:** 
Install a skill — from another agent (Claude/Hermes/Codex/local) or the community registry.
**Examples:**
```sh
navig skill install
```

## `navig skill lint`
**Status:** `stable` · **Since:** 
Validate a SKILL.md against the NAVIG authoring standard (docs/authoring-guide.md).
**Examples:**
```sh
navig skill lint
```

## `navig skill list`
**Status:** `stable` · **Since:** 
List available AI skills.
**Examples:**
```sh
navig skill list
```

## `navig skill new`
**Status:** `stable` · **Since:** 
Scaffold a new Claude-compatible SKILL.md skill.
**Examples:**
```sh
navig skill new
```

## `navig skill run`
**Status:** `stable` · **Since:** 
Run a skill command.
**Examples:**
```sh
navig skill run
```

## `navig skill show`
**Status:** `stable` · **Since:** 
Show detailed skill information (commands, examples, metadata).
**Examples:**
```sh
navig skill show
```

## `navig skill suggest`
**Status:** `stable` · **Since:** 
Detect this project's stack + active space and recommend community skills.
**Examples:**
```sh
navig skill suggest
```

## `navig skill synthesize`
**Status:** `stable` · **Since:** 
Synthesize new skill YAML files from repeated command patterns.
**Examples:**
```sh
navig skill synthesize
```

## `navig skill tree`
**Status:** `stable` · **Since:** 
Show skills grouped by category.
**Examples:**
```sh
navig skill tree
```

## `navig skill validate`
**Status:** `stable` · **Since:** 
Validate a SKILL.md against the NAVIG authoring standard (docs/authoring-guide.md).
**Examples:**
```sh
navig skill validate
```

## `navig social approve`
**Status:** `stable` · **Since:** 
Bind an operator approval to a post's EXACT content version (G1).
**Examples:**
```sh
navig social approve
```

## `navig social card`
**Status:** `stable` · **Since:** 
Send a post for review to your Telegram: the real image + text, ✅ Publish / ⏭ Skip.
**Examples:**
```sh
navig social card
```

## `navig social connect`
**Status:** `stable` · **Since:** 
Authorize an account and store its access token in the vault.
**Examples:**
```sh
navig social connect
```

## `navig social disconnect`
**Status:** `stable` · **Since:** 
Remove one account's stored access token (app credentials are kept).
**Examples:**
```sh
navig social disconnect
```

## `navig social discord channels`
**Status:** `stable` · **Since:** 
Text channels the stored bot token can post to (read-only listing).
**Examples:**
```sh
navig social discord channels
```

## `navig social engagement`
**Status:** `stable` · **Since:** 
Record engagement for a campaign — the *measure* side of the loop.
**Examples:**
```sh
navig social engagement
```

## `navig social fan-out`
**Status:** `stable` · **Since:** 
Publish ONE brief to many networks (X · Facebook · Dev.to · Telegram) with UTM tags.
**Examples:**
```sh
navig social fan-out
```

## `navig social presence crawl`
**Status:** `stable` · **Since:** 
Crawl every account in a space's registry and record what moved.
**Examples:**
```sh
navig social presence crawl
```

## `navig social presence plan`
**Status:** `stable` · **Since:** 
Every account in the registry — can NAVIG publish to it, and what's the next step?
**Examples:**
```sh
navig social presence plan
```

## `navig social presence trend`
**Status:** `stable` · **Since:** 
First → latest movement for every tracked account, across all snapshots.
**Examples:**
```sh
navig social presence trend
```

## `navig social receipts`
**Status:** `stable` · **Since:** 
What we've published — a campaign-tagged ledger of every live fan-out.
**Examples:**
```sh
navig social receipts
```

## `navig social redirect-uri`
**Status:** `stable` · **Since:** 
Print the redirect URLs to register in each provider's app settings.
**Examples:**
```sh
navig social redirect-uri
```

## `navig social refresh`
**Status:** `stable` · **Since:** 
Renew an expiring access token in place (uses stored refresh/app creds).
**Examples:**
```sh
navig social refresh
```

## `navig social reject`
**Status:** `stable` · **Since:** 
Record an operator rejection — the post will not pass the approval gate (G1).
**Examples:**
```sh
navig social reject
```

## `navig social relay cancel`
**Status:** `stable` · **Since:** 
Cancel a still-pending post. A sent post can't be un-sent from here.
**Examples:**
```sh
navig social relay cancel
```

## `navig social relay drain`
**Status:** `stable` · **Since:** 
Send everything that is due now, without waiting for the next cron tick.
**Examples:**
```sh
navig social relay drain
```

## `navig social relay post`
**Status:** `stable` · **Since:** 
Queue one post. Running the same command again does not queue it twice.
**Examples:**
```sh
navig social relay post
```

## `navig social relay queue`
**Status:** `stable` · **Since:** 
What is scheduled — the relay's stand-in for Telegram's Scheduled tab.
**Examples:**
```sh
navig social relay queue
```

## `navig social relay status`
**Status:** `stable` · **Since:** 
Is the relay up, which channels may it post to, and does anything need a human?
**Examples:**
```sh
navig social relay status
```

## `navig social report`
**Status:** `stable` · **Since:** 
Campaign scorecard — how did it do? Joins what you published (receipts) with
**Examples:**
```sh
navig social report
```

## `navig social stats`
**Status:** `stable` · **Since:** 
Public follower / subscriber / view counts for ANY handle — no space, no linked account.
**Examples:**
```sh
navig social stats
```

## `navig social status`
**Status:** `stable` · **Since:** 
Show which networks are connected (and, with --check, whether tokens work).
**Examples:**
```sh
navig social status
```

## `navig social sync`
**Status:** `stable` · **Since:** 
Pull live view/like counts from each network → the report's CTR goes real.
**Examples:**
```sh
navig social sync
```

## `navig social track`
**Status:** `stable` · **Since:** 
Follow ONE content id end-to-end: post, approval, per-network receipts (G4).
**Examples:**
```sh
navig social track
```

## `navig social upload`
**Status:** `stable` · **Since:** 
Upload a video to YouTube; select a connected channel with --account.
**Examples:**
```sh
navig social upload
```

## `navig software`
**Status:** `stable` · **Since:** 
Software management - run without subcommand to list packages.
**Examples:**
```sh
navig software
```

## `navig software list`
**Status:** `stable` · **Since:** 
List installed software packages.
**Examples:**
```sh
navig software list
```

## `navig software search`
**Status:** `stable` · **Since:** 
Search installed packages by name.
**Examples:**
```sh
navig software search
```

## `navig space`
**Status:** `stable` · **Since:** 
Run navig space
**Examples:**
```sh
navig space
```

## `navig space audit`
**Status:** `stable` · **Since:** 
Audit the whole spaces collection for structural drift.
**Examples:**
```sh
navig space audit
```

## `navig space books`
**Status:** `stable` · **Since:** 
Show or set the finance BOOK a space keeps its ledger in.
**Examples:**
```sh
navig space books
```

## `navig space check`
**Status:** `stable` · **Since:** 
Check a space and report what's present vs missing — then let you pick the next step.
**Examples:**
```sh
navig space check
```

## `navig space context`
**Status:** `stable` · **Since:** 
Show exactly what an agent may read from this Space's relations — with provenance.
**Examples:**
```sh
navig space context
```

## `navig space create`
**Status:** `stable` · **Since:** 
Create/initialize a space.
**Examples:**
```sh
navig space create
```

## `navig space current`
**Status:** `stable` · **Since:** 
Show the active space (NAVIG_SPACE override respected).
**Examples:**
```sh
navig space current
```

## `navig space delete`
**Status:** `stable` · **Since:** 
Delete a space and all its contents.
**Examples:**
```sh
navig space delete
```

## `navig space disable`
**Status:** `stable` · **Since:** 
Hide a space from the deck/switcher (the folder still works when you're in it).
**Examples:**
```sh
navig space disable
```

## `navig space doctor`
**Status:** `stable` · **Since:** 
Check a space and report what's present vs missing — then let you pick the next step.
**Examples:**
```sh
navig space doctor
```

## `navig space enable`
**Status:** `stable` · **Since:** 
Make a space visible in the deck/switcher and available to activate.
**Examples:**
```sh
navig space enable
```

## `navig space fold`
**Status:** `stable` · **Since:** 
Demote a sub-space: keep it working, hide it from discovery and the registry.
**Examples:**
```sh
navig space fold
```

## `navig space forget`
**Status:** `stable` · **Since:** 
Remove a space from the registry (does not delete the folder).
**Examples:**
```sh
navig space forget
```

## `navig space init`
**Status:** `stable` · **Since:** 
Create/initialize a space.
**Examples:**
```sh
navig space init
```

## `navig space install`
**Status:** `stable` · **Since:** 
Install a space bundle from the community registry (GitHub-backed).
**Examples:**
```sh
navig space install
```

## `navig space link`
**Status:** `stable` · **Since:** 
Authorize another Space's knowledge into this one (logical edge only).
**Examples:**
```sh
navig space link
```

## `navig space lint`
**Status:** `stable` · **Since:** 
Audit the whole spaces collection for structural drift.
**Examples:**
```sh
navig space lint
```

## `navig space list`
**Status:** `stable` · **Since:** 
List spaces across every root (with scope + enabled/active indicators).
**Examples:**
```sh
navig space list
```

## `navig space media config`
**Status:** `stable` · **Since:** 
Show the media layout, or write it with ``--init``.
**Examples:**
```sh
navig space media config
```

## `navig space media fix`
**Status:** `stable` · **Since:** 
Repair what can be repaired without a judgement call.
**Examples:**
```sh
navig space media fix
```

## `navig space media link`
**Status:** `stable` · **Since:** 
Attach one project to its media folder, creating the folder if needed.
**Examples:**
```sh
navig space media link
```

## `navig space media list`
**Status:** `stable` · **Since:** 
Show every media link and its target, healthy ones included.
**Examples:**
```sh
navig space media list
```

## `navig space media relink`
**Status:** `stable` · **Since:** 
Repoint one media link. Removes the link only — never the media.
**Examples:**
```sh
navig space media relink
```

## `navig space media verify`
**Status:** `stable` · **Since:** 
Check every media link across the configured trees.
**Examples:**
```sh
navig space media verify
```

## `navig space new`
**Status:** `stable` · **Since:** 
Create/initialize a space.
**Examples:**
```sh
navig space new
```

## `navig space parent`
**Status:** `stable` · **Since:** 
Set (or --clear) this Space's logical parent — hierarchy without moving folders.
**Examples:**
```sh
navig space parent
```

## `navig space register`
**Status:** `stable` · **Since:** 
Register an external `.navig/` folder so it shows in the deck (enabled).
**Examples:**
```sh
navig space register
```

## `navig space related`
**Status:** `stable` · **Since:** 
Show this Space's parent, links, children and inbound links — one table.
**Examples:**
```sh
navig space related
```

## `navig space rename`
**Status:** `stable` · **Since:** 
Give a space a new id — manifest, registry and the active-space pointer together.
**Examples:**
```sh
navig space rename
```

## `navig space switch`
**Status:** `stable` · **Since:** 
Activate a space — binds the agent's working directory to the workshop.
**Examples:**
```sh
navig space switch
```

## `navig space unfold`
**Status:** `stable` · **Since:** 
Undo `space fold` — the folder becomes a discoverable space again.
**Examples:**
```sh
navig space unfold
```

## `navig space unlink`
**Status:** `stable` · **Since:** 
Remove a read link recorded by ``space link`` (idempotent).
**Examples:**
```sh
navig space unlink
```

## `navig space use`
**Status:** `stable` · **Since:** 
Compatibility alias for `navig space switch <name>`.
**Examples:**
```sh
navig space use
```

## `navig space wire`
**Status:** `stable` · **Since:** 
Wire this folder into the agent ecosystem (alias of `navig wire`).
**Examples:**
```sh
navig space wire
```

## `navig stack down`
**Status:** `stable` · **Since:** 
Stop the NAVIG infrastructure stack.
**Examples:**
```sh
navig stack down
```

## `navig stack health`
**Status:** `stable` · **Since:** 
Run healthcheck on all stack services.
**Examples:**
```sh
navig stack health
```

## `navig stack info`
**Status:** `stable` · **Since:** 
Show stack configuration and paths.
**Examples:**
```sh
navig stack info
```

## `navig stack logs`
**Status:** `stable` · **Since:** 
View NAVIG stack logs.
**Examples:**
```sh
navig stack logs
```

## `navig stack status`
**Status:** `stable` · **Since:** 
Show NAVIG stack container status.
**Examples:**
```sh
navig stack status
```

## `navig stack up`
**Status:** `stable` · **Since:** 
Start the NAVIG infrastructure stack.
**Examples:**
```sh
navig stack up
```

## `navig start`
**Status:** `stable` · **Since:** 
Quick launcher - start NAVIG services with sensible defaults.
**Examples:**
```sh
navig start
```

## `navig stats`
**Status:** `stable` · **Since:** 
Display anonymous NAVIG install statistics.
**Examples:**
```sh
navig stats
```

## `navig status`
**Status:** `stable` · **Since:** 
Show current NAVIG status (active host/app, tunnel, gateway).
**Examples:**
```sh
navig status
```

## `navig store`
**Status:** `stable` · **Since:** 
Run navig store
**Examples:**
```sh
navig store
```

## `navig store disable`
**Status:** `stable` · **Since:** 
Disable / unwire an item without uninstalling.
**Examples:**
```sh
navig store disable
```

## `navig store enable`
**Status:** `stable` · **Since:** 
Enable / wire an item (module or plugin).
**Examples:**
```sh
navig store enable
```

## `navig store info`
**Status:** `stable` · **Since:** 
Full detail for one item, including health and provided commands.
**Examples:**
```sh
navig store info
```

## `navig store install`
**Status:** `stable` · **Since:** 
Install a plugin (validated through the host; pip fallback from the provider map).
**Examples:**
```sh
navig store install
```

## `navig store list`
**Status:** `stable` · **Since:** 
List everything connectable, grouped by kind, with wire states.
**Examples:**
```sh
navig store list
```

## `navig store open`
**Status:** `stable` · **Since:** 
Open a Bay webapp/app (or its checkout if it's premium and not owned).
**Examples:**
```sh
navig store open
```

## `navig store remove`
**Status:** `stable` · **Since:** 
Remove an installed plugin.
**Examples:**
```sh
navig store remove
```

## `navig store status`
**Status:** `stable` · **Since:** 
One-screen wiring summary — counts per kind/state, broken items first.
**Examples:**
```sh
navig store status
```

## `navig suggest`
**Status:** `stable` · **Since:** 
Intelligent command suggestions based on history and context.
**Examples:**
```sh
navig suggest
```

## `navig sync instructions`
**Status:** `stable` · **Since:** 
Regenerate every AI tool's instruction mirror from MASTER (cross-platform).
**Examples:**
```sh
navig sync instructions
```

## `navig sync status`
**Status:** `stable` · **Since:** 
Show sync status.
**Examples:**
```sh
navig sync status
```

## `navig system`
**Status:** `stable` · **Since:** 
Show system overview.
**Examples:**
```sh
navig system
```

## `navig system clean`
**Status:** `stable` · **Since:** 
Clean NAVIG caches and temporary files.
**Examples:**
```sh
navig system clean
```

## `navig system info`
**Status:** `stable` · **Since:** 
Show detailed system information.
**Examples:**
```sh
navig system info
```

## `navig tailscale ip`
**Status:** `stable` · **Since:** 
Show Tailscale IP of self or a peer.
**Examples:**
```sh
navig tailscale ip
```

## `navig tailscale ping`
**Status:** `stable` · **Since:** 
Ping a Tailscale peer.
**Examples:**
```sh
navig tailscale ping
```

## `navig tailscale status`
**Status:** `stable` · **Since:** 
Show Tailscale status and peer list.
**Examples:**
```sh
navig tailscale status
```

## `navig task`
**Status:** `stable` · **Since:** 
Task management - run without subcommand to list tasks.
**Examples:**
```sh
navig task
```

## `navig task add`
**Status:** `stable` · **Since:** 
Create a new task from template (retired — use `navig block new`).
**Examples:**
```sh
navig task add
```

## `navig task complete`
**Status:** `stable` · **Since:** 
Record a completed task — runs complete-task.sh (Unix) or complete-task.ps1 (Windows).
**Examples:**
```sh
navig task complete
```

## `navig task edit`
**Status:** `stable` · **Since:** 
Open task in default editor (retired — edit the Block's BLOCK.md).
**Examples:**
```sh
navig task edit
```

## `navig task list`
**Status:** `stable` · **Since:** 
List all available tasks/workflows (retired — see `navig block list`).
**Examples:**
```sh
navig task list
```

## `navig task remove`
**Status:** `stable` · **Since:** 
Delete a task (retired).
**Examples:**
```sh
navig task remove
```

## `navig task run`
**Status:** `stable` · **Since:** 
Execute a task/workflow (retired). If the name is a Block, use `navig apply`.
**Examples:**
```sh
navig task run
```

## `navig task show`
**Status:** `stable` · **Since:** 
Display task definition (retired — redirects to `navig block show`).
**Examples:**
```sh
navig task show
```

## `navig task test`
**Status:** `stable` · **Since:** 
Validate task syntax (retired — redirects to `navig block verify`).
**Examples:**
```sh
navig task test
```

## `navig telegram briefings`
**Status:** `stable` · **Since:** 
The morning and evening briefing cards: on/off, time, days.
**Examples:**
```sh
navig telegram briefings
```

## `navig telegram business alerts`
**Status:** `stable` · **Since:** 
Turn deletion alerts on or off (on = digest, unless you chose instant).
**Examples:**
```sh
navig telegram business alerts
```

## `navig telegram business deleted`
**Status:** `stable` · **Since:** 
What was deleted in your business chats — the DM alert, browsable.
**Examples:**
```sh
navig telegram business deleted
```

## `navig telegram business deletions flush`
**Status:** `stable` · **Since:** 
Send the pending digest now, instead of waiting for the window.
**Examples:**
```sh
navig telegram business deletions flush
```

## `navig telegram business deletions mode`
**Status:** `stable` · **Since:** 
How you hear about deletions.
**Examples:**
```sh
navig telegram business deletions mode
```

## `navig telegram business deletions mute`
**Status:** `stable` · **Since:** 
Stop announcing deletions from ONE chat (still recorded).
**Examples:**
```sh
navig telegram business deletions mute
```

## `navig telegram business deletions quiet`
**Status:** `stable` · **Since:** 
Keep timer expiries and whole-chat wipes out of the alerts (still recorded).
**Examples:**
```sh
navig telegram business deletions quiet
```

## `navig telegram business deletions record`
**Status:** `stable` · **Since:** 
Whether deletions are tracked at all.
**Examples:**
```sh
navig telegram business deletions record
```

## `navig telegram business deletions status`
**Status:** `stable` · **Since:** 
Show every deletion switch: record · mode · window · target · muted.
**Examples:**
```sh
navig telegram business deletions status
```

## `navig telegram business deletions target`
**Status:** `stable` · **Since:** 
Send deletion reports to a separate log chat instead of your DM.
**Examples:**
```sh
navig telegram business deletions target
```

## `navig telegram business deletions window`
**Status:** `stable` · **Since:** 
How long a digest collects before it is sent (default 900s / 15 min).
**Examples:**
```sh
navig telegram business deletions window
```

## `navig telegram business disable`
**Status:** `stable` · **Since:** 
Disable the business layer.
**Examples:**
```sh
navig telegram business disable
```

## `navig telegram business emoji`
**Status:** `stable` · **Since:** 
List or remap reaction emojis, e.g. `navig telegram business emoji 🎯 tiktok`.
**Examples:**
```sh
navig telegram business emoji
```

## `navig telegram business enable`
**Status:** `stable` · **Since:** 
Enable catching your business-profile conversations (owner-gated).
**Examples:**
```sh
navig telegram business enable
```

## `navig telegram business ping`
**Status:** `stable` · **Since:** 
Who gets a `/ping` status reply in business chats (default owner).
**Examples:**
```sh
navig telegram business ping
```

## `navig telegram business rights`
**Status:** `stable` · **Since:** 
List or change per-tool rights, e.g. `navig telegram business rights translate both`.
**Examples:**
```sh
navig telegram business rights
```

## `navig telegram business status`
**Status:** `stable` · **Since:** 
Show the business layer state + per-tool rights.
**Examples:**
```sh
navig telegram business status
```

## `navig telegram confirm`
**Status:** `stable` · **Since:** 
Step 2 - complete login with the code (+ 2FA password if asked).
**Examples:**
```sh
navig telegram confirm
```

## `navig telegram contacts list`
**Status:** `stable` · **Since:** 
List your saved Telegram contacts (optionally only tagged ones).
**Examples:**
```sh
navig telegram contacts list
```

## `navig telegram contacts missing`
**Status:** `stable` · **Since:** 
Who you filed that has since left your contact list — and why.
**Examples:**
```sh
navig telegram contacts missing
```

## `navig telegram contacts prune`
**Status:** `stable` · **Since:** 
Remove contacts by last-seen bucket (dry-run by default).
**Examples:**
```sh
navig telegram contacts prune
```

## `navig telegram contacts runs`
**Status:** `stable` · **Since:** 
List past tagging runs (what undo can reverse).
**Examples:**
```sh
navig telegram contacts runs
```

## `navig telegram contacts tag`
**Status:** `stable` · **Since:** 
File a group's members into your contacts, marked (dry-run by default).
**Examples:**
```sh
navig telegram contacts tag
```

## `navig telegram contacts tag-list`
**Status:** `stable` · **Since:** 
Mark an explicit list of @handles (dry-run by default).
**Examples:**
```sh
navig telegram contacts tag-list
```

## `navig telegram contacts undo`
**Status:** `stable` · **Since:** 
Reverse a tagging run: created contacts deleted, renamed ones restored.
**Examples:**
```sh
navig telegram contacts undo
```

## `navig telegram dedupe`
**Status:** `stable` · **Since:** 
Find duplicate media (live scan). Lists SAFE (exact/inbox) + REVIEW groups.
**Examples:**
```sh
navig telegram dedupe
```

## `navig telegram delete`
**Status:** `stable` · **Since:** 
Delete messages (revoke for all). Dry-run unless --confirm.
**Examples:**
```sh
navig telegram delete
```

## `navig telegram delete-chat`
**Status:** `stable` · **Since:** 
Delete a whole chat/group/channel for everyone. Dry-run unless --confirm.
**Examples:**
```sh
navig telegram delete-chat
```

## `navig telegram dialogs`
**Status:** `stable` · **Since:** 
List all your chats / channels / groups.
**Examples:**
```sh
navig telegram dialogs
```

## `navig telegram download-media`
**Status:** `stable` · **Since:** 
Download a channel's media BYTES into a ChatExport-shaped staging folder
**Examples:**
```sh
navig telegram download-media
```

## `navig telegram extensions disable`
**Status:** `stable` · **Since:** 
Switch a Telegram extension off.
**Examples:**
```sh
navig telegram extensions disable
```

## `navig telegram extensions enable`
**Status:** `stable` · **Since:** 
Switch a Telegram extension on.
**Examples:**
```sh
navig telegram extensions enable
```

## `navig telegram extensions info`
**Status:** `stable` · **Since:** 
Show what one extension owns and what switching it off does.
**Examples:**
```sh
navig telegram extensions info
```

## `navig telegram extensions list`
**Status:** `stable` · **Since:** 
Show every Telegram extension and whether it is on.
**Examples:**
```sh
navig telegram extensions list
```

## `navig telegram folders apply`
**Status:** `stable` · **Since:** 
Apply a folder layout from a plan file. Dry-run unless --confirm.
**Examples:**
```sh
navig telegram folders apply
```

## `navig telegram folders export`
**Status:** `stable` · **Since:** 
Dump current folders to an editable plan file (ids + raw peer ids).
**Examples:**
```sh
navig telegram folders export
```

## `navig telegram folders list`
**Status:** `stable` · **Since:** 
List your chat folders with their members.
**Examples:**
```sh
navig telegram folders list
```

## `navig telegram folders rename`
**Status:** `stable` · **Since:** 
Rename folders (label only, membership untouched). Dry-run unless --confirm.
**Examples:**
```sh
navig telegram folders rename
```

## `navig telegram forward`
**Status:** `stable` · **Since:** 
Forward (or --copy) messages to another chat/channel.
**Examples:**
```sh
navig telegram forward
```

## `navig telegram history sync`
**Status:** `stable` · **Since:** 
Backfill history into the catalog so search covers everything.
**Examples:**
```sh
navig telegram history sync
```

## `navig telegram links`
**Status:** `stable` · **Since:** 
Extract & classify links (tiktok/youtube/url) from a chat's recent messages.
**Examples:**
```sh
navig telegram links
```

## `navig telegram login`
**Status:** `stable` · **Since:** 
Step 1 - send a login code to your Telegram app.
**Examples:**
```sh
navig telegram login
```

## `navig telegram logout`
**Status:** `stable` · **Since:** 
Forget the stored Telegram user session.
**Examples:**
```sh
navig telegram logout
```

## `navig telegram members`
**Status:** `stable` · **Since:** 
List the members of a group you can see.
**Examples:**
```sh
navig telegram members
```

## `navig telegram move`
**Status:** `stable` · **Since:** 
Move messages to another chat/group (copy + delete). Dry-run unless --confirm.
**Examples:**
```sh
navig telegram move
```

## `navig telegram rename`
**Status:** `stable` · **Since:** 
Rename a chat/channel title (needs admin). Dry-run unless --confirm.
**Examples:**
```sh
navig telegram rename
```

## `navig telegram rename-bulk`
**Status:** `stable` · **Since:** 
Rename many chats from a plan file, flood-safe (one session, throttled,
**Examples:**
```sh
navig telegram rename-bulk
```

## `navig telegram search`
**Status:** `stable` · **Since:** 
Search all backfilled conversations + media (or --live in one chat).
**Examples:**
```sh
navig telegram search
```

## `navig telegram send`
**Status:** `stable` · **Since:** 
Send a Telegram message using the configured bot token.
**Examples:**
```sh
navig telegram send
```

## `navig telegram sessions clear`
**Status:** `stable` · **Since:** 
Clear a session's message history.
**Examples:**
```sh
navig telegram sessions clear
```

## `navig telegram sessions delete`
**Status:** `stable` · **Since:** 
Delete a session completely.
**Examples:**
```sh
navig telegram sessions delete
```

## `navig telegram sessions list`
**Status:** `stable` · **Since:** 
List all active Telegram sessions.
**Examples:**
```sh
navig telegram sessions list
```

## `navig telegram sessions prune`
**Status:** `stable` · **Since:** 
Remove inactive sessions.
**Examples:**
```sh
navig telegram sessions prune
```

## `navig telegram sessions show`
**Status:** `stable` · **Since:** 
Show details of a specific session.
**Examples:**
```sh
navig telegram sessions show
```

## `navig telegram setup`
**Status:** `stable` · **Since:** 
Store your Telegram api_id/api_hash (from my.telegram.org) in the vault.
**Examples:**
```sh
navig telegram setup
```

## `navig telegram status`
**Status:** `stable` · **Since:** 
Show Telegram bot status.
**Examples:**
```sh
navig telegram status
```

## `navig telegram tidy`
**Status:** `stable` · **Since:** 
Clear yesterday's routine messages from the bot's chat, every night.
**Examples:**
```sh
navig telegram tidy
```

## `navig telegram topics`
**Status:** `stable` · **Since:** 
List forum topics in a forum supergroup (pages through all of them).
**Examples:**
```sh
navig telegram topics
```

## `navig telegram-exports audit`
**Status:** `stable` · **Since:** 
Chained-move-aware integrity check: prove no export was lost.
**Examples:**
```sh
navig telegram-exports audit
```

## `navig telegram-exports dedupe`
**Status:** `stable` · **Since:** 
Find loose files that are byte-identical to a copy already inside their export.
**Examples:**
```sh
navig telegram-exports dedupe
```

## `navig telegram-exports match`
**Status:** `stable` · **Since:** 
Pair loose downloaded files back to the source chat by original name (+ exact size).
**Examples:**
```sh
navig telegram-exports match
```

## `navig telegram-exports notebook build`
**Status:** `stable` · **Since:** 
Copy EXPORT into OUT/_export/<date> (sha256-verified) and (re)generate the Markdown.
**Examples:**
```sh
navig telegram-exports notebook build
```

## `navig telegram-exports notebook faces`
**Status:** `stable` · **Since:** 
Group the faces in the People photos (locally) and draw a sheet to name them from.
**Examples:**
```sh
navig telegram-exports notebook faces
```

## `navig telegram-exports notebook name`
**Status:** `stable` · **Since:** 
Give a face group a name; its photos are regrouped (and moved) under it.
**Examples:**
```sh
navig telegram-exports notebook name
```

## `navig telegram-exports notebook ocr`
**Status:** `stable` · **Since:** 
Read the text inside every image (local Tesseract) so screenshots become searchable.
**Examples:**
```sh
navig telegram-exports notebook ocr
```

## `navig telegram-exports notebook photos`
**Status:** `stable` · **Since:** 
Sort images by what they show (people, places, screenshots, documents…) with a local model.
**Examples:**
```sh
navig telegram-exports notebook photos
```

## `navig telegram-exports notebook place`
**Status:** `stable` · **Since:** 
Move every media file out of _export/ into real folders under Media/ (logged, reversible).
**Examples:**
```sh
navig telegram-exports notebook place
```

## `navig telegram-exports notebook quarantine`
**Status:** `stable` · **Since:** 
Move extra byte-identical media copies from _export/ to _duplicates/ (logged, restorable).
**Examples:**
```sh
navig telegram-exports notebook quarantine
```

## `navig telegram-exports notebook restore`
**Status:** `stable` · **Since:** 
Put every quarantined duplicate back where the export had it.
**Examples:**
```sh
navig telegram-exports notebook restore
```

## `navig telegram-exports notebook search`
**Status:** `stable` · **Since:** 
Find a message or screenshot by any word in it — its text, the text in its image,
**Examples:**
```sh
navig telegram-exports notebook search
```

## `navig telegram-exports notebook unplace`
**Status:** `stable` · **Since:** 
Move every placed file back into _export/ exactly where the export had it.
**Examples:**
```sh
navig telegram-exports notebook unplace
```

## `navig telegram-exports notebook verify`
**Status:** `stable` · **Since:** 
Coverage gate: prove every message, link and file made it into the library.
**Examples:**
```sh
navig telegram-exports notebook verify
```

## `navig telegram-exports organize`
**Status:** `stable` · **Since:** 
File loose ``ChatExport_*`` folders at ROOT into ``Category/Chat/Date``.
**Examples:**
```sh
navig telegram-exports organize
```

## `navig telegram-exports verify`
**Status:** `stable` · **Since:** 
Re-classify filed exports and report any that no longer match their category.
**Examples:**
```sh
navig telegram-exports verify
```

## `navig telemetry`
**Status:** `stable` · **Since:** 
Show telemetry status.
**Examples:**
```sh
navig telemetry
```

## `navig telemetry disable`
**Status:** `stable` · **Since:** 
Disable telemetry.
**Examples:**
```sh
navig telemetry disable
```

## `navig telemetry enable`
**Status:** `stable` · **Since:** 
Enable telemetry.
**Examples:**
```sh
navig telemetry enable
```

## `navig test`
**Status:** `stable` · **Since:** 
Run navig test
**Examples:**
```sh
navig test
```

## `navig text channel data`
**Status:** `stable` · **Since:** 
The live numbers the cards quote — loud fetch, disk cache, never silently stale.
**Examples:**
```sh
navig text channel data
```

## `navig text channel lint`
**Status:** `stable` · **Since:** 
Check the master against the rules its own header declares.
**Examples:**
```sh
navig text channel lint
```

## `navig text channel sweep`
**Status:** `stable` · **Since:** 
Terminology sweep (cities → Zones): literal replacements, reported before written.
**Examples:**
```sh
navig text channel sweep
```

## `navig text channel sync`
**Status:** `stable` · **Since:** 
Push the master's values into the channel's scripts, positional by structure.
**Examples:**
```sh
navig text channel sync
```

## `navig text check`
**Status:** `stable` · **Since:** 
Show whether AI text generation is ready (a provider is configured).
**Examples:**
```sh
navig text check
```

## `navig text gen`
**Status:** `stable` · **Since:** 
Generate Markdown text from a prompt and save it locally.
**Examples:**
```sh
navig text gen
```

## `navig text lyrics fit`
**Status:** `stable` · **Since:** 
📏 Does a lyric sit on the beat? Syllables per line against the time one bar gives.
**Examples:**
```sh
navig text lyrics fit
```

## `navig text lyrics rhyme`
**Status:** `stable` · **Since:** 
🎯 Rhymes for a word from the words you already use — spelling-based, strong first.
**Examples:**
```sh
navig text lyrics rhyme
```

## `navig text lyrics scaffold`
**Status:** `stable` · **Since:** 
🧱 An empty lyric cut to the beat's measured bar map — one slot per line.
**Examples:**
```sh
navig text lyrics scaffold
```

## `navig text lyrics sheet`
**Status:** `stable` · **Since:** 
🗒 A text's hook + its voices table → a sheet for `navig audio beat demo`.
**Examples:**
```sh
navig text lyrics sheet
```

## `navig text lyrics words`
**Status:** `stable` · **Since:** 
🔎 Word-bank entries that mention a term — title hits first.
**Examples:**
```sh
navig text lyrics words
```

## `navig todo`
**Status:** `stable` · **Since:** 
`navig todo` with no verb shows the list — the thing you want 90% of the time.
**Examples:**
```sh
navig todo
```

## `navig todo add`
**Status:** `stable` · **Since:** 
Capture a task. The date can be part of the sentence, or exact with --at.
**Examples:**
```sh
navig todo add
```

## `navig todo categories`
**Status:** `stable` · **Since:** 
Your categories and how much is open in each.
**Examples:**
```sh
navig todo categories
```

## `navig todo done`
**Status:** `stable` · **Since:** 
Tick a task off. A recurring one rolls forward instead.
**Examples:**
```sh
navig todo done
```

## `navig todo import-legacy`
**Status:** `stable` · **Since:** 
Fold the old ~/.navig/tasks.json into this list — once.
**Examples:**
```sh
navig todo import-legacy
```

## `navig todo list`
**Status:** `stable` · **Since:** 
Show your tasks, soonest first.
**Examples:**
```sh
navig todo list
```

## `navig todo rm`
**Status:** `stable` · **Since:** 
Delete a task for good.
**Examples:**
```sh
navig todo rm
```

## `navig todo scan`
**Status:** `stable` · **Since:** 
Find `- [ ]` work already written down in your spaces.
**Examples:**
```sh
navig todo scan
```

## `navig todo when`
**Status:** `stable` · **Since:** 
Give a task a date — or move the one it has.
**Examples:**
```sh
navig todo when
```

## `navig tools list`
**Status:** `stable` · **Since:** 
List all registered tools.
**Examples:**
```sh
navig tools list
```

## `navig tools schema`
**Status:** `stable` · **Since:** 
Dump the OpenAPI schema for all registered tools.
**Examples:**
```sh
navig tools schema
```

## `navig tools show`
**Status:** `stable` · **Since:** 
Show full metadata for a single tool.
**Examples:**
```sh
navig tools show
```

## `navig tray install`
**Status:** `stable` · **Since:** 
Install NAVIG Tray (desktop shortcut + optional auto-start).
**Examples:**
```sh
navig tray install
```

## `navig tray start`
**Status:** `stable` · **Since:** 
Launch the NAVIG system tray app.
**Examples:**
```sh
navig tray start
```

## `navig tray status`
**Status:** `stable` · **Since:** 
Check if NAVIG tray app is running.
**Examples:**
```sh
navig tray status
```

## `navig tray stop`
**Status:** `stable` · **Since:** 
Stop the NAVIG tray app.
**Examples:**
```sh
navig tray stop
```

## `navig tray uninstall`
**Status:** `stable` · **Since:** 
Remove NAVIG Tray auto-start and desktop shortcut.
**Examples:**
```sh
navig tray uninstall
```

## `navig trigger`
**Status:** `stable` · **Since:** 
Event-driven automation triggers - run without subcommand for list.
**Examples:**
```sh
navig trigger
```

## `navig trigger add`
**Status:** `stable` · **Since:** 
Create a new trigger.
**Examples:**
```sh
navig trigger add
```

## `navig trigger clear-history`
**Status:** `stable` · **Since:** 
Clear trigger execution history.
**Examples:**
```sh
navig trigger clear-history
```

## `navig trigger disable`
**Status:** `stable` · **Since:** 
Disable a trigger (stops it from firing).
**Examples:**
```sh
navig trigger disable
```

## `navig trigger enable`
**Status:** `stable` · **Since:** 
Enable a disabled trigger.
**Examples:**
```sh
navig trigger enable
```

## `navig trigger fire`
**Status:** `stable` · **Since:** 
Manually fire a trigger.
**Examples:**
```sh
navig trigger fire
```

## `navig trigger history`
**Status:** `stable` · **Since:** 
Show trigger execution history.
**Examples:**
```sh
navig trigger history
```

## `navig trigger list`
**Status:** `stable` · **Since:** 
List all configured triggers.
**Examples:**
```sh
navig trigger list
```

## `navig trigger remove`
**Status:** `stable` · **Since:** 
Remove a trigger.
**Examples:**
```sh
navig trigger remove
```

## `navig trigger show`
**Status:** `stable` · **Since:** 
Show detailed trigger information.
**Examples:**
```sh
navig trigger show
```

## `navig trigger stats`
**Status:** `stable` · **Since:** 
Show trigger statistics.
**Examples:**
```sh
navig trigger stats
```

## `navig trigger test`
**Status:** `stable` · **Since:** 
Test a trigger (dry run).
**Examples:**
```sh
navig trigger test
```

## `navig tunnel`
**Status:** `stable` · **Since:** 
Tunnel management - run without subcommand for help.
**Examples:**
```sh
navig tunnel
```

## `navig tunnel auto`
**Status:** `stable` · **Since:** 
Auto-start tunnel if needed, auto-stop when done.
**Examples:**
```sh
navig tunnel auto
```

## `navig tunnel remove`
**Status:** `stable` · **Since:** 
Stop and remove SSH tunnel (canonical command).
**Examples:**
```sh
navig tunnel remove
```

## `navig tunnel run`
**Status:** `stable` · **Since:** 
Start SSH tunnel for active server (canonical command).
**Examples:**
```sh
navig tunnel run
```

## `navig tunnel show`
**Status:** `stable` · **Since:** 
Show tunnel status (canonical command).
**Examples:**
```sh
navig tunnel show
```

## `navig tunnel update`
**Status:** `stable` · **Since:** 
Restart tunnel (canonical command).
**Examples:**
```sh
navig tunnel update
```

## `navig undo`
**Status:** `stable` · **Since:** 
Undo the last green (undoable) operation — confirm-gated.
**Examples:**
```sh
navig undo
```

## `navig unpack doctor`
**Status:** `stable` · **Since:** 
Show which external tools and optional Python packages are available.
**Examples:**
```sh
navig unpack doctor
```

## `navig unpack game`
**Status:** `stable` · **Since:** 
Extract and export exactly one game (re-runs it even if done).
**Examples:**
```sh
navig unpack game
```

## `navig unpack ingest`
**Status:** `stable` · **Since:** 
Classify every archive (volume sets joined, duplicates detected) into _manifest.json.
**Examples:**
```sh
navig unpack ingest
```

## `navig unpack report`
**Status:** `stable` · **Since:** 
Batch status: every game, its state, method and languages.
**Examples:**
```sh
navig unpack report
```

## `navig unpack run`
**Status:** `stable` · **Since:** 
Extract and export every pending game, one by one (resumable: re-run skips done games).
**Examples:**
```sh
navig unpack run
```

## `navig update`
**Status:** `stable` · **Since:** 
Upgrade NAVIG.
**Examples:**
```sh
navig update
```

## `navig update check`
**Status:** `stable` · **Since:** 
Check current vs. latest available version (no changes made).
**Examples:**
```sh
navig update check
```

## `navig update history`
**Status:** `stable` · **Since:** 
Show recent update history.
**Examples:**
```sh
navig update history
```

## `navig update nodes`
**Status:** `stable` · **Since:** 
List all nodes NAVIG knows about (local + configured hosts).
**Examples:**
```sh
navig update nodes
```

## `navig update rollback`
**Status:** `stable` · **Since:** 
Roll back NAVIG to a specific version.
**Examples:**
```sh
navig update rollback
```

## `navig update run`
**Status:** `stable` · **Since:** 
Apply updates to one or more nodes.
**Examples:**
```sh
navig update run
```

## `navig update source`
**Status:** `stable` · **Since:** 
Show the configured update source.
**Examples:**
```sh
navig update source
```

## `navig update status`
**Status:** `stable` · **Since:** 
Show current installed version and update source config.
**Examples:**
```sh
navig update status
```

## `navig upgrade`
**Status:** `stable` · **Since:** 
Upgrade NAVIG to the latest version.
**Examples:**
```sh
navig upgrade
```

## `navig user set`
**Status:** `stable` · **Since:** 
Set a user profile value.
**Examples:**
```sh
navig user set
```

## `navig user show`
**Status:** `stable` · **Since:** 
Show the current user profile.
**Examples:**
```sh
navig user show
```

## `navig vault activate`
**Status:** `stable` · **Since:** 
Set a credential as the active (preferred) one for its provider.
**Examples:**
```sh
navig vault activate
```

## `navig vault add`
**Status:** `stable` · **Since:** 
Add a new credential to the vault.
**Examples:**
```sh
navig vault add
```

## `navig vault audit`
**Status:** `stable` · **Since:** 
Show audit log for credentials.
**Examples:**
```sh
navig vault audit
```

## `navig vault backup`
**Status:** `stable` · **Since:** 
Write an encrypted backup only the offline recovery key can open.
**Examples:**
```sh
navig vault backup
```

## `navig vault breach check`
**Status:** `stable` · **Since:** 
Scan emails against leak dumps now and store the findings.
**Examples:**
```sh
navig vault breach check
```

## `navig vault breach list`
**Status:** `stable` · **Since:** 
Show stored breach findings (passwords masked unless --reveal).
**Examples:**
```sh
navig vault breach list
```

## `navig vault breach status`
**Status:** `stable` · **Since:** 
Show connector config, monitor state and the last scan cycle.
**Examples:**
```sh
navig vault breach status
```

## `navig vault check-all`
**Status:** `stable` · **Since:** 
Validate every stored credential and print a status table.
**Examples:**
```sh
navig vault check-all
```

## `navig vault classify`
**Status:** `stable` · **Since:** 
Organise the vault: name, category, issuer, website and tags for every item.
**Examples:**
```sh
navig vault classify
```

## `navig vault clone`
**Status:** `stable` · **Since:** 
Clone a credential to a different profile.
**Examples:**
```sh
navig vault clone
```

## `navig vault delete`
**Status:** `stable` · **Since:** 
Delete a vault credential by provider name or credential ID.
**Examples:**
```sh
navig vault delete
```

## `navig vault devices enroll`
**Status:** `stable` · **Since:** 
Mint a one-time code (10 min) the new machine redeems with `navig vault join`.
**Examples:**
```sh
navig vault devices enroll
```

## `navig vault devices list`
**Status:** `stable` · **Since:** 
Every enrolled machine, its state and its grants.
**Examples:**
```sh
navig vault devices list
```

## `navig vault devices revoke`
**Status:** `stable` · **Since:** 
Revoke a machine now: its next request is refused and its key is destroyed.
**Examples:**
```sh
navig vault devices revoke
```

## `navig vault disable`
**Status:** `stable` · **Since:** 
Disable a credential.
**Examples:**
```sh
navig vault disable
```

## `navig vault edit`
**Status:** `stable` · **Since:** 
Edit an existing credential — its value, or its name / organisation only.
**Examples:**
```sh
navig vault edit
```

## `navig vault enable`
**Status:** `stable` · **Since:** 
Enable a credential.
**Examples:**
```sh
navig vault enable
```

## `navig vault export`
**Status:** `stable` · **Since:** 
Seal secrets into an SVG image — a portable backup, or a password to hand to someone.
**Examples:**
```sh
navig vault export
```

## `navig vault get`
**Status:** `stable` · **Since:** 
Get a credential value from the vault (masked unless --reveal / --raw).
**Examples:**
```sh
navig vault get
```

## `navig vault grant`
**Status:** `stable` · **Since:** 
Let one device read the secrets matching PATTERN (default: denied everything).
**Examples:**
```sh
navig vault grant
```

## `navig vault import`
**Status:** `stable` · **Since:** 
Add the secrets from a vault SVG to this vault — never overwrites one you already have.
**Examples:**
```sh
navig vault import
```

## `navig vault info`
**Status:** `stable` · **Since:** 
Show a full information panel for a credential.
**Examples:**
```sh
navig vault info
```

## `navig vault join`
**Status:** `stable` · **Since:** 
Enroll THIS machine with a vault authority using a one-time code.
**Examples:**
```sh
navig vault join
```

## `navig vault leave`
**Status:** `stable` · **Since:** 
Forget this machine's authority credentials and wipe its offline cache.
**Examples:**
```sh
navig vault leave
```

## `navig vault list`
**Status:** `stable` · **Since:** 
List credentials in the vault (organise them with `navig vault classify`).
**Examples:**
```sh
navig vault list
```

## `navig vault login add`
**Status:** `stable` · **Since:** 
Store a website login for AI auto-login. The password is never echoed.
**Examples:**
```sh
navig vault login add
```

## `navig vault login capture`
**Status:** `stable` · **Since:** 
Sign in ONCE in a visible browser (Google/GitHub buttons OK) — session saved to the vault.
**Examples:**
```sh
navig vault login capture
```

## `navig vault login get`
**Status:** `stable` · **Since:** 
Show a stored login. The password is masked unless --reveal/--json is given.
**Examples:**
```sh
navig vault login get
```

## `navig vault login list`
**Status:** `stable` · **Since:** 
List stored website logins (no passwords are shown).
**Examples:**
```sh
navig vault login list
```

## `navig vault login remove`
**Status:** `stable` · **Since:** 
Delete a stored website login.
**Examples:**
```sh
navig vault login remove
```

## `navig vault open`
**Status:** `stable` · **Since:** 
Show what a vault SVG holds — writes nothing to your vault.
**Examples:**
```sh
navig vault open
```

## `navig vault opener`
**Status:** `stable` · **Since:** 
Write the offline page that opens a vault SVG in any browser (for people without navig).
**Examples:**
```sh
navig vault opener
```

## `navig vault profile list`
**Status:** `stable` · **Since:** 
List all credential profiles.
**Examples:**
```sh
navig vault profile list
```

## `navig vault profile use`
**Status:** `stable` · **Since:** 
Set the active profile.
**Examples:**
```sh
navig vault profile use
```

## `navig vault providers`
**Status:** `stable` · **Since:** 
List supported providers with built-in validation.
**Examples:**
```sh
navig vault providers
```

## `navig vault recovery init`
**Status:** `stable` · **Since:** 
Create the offline recovery key (shown once). Migrates a legacy vault first.
**Examples:**
```sh
navig vault recovery init
```

## `navig vault recovery verify`
**Status:** `stable` · **Since:** 
Prove a recovery key opens this vault (reads it from a hidden prompt or stdin).
**Examples:**
```sh
navig vault recovery verify
```

## `navig vault remove`
**Status:** `stable` · **Since:** 
Delete a credential by provider path (`provider` or `provider/data_key`).
**Examples:**
```sh
navig vault remove
```

## `navig vault restore`
**Status:** `stable` · **Since:** 
Restore a backup into an empty directory (reads the recovery key from a prompt/stdin).
**Examples:**
```sh
navig vault restore
```

## `navig vault revoke`
**Status:** `stable` · **Since:** 
Remove grants from a device (the device stays enrolled; see `devices revoke`).
**Examples:**
```sh
navig vault revoke
```

## `navig vault rotate`
**Status:** `stable` · **Since:** 
Replace the vault master key (all items + keyslots re-wrapped atomically).
**Examples:**
```sh
navig vault rotate
```

## `navig vault run`
**Status:** `stable` · **Since:** 
Run a command with secrets injected — never on its command line.
**Examples:**
```sh
navig vault run
```

## `navig vault set`
**Status:** `stable` · **Since:** 
Set (add or update) a credential in the vault.
**Examples:**
```sh
navig vault set
```

## `navig vault show`
**Status:** `stable` · **Since:** 
Show details of a credential (legacy — prefer `vault info` for the rich view).
**Examples:**
```sh
navig vault show
```

## `navig vault status`
**Status:** `stable` · **Since:** 
Key custody at a glance: keyslots, recovery, item count, federation role.
**Examples:**
```sh
navig vault status
```

## `navig vault sync`
**Status:** `stable` · **Since:** 
Refresh the offline cache: drop expired and no-longer-granted values.
**Examples:**
```sh
navig vault sync
```

## `navig vault test`
**Status:** `stable` · **Since:** 
Test a credential against the provider API.
**Examples:**
```sh
navig vault test
```

## `navig vault validate`
**Status:** `stable` · **Since:** 
Validate a credential against the provider's API.
**Examples:**
```sh
navig vault validate
```

## `navig version`
**Status:** `stable` · **Since:** 
Show NAVIG version and system info.
**Examples:**
```sh
navig version
```

## `navig video cut`
**Status:** `stable` · **Since:** 
🎬 Render an EDL — the editable timeline — into its outputs.
**Examples:**
```sh
navig video cut
```

## `navig video doctor`
**Status:** `stable` · **Since:** 
Deep check: workspace layout, engines, ffmpeg, fonts. Exit 1 if a requirement fails.
**Examples:**
```sh
navig video doctor
```

## `navig video kinetic plan`
**Status:** `stable` · **Since:** 
🗓 The timeline without rendering — beat times, words, plates, cuts.
**Examples:**
```sh
navig video kinetic plan
```

## `navig video kinetic render`
**Status:** `stable` · **Since:** 
🎬 Render kinetic episodes — voice timing drives every cut.
**Examples:**
```sh
navig video kinetic render
```

## `navig video parity`
**Status:** `stable` · **Since:** 
⚖ Do two renders match? Container, pixels, audio — the porting contract.
**Examples:**
```sh
navig video parity
```

## `navig video status`
**Status:** `stable` · **Since:** 
Workspace, engines and render verbs — one glance at what is wired.
**Examples:**
```sh
navig video status
```

## `navig vm console`
**Status:** `stable` · **Since:** 
The VM's serial console: kernel, systemd and cloud-init output, as text.
**Examples:**
```sh
navig vm console
```

## `navig vm create`
**Status:** `stable` · **Since:** 
Build a VM from a cloud image: disk, first-boot seed, its own SSH key.
**Examples:**
```sh
navig vm create
```

## `navig vm destroy`
**Status:** `stable` · **Since:** 
Delete a VM and its folder. Only ever a folder navig created under the VM root.
**Examples:**
```sh
navig vm destroy
```

## `navig vm exec`
**Status:** `stable` · **Since:** 
Run one command in the VM; its exit status is this command's.
**Examples:**
```sh
navig vm exec
```

## `navig vm ip`
**Status:** `stable` · **Since:** 
The VM's IP address (reported by VMware Tools in the guest).
**Examples:**
```sh
navig vm ip
```

## `navig vm list`
**Status:** `stable` · **Since:** 
Every VM navig built, and whether it is running.
**Examples:**
```sh
navig vm list
```

## `navig vm password`
**Status:** `stable` · **Since:** 
The guest owner's password (for the login screen and sudo).
**Examples:**
```sh
navig vm password
```

## `navig vm push`
**Status:** `stable` · **Since:** 
Copy local files into the VM (scp, as its owner); its exit status is this command's.
**Examples:**
```sh
navig vm push
```

## `navig vm revert`
**Status:** `stable` · **Since:** 
Go back to a snapshot (the VM is powered off afterwards; start it again).
**Examples:**
```sh
navig vm revert
```

## `navig vm screenshot`
**Status:** `stable` · **Since:** 
Save a PNG of the VM's screen (needs VMware Tools running in the guest).
**Examples:**
```sh
navig vm screenshot
```

## `navig vm snapshot`
**Status:** `stable` · **Since:** 
Save the VM's current state under a name.
**Examples:**
```sh
navig vm snapshot
```

## `navig vm snapshots`
**Status:** `stable` · **Since:** 
The VM's snapshots.
**Examples:**
```sh
navig vm snapshots
```

## `navig vm ssh`
**Status:** `stable` · **Since:** 
Open a shell in the VM as its owner (extra arguments run as a command).
**Examples:**
```sh
navig vm ssh
```

## `navig vm start`
**Status:** `stable` · **Since:** 
Power a VM on.
**Examples:**
```sh
navig vm start
```

## `navig vm stop`
**Status:** `stable` · **Since:** 
Shut a VM down (cleanly, through the guest, unless --hard).
**Examples:**
```sh
navig vm stop
```

## `navig vm wait`
**Status:** `stable` · **Since:** 
Wait until first-boot provisioning has finished (and, for navig-os, the reboot).
**Examples:**
```sh
navig vm wait
```

## `navig voice budget`
**Status:** `stable` · **Since:** 
Show or set the daily budget for paid read-aloud voices.
**Examples:**
```sh
navig voice budget
```

## `navig voice list-voices`
**Status:** `stable` · **Since:** 
List available voices for a provider.
**Examples:**
```sh
navig voice list-voices
```

## `navig voice packs install`
**Status:** `stable` · **Since:** 
Download, checksum-verify and install one offline voice pack.
**Examples:**
```sh
navig voice packs install
```

## `navig voice packs list`
**Status:** `stable` · **Since:** 
Installed and available offline packs — engine state, size, license, status.
**Examples:**
```sh
navig voice packs list
```

## `navig voice packs remove`
**Status:** `stable` · **Since:** 
Delete one installed pack from disk (re-install downloads it again).
**Examples:**
```sh
navig voice packs remove
```

## `navig voice policy`
**Status:** `stable` · **Since:** 
Show or set which voices read-aloud may use.
**Examples:**
```sh
navig voice policy
```

## `navig voice speak`
**Status:** `stable` · **Since:** 
Synthesize speech from text.
**Examples:**
```sh
navig voice speak
```

## `navig voice transcribe`
**Status:** `stable` · **Since:** 
Transcribe audio file to text.
**Examples:**
```sh
navig voice transcribe
```

## `navig voice usage`
**Status:** `stable` · **Since:** 
Paid read-aloud characters per day, against the daily budget.
**Examples:**
```sh
navig voice usage
```

## `navig watch list`
**Status:** `stable` · **Since:** 
List active watches.
**Examples:**
```sh
navig watch list
```

## `navig watch start`
**Status:** `stable` · **Since:** 
Start watching a path for changes.
**Examples:**
```sh
navig watch start
```

## `navig web`
**Status:** `stable` · **Since:** 
Web server management - run without subcommand for help.
**Examples:**
```sh
navig web
```

## `navig web disable`
**Status:** `stable` · **Since:** 
Disable a web server site.
**Examples:**
```sh
navig web disable
```

## `navig web enable`
**Status:** `stable` · **Since:** 
Enable a web server site.
**Examples:**
```sh
navig web enable
```

## `navig web hestia add`
**Status:** `stable` · **Since:** 
Add HestiaCP user or domain.
**Examples:**
```sh
navig web hestia add
```

## `navig web hestia list`
**Status:** `stable` · **Since:** 
List HestiaCP resources (users, domains).
**Examples:**
```sh
navig web hestia list
```

## `navig web hestia remove`
**Status:** `stable` · **Since:** 
Remove HestiaCP user or domain.
**Examples:**
```sh
navig web hestia remove
```

## `navig web module-disable`
**Status:** `stable` · **Since:** 
Disable Apache module (Apache only).
**Examples:**
```sh
navig web module-disable
```

## `navig web module-enable`
**Status:** `stable` · **Since:** 
Enable Apache module (Apache only).
**Examples:**
```sh
navig web module-enable
```

## `navig web recommend`
**Status:** `stable` · **Since:** 
Display performance tuning recommendations.
**Examples:**
```sh
navig web recommend
```

## `navig web reload`
**Status:** `stable` · **Since:** 
Safely reload web server (tests config first).
**Examples:**
```sh
navig web reload
```

## `navig web test`
**Status:** `stable` · **Since:** 
Test web server configuration syntax.
**Examples:**
```sh
navig web test
```

## `navig web vhosts`
**Status:** `stable` · **Since:** 
List virtual hosts (enabled and available).
**Examples:**
```sh
navig web vhosts
```

## `navig whoami`
**Status:** `stable` · **Since:** 
Show your NAVIG node identity sigil card.
**Examples:**
```sh
navig whoami
```

## `navig wiki`
**Status:** `stable` · **Since:** 
Run navig wiki
**Examples:**
```sh
navig wiki
```

## `navig wiki add`
**Status:** `stable` · **Since:** 
Add a file to the wiki.
**Examples:**
```sh
navig wiki add
```

## `navig wiki edit`
**Status:** `stable` · **Since:** 
Open a wiki page in editor.
**Examples:**
```sh
navig wiki edit
```

## `navig wiki inbox process`
**Status:** `stable` · **Since:** 
Process inbox items with AI categorization.
**Examples:**
```sh
navig wiki inbox process
```

## `navig wiki init`
**Status:** `stable` · **Since:** 
Initialize wiki structure for current project.
**Examples:**
```sh
navig wiki init
```

## `navig wiki links broken`
**Status:** `stable` · **Since:** 
Find broken wiki links.
**Examples:**
```sh
navig wiki links broken
```

## `navig wiki list`
**Status:** `stable` · **Since:** 
List wiki pages.
**Examples:**
```sh
navig wiki list
```

## `navig wiki publish`
**Status:** `stable` · **Since:** 
Publish public wiki content.
**Examples:**
```sh
navig wiki publish
```

## `navig wiki rag add`
**Status:** `stable` · **Since:** 
Add content directly to the RAG knowledge base.
**Examples:**
```sh
navig wiki rag add
```

## `navig wiki rag query`
**Status:** `stable` · **Since:** 
Query the wiki knowledge base.
**Examples:**
```sh
navig wiki rag query
```

## `navig wiki rag rebuild`
**Status:** `stable` · **Since:** 
Rebuild the RAG index from wiki pages.
**Examples:**
```sh
navig wiki rag rebuild
```

## `navig wiki remove`
**Status:** `stable` · **Since:** 
Remove (archive) a wiki page.
**Examples:**
```sh
navig wiki remove
```

## `navig wiki search`
**Status:** `stable` · **Since:** 
Full-text search across wiki pages.
**Examples:**
```sh
navig wiki search
```

## `navig wiki show`
**Status:** `stable` · **Since:** 
View a wiki page.
**Examples:**
```sh
navig wiki show
```

## `navig wiki sync`
**Status:** `stable` · **Since:** 
Sync with global wiki.
**Examples:**
```sh
navig wiki sync
```

## `navig wire`
**Status:** `stable` · **Since:** 
Wire a folder into the agent ecosystem (workshop + .claude capability links).
**Examples:**
```sh
navig wire
```

## `navig work add`
**Status:** `stable` · **Since:** 
Add a new work item.
**Examples:**
```sh
navig work add
```

## `navig work archive`
**Status:** `stable` · **Since:** 
Archive a work item (moves to the 'archived' stage).
**Examples:**
```sh
navig work archive
```

## `navig work kinds`
**Status:** `stable` · **Since:** 
List all valid item kind names.
**Examples:**
```sh
navig work kinds
```

## `navig work list`
**Status:** `stable` · **Since:** 
List work items, optionally filtered.
**Examples:**
```sh
navig work list
```

## `navig work move`
**Status:** `stable` · **Since:** 
Move a work item to a new stage.
**Examples:**
```sh
navig work move
```

## `navig work show`
**Status:** `stable` · **Since:** 
Show details of a work item.
**Examples:**
```sh
navig work show
```

## `navig work stages`
**Status:** `stable` · **Since:** 
List all valid stage names.
**Examples:**
```sh
navig work stages
```

## `navig work update`
**Status:** `stable` · **Since:** 
Update fields on a work item.
**Examples:**
```sh
navig work update
```

## `navig wut`
**Status:** `stable` · **Since:** 
Print a concise context snapshot.
**Examples:**
```sh
navig wut
```
