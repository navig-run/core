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

# ─────────────────────────────────────────────────────────────────────────────
# Get-PythonScriptsDir
# ─────────────────────────────────────────────────────────────────────────────
Describe "Get-PythonScriptsDir" {
    It "falls back to parent-dir/Scripts when sysconfig returns a nonexistent path" {
        $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
        New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
        $scriptsDir = Join-Path $tmpDir "Scripts"
        New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null
        try {
            $result = Get-PythonScriptsDir -PythonExe (Join-Path $tmpDir "python.exe")
            $result | Should -Be $scriptsDir
        } finally {
            Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

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
    It "uses --quiet and --disable-pip-version-check" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Install-Navig.*?\n\}')
        $fn.Value | Should -Match '--quiet'
        $fn.Value | Should -Match '--disable-pip-version-check'
    }

    It "redirects stderr to a temp file (does not swallow errors)" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Install-Navig.*?\n\}')
        $fn.Value | Should -Match 'RedirectStandardError'
    }

    It "returns false on pip failure (does not call exit directly)" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Install-Navig.*?\n\}')
        $fn.Value | Should -Match 'return \$false'
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
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Main \{.*?\n\}')
        $fn.Value | Should -Match "Print-Section.*Environment"
        $fn.Value | Should -Match "Print-Section.*Requirements"
        $fn.Value | Should -Match "Print-Section.*Install"
        $fn.Value | Should -Match "Print-Section.*Verify"
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
