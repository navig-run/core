"""Is this shell command read-only — a diagnostic, not a change?

The question a mission has to answer for itself before it runs a command
without a human. On 2026-09-19 an autonomous "Remediate health issues" mission
asked the operator to approve `tool bash_exec` six times in one minute (it
never even showed WHAT it wanted to run), and earlier ones that did get through
quietly rewrote the operator's LLM routing. Both are the same missing thing: no
one could say which commands are safe to run unasked.

This classifier says it, conservatively. A command is read-only only when it
starts with a verb from the allow-list below and carries none of the flags that
turn that verb into a write. Anything unknown is NOT read-only — the cost of a
false "no" is a report instead of an action; the cost of a false "yes" is an
agent changing production unasked.

`bash_exec` passes its command through `shlex` with no shell, so pipes,
redirects and `&&` would reach the OS as literal arguments — a model that writes
them wanted a shell it does not have. They are "not read-only" here for the
same reason: the intent was composition, and composition can write.
"""

from __future__ import annotations

import os
import re
import shlex

# ── verbs that only read ─────────────────────────────────────────────────────

#: Plain read-only executables: the first token of the command must be one of
#: these (basename, case-insensitive, `.exe` stripped).
_READ_ONLY_VERBS: frozenset[str] = frozenset(
    {
        # files, text
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "find",
        "ls",
        "dir",
        "tree",
        "stat",
        "file",
        "wc",
        "du",
        "df",
        "type",
        "which",
        "where",
        "whereis",
        "realpath",
        "readlink",
        "basename",
        "dirname",
        "pwd",
        "echo",
        "printf",
        "sort",
        "uniq",
        "cut",
        "awk",
        "sed",
        "tr",
        "diff",
        "cmp",
        "md5sum",
        "sha256sum",
        "jq",
        "yq",
        "xxd",
        "hexdump",
        "strings",
        # identity, system, time
        "whoami",
        "id",
        "hostname",
        "uname",
        "date",
        "uptime",
        "env",
        "printenv",
        "free",
        "nproc",
        "lscpu",
        "lsblk",
        "lsof",
        "vmstat",
        "iostat",
        "top",
        "htop",
        "ps",
        "tasklist",
        "systeminfo",
        "ver",
        "getconf",
        "ulimit",
        # network — read-only probes
        "ping",
        "traceroute",
        "tracert",
        "nslookup",
        "dig",
        "host",
        "netstat",
        "ss",
        "ip",
        "ifconfig",
        "ipconfig",
        "arp",
        "route",
        "nc",
        "ncat",
        "telnet",
        "curl",
        "journalctl",
    }
)

#: Verbs whose FIRST SUB-COMMAND decides. A sub-command not listed is a write.
_READ_ONLY_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset(
        {
            "status",
            "log",
            "diff",
            "show",
            "branch",
            "remote",
            "rev-parse",
            "describe",
            "ls-files",
            "blame",
            "shortlog",
            "tag",
            "worktree",
            "config",
        }
    ),
    "docker": frozenset({"ps", "logs", "inspect", "stats", "version", "info", "images", "top"}),
    "systemctl": frozenset({"status", "is-active", "is-enabled", "list-units", "show", "cat"}),
    "schtasks": frozenset({"/query"}),
    "sc": frozenset({"query", "queryex", "qc"}),
    "wmic": frozenset({"get", "list"}),
}

#: For `navig <group> <verb>`: the verbs that read. `navig service restart` is a write.
_NAVIG_READ_VERBS: frozenset[str] = frozenset(
    {
        "status",
        "list",
        "ls",
        "show",
        "info",
        "pids",
        "get",
        "doctor",
        "health",
        "check",
        "tail",
        "view",
        "search",
        "stale",
        "conflicts",
        "version",
        "help",
        "explain",
        "diff",
        "verify",
        "stats",
    }
)
#: navig groups where the bare group (no verb) is itself a read.
_NAVIG_BARE_READS: frozenset[str] = frozenset(
    {"status", "doctor", "list", "version", "help", "pids"}
)

#: Flags that turn an otherwise read-only verb into a write, for any verb.
_WRITE_FLAGS_RE = re.compile(
    r"^(-o|--output|-O|--remote-name|-d|--data|--data-\w+|-X|--request|-T|--upload-file|"
    r"-F|--form|--delete|--in-place|/delete|/create|/change|/run|/end)$",
    re.I,
)
#: Flags that write for ONE verb only (`curl -i` includes headers; `sed -i` edits in place).
_WRITE_FLAGS_FOR: dict[str, re.Pattern[str]] = {
    "sed": re.compile(r"^-[a-zA-Z]*i", re.I),
    "git": re.compile(r"^(--force|-f|--delete|-d|-D|--push|--edit|-m|--amend|--unset|--add)$"),
}
#: Shell composition and redirection — `bash_exec` has no shell, but a model that
#: writes these wanted one, and composition can write.
_COMPOSITION_RE = re.compile(r"(\|\||&&|;|\||>|<|`|\$\()")

#: Verbs that are never read-only however they are spelled.
_MUTATING_VERBS: frozenset[str] = frozenset(
    {
        "rm",
        "del",
        "erase",
        "rmdir",
        "rd",
        "mv",
        "move",
        "cp",
        "copy",
        "xcopy",
        "robocopy",
        "chmod",
        "chown",
        "chattr",
        "icacls",
        "touch",
        "mkdir",
        "md",
        "ln",
        "mklink",
        "dd",
        "mkfs",
        "format",
        "fdisk",
        "parted",
        "mount",
        "umount",
        "kill",
        "pkill",
        "killall",
        "taskkill",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "sudo",
        "su",
        "runas",
        "doas",
        "apt",
        "apt-get",
        "yum",
        "dnf",
        "pacman",
        "brew",
        "pip",
        "pip3",
        "npm",
        "pnpm",
        "yarn",
        "cargo",
        "go",
        "python",
        "python3",
        "py",
        "node",
        "bash",
        "sh",
        "zsh",
        "cmd",
        "powershell",
        "pwsh",
        "wget",
        "scp",
        "rsync",
        "ssh",
        "sftp",
        "ftp",
        "tee",
        "reg",
        "regedit",
        "net",
        "netsh",
        "diskpart",
        "bcdedit",
        "wevtutil",
        "setx",
        "export",
    }
)


def _basename(token: str) -> str:
    t = token.strip().strip('"').strip("'").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return t[:-4] if t.endswith(".exe") else t


def _split(command: str) -> tuple[str, list[str]] | None:
    """(verb, rest) — or None when the command is not a plain argv."""
    text = (command or "").strip()
    if not text or _COMPOSITION_RE.search(text):
        return None
    try:
        # The same split `bash_exec` performs: POSIX rules off Windows, where a
        # backslash is an escape; on Windows it is a path separator.
        argv = shlex.split(text, posix=(os.name != "nt"))
    except ValueError:
        return None
    while argv and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0]):
        argv = argv[1:]  # leading VAR=value assignments
    if not argv:
        return None
    return _basename(argv[0]), argv[1:]


def is_read_only(command: str) -> bool:
    """True only when the whole command is provably a read. Unknown → False."""
    parts = _split(command)
    if parts is None:
        return False
    verb, rest = parts
    if verb in _MUTATING_VERBS:
        return False
    if any(_WRITE_FLAGS_RE.match(a) for a in rest):
        return False
    per_verb = _WRITE_FLAGS_FOR.get(verb)
    if per_verb is not None and any(per_verb.match(a) for a in rest):
        return False
    if verb == "navig":
        return _navig_is_read(rest)
    if verb in _READ_ONLY_SUBCOMMANDS:
        subs = _READ_ONLY_SUBCOMMANDS[verb]
        if verb == "wmic":  # `wmic <alias> [where …] get|list …` — the verb sits later
            return any(a.lower() in subs for a in rest) and not any(
                a.lower() in ("call", "set", "delete", "create") for a in rest
            )
        first = next((a for a in rest if not a.startswith("-")), None)
        return first is not None and first.lower() in subs
    return verb in _READ_ONLY_VERBS


def _navig_is_read(rest: list[str]) -> bool:
    """`navig <group> <verb>`: only the listed read verbs, never `set/add/remove/restart`."""
    words = [a for a in rest if not a.startswith("-")]
    if not words:
        return False
    head = words[0].lower()
    if head in _NAVIG_BARE_READS and len(words) == 1:
        return True
    if head in _NAVIG_READ_VERBS:
        return True
    return len(words) >= 2 and words[1].lower() in _NAVIG_READ_VERBS


def why_not_read_only(command: str) -> str:
    """A short reason for the denial text the agent reads."""
    text = (command or "").strip()
    if not text:
        return "empty command"
    if _COMPOSITION_RE.search(text):
        return "uses shell composition or redirection"
    parts = _split(text)
    if parts is None:
        return "could not be parsed"
    verb, rest = parts
    if verb in _MUTATING_VERBS:
        return f"`{verb}` changes state"
    per_verb = _WRITE_FLAGS_FOR.get(verb)
    if any(_WRITE_FLAGS_RE.match(a) for a in rest) or (
        per_verb is not None and any(per_verb.match(a) for a in rest)
    ):
        return "carries a flag that writes"
    if verb == "navig":
        return "not a read-only navig verb (status/list/show/doctor/…)"
    if verb in _READ_ONLY_SUBCOMMANDS:
        return f"not a read-only `{verb}` sub-command"
    return f"`{verb}` is not on the read-only list"
