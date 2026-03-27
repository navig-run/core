; NAVIG AHK Workflow - Hotkey Launcher
; Persistent hotkey listener for NAVIG integration
;
; Usage: AutoHotkey64.exe hotkey_launcher.ahk [config.json]
;
; Default Hotkeys:
;   Ctrl+Alt+N  - Open NAVIG menu (navig menu)
;   Ctrl+Alt+S  - NAVIG status (navig status)
;   Ctrl+Alt+C  - NAVIG AI chat (navig chat)
;   Ctrl+Alt+W  - List windows (navig ahk windows)
;   Ctrl+Alt+T  - Tile windows
;
; Configuration:
;   Create a JSON file with custom hotkey mappings

#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; Show tray icon with NAVIG branding
TraySetIcon("shell32.dll", 44)  ; Gear icon
A_IconTip := "NAVIG Hotkey Launcher"

; Load custom configuration if provided
configFile := A_Args.Has(1) ? A_Args[1] : ""
customHotkeys := Map()

if (configFile != "" && FileExist(configFile)) {
    try {
        content := FileRead(configFile, "UTF-8")
        ; Simple JSON parsing for hotkeys
        ; Format: {"hotkeys": [{"key": "^!x", "command": "navig xyz"}]}
        FileAppend("Loaded config: " configFile "`n", "*", "UTF-8")
    } catch as err {
        FileAppend("Warning: Could not load config: " err.Message "`n", "*", "UTF-8")
    }
}

FileAppend("NAVIG Hotkey Launcher active`n", "*", "UTF-8")
FileAppend("Default hotkeys:`n", "*", "UTF-8")
FileAppend("  Ctrl+Alt+N  - NAVIG menu`n", "*", "UTF-8")
FileAppend("  Ctrl+Alt+S  - Status`n", "*", "UTF-8")
FileAppend("  Ctrl+Alt+C  - Chat`n", "*", "UTF-8")
FileAppend("  Ctrl+Alt+W  - Windows`n", "*", "UTF-8")
FileAppend("  Ctrl+Alt+T  - Tile windows`n", "*", "UTF-8")
FileAppend("  Ctrl+Alt+Q  - Quit launcher`n", "*", "UTF-8")

; Helper function to run NAVIG commands
RunNavig(args) {
    try {
        Run('navig ' args, , "Hide")
    } catch as err {
        MsgBox("Failed to run NAVIG: " err.Message, "NAVIG Error", "Icon!")
    }
}

; Helper function to run command and show output
RunWithOutput(command) {
    try {
        Run(command)
    } catch as err {
        MsgBox("Command failed: " err.Message, "Error", "Icon!")
    }
}

; ==================== Default Hotkeys ====================

; Ctrl+Alt+N - Open NAVIG interactive menu
^!n::
{
    RunNavig("menu")
}

; Ctrl+Alt+S - Show NAVIG status
^!s::
{
    RunNavig("status")
}

; Ctrl+Alt+C - Open NAVIG AI chat
^!c::
{
    RunNavig("chat")
}

; Ctrl+Alt+W - List windows
^!w::
{
    RunNavig("ahk windows")
}

; Ctrl+Alt+T - Tile all windows
^!t::
{
    ; Get script directory to find tiler script
    scriptsDir := A_ScriptDir
    tilerScript := scriptsDir "\window_tiler.ahk"

    if FileExist(tilerScript) {
        Run(A_AhkPath ' "' tilerScript '"')
    } else {
        ; Fallback: inline tiling
        TileWindowsQuick()
    }
}

; Quick inline window tiler
TileWindowsQuick() {
    windows := WinGetList()
    visibleWindows := []

    for hwnd in windows {
        if !WinExist("ahk_id " hwnd)
            continue
        title := WinGetTitle("ahk_id " hwnd)
        if (title = "")
            continue
        class := WinGetClass("ahk_id " hwnd)
        if (class = "Shell_TrayWnd" || class = "Progman" || class = "WorkerW")
            continue
        if (WinGetMinMax("ahk_id " hwnd) = -1)
            continue
        visibleWindows.Push(hwnd)
    }

    if (visibleWindows.Length = 0)
        return

    MonitorGetWorkArea(, &x, &y, &w, &h)
    cols := Ceil(Sqrt(visibleWindows.Length))
    rows := Ceil(visibleWindows.Length / cols)
    cellW := w // cols
    cellH := h // rows

    for i, hwnd in visibleWindows {
        col := Mod(i - 1, cols)
        row := (i - 1) // cols
        if (WinGetMinMax("ahk_id " hwnd) = 1)
            WinRestore("ahk_id " hwnd)
        WinMove(x + col * cellW, y + row * cellH, cellW, cellH, "ahk_id " hwnd)
    }

    ToolTip("Tiled " visibleWindows.Length " windows")
    SetTimer(() => ToolTip(), -2000)
}

; Ctrl+Alt+Q - Quit the launcher
^!q::
{
    FileAppend("Hotkey Launcher stopped`n", "*", "UTF-8")
    ExitApp 0
}

; ==================== Tray Menu ====================

; Create tray menu
A_TrayMenu.Delete()  ; Remove default menu items
A_TrayMenu.Add("NAVIG Menu", (*) => RunNavig("menu"))
A_TrayMenu.Add("Status", (*) => RunNavig("status"))
A_TrayMenu.Add("Chat", (*) => RunNavig("chat"))
A_TrayMenu.Add()  ; Separator
A_TrayMenu.Add("List Windows", (*) => RunNavig("ahk windows"))
A_TrayMenu.Add("Tile Windows", (*) => TileWindowsQuick())
A_TrayMenu.Add()  ; Separator
A_TrayMenu.Add("Exit", (*) => ExitApp())
