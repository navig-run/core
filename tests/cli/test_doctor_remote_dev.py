"""Tests for ``navig doctor remote-dev`` — the read-only PC-off readiness report.

Every decision input of the assessment is a parameter, so nothing here needs a network, a
remote host or a live node registry. The point of these tests is the *honesty contract* the
rest of ``navig doctor`` is held to: a capability is READY only when every requirement
behind it was verified, and ``NEURON_OFF_READY`` is never asserted directly.
"""

from __future__ import annotations

import enum
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest
import typer

from navig.commands import doctor
from navig.readiness import remote_dev as rd

# ── helpers ──────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.status = status
        self.headers = headers
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _FakeOpener:
    """Stands in for ``urllib`` — records the request, answers with a scripted outcome."""

    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self._status = status
        self._headers = headers or {}
        self._body = body
        self.requests: list[Any] = []

    def open(self, request: Any, timeout: float | None = None) -> _FakeResponse:
        self.requests.append(request)
        if self._status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, self._status, "scripted", self._headers, None
            )
        return _FakeResponse(self._status, self._headers, self._body)


def _requirement(name: str, status: rd.Status, evidence: str = "evidence") -> rd.Requirement:
    return rd.Requirement(name, status, evidence)


def _capability(*statuses: rd.Status) -> rd.Capability:
    capability = rd.Capability("TEST_CAP", "Test capability", "purpose")
    for index, status in enumerate(statuses):
        capability.add(_requirement(f"req_{index}", status, f"evidence {index}"))
    return capability


def _repo_root(tmp_path: Path, *, script: bool = True, workflow: bool = False) -> Path:
    """A repository fixture with a controlled set of publication paths."""
    if script:
        script_path = tmp_path / "core" / "tools" / "release.sh"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text("#!/bin/sh\npython -m twine upload dist/*\n", encoding="utf-8")
    if workflow:
        workflow_path = tmp_path / ".github" / "workflows" / "release.yml"
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text("run: python -m twine upload dist/*\n", encoding="utf-8")
    return tmp_path


def _brain_host() -> dict[str, Any]:
    return {
        "name": "outpost",
        "host": "192.0.2.10",
        "port": 22,
        "user": "void",
        "metadata": {"role": "brain", "hostname": "outpost-server"},
    }


# ── the readiness derivation ─────────────────────────────────────────────────


def test_every_requirement_must_be_verified_for_ready() -> None:
    assert _capability(rd.Status.PASS, rd.Status.PASS).readiness is rd.Readiness.READY


def test_an_unverified_requirement_holds_the_capability_at_unverified() -> None:
    """An unknown is not a pass — this is the whole honesty contract in one line."""
    capability = _capability(rd.Status.PASS, rd.Status.UNKNOWN)
    assert capability.readiness is rd.Readiness.UNVERIFIED
    assert capability.readiness is not rd.Readiness.READY


def test_one_verified_failure_blocks_even_when_everything_else_passes() -> None:
    capability = _capability(rd.Status.PASS, rd.Status.FAIL, rd.Status.PASS)
    assert capability.readiness is rd.Readiness.BLOCKED


def test_a_capability_with_no_evidence_is_unverified_not_ready() -> None:
    empty = rd.Capability("EMPTY", "Empty", "nothing was checked")
    assert empty.readiness is rd.Readiness.UNVERIFIED


def test_headline_leads_with_the_verified_blocker() -> None:
    capability = _capability(rd.Status.PASS, rd.Status.FAIL, rd.Status.UNKNOWN)
    assert capability.headline().startswith("evidence 1")


def test_headline_keeps_a_verified_fact_visible_next_to_an_unknown() -> None:
    """'exactly one publisher, credentials unreadable' is a different report.

    Dropping the verified half would hide the answer to §14.1 of the brief behind an
    unreadable credential.
    """
    capability = _capability(rd.Status.PASS, rd.Status.UNKNOWN)
    headline = capability.headline()
    assert "evidence 0" in headline
    assert "could not verify" in headline


# ── the edge probe ───────────────────────────────────────────────────────────


def test_a_gated_302_is_a_pass_and_names_the_access_layer() -> None:
    opener = _FakeOpener(
        status=302,
        headers={"Location": "https://navig-www-pages.cloudflareaccess.com/cdn-cgi/access/login"},
    )
    requirement = rd.probe_http_edge("https://code.miztizm.com", opener=opener)

    assert requirement.status is rd.Status.PASS
    assert "Cloudflare Access" in requirement.evidence


def test_a_5xx_is_a_failure_not_an_unknown() -> None:
    """The edge answering badly is a verified negative, not an absence of evidence."""
    requirement = rd.probe_http_edge("https://code.miztizm.com", opener=_FakeOpener(status=502))

    assert requirement.status is rd.Status.FAIL
    assert "app behind it" in requirement.evidence


def test_a_transport_error_is_unknown_and_never_a_pass() -> None:
    class _Dead:
        def open(self, request: Any, timeout: float | None = None) -> Any:
            raise urllib.error.URLError("name resolution failed")

    requirement = rd.probe_http_edge("https://nowhere.invalid", opener=_Dead())

    assert requirement.status is rd.Status.UNKNOWN
    assert requirement.next_step


def test_no_configured_url_is_unknown() -> None:
    assert rd.probe_http_edge("").status is rd.Status.UNKNOWN


# ── the node registry ────────────────────────────────────────────────────────


class _Store:
    def __init__(self, nodes: list[Any] | None = None, error: Exception | None = None) -> None:
        self._nodes = nodes or []
        self._error = error

    def list_nodes(self) -> list[Any]:
        if self._error is not None:
            raise self._error
        return self._nodes


class _Node:
    def __init__(self, hostname: str, status: str) -> None:
        self.hostname = hostname
        self.status = status


def test_an_empty_registry_proves_nothing_can_be_dispatched() -> None:
    requirements = rd.probe_runtime_nodes(_Store())
    by_name = {r.name: r for r in requirements}

    assert by_name["runtime_node_registered"].status is rd.Status.FAIL
    assert "0 nodes" in by_name["runtime_node_registered"].evidence
    # A client machine's empty store is not the fleet: that has to stay unknown rather
    # than be reported as "the fleet has no nodes".
    assert by_name["fleet_registry_readable"].status is rd.Status.UNKNOWN
    assert "brain" in by_name["fleet_registry_readable"].evidence


def test_a_fleet_registry_read_over_http_passes_and_names_its_source() -> None:
    requirements = rd.probe_runtime_nodes(
        fetcher=lambda: ([_Node("outpost-server", "online")], ""),
        source="the brain's registry (http://brain:8789)",
    )
    by_name = {r.name: r for r in requirements}

    assert by_name["runtime_node_registered"].status is rd.Status.PASS
    assert "the brain's registry" in by_name["runtime_node_registered"].evidence
    assert "fleet_registry_readable" not in by_name


def test_a_str_enum_online_status_counts_as_online() -> None:
    class _Status(str, enum.Enum):
        ONLINE = "online"

    class _EnumNode:
        hostname = "outpost-server"
        status = _Status.ONLINE

    requirements = rd.probe_runtime_nodes(_Store([_EnumNode()]))
    by_name = {r.name: r for r in requirements}

    # str(NodeStatus.ONLINE) is "NodeStatus.ONLINE", so matching the raw string would
    # report a live node as offline.
    assert by_name["runtime_node_online"].status is rd.Status.PASS


def test_an_unreadable_fleet_registry_is_unknown_and_keeps_the_error() -> None:
    requirements = rd.probe_runtime_nodes(fetcher=lambda: (None, "HTTP 401"))

    assert [r.status for r in requirements] == [rd.Status.UNKNOWN]
    assert "HTTP 401" in requirements[0].evidence


def test_the_gateway_fetcher_reads_nodes_out_of_the_api_envelope() -> None:
    payload = {"ok": True, "data": {"nodes": [{"hostname": "outpost-server"}]}, "error": None}
    fetch = rd.gateway_node_fetcher(
        "http://brain:8789", "secret", opener=_FakeOpener(body=json.dumps(payload).encode())
    )

    nodes, error = fetch()

    assert error == ""
    assert [n["hostname"] for n in nodes] == ["outpost-server"]


def test_an_unreadable_registry_is_unknown_not_a_pass() -> None:
    requirements = rd.probe_runtime_nodes(_Store(error=OSError("permission denied")))

    assert requirements[0].status is rd.Status.UNKNOWN


def test_a_registered_but_offline_node_fails_the_online_requirement() -> None:
    requirements = rd.probe_runtime_nodes(_Store([_Node("outpost-server", "offline")]))
    by_name = {r.name: r for r in requirements}

    assert by_name["runtime_node_registered"].status is rd.Status.PASS
    assert by_name["runtime_node_online"].status is rd.Status.FAIL


def test_an_online_node_passes_both_requirements() -> None:
    requirements = rd.probe_runtime_nodes(_Store([_Node("outpost-server", "online")]))

    assert all(r.status is rd.Status.PASS for r in requirements)


# ── local CI runners ─────────────────────────────────────────────────────────


def test_a_local_runner_is_reported_but_not_judged_and_names_the_process() -> None:
    requirements = rd.probe_ci_runners(process_lister=lambda: ["Runner.Listener.exe", "code.exe"])
    by_name = {r.name: r for r in requirements}

    # A runner process here is a fact, not a verdict: on the brain it is the answer,
    # on a workstation it is the dependency. Only registration settles which.
    assert by_name["ci_runner_presence"].status is rd.Status.UNKNOWN
    assert "Runner.Listener.exe" in by_name["ci_runner_presence"].evidence
    assert "code.exe" not in by_name["ci_runner_presence"].evidence


def test_a_gitea_act_runner_is_recognised_whatever_the_spelling() -> None:
    for spelling in ("act-runner", "act_runner", "actions-runner"):
        requirements = rd.probe_ci_runners(process_lister=lambda s=spelling: [f"/usr/bin/{s}"])
        by_name = {r.name: r for r in requirements}

        assert by_name["ci_runner_presence"].status is rd.Status.UNKNOWN, spelling


def test_clean_process_list_passes_but_provisioning_stays_unknown() -> None:
    """We can prove no runner is *here*; we cannot prove one exists elsewhere."""
    requirements = rd.probe_ci_runners(process_lister=lambda: ["code.exe"])
    by_name = {r.name: r for r in requirements}

    assert by_name["ci_runner_presence"].status is rd.Status.PASS
    assert by_name["ci_provisioning_observable"].status is rd.Status.UNKNOWN
    assert "gh api" in by_name["ci_provisioning_observable"].next_step


def test_a_listing_failure_never_becomes_green() -> None:
    def boom() -> list[str]:
        raise OSError("no process table")

    requirements = rd.probe_ci_runners(process_lister=boom)

    assert requirements[0].status is rd.Status.UNKNOWN


# ── the remote build surface ─────────────────────────────────────────────────


def _ssh_answers(*answers: tuple[int, str, str]) -> Any:
    remaining = list(answers)

    def invoke(argv: list[str], timeout: float) -> tuple[int, str, str]:
        if not remaining:
            raise AssertionError(f"unexpected extra ssh call: {argv}")
        return remaining.pop(0)

    return invoke


READY_OUTPUT = "outpost-server\ncheckout=yes\ngit=yes\nnode=yes\npython3=yes"


def test_a_checkout_plus_a_toolchain_passes() -> None:
    requirements = rd.probe_remote_build_surface(
        _brain_host(), runner=_ssh_answers((0, READY_OUTPUT, ""))
    )

    assert all(r.status is rd.Status.PASS for r in requirements)
    assert "via registry address" in requirements[0].evidence


def test_a_missing_checkout_fails_and_says_what_to_do() -> None:
    output = "outpost-server\ncheckout=no\ngit=yes\nnode=yes\npython3=yes"
    requirements = rd.probe_remote_build_surface(
        _brain_host(), runner=_ssh_answers((0, output, ""))
    )
    by_name = {r.name: r for r in requirements}

    assert by_name["remote_checkout_present"].status is rd.Status.FAIL
    assert "cannot build what is not there" in by_name["remote_checkout_present"].evidence
    assert by_name["remote_checkout_present"].next_step


def test_a_missing_toolchain_fails_and_names_the_tool() -> None:
    output = "outpost-server\ncheckout=yes\ngit=yes\nnode=no\npython3=yes"
    requirements = rd.probe_remote_build_surface(
        _brain_host(), runner=_ssh_answers((0, output, ""))
    )
    by_name = {r.name: r for r in requirements}

    assert by_name["remote_toolchain_present"].status is rd.Status.FAIL
    assert "node" in by_name["remote_toolchain_present"].evidence


def test_the_alias_fallback_passes_and_names_the_registry_drift() -> None:
    """A stale address must be reported, not silently worked around."""
    requirements = rd.probe_remote_build_surface(
        _brain_host(),
        alias="outpost",
        runner=_ssh_answers(
            (255, "", "ssh: connect to host 192.0.2.10 port 22: Connection refused"),
            (0, READY_OUTPUT, ""),
        ),
    )

    assert requirements[0].status is rd.Status.PASS
    assert "registry address is stale" in requirements[0].evidence


def test_refused_authentication_is_a_failure() -> None:
    requirements = rd.probe_remote_build_surface(
        _brain_host(),
        alias=None,
        runner=_ssh_answers((255, "", "void@192.0.2.10: Permission denied (publickey).")),
    )

    assert requirements[0].status is rd.Status.FAIL


def test_an_unusable_connection_is_unknown_not_green() -> None:
    requirements = rd.probe_remote_build_surface(
        _brain_host(),
        alias="outpost",
        runner=_ssh_answers((255, "", "connect timeout"), (255, "", "connect timeout")),
    )

    assert requirements[0].status is rd.Status.UNKNOWN


# ── the publication authority (§14.1) ───────────────────────────────────────


def test_exactly_one_publisher_passes_and_leaves_credentials_unknown(tmp_path: Path) -> None:
    requirements = rd.probe_release_authority(_repo_root(tmp_path))
    by_name = {r.name: r for r in requirements}

    assert by_name["publication_authority"].status is rd.Status.PASS
    assert "exactly one publication path" in by_name["publication_authority"].evidence
    assert by_name["publication_credentials"].status is rd.Status.UNKNOWN


def test_two_publishers_fail_because_a_version_could_publish_twice(tmp_path: Path) -> None:
    requirements = rd.probe_release_authority(_repo_root(tmp_path, script=True, workflow=True))
    by_name = {r.name: r for r in requirements}

    assert by_name["publication_authority"].status is rd.Status.FAIL
    assert "two publication paths" in by_name["publication_authority"].evidence


def test_no_publisher_fails(tmp_path: Path) -> None:
    requirements = rd.probe_release_authority(_repo_root(tmp_path, script=False, workflow=False))

    assert requirements[0].status is rd.Status.FAIL


# ── the composite assessment ────────────────────────────────────────────────


def _all_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[rd.Capability]:
    """Everything verifiable is verified; only the credential stays unknown."""
    monkeypatch.setattr(
        rd,
        "probe_http_edge",
        lambda url, **_kw: _requirement("edge_reachable", rd.Status.PASS, "HTTP 302"),
    )
    monkeypatch.setattr(
        rd,
        "probe_runtime_nodes",
        lambda _store=None, **_kw: [
            _requirement("runtime_node_registered", rd.Status.PASS, "1 node(s) registered")
        ],
    )
    monkeypatch.setattr(
        rd,
        "probe_ci_runners",
        lambda **_kw: [_requirement("ci_runner_not_local_only", rd.Status.PASS, "none here")],
    )
    monkeypatch.setattr(
        rd,
        "probe_remote_build_surface",
        lambda *_a, **_kw: [
            _requirement("remote_ssh_reachable", rd.Status.PASS, "reached outpost-server"),
            _requirement("remote_checkout_present", rd.Status.PASS, "monorepo checked out"),
            _requirement("remote_toolchain_present", rd.Status.PASS, "git, node and python3"),
        ],
    )
    return rd.assess(repo_root=_repo_root(tmp_path), hosts=[_brain_host()])


def test_assess_reports_the_six_capabilities_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    keys = [c.key for c in _all_green(monkeypatch, tmp_path)]

    assert keys == [
        "REMOTE_EDIT_READY",
        "REMOTE_AGENT_READY",
        "REMOTE_CI_READY",
        "REMOTE_BUILD_READY",
        "REMOTE_RELEASE_READY",
        "NEURON_OFF_READY",
    ]


def test_one_unverified_credential_keeps_neuron_off_unready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The strongest case: every other capability verified READY and it still blocks.

    ``NEURON_OFF_READY`` is derived from the five capabilities and can never be asserted
    directly, so an unreadable publication credential alone must hold it back.
    """
    capabilities = _all_green(monkeypatch, tmp_path)
    by_key = {c.key: c for c in capabilities}

    assert by_key["REMOTE_EDIT_READY"].readiness is rd.Readiness.READY
    assert by_key["REMOTE_RELEASE_READY"].readiness is rd.Readiness.UNVERIFIED
    assert by_key["NEURON_OFF_READY"].readiness is rd.Readiness.BLOCKED
    assert by_key["NEURON_OFF_READY"].readiness is not rd.Readiness.READY
    assert "REMOTE_RELEASE_READY" in by_key["NEURON_OFF_READY"].headline()


def test_skipping_the_remote_probe_degrades_to_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--no-remote-probe`` must not look like a pass or like a proven failure."""
    _all_green(monkeypatch, tmp_path)
    capabilities = rd.assess(
        repo_root=_repo_root(tmp_path), hosts=[_brain_host()], check_remote_build=False
    )
    build = {c.key: c for c in capabilities}["REMOTE_BUILD_READY"]
    by_name = {r.name: r for r in build.requirements}

    assert build.readiness is rd.Readiness.UNVERIFIED
    assert by_name["remote_ssh_reachable"].status is rd.Status.UNKNOWN
    assert "--no-remote-probe" in by_name["remote_ssh_reachable"].evidence
    # A skipped probe must not invent the answers it did not collect.
    assert "remote_checkout_present" not in by_name


def test_a_registry_without_a_brain_host_blocks_the_remote_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _all_green(monkeypatch, tmp_path)
    capabilities = rd.assess(
        repo_root=_repo_root(tmp_path),
        hosts=[{"name": "nowhere", "host": "10.0.0.1", "metadata": {"role": "addition"}}],
    )
    build = {c.key: c for c in capabilities}["REMOTE_BUILD_READY"]

    assert build.readiness is rd.Readiness.BLOCKED
    assert "metadata.role: brain" in build.headline()


# ── the command ─────────────────────────────────────────────────────────────


def _patched_command(
    monkeypatch: pytest.MonkeyPatch,
    capabilities: list[rd.Capability],
) -> None:
    monkeypatch.setattr(doctor, "_remote_dev_config_value", lambda _key, default: default)
    monkeypatch.setattr(rd, "assess", lambda **_kw: capabilities)


def test_json_output_is_machine_readable_and_exits_nonzero_when_blocked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    capabilities = _all_green(monkeypatch, tmp_path)
    _patched_command(monkeypatch, capabilities)

    with pytest.raises(typer.Exit) as exit_info:
        doctor.remote_dev_cmd(json_output=True, editor_url="", no_remote_probe=False, verbose=False)

    payload = json.loads(capsys.readouterr().out)
    assert exit_info.value.exit_code == 1
    assert payload["neuron_off_ready"] is False
    assert [c["key"] for c in payload["capabilities"]][-1] == "NEURON_OFF_READY"
    assert payload["capabilities"][-1]["requirements"]


def test_json_output_exits_zero_only_when_every_capability_is_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ready = _capability(rd.Status.PASS)
    composite = _capability(rd.Status.PASS)
    monkeypatch.setattr(doctor, "_remote_dev_config_value", lambda _key, default: default)
    monkeypatch.setattr(rd, "assess", lambda **_kw: [ready, composite])

    with pytest.raises(typer.Exit) as exit_info:
        doctor.remote_dev_cmd(json_output=True, editor_url="", no_remote_probe=True, verbose=False)

    payload = json.loads(capsys.readouterr().out)
    assert exit_info.value.exit_code == 0
    assert payload["neuron_off_ready"] is True
