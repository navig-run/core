; NAVIG AHK Workflow - Clipboard Monitor
; Monitors clipboard changes and logs them
;
; Usage: AutoHotkey64.exe clipboard_monitor.ahk [logfile]
;
; Features:
;   - Logs all clipboard changes with timestamp
;   - Filters duplicate entries
;   - Can output to file or stdout
;
; Press Ctrl+Q to quit

#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; Configuration
logFile := A_Args.Has(1) ? A_Args[1] : ""
lastContent := ""
changeCount := 0

FileAppend("Clipboard Monitor started`n", "*", "UTF-8")
FileAppend("Press Ctrl+Q to quit`n`n", "*", "UTF-8")

; Clipboard change callback
OnClipboardChange(ClipboardChanged)

ClipboardChanged(dataType) {
    global lastContent, changeCount, logFile
    
    ; Only handle text
    if (dataType != 1)
        return
    
    content := A_Clipboard
    
    ; Skip if empty or duplicate
    if (content = "" || content = lastContent)
        return
    
    lastContent := content
    changeCount++
    
    ; Format timestamp
    timestamp := FormatTime(, "yyyy-MM-dd HH:mm:ss")
    
    ; Truncate for display
    displayContent := content
    if (StrLen(displayContent) > 100)
        displayContent := SubStr(displayContent, 1, 97) "..."
    
    ; Replace newlines for single-line output
    displayContent := StrReplace(displayContent, "`r`n", "↵")
    displayContent := StrReplace(displayContent, "`n", "↵")
    
    ; Log entry
    entry := Format("[{1}] #{2}: {3}`n", timestamp, changeCount, displayContent)
    
    ; Output to stdout
    FileAppend(entry, "*", "UTF-8")
    
    ; Also output to file if specified
    if (logFile != "") {
        try {
            fullEntry := Format("[{1}] #{2}`n{3}`n---`n", timestamp, changeCount, content)
            FileAppend(fullEntry, logFile, "UTF-8")
        }
    }
}

; Quit hotkey
^q::
{
    FileAppend("`nMonitor stopped. " changeCount " changes recorded.`n", "*", "UTF-8")
    ExitApp 0
}
