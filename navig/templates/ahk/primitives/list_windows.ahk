; NAVIG AHK Primitives - List Windows
; Usage: AutoHotkey64.exe list_windows.ahk [--json]
;
; Outputs list of all visible windows with:
;   - HWND (window handle)
;   - Title
;   - Class name
;   - Process ID
;   - Position and size

#Requires AutoHotkey v2.0
#SingleInstance Force

; Check for JSON output flag
jsonOutput := false
for arg in A_Args {
    if (arg = "--json" || arg = "-j") {
        jsonOutput := true
    }
}

; Get all windows
windows := WinGetList()

if (jsonOutput) {
    ; JSON output
    output := "["
    first := true
    
    for hwnd in windows {
        title := WinGetTitle("ahk_id " hwnd)
        
        ; Skip empty/hidden windows
        if (title = "")
            continue
        
        if (!WinExist("ahk_id " hwnd))
            continue
        
        class := WinGetClass("ahk_id " hwnd)
        pid := WinGetPID("ahk_id " hwnd)
        procName := WinGetProcessName("ahk_id " hwnd)
        
        try {
            WinGetPos(&x, &y, &w, &h, "ahk_id " hwnd)
        } catch {
            x := 0, y := 0, w := 0, h := 0
        }
        
        minMax := WinGetMinMax("ahk_id " hwnd)
        
        if (!first)
            output .= ","
        first := false
        
        ; Escape title for JSON
        titleEsc := StrReplace(title, '\', '\\')
        titleEsc := StrReplace(titleEsc, '"', '\"')
        titleEsc := StrReplace(titleEsc, '`n', '\n')
        titleEsc := StrReplace(titleEsc, '`r', '\r')
        titleEsc := StrReplace(titleEsc, '`t', '\t')
        
        output .= '`n  {"hwnd":' hwnd
        output .= ',"title":"' titleEsc '"'
        output .= ',"class":"' class '"'
        output .= ',"process":"' procName '"'
        output .= ',"pid":' pid
        output .= ',"x":' x ',"y":' y
        output .= ',"width":' w ',"height":' h
        output .= ',"minimized":' (minMax = -1 ? 'true' : 'false')
        output .= ',"maximized":' (minMax = 1 ? 'true' : 'false')
        output .= '}'
    }
    
    output .= "`n]"
    FileAppend(output, "*", "UTF-8")
    
} else {
    ; Human-readable output
    count := 0
    
    for hwnd in windows {
        title := WinGetTitle("ahk_id " hwnd)
        
        if (title = "")
            continue
        
        if (!WinExist("ahk_id " hwnd))
            continue
        
        class := WinGetClass("ahk_id " hwnd)
        pid := WinGetPID("ahk_id " hwnd)
        
        try {
            WinGetPos(&x, &y, &w, &h, "ahk_id " hwnd)
        } catch {
            x := 0, y := 0, w := 0, h := 0
        }
        
        minMax := WinGetMinMax("ahk_id " hwnd)
        state := minMax = -1 ? "[MIN]" : (minMax = 1 ? "[MAX]" : "")
        
        ; Truncate title if too long
        if (StrLen(title) > 50)
            title := SubStr(title, 1, 47) "..."
        
        count++
        line := Format("{1:6} | {2:-50} | {3:-15} | {4:5} | {5:4},{6:4} {7:4}x{8:4} {9}`n",
            hwnd, title, class, pid, x, y, w, h, state)
        FileAppend(line, "*", "UTF-8")
    }
    
    FileAppend("`n" count " windows found`n", "*", "UTF-8")
}

ExitApp 0
