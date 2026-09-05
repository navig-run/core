#Requires -Modules @{ ModuleName='Pester'; ModuleVersion='5.0.0' }
<#
.SYNOPSIS
    Pester v5 tests for install.ps1

.DESCRIPTION
    Tests installer behaviour:
    1. Dry-run exits 0 and prints expected output
    2. Two successive dry-runs produce identical exit code (idempotency)
    3. Invalid -Action flag exits 1 with error message
    4. Missing Python: installer prints warning/error output
    5. Dry-run leaves no side-effect files on disk
#>

Describe 'install.ps1' {

    # Pester 5 runs `It` bodies in a scope that does NOT see functions defined at the
    # top level of the file: top-level code runs during DISCOVERY, `It` bodies during
    # RUN. `Invoke-Installer` lived out there, so every test in this file died with
    # "The term 'Invoke-Installer' is not recognized" — 0 passed, 5 failed — and the
    # PowerShell installer had no working coverage at all. `BeforeAll` is the seam that
    # exists for exactly this, and it must define the helper, not just call it.
    BeforeAll {
    # ..\..\ — this file lives in core/tests/installer/, so the installer is two levels up.
    # It was one level when the file sat in core/tests/; Resolve-Path throws rather than
    # returning a wrong path, which is why the move showed up as "exit 64" on every test
    # instead of silently testing nothing.
    $InstallPs1 = (Resolve-Path (Join-Path $PSScriptRoot '..\..\install.ps1')).Path

    # Determine PowerShell host binary
    $PwshBin = if (Get-Command pwsh -ErrorAction SilentlyContinue) { 'pwsh' } else { 'powershell' }

    function Invoke-Installer {
        [CmdletBinding()]
        param(
            [string[]]$Arguments = @(),
            [hashtable]$Env = @{}
        )
        $psi = [System.Diagnostics.ProcessStartInfo]::new()
        $psi.FileName            = $PwshBin
        $psi.UseShellExecute     = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError  = $true
        $psi.CreateNoWindow      = $true

        $allArgs = @('-NoProfile', '-NonInteractive', '-File', $InstallPs1) + $Arguments
        $psi.Arguments = $allArgs -join ' '

        foreach ($kv in $Env.GetEnumerator()) {
            $psi.EnvironmentVariables[$kv.Key] = $kv.Value
        }

        $proc = [System.Diagnostics.Process]::Start($psi)
        $stdout = $proc.StandardOutput.ReadToEnd()
        $stderr = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()

        [PSCustomObject]@{
            ExitCode = $proc.ExitCode
            Stdout   = $stdout
            Stderr   = $stderr
            Output   = "$stdout`n$stderr"
        }
    }
    }


    Context 'Dry-run behaviour' {
        It 'exits 0 and prints dry-run marker' {
            $r = Invoke-Installer -Arguments @('-DryRun')
            $r.ExitCode | Should -Be 0
            $r.Output   | Should -Match '(?i)(dry.?run|DryRun)'
        }

        It 'is idempotent across two consecutive dry-runs' {
            $r1 = Invoke-Installer -Arguments @('-DryRun')
            $r2 = Invoke-Installer -Arguments @('-DryRun')
            $r1.ExitCode | Should -Be 0
            $r2.ExitCode | Should -Be 0
        }
    }

    Context 'Invalid arguments' {
        It 'exits 1 when -Action is set to an unsupported value' {
            $r = Invoke-Installer -Arguments @('-Action', 'bogus')
            $r.ExitCode | Should -Not -Be 0
            $r.Output   | Should -Match '(?i)(unsupported|invalid|unknown|bogus)'
        }
    }

    Context 'No dependency on a system Python' {
        It 'runs a dry-run with an empty PATH' {
            # This asserted the OPPOSITE until 2026-08-16: that an empty PATH must produce a
            # warning or a non-zero exit, because the pip-era installer needed a system
            # python. The uv rewrite removed that requirement entirely - the installer
            # fetches uv.exe and a uv-managed CPython into $RUNTIME_DIR - so the old
            # assertion was pinning a precondition that no longer exists, and it failed for
            # the right reason. Not depending on the host's python is the whole point of
            # the managed runtime, so that is what is pinned now.
            $emptyPath = [System.IO.Path]::GetTempPath()
            $r = Invoke-Installer -Arguments @('-DryRun') -Env @{ PATH = $emptyPath }
            $r.ExitCode | Should -Be 0
            $r.Output   | Should -Not -Match '(?i)(python (was )?not found|requires python)'
        }
    }

    Context 'Filesystem side-effects' {
        It 'creates no unexpected files after dry-run' {
            $before = (Get-ChildItem $env:USERPROFILE -Filter 'navig*' -ErrorAction SilentlyContinue | Measure-Object).Count
            Invoke-Installer -Arguments @('-DryRun') | Out-Null
            $after  = (Get-ChildItem $env:USERPROFILE -Filter 'navig*' -ErrorAction SilentlyContinue | Measure-Object).Count
            $after | Should -Be $before
        }
    }
}
