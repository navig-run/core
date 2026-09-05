#Requires -Version 5.1
# tests/installer/install.Tests.ps1
# Pester v5 unit tests for install.ps1
# Run: pwsh -Command "Invoke-Pester tests/installer/install.Tests.ps1 -Output Detailed"

BeforeAll {
    $RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
    $Script:InstallerPath = Join-Path $RepoRoot "install.ps1"

    # Dot-source the installer with the entry-point guard so functions are loaded
    # without Main() actually executing.
    $env:NAVIG_INSTALL_PS1_NO_RUN = "1"
    . $Script:InstallerPath
}

AfterAll {
    Remove-Item Env:\NAVIG_INSTALL_PS1_NO_RUN -ErrorAction SilentlyContinue
}

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
Describe "Initialize-NavigConfig" {
    It "creates .navig dir under USERPROFILE" {
        $tmpHome = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
        New-Item -ItemType Directory -Path $tmpHome -Force | Out-Null
        $oldProfile = $env:USERPROFILE
        $env:USERPROFILE = $tmpHome
        try {
            Initialize-NavigConfig
            Test-Path (Join-Path $tmpHome ".navig") | Should -Be $true
        } finally {
            $env:USERPROFILE = $oldProfile
            Remove-Item $tmpHome -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Normalize-NavigAction
# ─────────────────────────────────────────────────────────────────────────────
Describe "Normalize-NavigAction" {
    It "returns empty string for blank input" {
        Normalize-NavigAction "" | Should -Be ""
    }
    It "normalizes 'Install' to 'install'" {
        Normalize-NavigAction "Install" | Should -Be "install"
    }
    It "normalizes 'Uninstall' to 'uninstall'" {
        Normalize-NavigAction "Uninstall" | Should -Be "uninstall"
    }
    It "normalizes 'repair' to 'reinstall'" {
        Normalize-NavigAction "repair" | Should -Be "reinstall"
    }
    It "throws on unknown action" {
        { Normalize-NavigAction "deploy" } | Should -Throw
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Add-NavigBinToPath
# ─────────────────────────────────────────────────────────────────────────────
Describe "Add-NavigBinToPath" {
    It "adds dir to current session PATH" {
        $tmpBin = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
        New-Item -ItemType Directory -Path $tmpBin -Force | Out-Null
        $before = $env:PATH
        try {
            Add-NavigBinToPath -BinDir $tmpBin
            $env:PATH | Should -BeLike "*$tmpBin*"
        } finally {
            $env:PATH = $before
            Remove-Item $tmpBin -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    It "does not duplicate a dir already on PATH" {
        $tmpBin = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
        New-Item -ItemType Directory -Path $tmpBin -Force | Out-Null
        $env:PATH = "$tmpBin;$env:PATH"
        $before = $env:PATH
        try {
            Add-NavigBinToPath -BinDir $tmpBin
            ($env:PATH -split ';' | Where-Object { $_ -eq $tmpBin }).Count | Should -Be 1
        } finally {
            $env:PATH = $before
            Remove-Item $tmpBin -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    It "is a no-op for a nonexistent directory" {
        $before = $env:PATH
        Add-NavigBinToPath -BinDir "C:\does\not\exist\fake_navig_bin"
        $env:PATH | Should -Be $before
    }
}

# NOTE: the `Get-PythonScriptsDir` block was removed here. That function located the
# Scripts/ dir of a SYSTEM Python so the installer could find the console script — a
# need that disappeared when install.ps1 moved to a self-contained uv runtime plus a
# fixed launcher shim (New-NavigShim). The function no longer exists, so the test could
# only ever throw CommandNotFoundException; there is no successor behaviour to re-point
# it at. Deleted rather than kept green.

# ─────────────────────────────────────────────────────────────────────────────
# Test-NavigCommand  (source-level checks — does not exec navig)
# ─────────────────────────────────────────────────────────────────────────────
Describe "Test-NavigCommand — source checks" {
    It "reloads PATH from registry before probing" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Test-NavigCommand.*\n\}')
        $fn.Value | Should -Match 'GetEnvironmentVariable.*PATH.*Machine'
    }

    It "falls back to ScriptsDir\navig.exe when Get-Command returns nothing" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Test-NavigCommand.*\n\}')
        $fn.Value | Should -Match 'navig\.exe'
    }

    It "returns null on failure (does not call exit)" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Test-NavigCommand.*\n\}')
        $fn.Value | Should -Match 'return \$null'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Install-Navig — source checks
# ─────────────────────────────────────────────────────────────────────────────
Describe "Install-Navig — source checks" {
    # ⚠ The pattern MUST pin the end of the function name. `function Install-Navig.*?`
    # matches the FIRST declaration whose name merely STARTS with it — Install-NavigUv,
    # 100 lines earlier — so all three assertions here were reading the wrong function.
    # Two failed for that reason and the third PASSED VACUOUSLY (Install-NavigUv also
    # contains `return $false`). `function Install-Navig \{` matches only the real one.
    BeforeAll {
        $src = Get-Content $Script:InstallerPath -Raw
        $Script:InstallNavigFn = [regex]::Match($src, '(?s)function Install-Navig \{.*?\n\}').Value
        $Script:InvokeUvFn     = [regex]::Match($src, '(?s)function Invoke-NavigUv \{.*?\n\}').Value
    }

    It "targets the real function, not Install-NavigUv" {
        # Guards the regex above: without it the rest of this block asserts nothing.
        $Script:InstallNavigFn | Should -Not -BeNullOrEmpty
        $Script:InstallNavigFn | Should -Not -Match 'Get-NavigUvUrl'
    }

    It "installs into the ISOLATED venv via uv, never system pip" {
        # Replaces an assertion on `--quiet` / `--disable-pip-version-check`: those are
        # pip flags, and the installer no longer shells out to pip. It runs
        # `uv pip install --python <runtime venv>`, which is what keeps system Python
        # untouched — the property actually worth pinning.
        $Script:InstallNavigFn | Should -Match 'Invoke-NavigUv'
        $Script:InstallNavigFn | Should -Match '--python'
        $Script:InstallNavigFn | Should -Match 'RUNTIME_VENV_PY'
    }

    It "surfaces the failure output instead of swallowing it" {
        # The stderr capture moved into Invoke-NavigUv (which redirects it to a temp
        # file and returns the last lines as .Tail); Install-Navig's half of the
        # contract is to PRINT that tail rather than discard it.
        $Script:InstallNavigFn | Should -Match '\$r\.Tail'
        $Script:InstallNavigFn | Should -Match 'Write-NavHint'
        $Script:InvokeUvFn     | Should -Match 'RedirectStandardError'
    }

    It "returns false on failure (does not call exit directly)" {
        $Script:InstallNavigFn | Should -Match 'return \$false'
        $Script:InstallNavigFn | Should -Not -Match '\bexit \d'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Reinstall path
# ─────────────────────────────────────────────────────────────────────────────
Describe "Reinstall path — uninstall is called" {
    It "calls Invoke-NavigUninstall with -ForReinstall during reinstall" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Main \{.*?\n\}')
        $fn.Value | Should -Match 'Invoke-NavigUninstall.*ForReinstall'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Stop-NavigBackgroundArtifacts — the daemon must actually be stopped
#
# Source checks, deliberately: running the real function would stop the daemon and
# delete the scheduled task on whatever machine executes the suite.
# ─────────────────────────────────────────────────────────────────────────────
Describe "Stop-NavigBackgroundArtifacts — source checks" {
    BeforeAll {
        $src = Get-Content $Script:InstallerPath -Raw
        $Script:StopFn = [regex]::Match(
            $src, '(?s)function Stop-NavigBackgroundArtifacts \{.*?\n\}').Value
        $Script:UninstallFn = [regex]::Match(
            $src, '(?s)function Invoke-NavigUninstall \{.*?\n\}').Value
    }

    It "targets the real function" {
        $Script:StopFn | Should -Not -BeNullOrEmpty
    }

    It "asks the runtime to stop its own daemon, not just Get-Process navig" {
        # The daemon runs as pythonw.exe with "navig" deliberately absent from its command
        # line, so `Get-Process navig` matches nothing live. Without this the interpreter
        # keeps ~/.navig/runtime/venv open and the runtime removal can fail on a file lock.
        $Script:StopFn | Should -Match 'service.*uninstall'
        $Script:StopFn | Should -Match 'RUNTIME_VENV'
    }

    It "bounds the stop so a hung daemon cannot hang the uninstall" {
        # Register-NavigDaemon uses `Start-Process -Wait`, which waits forever. The
        # uninstall path must not: it waits with a timeout and then kills the child.
        $Script:StopFn | Should -Match 'WaitForExit'
        $Script:StopFn | Should -Match '\$p\.Kill\(\)'
    }

    It "keeps the blunt fallbacks, so a failed stop is no worse than before" {
        $Script:StopFn | Should -Match 'Get-Process navig'
        $Script:StopFn | Should -Match 'Stop-Service'
    }

    It "runs before the files are removed" {
        # Ordering is load-bearing: the venv must still exist for the runtime to be
        # able to stop itself.
        $stopAt   = $Script:UninstallFn.IndexOf('Stop-NavigBackgroundArtifacts')
        $removeAt = $Script:UninstallFn.IndexOf('Remove-NavigFiles')
        $stopAt   | Should -BeGreaterThan -1
        $removeAt | Should -BeGreaterThan -1
        $stopAt   | Should -BeLessThan $removeAt
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# No pipx/git artefacts remain
# ─────────────────────────────────────────────────────────────────────────────
Describe "Pip-only enforcement" {
    It "contains no pipx function definitions" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Not -Match 'function Install-Pipx'
        $src | Should -Not -Match 'function Find-Pipx'
    }

    It "contains no Install-NavigGit function" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Not -Match 'function Install-NavigGit'
    }

    It "contains no Invoke-WithSpinner function" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Not -Match 'function Invoke-WithSpinner'
    }

    It "Main calls Install-Navig (pip) not any git/pipx function" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Main \{.*?\n\}')
        $fn.Value | Should -Match 'Install-Navig'
        $fn.Value | Should -Not -Match 'Install-NavigGit'
        $fn.Value | Should -Not -Match 'Install-NavigPip'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Terminal capability detection
# ─────────────────────────────────────────────────────────────────────────────
Describe "Initialize-Terminal" {
    It "sets script-level NavColor to a boolean" {
        Initialize-Terminal
        $script:NavColor | Should -BeOfType [bool]
    }

    It "respects NO_COLOR env var" {
        $env:NO_COLOR = "1"
        try {
            Initialize-Terminal
            $script:NavColor | Should -Be $false
        } finally {
            Remove-Item Env:\NO_COLOR -ErrorAction SilentlyContinue
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Verbose output helper
# ─────────────────────────────────────────────────────────────────────────────
Describe "Write-NavVerbose" {
    It "produces no output when Verbose is false" {
        $script:Verbose = $false
        $output = Write-NavVerbose "should not appear" 6>&1
        $output | Should -BeNullOrEmpty
    }

    It "is gated on the Verbose flag in source" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Match 'function Write-NavVerbose'
        $src | Should -Match '\$Verbose'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Print-Header (branded box)
# ─────────────────────────────────────────────────────────────────────────────
Describe "Print-Header" {
    It "exists as a function" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Match 'function Print-Header'
    }

    It "contains a taglines array with multiple entries" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Match '\$script:Taglines\s*=\s*@\('
        $src | Should -Match 'NAVIG|servers|SSH|CLI'
    }

    It "contains unicode box-drawing characters or ASCII fallback" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Print-Header.*?\n\}')
        $fn.Value | Should -Match '(tl|tr|bl|br|hz|sym)'
    }

    It "uses Get-Random to select tagline dynamically" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Print-Header.*?\n\}')
        $fn.Value | Should -Match 'Get-Random'
        $fn.Value | Should -Match '\$script:Taglines'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Print-Done (success block)
# ─────────────────────────────────────────────────────────────────────────────
Describe "Print-Done" {
    It "exists as a function" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Match 'function Print-Done'
    }

    It "contains 'navig init'" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Print-Done.*?\n\}')
        $fn.Value | Should -Match 'navig init'
    }

    It "contains 'navig --version'" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Print-Done.*?\n\}')
        $fn.Value | Should -Match 'navig --version'
    }

    It "contains box-drawing elements" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Print-Done.*?\n\}')
        $fn.Value | Should -Match '(tl|tr|bl|br|hz|sym)'
    }

    It "does not contain old magenta box markers" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Not -Match 'Show-SuccessBanner'
        $src | Should -Not -Match '\+====+'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Print-Failure (error block)
# ─────────────────────────────────────────────────────────────────────────────
Describe "Print-Failure" {
    It "exists as a function" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Match 'function Print-Failure'
    }

    It "has Problem / Fix / Run rows" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Print-Failure.*?\n\}')
        $fn.Value | Should -Match 'Problem'
        $fn.Value | Should -Match 'Fix'
        $fn.Value | Should -Match 'Run'
    }

    It "highlights the Run command in Yellow" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Print-Failure.*?\n\}')
        $fn.Value | Should -Match 'Yellow'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Layout constants
# ─────────────────────────────────────────────────────────────────────────────
Describe "Layout constants" {
    It "defines LW (box width) constant" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Match '\$script:LW\s*='
    }

    It "defines LB (label column) constant" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Match '\$script:LB\s*='
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Main — phased structure
# ─────────────────────────────────────────────────────────────────────────────
Describe "Main — phased structure" {
    It "calls Print-Section for each phase" {
        # "Requirements" was the pip-era phase that probed for a system Python; the uv
        # rewrite replaced it with "Runtime" (build the isolated interpreter + venv) and
        # added "Daemon". Asserting the phases the installer ACTUALLY prints keeps this
        # honest — and asserting all five means dropping one from the user-visible
        # progress output fails the build.
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Main \{.*?\n\}')
        foreach ($phase in @("Environment", "Runtime", "Install", "Verify", "Daemon")) {
            $fn.Value | Should -Match "Print-Section.*$phase"
        }
    }

    It "calls Print-Done after successful install" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Main \{.*?\n\}')
        $fn.Value | Should -Match 'Print-Done'
    }

    It "calls Initialize-Terminal before Print-Header" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Main \{.*?\n\}')
        $initIdx   = $fn.Value.IndexOf('Initialize-Terminal')
        $headerIdx = $fn.Value.IndexOf('Print-Header')
        $initIdx   | Should -BeLessThan $headerIdx
    }

    It "calls Print-Failure on python-not-found" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Main \{.*?\n\}')
        $fn.Value | Should -Match 'Print-Failure'
    }
}
