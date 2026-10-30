---
name: "disk-reclaim"
description: "Find and reclaim disk space lost to regenerable build artifacts — Rust target, node_modules, .next, dist, build, out, __pycache__, .venv and tool caches — using a pruned walk that scans a 900 GB drive in seconds. Reports by default; deletes only on explicit confirmation."
user-invocable: true
version: "1.0.0"
author: "NAVIG Core Team"
category: "devops"
risk-level: "destructive"
confirmation-required: true

navig-commands:
  - name: "scan"
    syntax: "navig disk scan <root> [--min-mb <n>] [--only <kinds>]"
    description: "Report regenerable build folders under <root> with sizes. Never deletes."
    risk: "safe"

  - name: "clean"
    syntax: "navig disk clean <root> [--only <kinds>] [--min-mb <n>]"
    description: "Delete the regenerable build folders found under <root>."
    risk: "destructive"
    confirmation_msg: "⚠️  DESTRUCTIVE: this deletes node_modules / target / .next / dist under '{root}'. Every deleted folder must be rebuilt (Rust targets can take 10-30 min). Stop dev servers first. Continue?"
# Written against a planned command surface that is not implemented yet
# (KNOWN_MISSING in tests/quality/test_content_command_refs_resolve.py).
activatable: false
---

# Reclaiming disk space from build artifacts

> **Status: planned verb — do not call it yet.**
> This skill describes `navig disk scan` / `navig disk clean`, which **do not exist in navig
> yet** (they are being built on `skill/disk-reclaim`). Calling them fails with *No such
> command 'disk'*. Until the verb ships, do this by hand or with the patterns below; remove
> this note in the commit that lands `navig disk`.

Generated folders are the largest, safest thing to delete on a dev machine: they are
reproducible from source, and every toolchain rebuilds them on demand. The trap is that the
obvious approach — a full recursive size scan — takes tens of minutes on a real drive,
because the biggest offenders contain millions of small files.

**Reclaim by pattern, not by size scan.** Walk directories; the moment you match a known
regenerable folder, record it and *do not descend into it*. A 900 GB drive finishes in seconds.

## Recognize the offenders

| Folder | Produced by | Cost to rebuild | Typical size |
|---|---|---|---|
| **`target`** | Cargo (Rust), Tauri, wasm-pack | **high** — 5–30 min per crate | **5–70 GB** |
| **`node_modules`** | npm / pnpm / yarn | low — `pnpm install` | 0.3–65 GB |
| `.next` | Next.js build + cache | medium | 0.2–7 GB |
| `dist` / `build` / `out` | bundlers, exporters | medium | 0.02–13 GB |
| `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache` | Python | zero | MB |
| `.venv` / `venv` | Python venv | low — recreate | 0.05–0.5 GB |
| `.turbo`, `.parcel-cache`, `.angular`, `.svelte-kit`, `.nuxt` | tool caches | zero | MB–GB |
| `obj` | MSBuild / .NET | low | MB–GB |

**Ranking rule for a Tauri/React monorepo:** `src-tauri/target` > `node_modules` > `.next`.

Measured on the workstation this skill was written against: 16 folders held **342 GB**, of
which **11 were Rust `target` folders totalling ~250 GB**. The single largest was one
`apps/*/src-tauri/target` at **68.6 GB**. Deleting every `node_modules` on that drive would
have recovered far less than deleting the `target` folders.

## Run it

```powershell
# report only - nothing is deleted
.\reclaim-space.ps1 -Root C:\projects

# several drives
.\reclaim-space.ps1 -Root C:\,D:\,E:\ -MinMB 50

# only the expensive stuff
.\reclaim-space.ps1 -Root C:\projects -Only target,node_modules

# actually delete
.\reclaim-space.ps1 -Root C:\projects -Delete

# one class only
.\reclaim-space.ps1 -Root C:\projects -Only target -Delete
```

Output is grouped by kind, with a per-folder CSV (`reclaim-report-<stamp>.csv`) so the list
survives terminal scrollback.

## How the pruned walk works

```text
stack = [root]
while stack:
    for each subdirectory d:
        if d.name in KINDS:      # target, node_modules, .next, ...
            measure d; record; continue   # PRUNE - never enter it
        if d.name in SKIP:       # Windows, AppData, .git, ProgramData
            continue
        push d
```

Pruning is what makes it fast. A `target` containing 900,000 files is recorded in a single
`EnumerateFiles` pass and never walked again.

## Safety

- **Dry run by default.** `-Delete` is required to remove anything.
- **Stop dev servers and watchers first.** Deleting `node_modules` under a running
  `next dev` or `tauri dev` leaves that process broken.
- **`target` is slow to rebuild.** Deleting a 68 GB Rust target costs minutes of build time
  later. For projects you are actively working on, prefer `cargo clean` in that one project.
- **`node_modules` needs a reinstall.** Run the project's own `pnpm install` / `npm ci`
  afterwards. Never delete a `node_modules` you cannot reinstall (offline, missing lockfile).
- **Never point `-Root` at `C:\`, `C:\Program Files` or a whole user profile.** `dist` and
  `build` also exist inside installed applications.
- **Windows long paths and reserved names.** `Remove-Item` fails on paths over 260 characters
  and on folders containing a file named `nul`, `con`, `aux` or `prn`. The script retries
  those with the extended-path API: `[System.IO.Directory]::Delete("\\?\$path", $true)`.

## Check the result

```powershell
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
  Select-Object DeviceID,
    @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB)}},
    @{n='Pct';e={[math]::Round(100*$_.FreeSpace/$_.Size)}}
```

Keep 15–20 % free on a drive that runs builds. Below that, git writes, package installs and
compiler temp files compete for space and everything slows down.

## Gotchas that bite

- **A wrong `-Root` silently reports nothing.** Verify the path exists before trusting a zero result.
- **Symlinks and junctions** can double-count. Check with `Get-Item -Attributes ReparsePoint`
  if totals look inflated.
- **Cloud-synced folders** (OneDrive, Drive) re-download during a scan and appear to grow.
- **`target` inside a git worktree** is separate from the main checkout's own `target`.
  Delete both, or the space returns after the next build.
- **A folder named `.trash` is not automatically trash.** On the workstation this skill was
  written against, one trash folder held **3.4 TB** — 2.25 TB of it a personal video archive (files from
  2004–2026), 616 GB of production assets, and 130 GB of phone imports. The name was a leftover
  from a drive reorganisation. `.trash`, `$RECYCLE.BIN`, `Archive`, `.archive`, `.snapshot` and
  `trash` are therefore in the `SKIP` list and are never descended into. **Never infer
  disposability from a folder name — check the contents and the file dates first.**
