Source common.tape
Output dashboard.gif
Output dashboard.mp4
Output dashboard.webm
# A full-screen app: taller than the other scenes so the identity sigil gets its full 9x9 grid.
Set Height 900

# Off-camera, give the dashboard something real to report: a gateway running in the
# sandbox and a few operations in the ledger. Every one is a real navig command.
Hide
Type "cd $HOME_DEMO_WORK && (navig gateway start >/dev/null 2>&1 &) && navig host use prod-01 >/dev/null 2>&1 && navig run 'uptime' >/dev/null 2>&1; navig run 'df -h /' >/dev/null 2>&1; navig host test staging-01 >/dev/null 2>&1; navig ledger verify >/dev/null 2>&1; sleep 4; clear" Enter
Sleep 16s
Show

Type "navig dashboard" Sleep 400ms Enter
Sleep 9s
Type "d"
Sleep 8s
Type "x"
Sleep 2s
Type "q"
Sleep 1200ms

Hide
Type "navig gateway stop >/dev/null 2>&1; clear" Enter
Sleep 3s
