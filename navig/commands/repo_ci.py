"""CI fleet + repository views for ``navig repo gates``.

The heavy-job lanes (``navig.commands.repo.collect_gates``) answer "what is running on
this machine". This module adds the GitHub side the self-hosted runners created, as pure
data collectors plus classifiers, so ``navig repo gates`` can show one picture:

* **fleet** (machine-wide): runner online/offline/busy, free workers, Actions jobs running
  and queued (with queue delay), the bottleneck, per-job history, and what hosted minutes
  cost against the budget;
* **repository** (``--prs``): every open PR's ``gate-result`` check, the async land queue
  (``land`` / ``land-failed``), the last landed commit and why a landing is blocked.

Every GitHub read goes through one injectable seam, ``gh_api``, so the collectors are
testable without a network (the ``_gh_pr_rows`` pattern in ``repo.py``). A read that fails
yields ``None`` and the view says "unavailable" — it never invents a state.

Honesty rules (see tests/repo/test_repo_ci.py):
* an ETA is shown only from >= ``ETA_MIN_SAMPLES`` runs whose p90/p50 spread is <=
  ``ETA_MAX_SPREAD`` — otherwise "—";
* spend is the metered NET cost from GitHub's usage report; the self-hosted per-minute
  platform fee GitHub announced then postponed is reported separately as "if enacted";
* a bottleneck is named only from evidence in hand, else "none".
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: A runner count claims "free workers" only while GitHub says the runner is online and idle.
ETA_MIN_SAMPLES = 5
ETA_MAX_SPREAD = 2.5  # p90 / p50: wider than this and the median is not a promise
#: The self-hosted platform fee GitHub announced (then postponed) — reported only as a what-if.
SELF_HOSTED_FEE_PER_MIN = 0.002
INCLUDED_MINUTES_FREE_PLAN = 2000

GhApi = Callable[[str], Any]


def gh_api(path: str) -> Any:
    """``gh api <path>`` parsed as JSON, or None on any failure (not installed, no auth,
    network, 4xx). The single GitHub seam — tests replace it."""
    try:
        res = subprocess.run(  # noqa: S603 — fixed argv
            ["gh", "api", path],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except ValueError:
        return None


def ci_dir() -> Path:
    """The CI state dir — mirrors scripts/ci-report.mjs ``ciDir`` (beside the lane dir)."""
    explicit = os.environ.get("NAVIG_CI_DIR")
    if explicit:
        return Path(explicit)
    slots = os.environ.get("NAVIG_SLOT_DIR")
    if not slots:
        from navig.platform.paths import (
            config_dir,  # noqa: PLC0415 — lazy: not needed to import repo_ci
        )

        slots = str(config_dir() / "slots" / "heavy")
    return Path(slots).parent / "ci"


def _ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


# ── runners ──────────────────────────────────────────────────────────────────


def local_runner_services() -> dict[str, str]:
    """``{service name: state}`` for the runner services on this machine (Windows only)."""
    if sys.platform != "win32":
        return {}
    try:
        res = subprocess.run(  # noqa: S603,S607 — fixed argv
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-Service 'actions.runner.*' -ErrorAction SilentlyContinue | "
                "ForEach-Object { $_.Name + '|' + $_.Status }",
            ],
            capture_output=True,
            timeout=20,
            check=False,  # bytes: powershell writes the console codec
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    from navig.core.proc_text import decode_console_output  # noqa: PLC0415

    out: dict[str, str] = {}
    for line in decode_console_output(res.stdout).splitlines():
        name, _, state = line.strip().partition("|")
        if name:
            out[name] = state
    return out


def collect_runners(
    repo: str, *, api: GhApi = gh_api, services: dict[str, str] | None = None
) -> dict:
    """Runner rows from GitHub joined with this machine's service state.

    ``free`` counts runners that are online AND idle; an online runner with a stopped local
    service (or any offline one) is never free. Returns ``{"available": False}`` when GitHub
    could not be read — the caller shows "unavailable", not zero runners."""
    data = api(f"repos/{repo}/actions/runners?per_page=100")
    if not isinstance(data, dict) or "runners" not in data:
        return {"available": False, "runners": [], "online": 0, "busy": 0, "free": 0, "offline": 0}
    services = services or {}
    rows = []
    for r in data["runners"]:
        name = str(r.get("name") or "?")
        labels = sorted(
            str(lb.get("name")) for lb in r.get("labels", []) if lb.get("type") != "read-only"
        )
        svc = next((s for k, s in services.items() if k.endswith(f".{name}")), None)
        online = r.get("status") == "online"
        busy = bool(r.get("busy"))
        rows.append(
            {
                "name": name,
                "online": online,
                "busy": busy,
                "labels": labels,
                "service": svc,
                "state": "offline" if not online else "busy" if busy else "idle",
                "local": svc is not None,
            }
        )
    online = sum(1 for r in rows if r["online"])
    busy = sum(1 for r in rows if r["online"] and r["busy"])
    free = sum(
        1 for r in rows if r["online"] and not r["busy"] and r["service"] in (None, "Running")
    )
    return {
        "available": True,
        "runners": rows,
        "online": online,
        "busy": busy,
        "free": free,
        "offline": len(rows) - online,
    }


# ── Actions jobs ─────────────────────────────────────────────────────────────


def collect_actions(repo: str, *, api: GhApi = gh_api, now: float | None = None) -> dict:
    """Workflow runs queued / in progress, with how long each waited for a runner."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    out: dict[str, Any] = {"available": True, "running": [], "queued": []}
    for status in ("in_progress", "queued"):
        data = api(f"repos/{repo}/actions/runs?status={status}&per_page=30")
        if not isinstance(data, dict) or "workflow_runs" not in data:
            return {"available": False, "running": [], "queued": []}
        for run in data["workflow_runs"]:
            created = _ts(run.get("created_at"))
            started = _ts(run.get("run_started_at"))
            row = {
                "id": run.get("id"),
                "name": run.get("name"),
                "branch": run.get("head_branch"),
                "event": run.get("event"),
                "sha": str(run.get("head_sha") or "")[:9],
                "wait_s": max(0, int((started or now) - created)) if created else None,
                "age_s": max(0, int(now - (started or created))) if (started or created) else None,
            }
            out["running" if status == "in_progress" else "queued"].append(row)
    return out


# ── pull-request checks + the land queue ─────────────────────────────────────

_CHECK_NAMES = ("gate-result", "main-integration", "navig/land")


def collect_pr_checks(repo: str, *, api: GhApi = gh_api) -> dict:
    """Each open PR with its ``gate-result`` conclusion and land-queue label."""
    prs = api(f"repos/{repo}/pulls?state=open&per_page=50")
    if not isinstance(prs, list):
        return {"available": False, "prs": []}
    rows = []
    for pr in prs:
        sha = pr.get("head", {}).get("sha")
        labels = [lb.get("name") for lb in pr.get("labels", [])]
        check = None
        if sha:
            res = api(f"repos/{repo}/commits/{sha}/check-runs?check_name=gate-result&per_page=20")
            runs = [
                c
                for c in (res or {}).get("check_runs", [])
                if (c.get("app") or {}).get("slug") == "github-actions"
            ]
            runs.sort(
                key=lambda c: c.get("completed_at") or c.get("started_at") or "", reverse=True
            )
            if runs:
                c = runs[0]
                check = {
                    "status": c.get("status"),
                    "conclusion": c.get("conclusion"),
                    "fingerprint": (c.get("external_id") or "")[:12],
                    "url": c.get("html_url"),
                }
        rows.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "branch": pr.get("head", {}).get("ref"),
                "draft": bool(pr.get("draft")),
                "author": (pr.get("user") or {}).get("login"),
                "labels": labels,
                "check": check,
                "state": _pr_state(check, labels, bool(pr.get("draft"))),
            }
        )
    return {"available": True, "prs": rows}


def _pr_state(check: dict | None, labels: list[str], draft: bool) -> str:
    if "land" in labels:
        return "queued-to-land"
    if "land-failed" in labels:
        return "land-failed"
    if draft:
        return "draft"
    if not check:
        return "no-check"
    if check["status"] != "completed":
        return "checking"
    return "green" if check["conclusion"] == "success" else "red"


def read_land_log(path: Path | None = None, *, limit: int = 200) -> list[dict]:
    """The lander's audit log (``<ci dir>/land.jsonl``), newest last."""
    p = path or ci_dir() / "land.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def land_summary(log: list[dict], prs: list[dict]) -> dict:
    """Last landed commit, and — per queued/failed PR — why it is blocked (from the log)."""
    landed = [r for r in log if r.get("outcome") == "landed" and r.get("land")]
    by_pr: dict[Any, dict] = {}
    for r in log:
        if r.get("pr") is not None:
            by_pr[r["pr"]] = r
    blocked = []
    for pr in prs:
        if pr["state"] == "land-failed":
            last = by_pr.get(pr["number"]) or {}
            blocked.append(
                {
                    "number": pr["number"],
                    "branch": pr["branch"],
                    "outcome": last.get("outcome") or "unknown",
                    "why": (last.get("message") or "see the PR comment").splitlines()[0][:100],
                }
            )
    last = landed[-1] if landed else None
    return {
        "last_landed": (
            {
                "sha": last["land"][:9],
                "branch": last.get("branch"),
                "at": last.get("at"),
                "validation": last.get("validation"),
            }
            if last
            else None
        ),
        "blocked": blocked,
    }


# ── telemetry-driven: ETA, bottleneck ────────────────────────────────────────


def read_runs(path: Path | None = None, *, limit: int = 500) -> list[dict]:
    """Per-run telemetry rows (``<ci dir>/runs.jsonl``) written by scripts/ci-local.mjs."""
    p = path or ci_dir() / "runs.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _run_seconds(row: dict) -> float | None:
    a, b = _ts(row.get("startedAt")), _ts(row.get("finishedAt"))
    return (b - a) if a and b and b > a else None


def eta_estimate(runs: list[dict], profile: str) -> dict:
    """``{"seconds": p50, "p90": ..., "n": N}`` when the history is dependable, else
    ``{"seconds": None, "n": N, "why": "<reason>"}``. Only successful runs of the same
    profile count: a failed run's duration says nothing about how long a pass takes."""
    secs = sorted(
        s
        for r in runs
        if r.get("profile") == profile
        and r.get("conclusion") == "success"
        and (s := _run_seconds(r)) is not None
    )
    n = len(secs)
    if n < ETA_MIN_SAMPLES:
        return {
            "seconds": None,
            "n": n,
            "why": f"only {n} comparable run(s); need {ETA_MIN_SAMPLES}",
        }
    p50 = statistics.median(secs)
    p90 = secs[min(n - 1, int(0.9 * n))]
    if p50 <= 0 or p90 / p50 > ETA_MAX_SPREAD:
        return {"seconds": None, "n": n, "why": f"too variable (p90/p50 = {p90 / max(p50, 1):.1f})"}
    return {"seconds": p50, "p90": p90, "n": n}


def classify_bottleneck(
    *,
    lanes_busy: int,
    lanes_total: int,
    queued_local: int,
    runners: dict,
    actions: dict,
    cpu_percent: float | None = None,
    disk_queue: float | None = None,
    hung: list[str] | None = None,
) -> dict:
    """Name the limiting resource from evidence in hand, in a fixed precedence. Returns
    ``{"kind": ..., "why": ...}``; ``kind == "none"`` means nothing is limiting right now."""
    if hung:
        return {"kind": "hung-step", "why": f"{hung[0]} has run past twice its usual time"}
    if runners.get("available") and actions.get("available"):
        if actions["queued"] and runners["free"] > 0:
            return {
                "kind": "github-queued",
                "why": f"{len(actions['queued'])} job(s) queued at GitHub while {runners['free']} runner(s) are idle "
                "— a label mismatch or a dispatch delay, not capacity",
            }
    if queued_local and lanes_total and lanes_busy >= lanes_total:
        return {
            "kind": "lane-starved",
            "why": f"{queued_local} job(s) waiting; all {lanes_total} heavy lanes busy",
        }
    if cpu_percent is not None and cpu_percent > 90:
        return {"kind": "cpu-bound", "why": f"CPU at {cpu_percent:.0f}%"}
    if disk_queue is not None and disk_queue > 2 and (cpu_percent is None or cpu_percent < 60):
        return {"kind": "io-bound", "why": f"disk queue {disk_queue:.1f} with CPU not saturated"}
    return {"kind": "none", "why": "no limiting resource detected"}


def hung_steps(gate_rows: list[dict], step_p90_s: dict[str, float]) -> list[str]:
    """Running gates whose current step has run past 2x that step's p90 (when known)."""
    out = []
    for r in gate_rows:
        step = r.get("step")
        if not step or step.get("minutes") is None:
            continue
        p90 = step_p90_s.get(step["name"])
        if p90 and step["minutes"] * 60 > 2 * p90:
            out.append(f"{step['name']} ({step['minutes']}m, usual p90 {int(p90 // 60)}m)")
    return out


def step_p90s(runs: list[dict]) -> dict[str, float]:
    """p90 seconds per step name over successful runs' step records."""
    per: dict[str, list[float]] = {}
    for r in runs:
        if r.get("conclusion") != "success":
            continue
        for s in r.get("steps") or []:
            if s.get("state") == "pass" and isinstance(s.get("seconds"), (int, float)):
                per.setdefault(str(s.get("name")), []).append(float(s["seconds"]))
    return {
        k: sorted(v)[min(len(v) - 1, int(0.9 * len(v)))]
        for k, v in per.items()
        if len(v) >= ETA_MIN_SAMPLES
    }


# ── spend ────────────────────────────────────────────────────────────────────


def collect_spend(
    org: str,
    *,
    api: GhApi = gh_api,
    budget_usd: float = 10.0,
    self_hosted_minutes: float = 0.0,
    month: str | None = None,
) -> dict:
    """Actions spend for *month* (``YYYY-MM``, default this month) from GitHub's usage report.

    ``net_usd`` is what will actually be billed. Self-hosted minutes cost $0 today; the
    postponed platform fee is shown only as ``if_enacted_usd``."""
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    year, mon = month.split("-")
    data = api(f"organizations/{org}/settings/billing/usage?year={int(year)}&month={int(mon)}")
    if not isinstance(data, dict) or "usageItems" not in data:
        return {"available": False, "budget_usd": budget_usd}
    items = [i for i in data["usageItems"] if str(i.get("product", "")).lower() == "actions"]
    hosted = sum(float(i.get("quantity") or 0) for i in items if i.get("unitType") == "Minutes")
    gross = sum(float(i.get("grossAmount") or 0) for i in items)
    net = sum(float(i.get("netAmount") or 0) for i in items)
    return {
        "available": True,
        "month": month,
        "budget_usd": budget_usd,
        "hosted_minutes": round(hosted, 1),
        "included_minutes": INCLUDED_MINUTES_FREE_PLAN,
        "gross_usd": round(gross, 2),
        "net_usd": round(net, 2),
        "self_hosted_minutes": round(self_hosted_minutes, 1),
        "if_enacted_usd": round(self_hosted_minutes * SELF_HOSTED_FEE_PER_MIN, 2),
        "over_budget": net > budget_usd,
        "near_budget": net > 0.75 * budget_usd,
    }


def self_hosted_minutes(runs: list[dict], *, month: str | None = None) -> float:
    """Busy minutes on this machine's runners this month, from run telemetry (rows with a runner)."""
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    total = 0.0
    for r in runs:
        if r.get("runner") and str(r.get("startedAt", "")).startswith(month):
            total += (_run_seconds(r) or 0) / 60
    return total


# ── snapshot + rendering ─────────────────────────────────────────────────────


def repo_slug(root: Path | None) -> str | None:
    """``owner/name`` of *root*'s ``origin`` remote, or None."""
    if root is None:
        return None
    try:
        res = subprocess.run(  # noqa: S603,S607 — fixed argv
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    import re

    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", res.stdout.strip(), re.IGNORECASE)
    return m.group(1) if m else None


def _budget() -> float:
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        raw = get_config_manager().get("github.ci_budget_usd")
        return float(raw) if raw not in (None, "") else 10.0
    except Exception:  # noqa: BLE001 — a missing/odd config must not break the view
        return 10.0


def sample_cpu() -> float | None:
    try:
        import psutil  # noqa: PLC0415

        return float(psutil.cpu_percent(interval=0.4))
    except Exception:  # noqa: BLE001
        return None


def sample_disk_queue() -> float | None:
    """Current disk queue length via typeperf (Windows). ~1 s, so only on ``--deep``."""
    if sys.platform != "win32":
        return None
    try:
        res = subprocess.run(  # noqa: S603,S607 — fixed argv
            ["typeperf", r"\PhysicalDisk(_Total)\Current Disk Queue Length", "-sc", "1"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        for line in res.stdout.splitlines():
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) == 2:
                try:
                    return float(parts[1])
                except ValueError:
                    continue
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def build_fleet(
    gates: dict,
    *,
    repo: str | None,
    github: bool = True,
    deep: bool = False,
    api: GhApi = gh_api,
    services: dict[str, str] | None = None,
    now: float | None = None,
) -> dict:
    """The machine-wide picture: lanes (from ``collect_gates``) + runners + Actions + bottleneck + spend."""
    runs = read_runs()
    runners = {"available": False, "runners": [], "online": 0, "busy": 0, "free": 0, "offline": 0}
    actions = {"available": False, "running": [], "queued": []}
    spend = {"available": False, "budget_usd": _budget()}
    if github and repo:
        runners = collect_runners(
            repo, api=api, services=services if services is not None else local_runner_services()
        )
        actions = collect_actions(repo, api=api, now=now)
        org = repo.split("/")[0]
        spend = collect_spend(
            org, api=api, budget_usd=_budget(), self_hosted_minutes=self_hosted_minutes(runs)
        )
    running_rows = [r for r in gates["gates"] if r["state"] in ("running", "slow")]
    bottleneck = classify_bottleneck(
        lanes_busy=gates["lanes_busy"],
        lanes_total=gates["lanes"],
        queued_local=gates["queued"],
        runners=runners,
        actions=actions,
        cpu_percent=sample_cpu(),
        disk_queue=sample_disk_queue() if deep else None,
        hung=hung_steps(running_rows, step_p90s(runs)),
    )
    return {
        "runners": runners,
        "actions": actions,
        "spend": spend,
        "bottleneck": bottleneck,
        "eta": eta_estimate(runs, "fast"),
        "github": github and bool(repo),
        "repo": repo,
    }


def build_repo_view(repo: str | None, *, api: GhApi = gh_api) -> dict:
    """The repository picture: open PRs with checks, the land queue, the last landing."""
    if not repo:
        return {
            "available": False,
            "repo": None,
            "prs": [],
            "land": {"last_landed": None, "blocked": []},
        }
    checks = collect_pr_checks(repo, api=api)
    return {
        "available": checks["available"],
        "repo": repo,
        "prs": checks["prs"],
        "land": land_summary(read_land_log(), checks["prs"]),
    }


_RUNNER_CELL = {
    "idle": "[green]● idle[/green]",
    "busy": "[cyan]● busy[/cyan]",
    "offline": "[red]✗ offline[/red]",
}
_PR_CELL = {
    "green": "[green]● green[/green]",
    "red": "[red]✗ red[/red]",
    "checking": "[cyan]◌ checking[/cyan]",
    "no-check": "[dim]○ no check[/dim]",
    "draft": "[dim]○ draft[/dim]",
    "queued-to-land": "[cyan]◌ landing[/cyan]",
    "land-failed": "[red]✗ land failed[/red]",
}


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    m = int(seconds // 60)
    return f"{m}m" if m < 90 else f"{m // 60}h{m % 60:02d}"


def render_fleet(fleet: dict) -> None:
    """Runner table, free workers, Actions queue, bottleneck, ETA and spend."""
    from navig import console_helper as ch
    from navig.console_helper import Table

    if not fleet["github"]:
        ch.dim("runners · checks · spend: not shown (no GitHub remote, or --no-github)")
        return
    rn = fleet["runners"]
    if not rn["available"]:
        ch.warning(
            "GitHub runners: unavailable (gh not installed or not authenticated for this repo)"
        )
    elif not rn["runners"]:
        ch.info(
            "No self-hosted runners registered for this repo — run scripts/runners/install.ps1 (see docs/dev/ci-local.md)."
        )
    else:
        table = Table(box=None, show_header=True, padding=(0, 2))
        for h, kw in (
            ("Runner", {"no_wrap": True}),
            ("State", {"no_wrap": True}),
            ("Service", {"no_wrap": True}),
            ("Labels", {}),
        ):
            table.add_column(h, **kw)
        for r in rn["runners"]:
            svc = r["service"] or ("[dim]—[/dim]" if not r["local"] else "?")
            if r["service"] and r["service"] != "Running":
                svc = f"[red]{r['service']}[/red]"
            table.add_row(r["name"], _RUNNER_CELL[r["state"]], svc, ", ".join(r["labels"]))
        ch.console.print(table)
        ch.dim(
            f"{rn['free']} free of {len(rn['runners'])} workers · {rn['busy']} busy · {rn['offline']} offline"
        )
    act = fleet["actions"]
    if act["available"]:
        for tag, rows in (("running", act["running"]), ("queued", act["queued"])):
            for a in rows[:6]:
                ch.dim(
                    f"  {tag:<7} {a['name']} · {a['branch']} · {a['sha']} · "
                    f"{'waited ' + _dur(a['wait_s']) if tag == 'running' else 'waiting ' + _dur(a['age_s'])}"
                )
    bn = fleet["bottleneck"]
    colour = "dim" if bn["kind"] == "none" else "yellow"
    ch.console.print(f"[{colour}]bottleneck: {bn['kind']} — {bn['why']}[/{colour}]")
    eta = fleet["eta"]
    ch.dim(
        "typical fast gate: "
        + (
            f"~{_dur(eta['seconds'])} (p90 {_dur(eta['p90'])}, {eta['n']} runs)"
            if eta["seconds"]
            else f"— ({eta['why']})"
        )
    )
    sp = fleet["spend"]
    if sp["available"]:
        flag = (
            "[red]OVER BUDGET[/red] "
            if sp["over_budget"]
            else "[yellow]near budget[/yellow] "
            if sp["near_budget"]
            else ""
        )
        ch.console.print(
            f"spend {sp['month']}: {flag}${sp['net_usd']:.2f} billed of ${sp['budget_usd']:.2f} budget · "
            f"hosted {sp['hosted_minutes']:.0f}/{sp['included_minutes']} included min · "
            f"self-hosted {sp['self_hosted_minutes']:.0f} min on this machine = $0 "
            f"(${sp['if_enacted_usd']:.2f} if GitHub's postponed per-minute fee applied)"
        )
    else:
        ch.dim("spend: unavailable (needs org billing read access)")


def render_repo_view(view: dict) -> None:
    """Open PRs with their gate-result, the land queue, the last landing and blocked reasons."""
    from navig import console_helper as ch
    from navig.console_helper import Table

    if not view["repo"]:
        ch.warning(
            "No GitHub remote found for this directory — run from a checkout, or pass --repo <path>."
        )
        return
    if not view["available"]:
        ch.warning(
            f"{view['repo']}: pull requests unavailable (gh not installed or not authenticated)"
        )
        return
    if not view["prs"]:
        ch.info(f"{view['repo']}: no open pull requests.")
    else:
        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("PR", no_wrap=True, justify="right")
        table.add_column("State", no_wrap=True)
        table.add_column("Branch", no_wrap=True)
        table.add_column("Fingerprint", no_wrap=True)
        table.add_column("Next step")
        nxt = {
            "green": "npm run land -- --queue",
            "red": "fix the failing step, push",
            "checking": "wait — the gate is running",
            "no-check": "no gate-result yet (runners not installed?) — npm run land -- --now",
            "draft": "mark ready for review to gate",
            "queued-to-land": "wait — the lander will integrate it",
            "land-failed": "see the PR comment; fix, then queue again",
        }
        for pr in view["prs"]:
            fp = (pr["check"] or {}).get("fingerprint") or "—"
            table.add_row(
                f"#{pr['number']}",
                _PR_CELL.get(pr["state"], pr["state"]),
                (pr["branch"] or "")[:34],
                fp,
                nxt.get(pr["state"], ""),
            )
        ch.console.print(table)
    land = view["land"]
    if land["last_landed"]:
        ll = land["last_landed"]
        ch.dim(
            f"last landed: {ll['sha']} ({ll.get('branch') or '?'}) — {ll.get('validation') or 'validated'} · {ll.get('at') or ''}"
        )
    for b in land["blocked"]:
        ch.console.print(f"[red]✗ #{b['number']} blocked ({b['outcome']}):[/red] {b['why']}")
    ch.dim("next: npm run land -- --queue · watch: navig repo gates --watch 5")


# ── doctor ───────────────────────────────────────────────────────────────────

#: One doctor row: (label, ok, detail, warn). Doctor wraps each in its own ``_check`` — kept
#: plain here so this module never imports doctor (no cycle) and the rules are testable.
DoctorRow = tuple[str, bool, str, bool]

MIN_FREE_GB = 20


def _authenticated_users_can_modify(path: Path) -> bool | None:
    """Does *Authenticated Users* (SID S-1-5-11, locale-proof) hold Modify/Full on *path*?
    None when it cannot be determined (not Windows, or the ACL could not be read)."""
    if sys.platform != "win32":
        return None
    script = (
        f"(Get-Acl -LiteralPath '{path}').Access | ForEach-Object {{ "
        "try { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value + '|' + "
        "$_.FileSystemRights + '|' + $_.AccessControlType } catch {} }"
    )
    try:
        res = subprocess.run(  # noqa: S603,S607 — fixed argv; path is ours
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=30,
            check=False,  # bytes: powershell writes the console codec
        )
    except (OSError, subprocess.SubprocessError):
        return None
    from navig.core.proc_text import decode_console_output  # noqa: PLC0415

    text = decode_console_output(res.stdout)
    if res.returncode != 0 or not text.strip():
        return None
    for line in text.splitlines():
        sid, _, rest = line.strip().partition("|")
        rights, _, kind = rest.rpartition("|")
        if (
            sid == "S-1-5-11"
            and kind == "Allow"
            and ("Modify" in rights or "FullControl" in rights)
        ):
            return True
    return False


CI_LANE_GROUP = "navig-ci-users"


def _console_text(stdout: bytes) -> str:
    from navig.core.proc_text import decode_console_output  # noqa: PLC0415

    return decode_console_output(stdout)


def _ci_group_in_token(group: str = CI_LANE_GROUP) -> bool | None:
    """Is *group* in THIS process's logon token? None when the OS cannot be asked.

    A Windows (and POSIX) logon token is fixed at sign-in: a group added to the account
    afterwards is in the database but absent from every process that was already running, so
    the shared lane stays unreadable until the user signs out and back in.
    """
    argv = ["whoami", "/groups"] if sys.platform == "win32" else ["id", "-nG"]
    try:
        res = subprocess.run(  # noqa: S603,S607 — fixed argv, no user data
            argv, capture_output=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = _console_text(res.stdout)
    if res.returncode != 0 or not text.strip():
        return None
    wanted = group.casefold()
    if sys.platform == "win32":
        for line in text.splitlines():
            fields = line.split()
            # "whoami /groups" prints "MACHINE\group  Alias  S-1-…"; the SAM name is never localised.
            if fields and fields[0].split("\\")[-1].casefold() == wanted:
                return True
        return False
    return wanted in (name.strip().casefold() for name in text.split())


def _ci_group_lists_user(group: str = CI_LANE_GROUP) -> bool | None:
    """Does the account database list this account in *group*? None when unanswerable.

    Asks the directory, not the token — that is the difference this whole hint exists for.
    Cmdlet names and SAM names are English even on localised Windows, unlike `net localgroup`'s
    translated headers.
    """
    import getpass  # noqa: PLC0415

    user = getpass.getuser().casefold()
    if sys.platform == "win32":
        script = f"(Get-LocalGroupMember -Group '{group}' -ErrorAction Stop).Name"
        argv = ["powershell.exe", "-NoProfile", "-Command", script]
    else:
        argv = ["getent", "group", group]
    try:
        res = subprocess.run(  # noqa: S603,S607 — fixed argv; the group name is a constant
            argv, capture_output=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = _console_text(res.stdout)
    if res.returncode != 0 or not text.strip():
        return None
    if sys.platform == "win32":
        names = [
            line.split("\\")[-1].strip().casefold() for line in text.splitlines() if line.strip()
        ]
    else:
        _, _, members = text.strip().partition(":")
        names = [m.strip().casefold() for m in members.partition(":")[2].split(",") if m.strip()]
    return user in names


def _lane_grant_hint(path: Path) -> str:
    """The exact command that grants access — the operator copies it into an admin shell."""
    if sys.platform == "win32":
        return f'icacls "{path}" /grant "{CI_LANE_GROUP}:(OI)(CI)M" /T'
    return f'setfacl -R -m g:{CI_LANE_GROUP}:rwx "{path}"'


def _lane_access_hint(
    path: Path,
    *,
    group: str = CI_LANE_GROUP,
    in_token=None,
    lists_user=None,
) -> str:
    """Why *path* is not writable and what actually fixes it.

    "Add yourself to the group" was the only advice before this existed, and it is wrong for the
    common case on a machine whose operator was added after signing in: the group already lists
    them, so the fix is a new logon token, not another administrator. Each cause gets its own
    sentence so the row can only be read one way.
    """
    token = (in_token or _ci_group_in_token)(group)
    listed = (lists_user or _ci_group_lists_user)(group)
    if listed is False:
        add = (
            f"net localgroup {group} <user> /add"
            if sys.platform == "win32"
            else f"usermod -aG {group} <user>"
        )
        return f"add this account to {group} (an administrator has to run: {add})"
    if listed is True and token is False:
        return (
            f"{group} already lists this account but it is not in this logon session's token — "
            "sign out and back in (no administrator needed)"
        )
    if token is True:
        return (
            f"{group} is in this session's token, so the directory's ACL is the blocker — "
            f"an administrator must grant it: {_lane_grant_hint(path)}"
        )
    ask = "whoami /groups" if sys.platform == "win32" else "id -nG"
    return (
        f"could not read {group} membership here — compare `{ask}` with the group's member list, "
        "then sign out and back in if they disagree"
    )


def ci_runner_rows(
    *,
    services: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
    free_gb=None,
    acl_probe=_authenticated_users_can_modify,
    checkout: Path | None = None,
    group_in_token=None,
    group_lists_user=None,
) -> list[DoctorRow]:
    """Health of the self-hosted runner host. Empty when none is installed on this machine
    (not having runners is a choice, not a failure).

    ✓ means verified: a runner service that is not Running, a shared dir this user cannot
    write, a cache drive under the free-space floor, or a repo tree every local account can
    modify are each reported — the last as a warning, because it is the exposure a runner
    service account would inherit.
    """
    env = os.environ if env is None else env
    services = local_runner_services() if services is None else services
    if not services:
        return []
    rows: list[DoctorRow] = []
    down = sorted(n for n, s in services.items() if s != "Running")
    if down:
        rows.append(
            (
                "Runner services",
                False,
                f"{len(down)} of {len(services)} not running: {', '.join(d.split('.')[-1] for d in down)}",
                False,
            )
        )
    else:
        rows.append(("Runner services", True, f"{len(services)} running", False))

    slots = env.get("NAVIG_SLOT_DIR")
    if not slots:
        rows.append(
            (
                "Shared lane dir",
                False,
                "NAVIG_SLOT_DIR is not set machine-wide — runner jobs and local gates would use separate queues",
                False,
            )
        )
    else:
        p = Path(slots)
        try:
            probe = p / f".doctor-{os.getpid()}"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            rows.append(("Shared lane dir", True, f"{p} writable", False))
        except OSError as exc:
            hint = _lane_access_hint(p, in_token=group_in_token, lists_user=group_lists_user)
            rows.append(
                (
                    "Shared lane dir",
                    False,
                    f"{p} not writable by this user ({exc.__class__.__name__}) — {hint}",
                    False,
                )
            )

    root = env.get("NAVIG_CI_ROOT")
    if root:
        free = (free_gb or _free_gb)(Path(root))
        if free is None:
            rows.append(("Runner disk", False, f"COULD NOT VERIFY free space on {root}", True))
        elif free < MIN_FREE_GB:
            rows.append(
                (
                    "Runner disk",
                    False,
                    f"{free:.0f} GB free on {root} (floor {MIN_FREE_GB} GB) — jobs will fail infra:disk",
                    False,
                )
            )
        else:
            rows.append(("Runner disk", True, f"{free:.0f} GB free on {root}", False))

    if checkout is not None:
        exposed = acl_probe(checkout)
        if exposed is True:
            rows.append(
                (
                    "Repo write access",
                    False,
                    f"every local account (Authenticated Users) can MODIFY {checkout} — a runner service account could rewrite "
                    "the repo and its .git\\hooks; run scripts/runners/install.ps1 -TightenProjectsAcl",
                    True,
                )
            )
        elif exposed is False:
            rows.append(
                ("Repo write access", True, "Authenticated Users cannot modify the checkout", False)
            )
        else:
            rows.append(("Repo write access", False, "COULD NOT VERIFY the checkout's ACL", True))
    return rows


def _free_gb(path: Path) -> float | None:
    try:
        import shutil

        return shutil.disk_usage(path).free / 1024**3
    except OSError:
        return None
