; NAVIG AHK Primitives - Click
; Usage: AutoHotkey64.exe click.ahk <x> <y> [button] [clicks]
;
; Examples:
;   AutoHotkey64.exe click.ahk 100 200           ; Left click at (100, 200)
;   AutoHotkey64.exe click.ahk 100 200 right     ; Right click
;   AutoHotkey64.exe click.ahk 100 200 left 2    ; Double click

#Requires AutoHotkey v2.0
#SingleInstance Force

; Parse arguments
x := A_Args.Has(1) ? Integer(A_Args[1]) : 0
y := A_Args.Has(2) ? Integer(A_Args[2]) : 0
button := A_Args.Has(3) ? A_Args[3] : "Left"
clicks := A_Args.Has(4) ? Integer(A_Args[4]) : 1

; Validate
if (x = 0 && y = 0 && !A_Args.Has(1)) {
    FileAppend("Usage: click.ahk <x> <y> [button] [clicks]`n", "*", "UTF-8")
    ExitApp 1
}

; Execute click
try {
    Click x, y, button, clicks
    FileAppend("Clicked at (" x ", " y ") " button " x" clicks "`n", "*", "UTF-8")
    ExitApp 0
} catch as err {
    FileAppend("Error: " err.Message "`n", "*", "UTF-8")
    ExitApp 1
}
