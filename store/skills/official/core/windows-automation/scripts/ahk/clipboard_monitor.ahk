; workflows/clipboard_monitor.ahk
; Usage: AutoHotkey64.exe clipboard_monitor.ahk

#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

OnClipboardChange ClipChanged

ClipChanged(Type) {
    if (Type = 1) { ; Text
        FileAppend(A_Clipboard "`n", "clipboard_history.txt")
    }
}
