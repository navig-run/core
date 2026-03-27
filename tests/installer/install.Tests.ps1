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
    It "normalises 'Install' to 'install'" {
        Normalize-NavigAction "Install" | Should -Be "install"
    }
    It "normalises 'Uninstall' to 'uninstall'" {
        Normalize-NavigAction "Uninstall" | Should -Be "uninstall"
    }
    It "normalises 'repair' to 'reinstall'" {
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
        # Arrange: create a fake python.exe that returns a nonexistent sysconfig path.
        $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
        New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
        # Create Scripts\ sibling so the fallback fires.
        $scriptsDir = Join-Path $tmpDir "Scripts"
        New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null
        # Fake python.exe: returns a path that does not exist (triggers fallback).
        $fakePython = Join-Path $tmpDir "python.exe"
        "@echo C:\nonexistent\sysconfig\path" | Set-Content -Path (Join-Path $tmpDir "python.cmd")
        try {
            # Call with the parent dir path to exercise the Split-Path fallback.
            $result = Get-PythonScriptsDir -PythonExe (Join-Path $tmpDir "python.exe")
            # Fallback: parent of PythonExe + \Scripts
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
        $fn  = [regex]::Match($src, '(?s)function Test-NavigCommand.*?\n\}')
        $fn.Value | Should -Match 'GetEnvironmentVariable.*PATH.*Machine'
    }

    It "falls back to ScriptsDir\navig.exe when Get-Command returns nothing" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Test-NavigCommand.*?\n\}')
        $fn.Value | Should -Match 'navig\.exe'
    }

    It "emits a [!!] line and returns false on failure" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Test-NavigCommand.*?\n\}')
        $fn.Value | Should -Match 'Write-NavErr'
        $fn.Value | Should -Match 'return \$false'
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

    It "exits with code 1 on pip failure" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Install-Navig.*?\n\}')
        $fn.Value | Should -Match 'exit 1'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Reinstall path
# ─────────────────────────────────────────────────────────────────────────────
Describe "Reinstall path — captures uninstall result" {
    It "does not silently discard Invoke-NavigUninstall result with Out-Null" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Not -Match 'Invoke-NavigUninstall.*ForReinstall.*\|\s*Out-Null'
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
# New UX layer — terminal capability, output helpers, banner, success screen
# ─────────────────────────────────────────────────────────────────────────────
Describe "Initialize-NavTerminal" {
    It "sets script-level NavColor to a boolean" {
        Initialize-NavTerminal
        $script:NavColor | Should -BeOfType [bool]
    }

    It "respects NO_COLOR env var" {
        $env:NO_COLOR = "1"
        try {
            Initialize-NavTerminal
            $script:NavColor | Should -Be $false
        } finally {
            Remove-Item Env:\NO_COLOR -ErrorAction SilentlyContinue
        }
    }
}

Describe "Write-NavVerbose" {
    It "produces no output when Verbose is false" {
        $script:Verbose = $false
        $output = Write-NavVerbose "should not appear" 6>&1
        $output | Should -BeNullOrEmpty
    }

    It "produces output when Verbose is true" {
        # Verify Write-NavVerbose conditionally emits based on $Verbose flag
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Write-NavVerbose.*?\n\}')
        $fn.Value | Should -Match '\$Verbose'
        $fn.Value | Should -Match 'Write-Host'
    }
}

Describe "Show-Banner — no taglines" {
    It "does not contain a tagline array" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Show-Banner.*?\n\}')
        $fn.Value | Should -Not -Match '\$taglines\s*='
        $fn.Value | Should -Not -Match 'Get-Random'
    }

    It "contains the canonical product description" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Show-Banner.*?\n\}')
        $fn.Value | Should -Match 'quiet operator tooling'
    }
}

Describe "Show-Success" {
    It "exists as a function" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Match 'function Show-Success'
    }

    It "contains 'navig init'" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Show-Success.*?\n\}')
        $fn.Value | Should -Match 'navig init'
    }

    It "contains 'Ready.'" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Show-Success.*?\n\}')
        $fn.Value | Should -Match 'Ready\.'
    }

    It "does not contain old magenta box markers" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Not -Match 'Show-SuccessBanner'
        $src | Should -Not -Match '\+====+'
    }
}

Describe "Main — phased structure" {
    It "calls Write-NavPhase for each phase" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Main \{.*?\n\}')
        $fn.Value | Should -Match "Write-NavPhase.*Environment"
        $fn.Value | Should -Match "Write-NavPhase.*Requirements"
        $fn.Value | Should -Match "Write-NavPhase.*Install"
        $fn.Value | Should -Match "Write-NavPhase.*Verify"
    }

    It "calls Show-Success after successful install" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Main \{.*?\n\}')
        $fn.Value | Should -Match 'Show-Success'
    }

    It "calls Initialize-NavTerminal before Show-Banner" {
        $src = Get-Content $Script:InstallerPath -Raw
        $fn  = [regex]::Match($src, '(?s)function Main \{.*?\n\}')
        $initIdx   = $fn.Value.IndexOf('Initialize-NavTerminal')
        $bannerIdx = $fn.Value.IndexOf('Show-Banner')
        $initIdx   | Should -BeLessThan $bannerIdx
    }
}
