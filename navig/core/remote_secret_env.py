"""Hand a secret to a REMOTE command through stdin, never through its command line.

A remote command line is public twice over: locally it is an argument of the
``ssh`` process (and of navig's debug log), and on the server it is the argv of
the ``sh -c`` that runs it — readable by every user through ``ps``. Writing
``mysql -p'<pw>'`` or ``PGPASSWORD=<pw> psql`` into it leaked the database
password both ways.

:func:`with_secret_env` instead returns a command that READS each value from
stdin into an exported variable before running the real command, plus the text
to send on stdin. The variable lives only in that process tree's environment
(readable by the same user and root, never by ``ps``).

    cmd, stdin = with_secret_env("mysql -u root -e 'SELECT 1'", {"MYSQL_PWD": pw})
    discovery._execute_ssh(cmd, stdin=stdin)

The wrapper runs under ``sh -c`` so it works whatever the account's login shell
is. Commands that need their own stdin for data (``mysql < dump.sql``) cannot use
this and must take the secret another way.
"""

from __future__ import annotations

import re
import shlex

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def with_secret_env(command: str, env: dict[str, str | None]) -> tuple[str, str | None]:
    """Return ``(remote_command, stdin_text)`` that exports *env* from stdin.

    Empty/``None`` values are dropped. With nothing left, *command* is returned
    unchanged and ``stdin_text`` is ``None``.

    Raises:
        ValueError: a variable name is not a plain shell identifier, or a value
            contains a newline (it could not be read back as one line). The
            message names the variable, never the value.
    """
    names: list[str] = []
    values: list[str] = []
    for name, value in env.items():
        if value is None or value == "":
            continue
        if not _ENV_NAME.match(name):
            raise ValueError(f"not a shell variable name: {name!r}")
        if "\n" in value or "\r" in value:
            raise ValueError(f"{name} contains a line break and cannot be passed on stdin")
        names.append(name)
        values.append(value)
    if not names:
        return command, None
    reads = " && ".join(f"IFS= read -r {n}" for n in names)
    script = f"{reads} && export {' '.join(names)} && {command}"
    return f"sh -c {shlex.quote(script)}", "".join(f"{v}\n" for v in values)
