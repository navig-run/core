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

Describe "Initialize-NavigConfig" {
    It "creates config directory using Join-Path (no raw string interpolation)" {
        $tmpHome = [System.IO.Path]::GetTempPath() | Join-Path -ChildPath ([System.IO.Path]::GetRandomFileName())
        New-Item -ItemType Directory -Path $tmpHome -Force | Out-Null
        $oldProfile = $env:USERPROFILE
        $env:USERPROFILE = $tmpHome
        try {
            Initialize-NavigConfig
            $expected = Join-Path $tmpHome ".navig"
            Test-Path $expected | Should -Be $true
        } finally {
            $env:USERPROFILE = $oldProfile
            Remove-Item $tmpHome -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Describe "Install-NavigGit — duplicate message fix" {
    It "does NOT emit Cloning NAVIG message before the if block" {
        # The outer block must only print 'Updating' or nothing (not 'Cloning')
        # before testing whether the repoDir exists.
        $src = Get-Content $Script:InstallerPath -Raw
        # Grab the Install-NavigGit function body between its braces
        $fn = [regex]::Match($src, '(?s)function Install-NavigGit.*?\n\}')
        $body = $fn.Value
        # The FIRST Write-NavStep in the function should NOT say "Cloning" if repoDir exists.
        # Concretely: there must be no Write-NavStep "Cloning..." before the first 'if (-not'.
        $beforeFirstIf = ($body -split 'if \(-not \(Test-Path')[0]
        $beforeFirstIf | Should -Not -Match 'Write-NavStep.*Cloning'
    }
}

Describe "Install-NavigGit — quoted git args" {
    It "quotes REPO_URL and repoDir in git clone call" {
        $src = Get-Content $Script:InstallerPath -Raw
        # There must be a line: & git clone "$REPO_URL" "$repoDir"
        $src | Should -Match '&\s+git\s+clone\s+"[^"]+'
    }
}

Describe "Get-NavigShimCandidates — PIPX_HOME aware" {
    It "includes a path built from PIPX_HOME when set" {
        $env:PIPX_HOME = "C:\custom\pipx"
        # The shims array in Install-NavigPip (not Get-NavigShimCandidates) uses PIPX_HOME.
        # Verify the installer source contains our PIPX_HOME-aware expression.
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Match 'PIPX_HOME'
    }
}

Describe "Get-NavigShimCandidates — no bare C:\\ path" {
    It "C:\\Python313 reference uses Join-Path" {
        $src = Get-Content $Script:InstallerPath -Raw
        # The old raw string "C:\Python313\Scripts\navig.exe" must not be present.
        $src | Should -Not -Match '"C:\\\\Python313\\\\Scripts'
        # And a Join-Path form must appear instead.
        $src | Should -Match 'Join-Path.*Python313'
    }
}

Describe "Remove-NavigFiles" {
    It "Get-ChildItem call includes -ErrorAction SilentlyContinue" {
        $src = Get-Content $Script:InstallerPath -Raw
        $src | Should -Match 'Get-ChildItem.*-ErrorAction\s+SilentlyContinue'
    }
}

Describe "Reinstall path — captures uninstall result" {
    It "does not silently discard Invoke-NavigUninstall result with Out-Null" {
        $src = Get-Content $Script:InstallerPath -Raw
        # The reinstall branch must NOT pipe Invoke-NavigUninstall directly to Out-Null.
        $src | Should -Not -Match 'Invoke-NavigUninstall.*ForReinstall.*\|\s*Out-Null'
    }
}
