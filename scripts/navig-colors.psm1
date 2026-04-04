# navig-colors.psm1 -- Semantic ANSI color wrappers for NAVIG CLI
# Compatible: Windows * macOS * Linux -- PowerShell 7.2+
#
# ANSI ESC[ sequences are supported natively in PowerShell 7+ on all three
# platforms.  On Windows this module enables VT processing for edge-case hosts
# (e.g. conhost.exe) that do not enable it by default.

# ── Windows VT processing guard ───────────────────────────────────────────────
if ($IsWindows) {
    $null = [System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    # Enable ENABLE_VIRTUAL_TERMINAL_PROCESSING (bit 4) on stdout.
    # Windows Terminal, VS Code, and PowerShell 7 enable this automatically;
    # this guard covers legacy conhost.exe hosts that do not.
    try {
        $k32 = Add-Type -MemberDefinition @'
            [DllImport("kernel32.dll", SetLastError=true)]
            public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);
            [DllImport("kernel32.dll", SetLastError=true)]
            public static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);
            [DllImport("kernel32.dll", SetLastError=true)]
            public static extern IntPtr GetStdHandle(int nStdHandle);
'@ -Name K32 -Namespace NavColors -PassThru -ErrorAction SilentlyContinue

        if ($k32) {
            $STD_OUTPUT_HANDLE = -11
            $ENABLE_VT         = 0x0004
            $handle = $k32::GetStdHandle($STD_OUTPUT_HANDLE)
            $mode   = [uint32]0
            if ($k32::GetConsoleMode($handle, [ref]$mode)) {
                $null = $k32::SetConsoleMode($handle, $mode -bor $ENABLE_VT)
            }
        }
    } catch {
        # Already enabled or not a console host -- safe to ignore.
    }
}

# ── Color helper ──────────────────────────────────────────────────────────────
$_NavColorCodes = @{
    Green  = '32'
    Red    = '31'
    Yellow = '33'
    Cyan   = '36'
    White  = '37'
    Dim    = '2'
    Bold   = '1'
}

function Set-NavColor {
    <#
    .SYNOPSIS
        Wraps $Text in the ANSI escape sequence for $Color.
    .EXAMPLE
        Set-NavColor "daemon:1234" Green   # outputs green-colored text
    #>
    [OutputType([string])]
    param(
        [Parameter(Mandatory, Position = 0)][string]$Text,
        [Parameter(Mandatory, Position = 1)]
        [ValidateSet('Green', 'Red', 'Yellow', 'Cyan', 'White', 'Dim', 'Bold')]
        [string]$Color
    )
    $code = $_NavColorCodes[$Color]
    return "`e[${code}m${Text}`e[0m"
}

Export-ModuleMember -Function Set-NavColor
