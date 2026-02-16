; workflows/media_control.ahk
; Usage: media_control.ahk <command>
; Commands: playpause, next, prev, volup, voldown, mute

#Requires AutoHotkey v2.0
#SingleInstance Force

cmd := A_Args.Has(1) ? A_Args[1] : ""

switch cmd {
    case "playpause": Send "{Media_Play_Pause}"
    case "next": Send "{Media_Next}"
    case "prev": Send "{Media_Prev}"
    case "volup": Send "{Volume_Up}"
    case "voldown": Send "{Volume_Down}"
    case "mute": Send "{Volume_Mute}"
}

ExitApp 0
