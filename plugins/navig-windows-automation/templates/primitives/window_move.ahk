; primitives/window_move.ahk
; Usage: AutoHotkey64.exe window_move.ahk "Title" x y w h

#Requires AutoHotkey v2.0
#SingleInstance Force

title := A_Args.Has(1) ? A_Args[1] : "A" ; Default to active window
x := A_Args.Has(2) ? A_Args[2] : ""
y := A_Args.Has(3) ? A_Args[3] : ""
w := A_Args.Has(4) ? A_Args[4] : ""
h := A_Args.Has(5) ? A_Args[5] : ""

if WinExist(title) {
    WinMove(x, y, w, h, title)
}
ExitApp 0
