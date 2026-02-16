; NAVIG AHK Primitives - Send Keys
; Usage: AutoHotkey64.exe send_keys.ahk "<keys>"
;
; Key Syntax:
;   ^ = Ctrl
;   ! = Alt
;   + = Shift
;   # = Win
;   {Enter}, {Tab}, {Escape}, {Backspace}, etc.
;
; Examples:
;   AutoHotkey64.exe send_keys.ahk "^c"           ; Ctrl+C
;   AutoHotkey64.exe send_keys.ahk "^!t"          ; Ctrl+Alt+T
;   AutoHotkey64.exe send_keys.ahk "{Enter}"      ; Enter key
;   AutoHotkey64.exe send_keys.ahk "#e"           ; Win+E (Explorer)

#Requires AutoHotkey v2.0
#SingleInstance Force

; Parse arguments
keys := A_Args.Has(1) ? A_Args[1] : ""

; Validate
if (keys = "") {
    FileAppend("Usage: send_keys.ahk `"<keys>`"`n", "*", "UTF-8")
    FileAppend("Examples:`n", "*", "UTF-8")
    FileAppend("  ^c     - Ctrl+C`n", "*", "UTF-8")
    FileAppend("  ^!t    - Ctrl+Alt+T`n", "*", "UTF-8")
    FileAppend("  {Enter} - Enter key`n", "*", "UTF-8")
    ExitApp 1
}

; Send keys
try {
    Send(keys)
    FileAppend("Sent: " keys "`n", "*", "UTF-8")
    ExitApp 0
} catch as err {
    FileAppend("Error: " err.Message "`n", "*", "UTF-8")
    ExitApp 1
}
