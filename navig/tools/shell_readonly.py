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

2026-10-15: eight read-only diagnostics looped approval cards in Telegram
(`git -C <path> status`, `git --version`, `gh repo list`, `powershell -Command
"Get-ChildItem …"`, `cmd /c "dir … 2>nul & dir …"`). Four gaps closed, same
conservatism: a wrapper (`powershell -Command` / `cmd /c` / `bash -c`) is
unwrapped and its PAYLOAD classified by these same rules; `git`'s global
options (`-C …`) are skipped when locating the sub-command; `gh` gets a
second-level read table; a Windows `2>nul` redirect is the null device, not a
file write. The other direction closed too: `git remote add`, `git branch
<new>`, `git tag <new>`, `git config <k> <v>`, `git worktree add`, bare `git
stash` and `find -delete`/`-exec` were reads-on-paper that change state.
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
        # PowerShell read-only cmdlets — reached by unwrapping
        # `powershell -Command <payload>` (see `_unwrap_wrapper`). Explicit
        # names only, no `get-*` prefix: a prefix would also trust a random
        # `get-pip`-style installer that happens to live on PATH.
        "get-childitem",
        "get-content",
        "get-location",
        "get-date",
        "get-process",
        "get-variable",
        "get-command",
        "get-host",
        "write-output",
        "write-verbose",
        "write-warning",
        "write-information",
        "write-debug",
        "write-error",
        "select-object",
        "select-string",
        "sort-object",
        "measure-object",
        "group-object",
        "compare-object",
        "out-string",
        "out-null",
        "format-table",
        "format-list",
        "format-wide",
        "convertto-json",
        "convertto-csv",
        "convertto-html",
        "test-path",
        "resolve-path",
        "split-path",
        "join-path",
    }
)

#: Verbs whose FIRST SUB-COMMAND decides. A sub-command not listed is a write.
#: (`git` and `gh` are NOT here — they need second-level rules and are handled
#: by `_git_is_read` / `_gh_is_read`, the single source for their answers.)
_READ_ONLY_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "docker": frozenset({"ps", "logs", "inspect", "stats", "version", "info", "images", "top"}),
    "systemctl": frozenset({"status", "is-active", "is-enabled", "list-units", "show", "cat"}),
    "schtasks": frozenset({"/query"}),
    "sc": frozenset({"query", "queryex", "qc"}),
    "wmic": frozenset({"get", "list"}),
}

# ── git / gh: two-level classification ───────────────────────────────────────
#
# First-level-only was wrong in BOTH directions once real commands were lined
# up against it (2026-10-15, Telegram approval cards): `git -C <path> status`
# read the PATH as the sub-command and paged, while `git remote add`,
# `git branch <new>`, `git tag <new>`, `git config <k> <v>` and
# `git worktree add` — all first-level matches — silently passed as reads.

#: git global options that CONSUME the next token as a value. Skipping their
#: value is what lets `git -C C:\some\path status` reach `status` at all.
_GIT_VALUE_OPTS: frozenset[str] = frozenset(
    {"-C", "--git-dir", "--work-tree", "--exec-path", "--config-env", "--attr-source"}
)

#: git sub-commands that never write, whatever arguments follow (revs, paths,
#: filters, format flags).
_GIT_PLAIN_READ: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "rev-parse",
        "describe",
        "ls-files",
        "blame",
        "shortlog",
    }
)

#: `git branch` / `git tag` with one of these flags never create or delete —
#: they select and print. Without one, a trailing word is a NAME being created.
_GIT_LIST_FLAGS: frozenset[str] = frozenset(
    {"--list", "-l", "--contains", "--points-at", "--merged", "--no-merged", "--show-current"}
)

#: `gh <group> <read-sub>` — second-level table. A sub-command NOT listed here
#: (merge, create, delete, download, login, run …) is a write. `gh api` is
#: deliberately absent: it POSTs/DELETEs by flag, so it always pages.
_GH_READ_GROUPS: dict[str, frozenset[str]] = {
    "repo": frozenset({"list", "view", "status"}),
    "pr": frozenset({"list", "view", "diff", "status", "checks", "ready"}),
    "issue": frozenset({"list", "view", "status"}),
    "run": frozenset({"list", "view", "view-job", "logs"}),
    "workflow": frozenset({"list", "view", "view-run"}),
    "gist": frozenset({"list", "view"}),
    "release": frozenset({"list", "view"}),
    "auth": frozenset({"status"}),  # login/token/activate switch credentials
}

#: Wrappers whose ENTIRE remainder after the flag is a nested command line.
#: The payload is classified by these same rules — a wrapper is quoting, not
#: a verdict (`bash -c 'ls'` is a read; `cmd /c del x` is not).
_PS_WRAPPERS: frozenset[str] = frozenset({"powershell", "pwsh"})
_WRAPPER_VERBS: frozenset[str] = frozenset({"bash", "sh", "zsh", "ksh", "dash", "cmd"}) | _PS_WRAPPERS

#: Payload kinds rejected before classification: a PowerShell script block is
#: arbitrary code (`Where-Object { Remove-Item x; $true }` runs the remove).
_PS_BLOCK_RE = re.compile(r"[{}]")

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
#: The optional `=value` suffix matters: `git log --output=x` writes a file
#: and the bare `^--output$` never matched it.
_WRITE_FLAGS_RE = re.compile(
    r"^(-o|--output|-O|--remote-name|-d|--data|--data-\w+|-X|--request|-T|--upload-file|"
    r"-F|--form|--delete|--in-place|/delete|/create|/change|/run|/end)(=.*)?$",
    re.I,
)
#: Flags that write for ONE verb only (`curl -i` includes headers; `sed -i` edits in place;
#: `find -delete` unlinks, `find -exec`/`-ok` run arbitrary commands).
_WRITE_FLAGS_FOR: dict[str, re.Pattern[str]] = {
    "sed": re.compile(r"^-[a-zA-Z]*i", re.I),
    "git": re.compile(r"^(--force|-f|--delete|-d|-D|-M|--push|--edit|-m|--amend|--unset|--add)$"),
    "find": re.compile(
        r"^-(delete|exec|execdir|ok|okdir|fls|fprint\d*|fprintf)$",
        re.I,
    ),
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


#: Redirects that provably cannot write a file: the null device and fd-dup.
#: Anchored so `>/dev/nullify` is NOT stripped (that one creates a file).
#: `2>&1`/`1>&1` only duplicate descriptors. `nul` is the Windows null device —
#: `dir … 2>nul` is THE idiom for discarding stderr and used to reject the
#: whole line (2026-10-15: half the approval cards were `cmd /c "… 2>nul …"`).
_HARMLESS_REDIRECT_RE = re.compile(
    r"(?:[0-9]&?>|&>)\s*(?:/dev/null|nul)(?![\w./])|2>&1|1>&1",
    re.I,
)

#: Shell separators that compose independent commands/pipeline stages. Includes
#: a bare `&` (background): `ls & rm -rf /` runs the rm — the old composition
#: regex matched only `&&`, so this form slipped past it entirely.
_SEGMENT_SEP_RE = re.compile(r"\|\||&&|[;|&]")


def _strip_harmless_redirects(text: str) -> str:
    return _HARMLESS_REDIRECT_RE.sub(" ", text)


#: Hard cap on wrapper-in-wrapper unwrapping (`bash -c "sh -c …"`). Payloads
#: shrink at every step, so 4 is generous; the cap exists so a crafted line
#: can never turn classification into a RecursionError.
_UNWRAP_MAX_DEPTH = 4


def _unwrap_wrapper(text: str) -> tuple[str, str] | None:
    """``(payload, wrapper)`` when the WHOLE command is ``<wrapper> <flag> payload``.

    Supported: ``powershell``/``pwsh -Command|-c``, ``cmd /c``,
    ``bash``/``sh``/``zsh``/``ksh``/``dash`` -c. Everything after the flag is
    the payload — that matches how each wrapper parses (powershell and cmd
    take the whole remainder; for ``bash -c`` including the ``$0``/``$1``
    words is the SAFE direction: they get classified too instead of dropped).

    Quote stripping is outer-only; residue like ``bash -c 'ls' && rm x``
    survives the join and the payload classifier then sees the ``&& rm x``.
    The caller classifies the payload with the SAME rules — unwrapping is
    quoting, not a verdict. Composition is deliberately NOT checked here: the
    payload's ``;``/``|``/``>`` are inside quotes at this level and must be
    judged after unwrapping, segment-wise, where they are visible.
    """
    try:
        argv = shlex.split(text, posix=(os.name != "nt"))
    except ValueError:
        return None
    if not argv:
        return None
    verb = _basename(argv[0])
    if verb not in _WRAPPER_VERBS:
        return None
    rest = argv[1:]
    if verb in _PS_WRAPPERS:
        flags = {"-command", "/command", "-c", "/c"}
    elif verb == "cmd":
        flags = {"/c"}
    else:
        flags = {"-c"}
    idx = next((i for i, a in enumerate(rest) if a.lower() in flags), None)
    if idx is None or idx + 1 >= len(rest):
        return None
    payload = " ".join(rest[idx + 1 :]).strip()
    if len(payload) >= 2 and payload[0] == payload[-1] and payload[0] in "\"'":
        payload = payload[1:-1].strip()
    if not payload:
        return None
    return payload, verb


def is_read_only(command: str, _depth: int = 0) -> bool:
    """True only when the whole command is provably a read. Unknown → False.

    Composition is checked SEGMENT-wise (2026-10-13): every pipeline stage and
    every ``;``/``&&``/``&``-separated segment must independently be read-only,
    so ``grep x f | head -20`` and ``ls; pwd`` pass while ``ls; rm -rf /`` does
    not. Blanket rejection made the common diagnostic forms (``… | head``,
    ``2>/dev/null``) unapprovable, which looped approval cards in Telegram.
    Quote-blind splitting is safe in the reject direction: a metachar inside
    quotes makes US split (each fragment must then still pass) while the shell
    would not — we only ever reject more. Command substitution (backticks /
    ``$()``) and every remaining redirect still reject the whole line.

    Wrappers unwrap FIRST (2026-10-15), before composition checks: the payload
    of ``powershell -Command "a; b"`` has to be judged as a whole command, not
    split on a ``;`` that was inside quotes. See :func:`_unwrap_wrapper`.
    """
    text = _strip_harmless_redirects(command or "")
    if not text.strip():
        return False
    if _depth < _UNWRAP_MAX_DEPTH:
        unwrapped = _unwrap_wrapper(text)
        if unwrapped is not None:
            payload, wrapper = unwrapped
            if wrapper in _PS_WRAPPERS and _PS_BLOCK_RE.search(payload):
                return False  # a script block runs arbitrary code
            return is_read_only(payload, _depth=_depth + 1)
    if "`" in text or "$(" in text:
        return False
    if re.search(r"[<>]", text):
        return False  # any remaining redirect reads or writes a real file
    segments = [s for s in _SEGMENT_SEP_RE.split(text) if s.strip()]
    if not segments:
        return False
    return all(_segment_is_read_only(s, _depth) for s in segments)


def _segment_is_read_only(segment: str, _depth: int = 0) -> bool:
    """One separator-free command — the original single-command rules."""
    if _depth < _UNWRAP_MAX_DEPTH:
        unwrapped = _unwrap_wrapper(segment)
        if unwrapped is not None:
            payload, wrapper = unwrapped
            if wrapper in _PS_WRAPPERS and _PS_BLOCK_RE.search(payload):
                return False
            return is_read_only(payload, _depth=_depth + 1)
    parts = _split(segment)
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
    if verb == "git":
        return _git_is_read(rest)
    if verb == "gh":
        return _gh_is_read(rest)
    if verb in _READ_ONLY_SUBCOMMANDS:
        subs = _READ_ONLY_SUBCOMMANDS[verb]
        if verb == "wmic":  # `wmic <alias> [where …] get|list …` — the verb sits later
            return any(a.lower() in subs for a in rest) and not any(
                a.lower() in ("call", "set", "delete", "create") for a in rest
            )
        first = next((a for a in rest if not a.startswith("-")), None)
        return first is not None and first.lower() in subs
    return verb in _READ_ONLY_VERBS


def _git_is_read(rest: list[str]) -> bool:
    """`git` — global options skipped, then a second-level verdict.

    A bare git invocation (``git``, ``git --version``, ``git -C <path>``) only
    prints usage/version: git performs no operation without a sub-command.
    """
    sub: str | None = None
    tail: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in _GIT_VALUE_OPTS and "=" not in a:
            i += 2  # the NEXT token is this option's value, not a sub-command
            continue
        if a.startswith("-"):
            i += 1  # a global flag (`--version`, `-p`, `--git-dir=x`, …)
            continue
        sub = a.lower()
        tail = rest[i + 1 :]
        break
    if sub is None:
        return True  # usage/version only — git writes nothing without a sub-command
    words = [a.lower() for a in tail if not a.startswith("-")]
    if sub in _GIT_PLAIN_READ:
        return True
    if sub == "config":
        # `git config <key>` reads; `git config <key> <value>` writes.
        return len(words) <= 1
    if sub == "remote":
        # bare/`-v` list; `show`/`get-url` read; add/remove/rename/prune/set-url write.
        return not words or words[0] in ("show", "get-url")
    if sub == "worktree":
        return not words or words[0] == "list"
    if sub in ("branch", "tag"):
        # bare lists; with a trailing NAME it creates one (`git branch foo`,
        # `git tag v1.0`) unless a select-and-print flag says otherwise.
        return not words or any(a in _GIT_LIST_FLAGS for a in tail)
    if sub == "stash":
        # bare `git stash` SAVES the working tree — only list/show are reads.
        return bool(words) and words[0] in ("list", "show")
    return False


def _gh_is_read(rest: list[str]) -> bool:
    """`gh` — second-level table; `gh api` and anything unknown always pages."""
    first = next((a for a in rest if not a.startswith("-")), None)
    if first is None:
        return True  # bare `gh` / `gh --version` prints usage
    head = first.lower()
    if head in ("search", "help", "version", "status"):
        return True  # every gh search is a read; help/version/status print
    if head not in _GH_READ_GROUPS:
        return False  # api, browse, config, run/download, pr/merge, …
    after = rest[rest.index(first) + 1 :]
    words = [a.lower() for a in after if not a.startswith("-")]
    if not words:
        return True  # bare `gh pr` prints usage
    return words[0] in _GH_READ_GROUPS[head]


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


def why_not_read_only(command: str, _depth: int = 0) -> str:
    """A short reason for the denial text the agent reads.

    Mirrors :func:`is_read_only` step for step: once composition became
    segment-wise, the old blanket "uses shell composition" reason would fire
    only when the WHOLE line is unparsable — the honest reason is which
    segment disqualified it. A wrapper reports its PAYLOAD's reason: the
    answer for `powershell -Command Remove-Item x` is the `Remove-Item`, not
    "powershell changes state".
    """
    text = _strip_harmless_redirects(command or "")
    if not text.strip():
        return "empty command"
    if _depth < _UNWRAP_MAX_DEPTH:
        unwrapped = _unwrap_wrapper(text)
        if unwrapped is not None:
            payload, wrapper = unwrapped
            if wrapper in _PS_WRAPPERS and _PS_BLOCK_RE.search(payload):
                return "powershell script block runs arbitrary code"
            sub = why_not_read_only(payload, _depth=_depth + 1)
            return sub if sub == "read-only" else f"payload: {sub}"
    if "`" in text or "$(" in text:
        return "uses command substitution"
    if re.search(r"[<>]", text):
        return "uses redirection"
    segments = [s for s in _SEGMENT_SEP_RE.split(text) if s.strip()]
    if not segments:
        return "empty command"
    for segment in segments:
        if not _segment_is_read_only(segment, _depth):
            # One segment's own reason, named so the agent knows where to look.
            reason = _single_reason(segment, _depth)
            return reason if len(segments) == 1 else f"in `{segment.strip()}`: {reason}"
    return "read-only"


def _single_reason(segment: str, _depth: int = 0) -> str:
    """The reason ONE separator-free command is not a read."""
    if _depth < _UNWRAP_MAX_DEPTH:
        unwrapped = _unwrap_wrapper(segment)
        if unwrapped is not None:
            payload, wrapper = unwrapped
            if wrapper in _PS_WRAPPERS and _PS_BLOCK_RE.search(payload):
                return "powershell script block runs arbitrary code"
            sub = why_not_read_only(payload, _depth=_depth + 1)
            return sub if sub == "read-only" else f"payload: {sub}"
    parts = _split(segment)
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
    if verb in ("git", "gh"):
        return f"not a read-only `{verb}` sub-command"
    if verb in _READ_ONLY_SUBCOMMANDS:
        return f"not a read-only `{verb}` sub-command"
    return f"`{verb}` is not on the read-only list"
