; primitives/click.ahk
; Usage: AutoHotkey64.exe click.ahk <x> <y> [button] [clicks]

#Requires AutoHotkey v2.0
#SingleInstance Force

x := A_Args.Has(1) ? A_Args[1] : 0
y := A_Args.Has(2) ? A_Args[2] : 0
button := A_Args.Has(3) ? A_Args[3] : "Left"
clicks := A_Args.Has(4) ? A_Args[4] : 1

; Coordinate mode relative to screen is usually safer for absolute positioning
CoordMode "Mouse", "Screen"
Click x, y, button, clicks
ExitApp 0
