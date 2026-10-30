"""Read-only assessment of NAVIG's NEURON-off ("PC-off") development capability.

``navig doctor remote-dev`` answers one question with probes instead of prose: *can NAVIG
edit, run coding agents, gate CI, build and publish while the owner's Windows workstation
is powered off?*

Six capabilities are assessed independently and never collapsed into one green light,
because a host can be ready for remote editing while being nowhere near ready to publish.

Design rules — every one of them is load-bearing:

* **Read-only.** No probe writes, installs, publishes, restarts or deletes anything. The
  assessment is safe to run on a live production host at any moment.
* **Bounded.** Every network probe carries a timeout, so a probe that cannot answer returns
  :attr:`Status.UNKNOWN` instead of hanging the diagnostic.
* **Honest.** ``UNKNOWN`` never counts as ready. A capability is ``READY`` only when every
  requirement passed, ``BLOCKED`` when one failed, and ``UNVERIFIED`` when the only thing
  standing in the way is that we could not look. This mirrors the rule the main doctor
  report enforces in ``tests/cli/test_doctor_honesty.py``: a tick means *verified*.
* **Injectable.** Probes are plain callables, so the decision logic is tested without
  touching the network, the filesystem or the clock.
"""

from __future__ import annotations

import enum
import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

__all__ = [
    "Capability",
    "Readiness",
    "Requirement",
    "Status",
    "assess",
    "DEFAULT_EDITOR_URL",
    "read_host_registry",
]

#: The persistent remote-development entry point. Overridable through the
#: ``remote_dev.editor_url`` config key so a self-hosted install is not hard-coded here.
DEFAULT_EDITOR_URL = "https://code.miztizm.com"

#: Where the canonical monorepo is expected to live on a remote build host.
DEFAULT_REMOTE_REPO_PATH = "~/projects/navig"


class Status(str, enum.Enum):
    """Outcome of a single requirement."""

    PASS = "pass"
    #: The requirement is provably not met.
    FAIL = "fail"
    #: We could not look. Never treat this as success.
    UNKNOWN = "unknown"


class Readiness(str, enum.Enum):
    """Outcome of a whole capability, derived from its requirements."""

    READY = "READY"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class Requirement:
    """One check behind a capability."""

    name: str
    status: Status
    evidence: str
    next_step: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status.value,
            "evidence": self.evidence,
            "next_step": self.next_step,
        }


@dataclass
class Capability:
    """One independently reported readiness flag."""

    key: str
    label: str
    purpose: str = ""
    requirements: list[Requirement] = field(default_factory=list)

    def add(self, requirement: Requirement) -> None:
        self.requirements.append(requirement)

    @property
    def readiness(self) -> Readiness:
        if not self.requirements:
            return Readiness.UNVERIFIED
        if any(r.status is Status.FAIL for r in self.requirements):
            return Readiness.BLOCKED
        if all(r.status is Status.PASS for r in self.requirements):
            return Readiness.READY
        return Readiness.UNVERIFIED

    @property
    def blockers(self) -> list[Requirement]:
        return [r for r in self.requirements if r.status is Status.FAIL]

    @property
    def unknowns(self) -> list[Requirement]:
        return [r for r in self.requirements if r.status is Status.UNKNOWN]

    def headline(self) -> str:
        """One line of evidence for the summary table."""
        if self.blockers:
            first = self.blockers[0]
            return first.evidence
        if self.unknowns:
            unknown = f"could not verify: {self.unknowns[0].evidence}"
            verified = "; ".join(
                r.evidence for r in self.requirements if r.status is Status.PASS and r.evidence
            )
            # A verified fact must not disappear behind an unknown one: "exactly one
            # publication path, credentials unreadable" is a different report from
            # "credentials unreadable" alone.
            return f"{verified}; {unknown}" if verified else unknown
        return "; ".join(r.evidence for r in self.requirements if r.evidence) or "all checks passed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "purpose": self.purpose,
            "readiness": self.readiness.value,
            "headline": self.headline(),
            "requirements": [r.as_dict() for r in self.requirements],
        }


# ──────────────────────────────────────────────────────────────────────────────
# Probing primitives. Each returns a Requirement; none mutates anything.
# ──────────────────────────────────────────────────────────────────────────────


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow redirects: the redirect itself is the evidence.

    A Cloudflare Access gate answers ``302`` to the login host. Following it would hide the
    only interesting fact — that the edge is serving the app and protecting it.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - stdlib hook
        return None


def probe_http_edge(
    url: str,
    *,
    timeout: float = 6.0,
    opener: Any | None = None,
) -> Requirement:
    """Probe an HTTP(S) surface and classify the answer.

    2xx/3xx means the edge is serving it. 5xx means the edge is up but the app behind it is
    not. A transport error means we could not look, which is ``UNKNOWN`` — not a failure and
    not a pass.
    """
    if not url:
        return Requirement(
            "edge_reachable",
            Status.UNKNOWN,
            "no editor URL configured",
            "set remote_dev.editor_url (for example https://code.miztizm.com)",
        )

    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "navig-doctor"})
    handle = opener or urllib.request.build_opener(_NoRedirect)
    code: int | None = None
    headers: Any = {}
    try:
        with handle.open(request, timeout=timeout) as response:
            code = int(getattr(response, "status", 0) or 0)
            headers = getattr(response, "headers", {}) or {}
    except urllib.error.HTTPError as exc:  # 3xx/4xx/5xx all arrive here with NoRedirect
        code = int(exc.code)
        headers = exc.headers or {}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return Requirement(
            "edge_reachable",
            Status.UNKNOWN,
            f"could not reach {url}: {exc}",
            "check DNS/network, then re-run `navig doctor remote-dev`",
        )

    gated = "cloudflare-access" in str(headers.get("Www-Authenticate", "")).lower() or (
        "cloudflareaccess.com" in str(headers.get("Location", "")).lower()
    )
    suffix = "; Cloudflare Access in front" if gated else ""

    if 200 <= code < 400:
        return Requirement("edge_reachable", Status.PASS, f"{url} answered HTTP {code}{suffix}")
    if code >= 500:
        return Requirement(
            "edge_reachable",
            Status.FAIL,
            f"{url} answered HTTP {code} — the edge is up but the app behind it is not{suffix}",
            "check the reverse tunnel and the origin service on the remote host",
        )
    return Requirement(
        "edge_reachable",
        Status.FAIL,
        f"{url} answered HTTP {code}{suffix}",
        "confirm the Cloudflare Access policy allows this identity",
    )


def read_host_registry(hosts_dir: Path | None = None) -> list[dict[str, Any]]:
    """Read every host definition from the existing NAVIG host registry.

    Read-only by construction: this never constructs a config provider, never writes and
    never repairs. A diagnostic that mutated the inventory it is describing would be
    worthless as evidence, and a failed read here must not become a write.

    Backup files (``*.bak-*``) and runbooks living beside the definitions are skipped.
    """
    directory = hosts_dir or _default_hosts_dir()
    if directory is None or not directory.is_dir():
        return []

    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is a hard dependency of NAVIG
        return []

    hosts: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.yaml")):
        if ".bak" in path.name or path.name.endswith(".runbook.md"):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        data.setdefault("name", path.stem)
        data["_source"] = str(path)
        hosts.append(data)
    return hosts


def _default_hosts_dir() -> Path | None:
    try:
        from navig.platform.paths import config_dir

        return Path(config_dir()) / "hosts"
    except Exception:  # pragma: no cover - defensive; config_dir is imported everywhere
        return None


def _metadata(host: dict[str, Any]) -> dict[str, Any]:
    meta = host.get("metadata")
    return meta if isinstance(meta, dict) else {}


def find_hosts_by_role(hosts: Iterable[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    """Every registered host whose ``metadata.role`` equals ``role``.

    Role is the only portable answer to "which machine is the brain": the CLAUDE.md
    topology note is explicit that a role must never be guessed from a host name.
    """
    wanted = role.strip().lower()
    return [h for h in hosts if str(_metadata(h).get("role", "")).strip().lower() == wanted]


def _tcp_reachable(host: str, port: int, timeout: float = 4.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((str(host), int(port)), timeout=timeout):
            return True, ""
    except (OSError, TimeoutError, ValueError) as exc:
        return False, str(exc)


def probe_host_address(host: dict[str, Any], *, timeout: float = 4.0) -> Requirement:
    """Is the address recorded in the registry actually answering?"""
    address = str(host.get("host") or "").strip()
    port = int(host.get("port") or 22)
    if not address:
        return Requirement(
            "address_reachable",
            Status.UNKNOWN,
            f"host {host.get('name')!r} records no address",
            "set host/port in the host definition",
        )
    ok, error = _tcp_reachable(address, port, timeout=timeout)
    if ok:
        return Requirement(
            "address_reachable", Status.PASS, f"registry address {address}:{port} accepts TCP"
        )
    return Requirement(
        "address_reachable",
        Status.FAIL,
        f"registry address {address}:{port} is not answering ({error or 'timeout'})",
        f"update ~/.navig/hosts/{host.get('name')}.yaml — the recorded address looks stale",
    )


# ── runtime node registry ─────────────────────────────────────────────────────


def _status_text(node: Any) -> str:
    """Render a node's status as the word a human reads, whatever type carries it.

    ``Node.status`` is a ``NodeStatus(str, Enum)``, and ``str()`` on that yields
    ``"NodeStatus.ONLINE"`` — matching on the raw string silently reports every online
    node as offline, which is how a registry with a live node can fail its own check.
    """
    value = getattr(node, "status", "")
    return str(getattr(value, "value", value)).lower()


_REGISTER_NEXT_STEP = (
    "register the execution host through the gateway API: POST /runtime/nodes with "
    "`Authorization: Bearer $(navig config get gateway.auth.token)`, on the host that "
    "runs the brain"
)


def gateway_node_fetcher(
    brain_url: str,
    token: str,
    *,
    timeout: float = 6.0,
    opener: Any | None = None,
) -> Callable[[], tuple[list[Any] | None, str]]:
    """Return a reader for the *fleet* registry over the brain's gateway API.

    The registry that decides placement lives on the brain, not on whichever machine
    happens to run this diagnostic: a store read on a client describes the wrong
    installation. A client can only see the fleet through ``GET /runtime/nodes``, which
    needs the brain's gateway token — so this reader is opt-in, and its absence is
    reported as unknown rather than as "no nodes exist".
    """

    def fetch() -> tuple[list[Any] | None, str]:
        request = urllib.request.Request(
            f"{brain_url.rstrip('/')}/runtime/nodes",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        handle = opener or urllib.request.build_opener()
        try:
            with handle.open(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            return None, f"GET {brain_url}/runtime/nodes answered HTTP {exc.code}"
        except Exception as exc:
            return None, f"could not read {brain_url}/runtime/nodes: {exc}"

        data = payload.get("data") if isinstance(payload, dict) else None
        nodes = data.get("nodes") if isinstance(data, dict) else None
        if not isinstance(nodes, list):
            return None, f"GET {brain_url}/runtime/nodes returned no node list"
        return nodes, ""

    return fetch


def probe_runtime_nodes(
    store: Any | None = None,
    *,
    fetcher: Callable[[], tuple[list[Any] | None, str]] | None = None,
    source: str = "this host's runtime store",
) -> list[Requirement]:
    """Read the distributed-execution node registry.

    This is the existing ``RuntimeStore`` — the assessment must not invent a second
    inventory of machines. An empty registry is ``FAIL`` for remote agents because it
    positively proves nothing can be dispatched anywhere but the local process; an
    unreadable registry is ``UNKNOWN``.

    ``source`` names the store the numbers came from. When only the local store could be
    read, the fleet registry gets its own ``UNKNOWN`` row rather than being equated with
    a client machine's empty file.
    """
    if fetcher is not None:
        nodes, error = fetcher()
        if nodes is None:
            return [
                Requirement(
                    "runtime_node_registered",
                    Status.UNKNOWN,
                    f"could not read the fleet registry: {error}",
                    "run this command on the brain, or set remote_dev.brain_url and "
                    "gateway.auth.token here",
                )
            ]
    else:
        try:
            if store is None:
                from navig.contracts.store import RuntimeStore

                store = RuntimeStore()
            nodes = list(store.list_nodes())
        except Exception as exc:
            return [
                Requirement(
                    "runtime_node_registered",
                    Status.UNKNOWN,
                    f"could not read {source}: {exc}",
                    "inspect the runtime store on the host that runs the gateway",
                )
            ]

    if not nodes:
        rows = [
            Requirement(
                "runtime_node_registered",
                Status.FAIL,
                f"0 nodes in {source} — nothing can be dispatched off this machine",
                _REGISTER_NEXT_STEP,
            )
        ]
        if fetcher is None:
            rows.append(
                Requirement(
                    "fleet_registry_readable",
                    Status.UNKNOWN,
                    "the fleet registry is the brain's; this machine's store is not it",
                    "run this command on the brain, or set remote_dev.brain_url and "
                    "gateway.auth.token here to read GET /runtime/nodes",
                )
            )
        return rows

    online = [n for n in nodes if _status_text(n) == "online"]
    ids = ", ".join(str(getattr(n, "hostname", n)) for n in nodes[:4])
    return [
        Requirement(
            "runtime_node_registered",
            Status.PASS,
            f"{len(nodes)} node(s) in {source}: {ids}",
        ),
        Requirement(
            "runtime_node_online",
            Status.PASS if online else Status.FAIL,
            (
                f"{len(online)} of {len(nodes)} node(s) online"
                if online
                else f"none of the {len(nodes)} registered node(s) is online"
            ),
            "" if online else "start the remote agent and let it advertise itself",
        ),
    ]


# ── CI: is this workstation the only runner? ─────────────────────────────────


_RUNNER_RE = re.compile(r"Runner\.Listener|actions-runner|act[-_]runner", re.IGNORECASE)


def probe_ci_runners(
    *,
    process_lister: Callable[[], Sequence[str]] | None = None,
) -> list[Requirement]:
    """Detect CI runners hosted on *this* machine.

    A runner found here is the concrete evidence that CI depends on this workstation: every
    job it serves stops when it powers off. Runner *provisioning* on GitHub cannot be
    observed locally, so that half is reported as ``UNKNOWN`` with the exact command that
    can answer it — never as a pass.
    """
    lister = process_lister or _list_processes
    try:
        lines = list(lister())
    except Exception as exc:
        return [
            Requirement(
                "ci_runner_not_local_only",
                Status.UNKNOWN,
                f"could not enumerate local processes: {exc}",
                "run `navig doctor remote-dev` again from a shell",
            )
        ]

    local = sorted({line.strip() for line in lines if line and _RUNNER_RE.search(line)})
    requirements = [
        Requirement(
            "ci_runner_presence",
            # Not PASS/FAIL: finding a runner on this machine does not say whether the
            # required checks survive the workstation powering off. On the brain that
            # same process is the answer; on a workstation it is the dependency. Only
            # the registration says which, so the probe refuses to guess.
            Status.UNKNOWN if local else Status.PASS,
            (
                f"{len(local)} CI runner process(es) run on this machine, and where they "
                "are hosted decides whether checks survive: " + "; ".join(local[:3])
            )
            if local
            else "no CI runner processes on this machine",
            ""
            if not local
            else "check the runner's registration to see which machine holds it: "
            "gh api repos/navig-run/labs/actions/runners (GitHub) or "
            "GET /api/v1/actions/runners on the Gitea host",
        ),
        Requirement(
            "ci_provisioning_observable",
            Status.UNKNOWN,
            "hosted-runner provisioning is only observable through the GitHub API",
            "gh api repos/navig-run/labs/actions/runners --jq '.runners[] | "
            "{name, status, labels: [.labels[].name]}'",
        ),
    ]
    return requirements


def _list_processes() -> list[str]:
    """Best-effort, read-only process listing across the platforms NAVIG runs on."""
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True,
                timeout=8,
                check=False,
            )
        else:
            completed = subprocess.run(
                ["ps", "-eo", "args"],
                capture_output=True,
                timeout=8,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        return []
    raw = completed.stdout or b""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    lines = [line for line in text.splitlines() if line.strip()]
    if os.name != "nt":
        return lines
    # Only the image name survives: `tasklist` localises the remaining columns through
    # the console code page (a Russian console renders the memory column as `115я252 K`),
    # and a diagnostic must not leak its own encoding into the evidence it prints.
    names: list[str] = []
    for line in lines:
        head = line.split('","', 1)[0].strip('"').strip()
        if head:
            names.append(head)
    return names


# ── remote build surface ─────────────────────────────────────────────────────


def _ssh_command(host: dict[str, Any], remote_command: str) -> list[str]:
    argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    user = str(host.get("user") or "").strip()
    address = str(host.get("host") or "").strip()
    if user:
        argv += ["-l", user]
    if host.get("port"):
        argv += ["-p", str(host["port"])]
    key = str(host.get("ssh_key") or "").strip()
    if key:
        argv += ["-i", key]
    argv += [address or str(host.get("name") or ""), remote_command]
    return argv


def probe_remote_build_surface(
    host: dict[str, Any],
    *,
    repo_path: str = DEFAULT_REMOTE_REPO_PATH,
    runner: Callable[[list[str], float], tuple[int, str, str]] | None = None,
    timeout: float = 12.0,
    alias: str | None = None,
) -> list[Requirement]:
    """Can the remote host build NAVIG?  One bounded SSH round-trip answers it.

    The probe asks only three read-only questions: what the host calls itself, whether the
    monorepo is checked out, and whether the toolchain is on PATH. Nothing is written.

    If the address recorded in the registry does not answer but the operator's SSH alias for
    the same host does, that is reported as a pass **with the drift named**, not hidden.
    """
    invoke = runner or _run
    script = (
        "hostname; "
        f"test -d {repo_path} && echo checkout=yes || echo checkout=no; "
        "command -v git >/dev/null && echo git=yes || echo git=no; "
        "command -v node >/dev/null && echo node=yes || echo node=no; "
        "command -v python3 >/dev/null && echo python3=yes || echo python3=no"
    )

    attempts: list[tuple[str, list[str]]] = [("registry address", _ssh_command(host, script))]
    if alias:
        attempts.append(
            (
                f"ssh alias {alias!r}",
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", alias, script],
            )
        )

    errors: list[str] = []
    for how, argv in attempts:
        code, out, err = invoke(argv, timeout)
        if code != 0:
            errors.append(f"{how}: {err.strip() or f'exit {code}'}")
            continue
        return _build_requirements(out, via=how, host=host)

    if errors and all("Permission denied" in e for e in errors):
        return [
            Requirement(
                "remote_ssh_reachable",
                Status.FAIL,
                "SSH authentication was refused by the remote host",
                "verify the key in the host definition is authorised remotely",
            )
        ]
    return [
        Requirement(
            "remote_ssh_reachable",
            Status.UNKNOWN,
            "could not reach the remote build host over SSH: " + " | ".join(errors),
            "check the address in ~/.navig/hosts and that sshd accepts this key",
        )
    ]


def _build_requirements(out: str, *, via: str, host: dict[str, Any]) -> list[Requirement]:
    facts = dict(
        line.split("=", 1)
        for line in out.splitlines()
        if "=" in line and len(line.split("=", 1)) == 2
    )
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    remote_name = lines[0] if lines else str(host.get("name") or "remote")

    drift = ""
    if via != "registry address":
        drift = (
            f"; registry address {host.get('host')}:{host.get('port') or 22} did not answer, "
            "reached through the SSH alias instead — registry address is stale"
        )

    requirements = [
        Requirement("remote_ssh_reachable", Status.PASS, f"reached {remote_name} via {via}{drift}")
    ]

    has_checkout = facts.get("checkout") == "yes"
    requirements.append(
        Requirement(
            "remote_checkout_present",
            Status.PASS if has_checkout else Status.FAIL,
            (
                f"monorepo checked out on {remote_name}"
                if has_checkout
                else f"no monorepo checkout on {remote_name} — it cannot build what is not there"
            ),
            ""
            if has_checkout
            else f"clone the canonical repo on {remote_name} (the checkout is a build input, "
            "not a credential)",
        )
    )

    missing = [tool for tool in ("git", "node", "python3") if facts.get(tool) != "yes"]
    requirements.append(
        Requirement(
            "remote_toolchain_present",
            Status.PASS if not missing else Status.FAIL,
            (
                "git, node and python3 are on PATH"
                if not missing
                else f"missing on PATH: {', '.join(missing)}"
            ),
            "" if not missing else f"install {', '.join(missing)} on {remote_name}",
        )
    )
    return requirements


def _run(argv: list[str], timeout: float) -> tuple[int, str, str]:
    """Run `ssh <host> <our own command>` and return ``(status, stdout, stderr)``.

    The codec is named, not inherited: what crosses the wire here is the output of a command
    *this module wrote* — a remote ``git rev-parse``, a ``PATH`` probe, three version strings —
    so it is UTF-8 regardless of the console code page on either side. Leaving it to the
    locale (``text=True`` with no ``encoding=``) is what
    ``tests/quality/test_dynamic_subprocess_encoding.py`` refuses, because it decodes a
    user-facing tool's bytes with whatever the machine happens to be set to.
    """
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


# ── release: is there exactly one publisher? ─────────────────────────────────


_PUBLISH_MARKERS = ("twine upload", "npm publish", "gh release create", "wrangler publish")


def probe_release_authority(repo_root: Path, *, editor_url: str = "") -> list[Requirement]:
    """§14.1 of the brief: which mechanism actually publishes?

    Two live publication paths are a ``FAIL`` — a version can be published twice, or the
    wrong one can win. The check reads the two documented publishers and reports what each
    one claims, so the operator can pick exactly one.
    """
    script = repo_root / "core" / "tools" / "release.sh"
    workflow = repo_root / ".github" / "workflows" / "release.yml"

    publishers: list[str] = []
    for path, label in (
        (script, "core/tools/release.sh"),
        (workflow, ".github/workflows/release.yml"),
    ):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(marker in text for marker in _PUBLISH_MARKERS):
            publishers.append(label)

    requirements: list[Requirement] = []
    if not publishers:
        requirements.append(
            Requirement(
                "publication_authority",
                Status.FAIL,
                "no publication path found in the repository",
                "locate the real release mechanism before claiming release readiness",
            )
        )
    elif len(publishers) == 1:
        requirements.append(
            Requirement(
                "publication_authority",
                Status.PASS,
                f"exactly one publication path: {publishers[0]}",
            )
        )
    else:
        requirements.append(
            Requirement(
                "publication_authority",
                Status.FAIL,
                "two publication paths can both publish: " + " and ".join(publishers),
                "choose one authoritative publisher and make the other inert",
            )
        )

    requirements.append(
        Requirement(
            "publication_credentials",
            Status.UNKNOWN,
            "publishing credentials are not readable from here by design",
            "verify the token scope in the vault; never print the secret",
        )
    )
    return requirements


# ──────────────────────────────────────────────────────────────────────────────
# The assessment
# ──────────────────────────────────────────────────────────────────────────────


def assess(
    *,
    repo_root: Path,
    editor_url: str = DEFAULT_EDITOR_URL,
    hosts: Iterable[dict[str, Any]] | None = None,
    store: Any | None = None,
    ssh_runner: Callable[[list[str], float], tuple[int, str, str]] | None = None,
    process_lister: Callable[[], Sequence[str]] | None = None,
    remote_repo_path: str = DEFAULT_REMOTE_REPO_PATH,
    ssh_alias: str | None = "outpost",
    check_remote_build: bool = True,
    brain_url: str = "",
    gateway_token: str = "",
    node_fetcher: Callable[[], tuple[list[Any] | None, str]] | None = None,
) -> list[Capability]:
    """Assess all six capabilities and return them in reporting order.

    Every decision input is a parameter, which is what makes the whole thing testable
    without a network, a remote host or a live node registry.
    """
    registry = list(hosts) if hosts is not None else read_host_registry()
    brain_hosts = find_hosts_by_role(registry, "brain")

    editor = Capability(
        "REMOTE_EDIT_READY",
        "Remote editor",
        "A browser can reach the persistent editor without the workstation.",
    )
    editor.add(probe_http_edge(editor_url))

    agent = Capability(
        "REMOTE_AGENT_READY",
        "Coding agents",
        "A coding agent can be dispatched to, and run on, another machine.",
    )
    node_source = "this host's runtime store"
    fetcher = node_fetcher
    if fetcher is None and brain_url and gateway_token:
        fetcher = gateway_node_fetcher(brain_url, gateway_token)
        node_source = f"the brain's registry ({brain_url})"
    for requirement in probe_runtime_nodes(store, fetcher=fetcher, source=node_source):
        agent.add(requirement)

    ci = Capability(
        "REMOTE_CI_READY",
        "Remote CI",
        "Required checks run on infrastructure that does not power off with the workstation.",
    )
    for requirement in probe_ci_runners(process_lister=process_lister):
        ci.add(requirement)

    build = Capability(
        "REMOTE_BUILD_READY",
        "Remote build",
        "A remote host holds the source and the toolchain to build and verify a release.",
    )
    if not brain_hosts:
        build.add(
            Requirement(
                "brain_host_registered",
                Status.FAIL,
                "no host in the registry has metadata.role: brain",
                "record the brain host in ~/.navig/hosts so placement can target it",
            )
        )
    else:
        brain = brain_hosts[0]
        build.add(
            Requirement(
                "brain_host_registered",
                Status.PASS,
                f"{brain.get('name')} is registered with role brain",
            )
        )
        if check_remote_build:
            for requirement in probe_remote_build_surface(
                brain,
                repo_path=remote_repo_path,
                runner=ssh_runner,
                alias=ssh_alias,
            ):
                build.add(requirement)
        else:
            build.add(
                Requirement(
                    "remote_ssh_reachable",
                    Status.UNKNOWN,
                    "remote build probe skipped (--no-remote-probe)",
                    "re-run without --no-remote-probe to verify the build host",
                )
            )

    release = Capability(
        "REMOTE_RELEASE_READY",
        "Release",
        "A verified artifact can be published from the remote side under one authority.",
    )
    for requirement in probe_release_authority(repo_root, editor_url=editor_url):
        release.add(requirement)

    capabilities = [editor, agent, ci, build, release]

    neuron_off = Capability(
        "NEURON_OFF_READY",
        "Workstation off",
        "The composite claim: every capability above holds with the workstation powered off.",
    )
    not_ready = [c for c in capabilities if c.readiness is not Readiness.READY]
    if not_ready:
        neuron_off.add(
            Requirement(
                "all_capabilities_ready",
                Status.FAIL,
                "not ready: " + ", ".join(f"{c.key} ({c.readiness.value})" for c in not_ready),
                "clear the blockers above; this flag is derived, never asserted directly",
            )
        )
    else:
        neuron_off.add(
            Requirement(
                "all_capabilities_ready",
                Status.PASS,
                "all five capabilities verified; the workstation is not required",
            )
        )
    capabilities.append(neuron_off)
    return capabilities
