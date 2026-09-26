"""`is_read_only` — which shell commands a mission may run without asking.

Conservative by design: every case below that reads True is a diagnostic an
agent can run a hundred times without changing anything; everything else —
including anything unknown — is False. A false "no" costs a report; a false
"yes" costs an agent changing production unasked.
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
    "cat a | grep b",
    "echo hi > f",
    "ls && rm x",
    "ssh host uptime",
    "sudo ls",
    "wmic process where name='x' call terminate",
    "nvidia-smi",
    "powershell -Command Get-Process",
    "bash -c 'ls'",
    "reg add HKCU\\x /v y /d z",
]


@pytest.mark.parametrize("cmd", READ_ONLY)
def test_read_only_diagnostics_are_recognised(cmd):
    assert is_read_only(cmd), cmd


@pytest.mark.parametrize("cmd", NOT_READ_ONLY)
def test_anything_that_can_write_or_is_unknown_is_not(cmd):
    assert not is_read_only(cmd), cmd


def test_the_reason_names_what_disqualified_the_command():
    assert "changes state" in why_not_read_only("rm -rf x")
    assert "composition" in why_not_read_only("cat a | grep b")
    assert "flag that writes" in why_not_read_only("sed -i x f")
    assert "read-only list" in why_not_read_only("nvidia-smi")
    assert "navig verb" in why_not_read_only("navig mode set x y")
    assert "sub-command" in why_not_read_only("git push")


def test_unknown_verbs_default_to_not_read_only_never_the_other_way():
    """The floor: a brand-new binary the list has never heard of is not a read."""
    assert not is_read_only("totally-unknown-tool --version")
    assert not is_read_only("./script.sh")
