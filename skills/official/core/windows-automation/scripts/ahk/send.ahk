; primitives/send.ahk
; Usage: AutoHotkey64.exe send.ahk "keys"
; Example: send.ahk "{Ctrl down}c{Ctrl up}"

#Requires AutoHotkey v2.0
#SingleInstance Force

keys := A_Args.Has(1) ? A_Args[1] : ""

if (keys != "") {
    Send(keys)
}
ExitApp 0
