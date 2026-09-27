Source common.tape
Output dedupe.gif
Output dedupe.mp4
Output dedupe.webm

# Off-camera: seven photos, three of them near-duplicates (a resized copy, a re-encoded
# edit, an exact copy). Generated with Pillow so the demo ships no real photos.
Hide
Type "cd $HOME_DEMO_WORK && bash $SHOWCASE_DIR/plugin-fixtures.sh dedupe && clear" Enter
Sleep 3s
Show

Type "ls photos" Sleep 300ms Enter
Sleep 1500ms
Type "ndup scan photos --near -v" Sleep 300ms Enter
Sleep 3s
Type "ndup scan photos --near --move _dupes" Sleep 300ms Enter
Sleep 3s
Type "ndup restore _dupes" Sleep 300ms Enter
Sleep 2500ms
