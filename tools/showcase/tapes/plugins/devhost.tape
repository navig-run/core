Source common.tape
Output devhost.gif
Output devhost.mp4
Output devhost.webm

# Off-camera: a dev server on :5173 (any stack works — this one is python's http.server).
Hide
Type "cd $HOME_DEMO_WORK && bash $SHOWCASE_DIR/plugin-fixtures.sh devhost && clear" Enter
Sleep 2s
Show

Type "navig devhost add app.test --port 5173" Sleep 300ms Enter
Sleep 3s
Type "(navig devhost up app.test >/dev/null 2>&1 &) ; sleep 2" Sleep 300ms Enter
Sleep 3s
Type "curl https://app.test" Sleep 300ms Enter
Sleep 2s
Type "navig devhost status" Sleep 300ms Enter
Sleep 3s

Hide
Type "bash $SHOWCASE_DIR/plugin-fixtures.sh devhost-cleanup; clear" Enter
Sleep 2s
