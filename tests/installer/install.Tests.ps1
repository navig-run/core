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
