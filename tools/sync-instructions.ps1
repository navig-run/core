#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Sync NAVIG agent instruction files across all tool targets (VS Code, Claude, Cursor, Codex).

.DESCRIPTION
    Single Source of Truth propagation script.

    Source tree  : .github/copilot-instructions.md  (full system prompt)
                   .github/instructions/*.instructions.md (per-concern rules)

    Targets:
      .agents/AGENTS.md              ← copilot-instructions.md (SKIP-tagged sections removed)
      .agents/rules/<name>.md        ← each instructions file, YAML front-matter stripped
      .agents/.versions.json         ← MD5 checksum manifest (detects drift on next run)

.USAGE
    .\scripts\sync-instructions.ps1             # dry-run: show what would change
    .\scripts\sync-instructions.ps1 -Apply      # write files + update manifest
    .\scripts\sync-instructions.ps1 -Apply -Verbose  # full diff output

.NOTES
    Idempotent: safe to re-run. Only writes files whose content has changed.
    No external dependencies — pure PowerShell built-ins only.
#>

[CmdletBinding()]
param(
    # When set, files are actually written. Without this flag the script is a dry-run.
    [switch] $Apply,

    # Show a unified-style diff for every changed file.
    [switch] $ShowDiff
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─── Paths ───────────────────────────────────────────────────────────────────

$Root           = Split-Path $PSScriptRoot -Parent
$GithubDir      = Join-Path $Root ".github"
$InstructionDir = Join-Path $GithubDir "instructions"
$CopilotMaster  = Join-Path $GithubDir "copilot-instructions.md"
$AgentsDir      = Join-Path $Root ".agents"
$RulesDir       = Join-Path $AgentsDir "rules"
$AgentsMd       = Join-Path $AgentsDir "AGENTS.md"
$VersionsFile   = Join-Path $AgentsDir ".versions.json"

# ─── Name mapping: instructions filename stem → rules filename ───────────────
# Key   = stem of .github/instructions/<key>.instructions.md
# Value = filename written to .agents/rules/<value>.md

$NameMap = [ordered]@{
    "contributor"    = "contributor-rules"
    "dev"            = "dev-rules"
    "devloop"        = "devloop"
    "directives"     = "directives"
    "exception-policy" = "exception-policy"
    "git"            = "git"
    "navig"          = "cli-navig"
    "owner"          = "architect"
    "wiki"           = "wiki"
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

function Get-MD5([string]$text) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
    $hash  = [System.Security.Cryptography.MD5]::Create().ComputeHash($bytes)
    return ($hash | ForEach-Object { $_.ToString("x2") }) -join ""
}

function Strip-FrontMatter([string]$content) {
    <#
    Remove YAML front-matter block:
        ---
        applyTo: '**'
        ---
    and any leading blank lines that follow it.
    #>
    if ($content -match '(?s)\A---\s*\r?\n.*?\r?\n---\s*\r?\n') {
        $content = $content -replace '(?s)\A---\s*\r?\n.*?\r?\n---\s*\r?\n', ''
    }
    return $content.TrimStart("`r", "`n")
}

function Strip-SkipTags([string]$content) {
    <#
    Remove lines containing SKIP directives, e.g.:
        ## Agent Roster <!-- SKIP:cursor SKIP:cline -->
    and the content block that follows until the next top-level heading.
    Used when generating AGENTS.md from copilot-instructions.md.
    #>
    # Remove inline <!-- SKIP:... --> annotations from headings (keep the heading)
    $content = $content -replace '\s*<!-- SKIP:[^>]+ -->', ''
    return $content
}

function Read-Versions {
    if (Test-Path $VersionsFile) {
        try {
            return Get-Content $VersionsFile -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable
        } catch {
            Write-Warning "Could not parse .versions.json — starting fresh manifest."
        }
    }
    return @{}
}

function Write-Versions([hashtable]$manifest) {
    # Build sorted PSCustomObject so ConvertTo-Json serialises correctly
    $obj = [PSCustomObject]@{}
    foreach ($key in ($manifest.Keys | Sort-Object)) {
        $entry = $manifest[$key]
        $obj | Add-Member -NotePropertyName $key -NotePropertyValue (
            [PSCustomObject]@{ template = $entry.template; installed = $entry.installed }
        )
    }
    $obj | ConvertTo-Json -Depth 4 | Set-Content $VersionsFile -Encoding UTF8 -NoNewline
}

function Write-FileIfChanged([string]$path, [string]$newContent, [string]$label, [hashtable]$manifest) {
    $relKey  = (Resolve-Path -Relative $path -ErrorAction SilentlyContinue) -replace '^\.\\', ''
    $newHash = Get-MD5 $newContent

    $existingHash = ""
    if (Test-Path $path) {
        $existingHash = Get-MD5 (Get-Content $path -Raw -Encoding UTF8)
    }

    $manifestEntry = @{ template = $newHash; installed = $newHash }

    if ($newHash -eq $existingHash) {
        Write-Host "  [dim]  ── unchanged  $label[/dim]" -ForegroundColor DarkGray
        $manifest[$relKey] = $manifestEntry
        return
    }

    if ($Apply) {
        $dir = Split-Path $path -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        [System.IO.File]::WriteAllText($path, $newContent, [System.Text.Encoding]::UTF8)
        Write-Host "  ✔  written   $label" -ForegroundColor Cyan
    } else {
        Write-Host "  ○  would write  $label" -ForegroundColor Yellow
    }

    if ($ShowDiff -and (Test-Path $path)) {
        $old = (Get-Content $path -Raw -Encoding UTF8) -split "`n"
        $new = $newContent -split "`n"
        $maxLines = [Math]::Max($old.Count, $new.Count)
        $shown = 0
        for ($i = 0; $i -lt $maxLines -and $shown -lt 20; $i++) {
            $o = if ($i -lt $old.Count) { $old[$i] } else { "" }
            $n = if ($i -lt $new.Count) { $new[$i] } else { "" }
            if ($o -ne $n) {
                Write-Host "    - $o" -ForegroundColor Red
                Write-Host "    + $n" -ForegroundColor Green
                $shown++
            }
        }
        if ($shown -eq 20) { Write-Host "    … (truncated)" -ForegroundColor DarkGray }
    }

    $manifest[$relKey] = $manifestEntry
}

# ─── Main ─────────────────────────────────────────────────────────────────────

Push-Location $Root
try {
    $manifest  = Read-Versions
    $changed   = 0
    $missing   = 0

    Write-Host ""
    Write-Host "  NAVIG sync-instructions" -ForegroundColor Cyan
    Write-Host "  $(if ($Apply) { 'APPLY mode' } else { 'DRY-RUN mode  (pass -Apply to write)' })" -ForegroundColor DarkGray
    Write-Host ""

    # ── 1. copilot-instructions.md → .agents/AGENTS.md ──────────────────────

    Write-Host "  [1/3] Syncing AGENTS.md from copilot-instructions.md" -ForegroundColor White

    if (-not (Test-Path $CopilotMaster)) {
        Write-Warning "  MISSING: $CopilotMaster"
        $missing++
    } else {
        $raw     = Get-Content $CopilotMaster -Raw -Encoding UTF8
        $stripped = Strip-SkipTags $raw
        Write-FileIfChanged $AgentsMd $stripped "AGENTS.md" $manifest
    }

    Write-Host ""

    # ── 2. instructions/*.instructions.md → .agents/rules/*.md ──────────────

    Write-Host "  [2/3] Syncing .agents/rules/ from .github/instructions/" -ForegroundColor White

    foreach ($stem in $NameMap.Keys) {
        $srcFile  = Join-Path $InstructionDir "${stem}.instructions.md"
        $dstName  = "$($NameMap[$stem]).md"
        $dstFile  = Join-Path $RulesDir $dstName

        if (-not (Test-Path $srcFile)) {
            Write-Host "  ⚠  missing source  ${stem}.instructions.md  (skipped)" -ForegroundColor DarkYellow
            $missing++
            continue
        }

        $raw      = Get-Content $srcFile -Raw -Encoding UTF8
        $stripped = Strip-FrontMatter $raw
        Write-FileIfChanged $dstFile $stripped $dstName $manifest
    }

    Write-Host ""

    # ── 3. Audit: rules files that exist but are not in the map ─────────────

    Write-Host "  [3/3] Auditing untracked files in .agents/rules/" -ForegroundColor White

    if (Test-Path $RulesDir) {
        $mappedTargets = $NameMap.Values | ForEach-Object { "$_.md" }
        $onDisk = Get-ChildItem $RulesDir -Filter "*.md" | Select-Object -ExpandProperty Name
        foreach ($f in $onDisk) {
            if ($f -notin $mappedTargets) {
                Write-Host "  ⚠  untracked rule file (not in NameMap): $f" -ForegroundColor DarkYellow
            }
        }
    }

    # ── 4. Clean stale ghost entries from manifest ───────────────────────────

    # Remove known-stale bun entries
    $ghosts = @(
        "hooks\enforce-bun-install.sh",
        "hooks\enforce-bun-run.py",
        "hooks/enforce-bun-install.sh",
        "hooks/enforce-bun-run.py"
    )
    foreach ($g in $ghosts) {
        if ($manifest.ContainsKey($g)) {
            $manifest.Remove($g)
            Write-Host "  ✂  removed stale ghost: $g" -ForegroundColor DarkRed
        }
    }

    # Deduplicate settings/claude.json vs settings\claude.json
    $dupe1 = "settings/claude.json"
    $dupe2 = "settings\claude.json"
    if ($manifest.ContainsKey($dupe1) -and $manifest.ContainsKey($dupe2)) {
        $manifest.Remove($dupe1)
        Write-Host "  ✂  removed duplicate key: $dupe1" -ForegroundColor DarkRed
    }

    # ── 5. Write updated manifest ────────────────────────────────────────────

    if ($Apply) {
        Write-Versions $manifest
        Write-Host ""
        Write-Host "  ✔  .agents/.versions.json updated" -ForegroundColor Cyan
    } else {
        Write-Host ""
        Write-Host "  ○  .versions.json not written (dry-run)" -ForegroundColor DarkGray
    }

    # ── Summary ──────────────────────────────────────────────────────────────

    Write-Host ""
    Write-Host "  ─────────────────────────────────────────" -ForegroundColor DarkGray
    if ($missing -gt 0) {
        Write-Host "  ⚠  $missing source file(s) missing — review output above" -ForegroundColor Yellow
    }
    if (-not $Apply) {
        Write-Host "  Run with -Apply to write changes." -ForegroundColor Yellow
    } else {
        Write-Host "  Done." -ForegroundColor Green
    }
    Write-Host ""

} finally {
    Pop-Location
}
