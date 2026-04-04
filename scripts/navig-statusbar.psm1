# navig-statusbar.psm1 -- Powerline-style status bar for NAVIG CLI
# Compatible: Windows * macOS * Linux -- PowerShell 7.2+
#
# Requires JetBrainsMono Nerd Font (or any Nerd Font) installed and
# configured as the terminal font.
#
# Usage:
#   Import-Module "$PSScriptRoot\navig-statusbar.psm1"
#   Show-NavStatus -Model "Qwen3-9B" -Task "coding" -Speed "54" -VRAM "7.8/8GB" -DaemonUp $true -DaemonPid 23240

Import-Module "$PSScriptRoot\navig-icons.psm1"  -DisableNameChecking
Import-Module "$PSScriptRoot\navig-colors.psm1" -DisableNameChecking

function Show-NavStatus {
    <#
    .SYNOPSIS
        Renders a Powerline-style one-line status bar to stdout.

    .DESCRIPTION
        Outputs a single horizontal bar with the following segments:
          [OS icon] NAVIG  |  [status]  |  [model]  |  [task]  |  [speed]  |  [VRAM]

        Glyphs require a Nerd Font.  Run scripts/Install-NerdFont.ps1 first.

    .PARAMETER Model
        Active LLM model name (e.g. "Qwen3.5-9B").

    .PARAMETER Task
        Current task description (e.g. "coding", "idle").

    .PARAMETER Speed
        Inference speed in tokens per second (display string, e.g. "54").

    .PARAMETER VRAM
        VRAM usage display string (e.g. "7.8/8GB" or "--").

    .PARAMETER DaemonUp
        $true if the NAVIG daemon process is running.

    .PARAMETER DaemonPid
        PID of the running daemon (shown when DaemonUp is $true).

    .EXAMPLE
        Show-NavStatus -Model "Qwen3-9B" -Task "coding" -Speed "54" -VRAM "7.8/8GB" -DaemonUp $true -DaemonPid 23240
    #>
    [CmdletBinding()]
    param(
        [string]$Model     = "—",
        [string]$Task      = "idle",
        [string]$Speed     = "—",
        [string]$VRAM      = "—",
        [bool]  $DaemonUp  = $false,
        [int]   $DaemonPid = 0
    )

    # Glyph shortcuts
    $g = $NAVIG

    # Platform OS icon
    $osIcon = if ($IsWindows) { $g.Windows }
              elseif ($IsMacOS) { $g.Apple }
              else              { $g.Linux }

    # Daemon status segment
    $status = if ($DaemonUp) {
        Set-NavColor "$($g.Ready) daemon:$DaemonPid" Green
    } else {
        Set-NavColor "$($g.Stopped) stopped" Red
    }

    # Assemble segments
    $seg1 = " $osIcon NAVIG $($g.Sep) "
    $seg2 = " $status $($g.SepThin) "
    $seg3 = " $($g.Agent) $Model $($g.SepThin) "
    $seg4 = " $($g.Code) $Task $($g.SepThin) "
    $seg5 = " $($g.Bolt) $Speed t/s $($g.SepThin) "
    $seg6 = " $($g.GPU) $VRAM $($g.Arrow) "

    Write-Host "$seg1$seg2$seg3$seg4$seg5$seg6"
}

Export-ModuleMember -Function Show-NavStatus
