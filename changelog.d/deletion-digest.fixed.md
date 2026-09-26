- **Every restart greeted the operator two or three times.** The boot announcement was guarded by
  a per-process flag documented as "announce ONCE per process" — and that is the wrong scope, which
  their own chat showed: the supervisor runs a gateway *and* a telegram_worker, each building its
  own `NavigGateway` and so its own Telegram channel, and each honoured the flag faithfully.
  Measured in the live log: 19:10 ×2, 19:30 ×3 (two boot messages plus the engagement greeting).
  The claim is now made where both processes can see it — an atomic `open(..., "x")` marker under
  the cache dir, not read-then-write, because the two siblings start in the *same second* and a
  check-then-act would let both through. A marker older than the window is replaced, so the next
  real boot still says hello; it fails **open**, because greeting once too often beats an install
  that never greets. Switch it off entirely with `telegram.boot_greeting.enabled false`, or widen
  the window with `telegram.boot_greeting.dedupe_sec`.
- **`navig telegram business deletions target|mute` could not accept a Telegram chat id.** Every
  group and channel id is negative, and Click reads a leading `-` as an option, so
  `deletions target -1001234567890` failed with *"No such option: -1"* — the commands were
  unusable for exactly the ids they take. Found by running them, not by reading them; pinned by a
  CliRunner test that fails with that message when the fix is reverted.
- **A deletion in a muted or silenced chat is now logged at INFO rather than vanishing.** "I chose
  not to tell you" is a different fact from "nothing happened", and the difference matters when an
  operator is trying to work out why they heard nothing.
