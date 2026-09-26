Source common.tape
Output hero.gif
Output hero.mp4
Output hero.webm

Hide
Type "cd $HOME_DEMO_WORK && navig host use prod-01 >/dev/null 2>&1; clear" Enter
Sleep 800ms
Show

Type "navig host list" Sleep 400ms Enter
Sleep 2500ms
Type "navig run 'uptime && df -h / && free -h'" Sleep 400ms Enter
Sleep 4s
Type "navig apply safe-deployment --dry-run" Sleep 400ms Enter
Sleep 4500ms
