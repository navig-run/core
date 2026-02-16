import subprocess
from pathlib import Path


def run_readonly_git(repo_path: str):
    result = subprocess.run(
        ["git", "-C", repo_path, "status", "--short"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def run_bounded_log_read(path: str, lines: int = 200):
    safe_lines = max(1, min(lines, 500))
    p = Path(path)
    if not p.exists():
        return {"error": "file_not_found"}
    content = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"lines": content[-safe_lines:]}


def run_in_sandbox(repo_path: str, command: str):
    cmd = [
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{repo_path}:/repo:ro",
        "-w", "/repo",
        "python:3.12-slim",
        "bash", "-lc", command,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return {"exit_code": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}
    except FileNotFoundError:
        return {
            "exit_code": 127,
            "stdout": "",
            "stderr": "docker CLI not available in tool-gateway container",
            "mode": "fallback",
        }
