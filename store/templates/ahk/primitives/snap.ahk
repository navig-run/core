; primitives/snap.ahk
; Snap window to screen position
; Usage: snap.ahk "selector" "position"

#Requires AutoHotkey v2.0
#SingleInstance Force

if A_Args.Length < 2 {
    FileAppend("Usage: snap.ahk `"<selector>`" `"<position>`"`nPositions: left, right, top, bottom, top-left, top-right, bottom-left, bottom-right, center, maximize, minimize, restore", "**")
    ExitApp(1)
}

selector := A_Args[1]
position := StrLower(A_Args[2])

try {
    target := selector
    if (target = "" || target = "A")
        target := "A" ; Active window
    else if !WinExist(target) {
        ; Try checking if selector is just a title part
        if WinExist(target)
            target := "ahk_id " . WinExist(target)
        else {
            FileAppend("Window not found: " . target, "**")
            ExitApp(1)
        }
    }

    hwnd := WinExist(target)

    if (position = "maximize") {
        WinMaximize(target)
        FileAppend("Maximized", "*")
        ExitApp(0)
    } else if (position = "minimize") {
        WinMinimize(target)
        FileAppend("Minimized", "*")
        ExitApp(0)
    } else if (position = "restore") {
        WinRestore(target)
        FileAppend("Restored", "*")
        ExitApp(0)
    }

    ; Get monitor handle
    hMon := DllCall("User32\\MonitorFromWindow", "Ptr", hwnd, "UInt", 0x2)

    ; Get monitor info
    NumPut("UInt", 40, monitorInfo := Buffer(40))
    DllCall("User32\\GetMonitorInfo", "Ptr", hMon, "Ptr", monitorInfo)

    ; Rects
    wal := NumGet(monitorInfo, 20, "Int")
    wat := NumGet(monitorInfo, 24, "Int")
    war := NumGet(monitorInfo, 28, "Int")
    wab := NumGet(monitorInfo, 32, "Int")

    wW := war - wal
    wH := wab - wat

    posX := wal
    posY := wat
    newW := wW
    newH := wH

    if (position = "left") {
        newW := Integer(wW / 2)
    } else if (position = "right") {
        posX := wal + Integer(wW / 2)
        newW := Integer(wW / 2)
    } else if (position = "top") {
        newH := Integer(wH / 2)
    } else if (position = "bottom") {
        posY := wat + Integer(wH / 2)
        newH := Integer(wH / 2)
    } else if (position = "top-left") {
        newW := Integer(wW / 2)
        newH := Integer(wH / 2)
    } else if (position = "top-right") {
        posX := wal + Integer(wW / 2)
        newW := Integer(wW / 2)
        newH := Integer(wH / 2)
    } else if (position = "bottom-left") {
        posY := wat + Integer(wH / 2)
        newW := Integer(wW / 2)
        newH := Integer(wH / 2)
    } else if (position = "bottom-right") {
        posX := wal + Integer(wW / 2)
        posY := wat + Integer(wH / 2)
        newW := Integer(wW / 2)
        newH := Integer(wH / 2)
    } else if (position = "center") {
        WinGetPos(,, &curW, &curH, target)
        posX := wal + Integer((wW - curW) / 2)
        posY := wat + Integer((wH - curH) / 2)
        newW := curW
        newH := curH
    }

    if (WinGetMinMax(target) != 0)
        WinRestore(target)

    WinMove(posX, posY, newW, newH, target)
    FileAppend("Snapped to " . position, "*")

} catch as e {
    FileAppend("Error: " . e.Message, "**")
    ExitApp(1)
}
ExitApp(0)
