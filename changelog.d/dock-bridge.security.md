- **The deck API no longer trusts every browser extension.** A local request from a
  `chrome-extension://` / `moz-extension://` origin used to get the desktop bypass whatever
  extension sent it. Once the NAVIG Dock pairs (`navig.dock.pairing`, trust on first use, only a
  token hash stored), only the paired extension id is trusted; an unreadable pairing file
  refuses extension origins rather than trusting them all.
