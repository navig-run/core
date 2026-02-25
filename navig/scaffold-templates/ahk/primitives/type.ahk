; NAVIG AHK Primitives - Type Text
; Usage: AutoHotkey64.exe type.ahk "<text>" [delay_ms]
;
; Examples:
;   AutoHotkey64.exe type.ahk "Hello World"
;   AutoHotkey64.exe type.ahk "Slow typing" 50

#Requires AutoHotkey v2.0
#SingleInstance Force

; Parse arguments
text := A_Args.Has(1) ? A_Args[1] : ""
delay := A_Args.Has(2) ? Integer(A_Args[2]) : 0

; Validate
if (text = "") {
    FileAppend("Usage: type.ahk `"<text>`" [delay_ms]`n", "*", "UTF-8")
    ExitApp 1
}

; Set delay if specified
if (delay > 0) {
    SetKeyDelay(delay)
}

; Type the text
try {
    SendText(text)
    FileAppend("Typed " StrLen(text) " characters`n", "*", "UTF-8")
    ExitApp 0
} catch as err {
    FileAppend("Error: " err.Message "`n", "*", "UTF-8")
    ExitApp 1
}
