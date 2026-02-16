; primitives/type.ahk
; Usage: AutoHotkey64.exe type.ahk "text to type"

#Requires AutoHotkey v2.0
#SingleInstance Force

text := A_Args.Has(1) ? A_Args[1] : ""

if (text != "") {
    SendText(text)
}
ExitApp 0
