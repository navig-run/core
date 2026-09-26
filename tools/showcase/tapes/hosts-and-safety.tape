Source common.tape
Output hosts-and-safety.gif
Output hosts-and-safety.mp4
Output hosts-and-safety.webm

Hide
Type "cd $HOME_DEMO_WORK && navig host use prod-01 >/dev/null 2>&1; clear" Enter
Sleep 800ms
Show

Type "navig host test prod-01" Sleep 400ms Enter
Sleep 3s
Type "navig run 'uptime'" Sleep 400ms Enter
Sleep 2500ms
# a SECOND agent session tries to touch the same host
Type "NAVIG_SESSION_ID=agent-b navig run 'systemctl restart nginx'" Sleep 400ms Enter
Sleep 4500ms
# and a destructive command asks before it runs
Type "navig run 'rm -rf /var/www'" Sleep 400ms Enter
Sleep 2500ms
Type "n" Sleep 600ms Enter
Sleep 2500ms
