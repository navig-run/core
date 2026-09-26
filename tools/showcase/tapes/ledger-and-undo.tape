Source common.tape
Output ledger-and-undo.gif
Output ledger-and-undo.mp4
Output ledger-and-undo.webm

Hide
Type "cd $HOME_DEMO_WORK && navig host use prod-01 >/dev/null 2>&1 && navig run 'uptime' >/dev/null 2>&1 && navig apply safe-deployment --dry-run >/dev/null 2>&1 && navig config set execution.confirmation_level verbose >/dev/null 2>&1; clear" Enter
Sleep 800ms
Show

Type "navig ledger show" Sleep 400ms Enter
Sleep 4s
Type "navig ledger verify" Sleep 400ms Enter
Sleep 2500ms
Type "navig undo --list" Sleep 400ms Enter
Sleep 3500ms
