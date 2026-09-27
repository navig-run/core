- **A task finished, moved or deleted outside Telegram kept its reminders — and pinged.**
  `pim.reminders` exists to prevent exactly that ghost, and only the Telegram card used
  it: `navig todo done|rm|when` and the agent's `task_done` each changed a task's
  schedule and left the old reminder rows pointing at the old state, so a task ticked
  off at the terminal still announced itself by name that evening. The two capture
  paths had the mirror bug — `navig todo add --remind 3d` and the agent's `task_add`
  stored a date and scheduled nothing, so the flag was decorative. Every surface now
  re-derives its reminders through one helper, delivering to the operator's own chat
  (`telegram.allowed_users[0]`, the rule the gateway's boot message already uses);
  with no Telegram configured it is a quiet no-op and says so rather than pretending.
- **The Todo extension's off switch did not actually stop its reminders.** The banner
  promised "tasks are kept, reminders are not delivered"; habits enforce that promise
  by declining to write the reminder row, and the todo extension had copied the promise
  without the enforcement — so switching it off silenced the commands and the buttons
  while the pings kept arriving. Now no row is written while it is off, and a row
  scheduled before the switch was flipped is HELD rather than delivered (held, not
  discarded: turn it back on the same day and it still arrives). A reminder that
  belongs to no todo — `/remindme`, a habit, a deck app — is untouched by the switch,
  and a gate that cannot be read delivers rather than mutes.
- **Cancellation no longer destroys the evidence when it fails.** `cancel_for` dropped
  the todo→reminder links first and cancelled second, so an unreachable reminder table
  left rows alive with nothing left that knew what they belonged to. The links are now
  dropped last, kept entirely when the table cannot be opened, and a row that refuses
  to cancel is named in a warning instead of vanishing into a silent zero. 23 tests,
  all ten load-bearing lines mutation-tested.
