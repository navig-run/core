"""`is_read_only` — which shell commands a mission may run without asking.

Conservative by design: every case below that reads True is a diagnostic an
agent can run a hundred times without changing anything; everything else —
including anything unknown — is False. A false "no" costs a report; a false
"yes" costs an agent changing production unasked.

Composition is SEGMENT-wise: every pipeline stage / `;`-separated segment must
independently be a read (2026-10-13) — `grep x f | head` is provably a read and
must auto-approve, while `ls; rm -rf /` never can be.
"""

from __future__ import annotations

import pytest

from navig.tools.shell_readonly import is_read_only, why_not_read_only

READ_ONLY = [
    "navig doctor",
    "navig service status",
    "navig service pids --plain",
    "navig mode list",
    "navig config get gateway.port",
    "navig host list",
    "git status",
    "git log --oneline -5",
    "git diff --stat",
    "curl -s https://api.example.com/health",
    "curl -I https://example.com",
    "cat /var/log/syslog",
    "tail -n 50 x.log",
    "grep -n error x.log",
    "sed -n 1,5p f",
    "ls -la",
    "ps aux",
    "tasklist",
    "schtasks /query /tn X",
    "docker ps",
    "docker logs --tail 20 web",
    "systemctl status navig",
    "journalctl -u navig -n 50",
    "ping -n 2 1.1.1.1",
    "nslookup example.com",
    "df -h",
    "FOO=1 uptime",
    "wmic process get name",
    "C:\\Windows\\System32\\tasklist.exe",
    # Segment-wise composition: every stage is itself a read.
    "cat a | grep b",
    "grep -n error x.log | head -20",
    "ls; pwd",
    # The exact commands that looped approval cards in Telegram (2026-10-13):
    # null-device redirects and pipes into pure filters cannot write a file.
    "grep -ril --include='*' -e subscription -e renewal .navig 2>/dev/null | head -20",
    "ls .navig/ 2>/dev/null; ls .navig/sessions 2>/dev/null | tail -5",
    'grep -in "subscription" .navig/navig.log | tail -30',
    # The eight cards that looped on 2026-10-15 — every one a proven read:
    # git global options skipped, gh second-level reads, wrappers unwrapped,
    # the Windows null device recognized.
    'git -C "C:/Users/x/.navig-os/workspaces/my-workspace" status',
    "cmd /c \"dir /s /b /a:d C:/Users/x/.git 2>nul & dir /b C:/Users/x/.navig-os\"",
    'powershell.exe -NoProfile -Command "Get-ChildItem -Path C:/Users/x -Filter .git'
    " -Directory -Hidden -Recurse -Depth 4 -ErrorAction SilentlyContinue"
    ' | Select-Object -ExpandProperty FullName"',
    "C:/Users/x/scoop/shims/git.exe --version",
    'powershell.exe -NoProfile -Command "Write-Output (Get-Location).Path"',
    "git -C C:/ws/my-workspace rev-parse --is-inside-work-tree",
    "gh auth status",
    "gh repo list --limit 30 --json name,description,visibility,updatedAt",
    # Wrappers whose payload is provably a read (2026-10-15): quoting is not
    # a verdict — the payload is classified by the same rules.
    "bash -c 'ls'",
    "powershell -Command Get-Process",
    "cmd /c dir /b",
    # git second-level reads / gh reads.
    "git stash list",
    "git remote get-url origin",
    "git config user.email",
    "git worktree list",
    "git branch --show-current",
    "git tag --list 'v*'",
    "gh pr list --state open",
    "gh issue view 12",
    "gh search repos navig",
    # The Windows null device is not a file write.
    "dir /b 2>nul",
]

NOT_READ_ONLY = [
    "",
    "navig mode set big_tasks --provider x --model y",
    "navig service restart",
    "navig config set a b",
    "navig cdp stop --all",
    "navig host remove x",
    "git push origin main",
    "git commit -m x",
    "git checkout -b x",
    "git branch -D x",
    "curl -X POST https://x -d '{}'",
    "curl -o out.bin https://x",
    "sed -i s/a/b/ f",
    "taskkill /PID 1 /F",
    "rm -rf /tmp/x",
    "del /q x",
    "python -c 'print(1)'",
    "pip install x",
    "npm install",
    "schtasks /run /tn X",
    "schtasks /delete /tn X /f",
    "docker restart x",
    "systemctl restart navig",
    "echo hi > f",
    "ls && rm x",
    "ls; rm -rf /",  # a read segment never rescues a write segment
    "ls & rm x",  # bare & runs the second command — the pre-2026-10-13 hole
    "grep x f | tee out.txt",  # tee writes
    "cat f | sh",  # a pipeline stage may not be a shell either
    "cat a`rm x`",  # command substitution hides anything
    "ls $(rm x)",
    "ssh host uptime",
    "sudo ls",
    "wmic process where name='x' call terminate",
    "nvidia-smi",
    "reg add HKCU\\x /v y /d z",
    # First-level matches that CHANGE STATE (2026-10-15) — the false-"yes"
    # half of the same classifier: these all read as reads by name alone.
    "git remote add origin https://example.com/x.git",
    "git branch feature-x",
    "git tag v1.0",
    "git config user.email someone@example.com",
    "git worktree add ../wt",
    "git stash",  # bare stash SAVES the working tree
    "gh pr merge 5",
    "gh repo delete x",
    "gh auth login",
    "gh api -X POST /repos/x/y",
    "gh run download 5",
    "find . -delete",
    "find . -exec rm {} +",
    # Wrapper payloads that write — unwrapping classifies the PAYLOAD, so a
    # write inside quoting is still a write.
    "powershell -Command Remove-Item x",
    'powershell -Command "Get-Process | Stop-Process"',
    "cmd /c del x",
    "bash -c 'ls' && rm x",  # residue after the payload still counts
    "powershell -Command \"Get-Process | Where-Object { Remove-Item x }\"",  # script block
]


@pytest.mark.parametrize("cmd", READ_ONLY)
def test_read_only_diagnostics_are_recognised(cmd):
    assert is_read_only(cmd), cmd


@pytest.mark.parametrize("cmd", NOT_READ_ONLY)
def test_anything_that_can_write_or_is_unknown_is_not(cmd):
    assert not is_read_only(cmd), cmd


def test_the_reason_names_what_disqualified_the_command():
    assert "changes state" in why_not_read_only("rm -rf x")
    assert "flag that writes" in why_not_read_only("sed -i x f")
    assert "read-only list" in why_not_read_only("nvidia-smi")
    assert "navig verb" in why_not_read_only("navig mode set x y")
    assert "sub-command" in why_not_read_only("git push")


def test_the_reason_names_the_disqualifying_segment_and_the_redirect():
    # Segment-wise: the reason must point at the segment that wrote, not at
    # "composition" as a blanket class (a read|read pipeline is fine now).
    reason = why_not_read_only("ls; rm -rf /tmp/x")
    assert "rm" in reason and "changes state" in reason
    assert "redirection" in why_not_read_only("echo hi > f")
    assert "substitution" in why_not_read_only("ls $(rm x)")


def test_a_read_only_pipeline_is_a_read():
    assert is_read_only("grep -n error x.log | head -20")
    assert why_not_read_only("grep -n error x.log | head -20") == "read-only"


def test_unknown_verbs_default_to_not_read_only_never_the_other_way():
    """The floor: a brand-new binary the list has never heard of is not a read."""
    assert not is_read_only("totally-unknown-tool --version")
    assert not is_read_only("./script.sh")
