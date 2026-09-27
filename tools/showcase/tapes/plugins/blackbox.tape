Source common.tape
Output blackbox.gif
Output blackbox.mp4
Output blackbox.webm

# Off-camera: a tiny app that installs the crash handler, then divides by zero.
Hide
Type "cd $HOME_DEMO_WORK && bash $SHOWCASE_DIR/plugin-fixtures.sh blackbox && clear" Enter
Sleep 800ms
Show

Type "nbb record system 'v2.4 rollout started'" Sleep 300ms Enter
Sleep 1s
Type "nbb record warning 'p95 latency 840 ms on api-01'" Sleep 300ms Enter
Sleep 1s
Type "python3 app.py" Sleep 300ms Enter
Sleep 2s
Type "nbb crashes" Sleep 300ms Enter
Sleep 2s
Type "nbb events" Sleep 300ms Enter
Sleep 2500ms
Type "nbb bundle -o incident.navbox && nbb inspect incident.navbox" Sleep 300ms Enter
Sleep 4s
