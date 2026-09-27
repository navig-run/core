- **Document expiry reminders arrive by themselves.** The notify scheduler now ticks
  navig-cabinet's reminders the same way it ticks email: a soft import, and only when
  the `cabinet` module is on. It warns 90, 30 and 7 days before a passport, ID card or
  insurance policy expires, then once when it has. The warning goes through the notify
  router and names only the category and id, never the title. The daemon reads a small
  machine-bound index, so a passphrase-locked cabinet reminds too.
  `navig config set cabinet.reminders.enabled false` turns it off; `navig cabinet remind`
  sends on demand.
