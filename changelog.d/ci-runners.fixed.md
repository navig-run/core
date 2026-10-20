- **`navig repo gates` called a busy lane free, and ignored the push lane.** A gate running
  under another Windows account (the runner service) raised `AccessDenied`, which the view read
  as a dead process — while the lane protocol counts it alive. It now shows `running (other
  user)` and reads that job's step from a per-pid run-log mirror. The default lane count was 3
  (the protocol's is 4) and `push-N` lane files were not recognised at all.
- **The uninstaller removed every `.local\bin` PATH entry, and any entry containing "navig".**
  That broke uv, pipx and any developer's own `…\navig\tools` directory. It now removes only the
  shim directory when NAVIG was its last occupant, anything under `~\.navig`, and a legacy
  `navig\bin|Scripts|runtime` directory.
- **A gate run's exit status is no longer its verdict.** `ci-local --report` computes the
  conclusion from the step records: a run that was killed, left a step `running`, never got a
  lane, or (in strict mode) skipped a step because a prerequisite is missing is a failure with
  a named cause — never a pass. xdist worker counts now follow the heavy lane's CPU share
  (previously ignored), and run markers older than six hours are dropped even if their pid has
  since been reused.
