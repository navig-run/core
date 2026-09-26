Source common.tape
Output schema-for-agents.gif
Output schema-for-agents.mp4
Output schema-for-agents.webm

Hide
Type "cd $HOME_DEMO_WORK && clear" Enter
Sleep 800ms
Show

Type `navig --schema | jq '{commands: .total, schema: .schema_version}'` Sleep 400ms Enter
Sleep 3s
Type `navig --schema | jq '.commands[] | select(.path == "navig host test") | {path, summary, arguments, status}'` Sleep 400ms Enter
Sleep 4s
Type `navig block list --json | jq -c '[.[] | select(.verify != "none") | {id, verify}]'` Sleep 400ms Enter
Sleep 3s
Type `navig host list --json | jq '.hosts[] | {name, host, user}'` Sleep 400ms Enter
Sleep 4s
