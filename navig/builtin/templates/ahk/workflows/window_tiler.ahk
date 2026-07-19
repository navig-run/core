; workflows/window_tiler.ahk
; Tiles visible windows in a grid layout

#Requires AutoHotkey v2.0
#SingleInstance Force

TileWindows() {
    ; Get visible windows
    windows := WinGetList("", , "Program Manager")
    visibleWindows := []

    for hwnd in windows {
        if WinExist("ahk_id " hwnd) {
            style := WinGetStyle("ahk_id " hwnd)
            ; Check if window is visible (0x10000000) and not minimized (0x20000000)
            if (style & 0x10000000) && !(style & 0x20000000) {
                title := WinGetTitle("ahk_id " hwnd)
                if (title != "" && title != "Program Manager") {
                    visibleWindows.Push(hwnd)
                }
            }
        }
    }

    if visibleWindows.Length = 0 {
        return
    }

    ; Get screen dimensions of primary monitor
    MonitorGetWorkArea(1, &left, &top, &right, &bottom)
    w := right - left
    h := bottom - top

    ; Calculate grid
    cols := Ceil(Sqrt(visibleWindows.Length))
    rows := Ceil(visibleWindows.Length / cols)

    cellW := w // cols
    cellH := h // rows

    for i, hwnd in visibleWindows {
        col := Mod(i - 1, cols)
        row := (i - 1) // cols

        posX := left + (col * cellW)
        posY := top + (row * cellH)

        try {
            WinMove(posX, posY, cellW, cellH, "ahk_id " hwnd)
        }
    }
}

TileWindows()
ExitApp 0
