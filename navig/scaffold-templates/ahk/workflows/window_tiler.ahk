; NAVIG AHK Workflow - Window Tiler
; Arranges all visible windows in a grid layout
;
; Usage: AutoHotkey64.exe window_tiler.ahk [columns]
;
; Examples:
;   AutoHotkey64.exe window_tiler.ahk      ; Auto-calculate grid
;   AutoHotkey64.exe window_tiler.ahk 2    ; Force 2 columns

#Requires AutoHotkey v2.0
#SingleInstance Force

; Configuration
cols := A_Args.Has(1) ? Integer(A_Args[1]) : 0  ; 0 = auto-calculate

; Get visible windows (excluding system windows)
TileWindows() {
    global cols

    ; Get all windows
    windows := WinGetList()
    visibleWindows := []

    ; Filter to visible, non-minimized windows
    excludeClasses := ["Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman",
                       "WorkerW", "ApplicationFrameWindow"]

    for hwnd in windows {
        if !WinExist("ahk_id " hwnd)
            continue

        title := WinGetTitle("ahk_id " hwnd)
        if (title = "")
            continue

        class := WinGetClass("ahk_id " hwnd)

        ; Skip system windows
        skip := false
        for excluded in excludeClasses {
            if (class = excluded) {
                skip := true
                break
            }
        }
        if (skip)
            continue

        ; Skip minimized windows
        if (WinGetMinMax("ahk_id " hwnd) = -1)
            continue

        visibleWindows.Push(hwnd)
    }

    count := visibleWindows.Length

    if (count = 0) {
        FileAppend("No windows to tile`n", "*", "UTF-8")
        return
    }

    ; Get work area (excludes taskbar)
    MonitorGetWorkArea(, &areaX, &areaY, &areaW, &areaH)

    ; Calculate grid
    if (cols = 0) {
        ; Auto-calculate: aim for roughly square cells
        cols := Ceil(Sqrt(count))
    }
    rows := Ceil(count / cols)

    ; Cell dimensions
    cellW := areaW // cols
    cellH := areaH // rows

    FileAppend("Tiling " count " windows in " cols "x" rows " grid`n", "*", "UTF-8")
    FileAppend("Cell size: " cellW "x" cellH "`n", "*", "UTF-8")

    ; Position each window
    for i, hwnd in visibleWindows {
        col := Mod(i - 1, cols)
        row := (i - 1) // cols

        posX := areaX + (col * cellW)
        posY := areaY + (row * cellH)

        title := WinGetTitle("ahk_id " hwnd)

        ; Restore if maximized before moving
        if (WinGetMinMax("ahk_id " hwnd) = 1) {
            WinRestore("ahk_id " hwnd)
        }

        WinMove(posX, posY, cellW, cellH, "ahk_id " hwnd)

        ; Truncate title for output
        if (StrLen(title) > 30)
            title := SubStr(title, 1, 27) "..."

        FileAppend("  " title " → (" posX ", " posY ")`n", "*", "UTF-8")
    }

    FileAppend("Done!`n", "*", "UTF-8")
}

TileWindows()
ExitApp 0
