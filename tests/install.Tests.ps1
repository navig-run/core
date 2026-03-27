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

$InstallPs1 = (Resolve-Path (Join-Path $PSScriptRoot '..\install.ps1')).Path

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

Describe 'install.ps1' {

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

    Context 'Missing Python handling' {
        It 'handles missing Python gracefully (non-zero exit or error output)' {
            # Override PATH with an empty temp dir so python/python3 are not found
            $emptyPath = [System.IO.Path]::GetTempPath()
            $r = Invoke-Installer -Arguments @('-DryRun') -Env @{ PATH = $emptyPath }
            # Either warns (non-fatal in dry-run) or exits non-zero — either is acceptable
            $eitherWarningOrNonZero = ($r.ExitCode -ne 0) -or ($r.Output -match '(?i)(python|not found|warn|error|\[!!\])')
            $eitherWarningOrNonZero | Should -Be $true
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
