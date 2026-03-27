; NAVIG AHK Example - Getting Started
; A simple example demonstrating common AHKv2 operations
;
; This script shows how to:
;   1. Click at coordinates
;   2. Type text
;   3. Send hotkeys
;   4. Manipulate windows
;   5. Use clipboard

#Requires AutoHotkey v2.0
#SingleInstance Force

; ==================== Example 1: Basic Output ====================

FileAppend("NAVIG AHK Example Script`n", "*", "UTF-8")
FileAppend("========================`n`n", "*", "UTF-8")

; Get basic system info
FileAppend("System Info:`n", "*", "UTF-8")
FileAppend("  AHK Version: " A_AhkVersion "`n", "*", "UTF-8")
FileAppend("  Screen Size: " A_ScreenWidth "x" A_ScreenHeight "`n", "*", "UTF-8")
FileAppend("  Working Dir: " A_WorkingDir "`n", "*", "UTF-8")
FileAppend("`n", "*", "UTF-8")

; ==================== Example 2: Window Operations ====================

FileAppend("Window Operations:`n", "*", "UTF-8")

; Get active window info
if WinExist("A") {
    title := WinGetTitle("A")
    class := WinGetClass("A")
    pid := WinGetPID("A")

    FileAppend("  Active Window: " title "`n", "*", "UTF-8")
    FileAppend("  Class: " class "`n", "*", "UTF-8")
    FileAppend("  PID: " pid "`n", "*", "UTF-8")
}

; Count all windows
windows := WinGetList()
FileAppend("  Total Windows: " windows.Length "`n", "*", "UTF-8")
FileAppend("`n", "*", "UTF-8")

; ==================== Example 3: Mouse Position ====================

FileAppend("Mouse Position:`n", "*", "UTF-8")
MouseGetPos(&mx, &my)
FileAppend("  Current: (" mx ", " my ")`n", "*", "UTF-8")
FileAppend("`n", "*", "UTF-8")

; ==================== Example 4: Clipboard ====================

FileAppend("Clipboard:`n", "*", "UTF-8")
clipLen := StrLen(A_Clipboard)
if (clipLen > 0) {
    preview := SubStr(A_Clipboard, 1, 50)
    preview := StrReplace(preview, "`n", "↵")
    FileAppend("  Content: " preview (clipLen > 50 ? "..." : "") "`n", "*", "UTF-8")
    FileAppend("  Length: " clipLen " chars`n", "*", "UTF-8")
} else {
    FileAppend("  (empty)`n", "*", "UTF-8")
}
FileAppend("`n", "*", "UTF-8")

; ==================== Example 5: List Top Windows ====================

FileAppend("Top 5 Windows:`n", "*", "UTF-8")

count := 0
for hwnd in windows {
    title := WinGetTitle("ahk_id " hwnd)
    if (title = "")
        continue

    class := WinGetClass("ahk_id " hwnd)
    if (class = "Shell_TrayWnd" || class = "Progman")
        continue

    count++
    if (count > 5)
        break

    ; Truncate title
    if (StrLen(title) > 40)
        title := SubStr(title, 1, 37) "..."

    FileAppend("  " count ". " title "`n", "*", "UTF-8")
}

FileAppend("`n", "*", "UTF-8")

; ==================== Interactive Examples (commented out) ====================

; Uncomment these to test interactive operations:

; Example: Click at center of screen
; centerX := A_ScreenWidth // 2
; centerY := A_ScreenHeight // 2
; Click centerX, centerY

; Example: Type text
; SendText("Hello from NAVIG!")

; Example: Send hotkey (Ctrl+A to select all)
; Send("^a")

; Example: Open Notepad
; Run("notepad.exe")

; Example: Move a window
; if WinExist("Notepad") {
;     WinMove(100, 100, 800, 600, "Notepad")
; }

; ==================== Done ====================

FileAppend("Example complete!`n", "*", "UTF-8")
FileAppend("`nTo run interactive examples, edit this script and uncomment the desired sections.`n", "*", "UTF-8")

ExitApp 0
