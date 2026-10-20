"""NAVIG OS on a Linux install — the pieces core needs to host the surface.

Kept apart from ``navig.commands.os_cmd`` because the kiosk session runs
``python -m navig.os_host.launch`` as a restricted user who must not get a
``~/.navig``, an onboarding run, or the CLI's import cost.
"""
