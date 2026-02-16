; workflows/app_launcher.ahk
; Usage: AutoHotkey64.exe app_launcher.ahk "AppPath"

#Requires AutoHotkey v2.0
#SingleInstance Force

appPath := A_Args.Has(1) ? A_Args[1] : ""

if (appPath != "") {
    Run(appPath)
}
ExitApp 0
