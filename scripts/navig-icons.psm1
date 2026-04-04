# navig-icons.psm1 -- Nerd Font glyph registry for NAVIG CLI
# Requires : JetBrainsMono Nerd Font (or any Nerd Font) installed
# Compatible: Windows * macOS * Linux -- PowerShell 7.2+
#
# All callers reference $NAVIG.<key>  -- never hardcode codepoints elsewhere.
# To add a glyph: add one entry here.  Done.

# Ensure UTF-8 output encoding on all platforms before any glyph is emitted.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding            = [System.Text.Encoding]::UTF8

$NAVIG = @{
    # ── AI / Agents ────────────────────────────────────────────────────────
    Agent    = [char]::ConvertFromUtf32(0xF06D4)  # nf-md-robot
    Brain    = [char]::ConvertFromUtf32(0xF18B4)  # nf-md-brain
    Code     = [char]0xF121                        # nf-fa-code
    Magic    = [char]0xF0D0                        # nf-fa-magic

    # ── Status indicators ──────────────────────────────────────────────────
    Ready    = [char]0xF111   # nf-fa-circle  (ANSI green applied by caller)
    Stopped  = [char]0xF111   # nf-fa-circle  (ANSI red)
    Loading  = [char]0xF110   # nf-fa-spinner
    Idle     = [char]0xF111   # nf-fa-circle  (ANSI white)
    Ok       = [char]0xF058   # nf-fa-check_circle
    Fail     = [char]0xF057   # nf-fa-times_circle
    Warn     = [char]0xF071   # nf-fa-exclamation_triangle

    # ── Performance ────────────────────────────────────────────────────────
    Bolt     = [char]0xF0E7   # nf-fa-bolt
    Rocket   = [char]0xF135   # nf-fa-rocket
    Chart    = [char]0xF080   # nf-fa-bar_chart
    GPU      = [char]0xF878   # nf-fa-microchip

    # ── Powerline separators ───────────────────────────────────────────────
    Sep      = [char]0xE0B0   # nf-pl-right_hard_divider
    SepThin  = [char]0xE0B1   # nf-pl-right_soft_divider
    Prompt   = [char]0xE0B6   # nf-pl-left_half_circle_thick
    Arrow    = [char]0xE0B4   # nf-pl-right_half_circle_thick

    # ── Files / System ─────────────────────────────────────────────────────
    Folder   = [char]0xF115   # nf-fa-folder_open
    File     = [char]0xF15B   # nf-fa-file
    Terminal = [char]0xF489   # nf-dev-terminal
    Linux    = [char]0xF17C   # nf-fa-linux
    Apple    = [char]0xF179   # nf-fa-apple
    Windows  = [char]0xF17A   # nf-fa-windows
    Docker   = [char]0xF308   # nf-dev-docker
    Git      = [char]0xF1D3   # nf-dev-git
}

Export-ModuleMember -Variable NAVIG
