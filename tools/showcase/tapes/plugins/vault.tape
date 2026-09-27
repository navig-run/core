Source common.tape
Output vault.gif
Output vault.mp4
Output vault.webm

# The value below is a made-up placeholder; nothing real is stored or shown.
Hide
Type "cd $HOME_DEMO_WORK && export DEMO_STRIPE_KEY=sk_test_DEMO_not_a_real_key && clear" Enter
Sleep 800ms
Show

Type "nv add stripe --from-env DEMO_STRIPE_KEY --label billing" Sleep 300ms Enter
Sleep 1500ms
Type "nv list" Sleep 300ms Enter
Sleep 2s
Type "navig vault list" Sleep 300ms Enter
Sleep 3s
Type "nv providers | head -6" Sleep 300ms Enter
Sleep 2500ms
