- **`navig doctor` no longer reports a recycled process as the gateway.** `gateway.pid`
  outlives the gateway that wrote it, and Windows hands the number to somebody else; the
  runtime-inventory rows then named that process as the daemon — measured on a client
  whose pid file from 22 Oct named a Chrome renderer, so every plugin row printed
  `python -m pip install -e …   # into C:\Program Files\Google\Chrome\Application\chrome.exe`.
  The pid is now trusted only when the process really is a NAVIG gateway (python +
  `-m navig … gateway`), otherwise the daemon's Python is reported **unknown**, and a
  repair hint names an interpreter only when that interpreter is the daemon's — never
  the caller's.
