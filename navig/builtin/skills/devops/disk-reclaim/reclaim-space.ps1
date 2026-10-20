# =====================================================================
#  reclaim-space.ps1
#  Finds and optionally deletes regenerable build/dependency folders
#  (node_modules, .next, dist, build, target, __pycache__, .venv, caches).
#
#  FAST: it prunes - it never descends into a folder it already matched,
#  so a 900 GB drive takes seconds, not minutes.
#
#  SAFE BY DEFAULT: reports only. Nothing is deleted without -Delete.
#
#  USAGE
#    .\reclaim-space.ps1                           # report C:\projects
#    .\reclaim-space.ps1 -Root C:\,C:\,C:\          # report several roots
#    .\reclaim-space.ps1 -Root C:\projects -MinMB 50
#    .\reclaim-space.ps1 -Root C:\projects -Delete  # actually delete
#    .\reclaim-space.ps1 -Root C:\projects -Delete -Only node_modules
# =====================================================================

param(
  [string[]]$Root = @('C:\projects'),
  [int]$MinMB = 20,
  [switch]$Delete,
  [string[]]$Only = @(),          # restrict to these kinds: node_modules,.next,dist,...
  [int]$MaxDepth = 10,
  [switch]$IncludeGit,
  [switch]$Quiet
)

$ErrorActionPreference = 'SilentlyContinue'

# Folders that are always regenerable. Pruned when found.
$KINDS = [ordered]@{
  'node_modules'    = 'node_modules'
  '.next'           = '.next'
  '.turbo'          = '.turbo'
  '.nuxt'           = '.nuxt'
  '.svelte-kit'     = '.svelte-kit'
  '.parcel-cache'   = '.parcel-cache'
  'dist'            = 'dist'
  'build'           = 'build'
  'out'             = 'out'
  'target'          = 'target'
  '__pycache__'     = '__pycache__'
  '.pytest_cache'   = '.pytest_cache'
  '.ruff_cache'     = '.ruff_cache'
  '.mypy_cache'     = '.mypy_cache'
  '.venv'           = '.venv'
  'venv'            = 'venv'
  'coverage'        = 'coverage'
  '.cache'          = '.cache'
  '.angular'        = '.angular'
  'obj'             = 'obj'
}
# Folders we never enter at all
$SKIP = @('$Recycle.Bin','System Volume Information','Windows','WinSxS','ProgramData',
          'AppData','node_modules','.git','.hg','.svn','OneDrive',
          # NEVER descend into anything that looks like a trash/archive holding area.
          # A folder can be named `.trash` and still hold irreplaceable media - the
          # name is not evidence. Skipping costs nothing; a wrong match is unrecoverable.
          '.trash','.Trash','.Trash-1000','trash','Trash','$RECYCLE.BIN','.recycle',
          'Archive','.archive','_archive','MEDIA-ARCHIVE','.snapshot','.snapshots')

if ($Only.Count -gt 0) {
  $kinds = @($Only | ForEach-Object { $_.Trim() })
} else {
  $kinds = @($KINDS.Keys)
}

function Get-DirBytes([string]$path) {
  $total = [int64]0
  $n = 0
  try {
    foreach ($f in [System.IO.Directory]::EnumerateFiles($path, '*', [System.IO.SearchOption]::AllDirectories)) {
      try { $total += (New-Object System.IO.FileInfo $f).Length; $n++ } catch {}
    }
  } catch {}
  return @($total, $n)
}

Write-Host "reclaim-space: scanning $($Root -join ', ')  (kinds: $($kinds -join ', '))" -ForegroundColor Cyan
$sw = [System.Diagnostics.Stopwatch]::StartNew()

$hits = New-Object System.Collections.Generic.List[object]
$stack = New-Object System.Collections.Generic.Stack[object]
foreach ($r in $Root) {
  if (Test-Path -LiteralPath $r) {
    $stack.Push(@{ Path = (Resolve-Path -LiteralPath $r).Path; Depth = 0 })
  } else { Write-Host "  skipped (missing): $r" -ForegroundColor DarkGray }
}

while ($stack.Count -gt 0) {
  $cur = $stack.Pop()
  if ($cur.Depth -gt $MaxDepth) { continue }
  try { $di = New-Object System.IO.DirectoryInfo $cur.Path } catch { continue }
  try { $subs = $di.EnumerateDirectories() } catch { continue }

  foreach ($d in $subs) {
    $name = $d.Name
    if ($kinds -contains $name) {
      $bytes, $files = Get-DirBytes $d.FullName
      $mb = [math]::Round($bytes / 1MB, 1)
      if ($mb -ge $MinMB) {
        $hits.Add([pscustomobject]@{
          Kind     = $name
          MB       = $mb
          Files    = $files
          Modified = $d.LastWriteTime.ToString('yyyy-MM-dd')
          Path     = $d.FullName
        })
        if (-not $Quiet) {
          Write-Host ("  {0,9:N1} MB  {1,-14} {2}" -f $mb, $name, $d.FullName) -ForegroundColor DarkGray
        }
      }
      continue   # PRUNE - never descend into a matched folder
    }
    if (-not $IncludeGit -and $SKIP -contains $name) { continue }
    $stack.Push(@{ Path = $d.FullName; Depth = $cur.Depth + 1 })
  }
}

$totalMB = [math]::Round((($hits | Measure-Object MB -Sum).Sum), 1)
$totalGB = [math]::Round($totalMB / 1024, 2)

Write-Host ""
Write-Host "  found $($hits.Count) folder(s), $totalMB MB ($totalGB GB)   in $([math]::Round($sw.Elapsed.TotalSeconds,1))s" -ForegroundColor Cyan

if ($hits.Count -eq 0) { return }

Write-Host ""
$hits | Group-Object Kind | Sort-Object { ($_.Group | Measure-Object MB -Sum).Sum } -Descending |
  ForEach-Object {
    $mb = [math]::Round((($_.Group | Measure-Object MB -Sum).Sum),1)
    Write-Host ("  by kind: {0,-16} {1,9:N1} MB   ({2} folders)" -f $_.Name, $mb, $_.Count) -ForegroundColor Yellow
  }

$report = Join-Path ([IO.Path]::GetTempPath()) "navig-reclaim-report-$(Get-Date -Format yyyyMMdd-HHmmss).csv"
$hits | Sort-Object MB -Descending | Export-Csv -LiteralPath $report -NoTypeInformation -Encoding UTF8
Write-Host ""
Write-Host "  full list: $report" -ForegroundColor Green
Write-Host ""
Write-Host "  largest 15:" -ForegroundColor Cyan
$hits | Sort-Object MB -Descending | Select-Object -First 15 |
  ForEach-Object { Write-Host ("    {0,9:N1} MB  {1,-14} {2}" -f $_.MB, $_.Kind, $_.Path) }

if (-not $Delete) {
  Write-Host ""
  Write-Host "  DRY RUN - nothing deleted." -ForegroundColor Yellow
  Write-Host "  Re-run with -Delete to remove them." -ForegroundColor Yellow
  Write-Host "  NOTE: deleting node_modules means the next 'npm/pnpm install' rebuilds it." -ForegroundColor DarkGray
  Write-Host "        Stop dev servers and watchers first." -ForegroundColor DarkGray
  return
}

# ---- delete ----
Write-Host ""
Write-Host "  DELETING $($hits.Count) folder(s) ..." -ForegroundColor Red
$ok = 0; $fail = 0; $freed = 0
foreach ($h in ($hits | Sort-Object MB -Descending)) {
  try {
    Remove-Item -LiteralPath $h.Path -Recurse -Force -ErrorAction Stop
    $ok++; $freed += $h.MB
    Write-Host ("    deleted {0,9:N1} MB  {1}" -f $h.MB, $h.Path) -ForegroundColor DarkGray
  } catch {
    # reserved names (nul) and long paths need the extended-path API
    try {
      [System.IO.Directory]::Delete("\\?\$($h.Path)", $true)
      $ok++; $freed += $h.MB
      Write-Host ("    deleted {0,9:N1} MB  {1}  (via \\?\)" -f $h.MB, $h.Path) -ForegroundColor DarkGray
    } catch {
      $fail++
      Write-Host ("    FAILED  {0}  -- {1}" -f $h.Path, $_.Exception.Message) -ForegroundColor Red
    }
  }
}
Write-Host ""
Write-Host ("  deleted {0} folder(s) = {1:N1} MB ({2:N2} GB)   failed: {3}" -f $ok, $freed, ($freed/1024), $fail) -ForegroundColor Green

