; primitives/window_close.ahk
; Usage: AutoHotkey64.exe window_close.ahk "Title"

#Requires AutoHotkey v2.0
#SingleInstance Force

title := A_Args.Has(1) ? A_Args[1] : "A"

if WinExist(title) {
    WinClose(title)
}
ExitApp 0
