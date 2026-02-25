; NAVIG AHK Primitives - Window Move/Resize
; Usage: AutoHotkey64.exe window_move.ahk "<selector>" <x> <y> [width] [height]
;
; Selector can be:
;   - Window title (partial match)
;   - ahk_exe notepad.exe
;   - ahk_class Notepad
;   - ahk_id 0x12345
;
; Examples:
;   AutoHotkey64.exe window_move.ahk "Notepad" 100 100
;   AutoHotkey64.exe window_move.ahk "Notepad" 100 100 800 600
;   AutoHotkey64.exe window_move.ahk "ahk_exe code.exe" 0 0 1920 1080

#Requires AutoHotkey v2.0
#SingleInstance Force

; Parse arguments
selector := A_Args.Has(1) ? A_Args[1] : ""
x := A_Args.Has(2) ? Integer(A_Args[2]) : 0
y := A_Args.Has(3) ? Integer(A_Args[3]) : 0
width := A_Args.Has(4) ? Integer(A_Args[4]) : ""
height := A_Args.Has(5) ? Integer(A_Args[5]) : ""

; Validate
if (selector = "") {
    FileAppend("Usage: window_move.ahk `"<selector>`" <x> <y> [width] [height]`n", "*", "UTF-8")
    ExitApp 1
}

; Check if window exists
if !WinExist(selector) {
    FileAppend("Window not found: " selector "`n", "*", "UTF-8")
    ExitApp 1
}

; Move/resize window
try {
    if (width != "" && height != "") {
        WinMove(x, y, width, height, selector)
        FileAppend("Moved window to (" x ", " y ") size " width "x" height "`n", "*", "UTF-8")
    } else {
        WinMove(x, y, , , selector)
        FileAppend("Moved window to (" x ", " y ")`n", "*", "UTF-8")
    }
    ExitApp 0
} catch as err {
    FileAppend("Error: " err.Message "`n", "*", "UTF-8")
    ExitApp 1
}
