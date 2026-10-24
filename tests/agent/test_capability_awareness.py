"""The "can you access <machine>?" answer is runtime-derived and never fabricated.

Incident 2026-10-22: the operator asked whether NAVIG could read DeepSeek conversations
on his computer Neuron. The honest answer has three separate parts — configured route,
authorization, reachability — and the reachability part must never be guessed: this
module deliberately runs no network probes, so the reply says "not verified".
"""

from __future__ import annotations

import json
import sqlite3

from navig.agent import capability_awareness as ca


def _ssh(home, hosts: list[str]) -> None:
    path = home / ".ssh"
    path.mkdir(parents=True)
    (path / "config").write_text(
        "\n".join(f"Host {h}" for h in hosts) + "\n", encoding="utf-8"
    )


def _remote_db(tmp_path, rows: list[tuple[str, str, str | None]]) -> str:
    path = tmp_path / "os_remote.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "create table devices (name text, kind text, last_seen text, revoked_at text)"
    )
    conn.executemany("insert into devices values (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return str(path)


def test_ssh_hosts_lists_aliases_and_skips_patterns_and_github(tmp_path):
    home = tmp_path / "home"
    _ssh(home, ["labbox", "github.com"])

    assert ca.ssh_hosts(home) == ["labbox"]
    assert "ssh:labbox" in ca.configured_routes(home)


def test_a_brain_link_means_this_install_is_a_client(tmp_path):
    home = tmp_path / "home"
    (home / ".navig-os").mkdir(parents=True)
    (home / ".navig-os" / "brain-link.json").write_text(
        json.dumps({"brainName": "outpost-server", "deviceId": "83d011d1"}), encoding="utf-8"
    )

    routes = ca.configured_routes(home)
    assert any("brain-link" in route for route in routes)


def test_remote_devices_are_browsers_not_executors(tmp_path):
    db = _remote_db(
        tmp_path,
        [
            ("Chrome on Windows", "remote", "2026-09-29", None),
            ("Phone", "remote", None, None),
            ("Retired", "remote", "2026-01-01", "2026-01-02"),
        ],
    )
    home = tmp_path / "home"
    home.mkdir()

    state = ca.describe("chrome on windows", home=home, db_path=db)
    assert state.remote_devices == ["Chrome on Windows", "Phone"]
    assert any("web device, not an executor" in note for note in state.notes)
    assert state.reachable == "not probed"


def test_an_unknown_machine_gets_an_honest_no_route_note(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    state = ca.describe("neuron", home=home, db_path=str(tmp_path / "missing.db"))
    assert state.configured_routes == []
    assert state.remote_devices == []
    assert any("No configured route names neuron" in note for note in state.notes)
    assert state.requires_consent is True


def test_ordinary_chat_gets_no_capability_block(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    assert ca.prompt_block("What's the weather like?", home=home, db_path=str(tmp_path / "x.db")) == ""


def test_an_access_question_gets_configured_route_and_no_bogus_reachability(tmp_path):
    home = tmp_path / "home"
    _ssh(home, ["labbox"])

    block = ca.prompt_block(
        "Can you read my DeepSeek conversations on my computer Neuron?",
        home=home,
        db_path=str(tmp_path / "missing.db"),
    )

    assert "Capability awareness" in block
    assert "ssh:labbox" in block
    assert "reachability: not probed" in block
    assert "consent" in block
    # The block names the words only to forbid them — it must never MAKE the claim.
    assert "is online" not in block.lower()
    assert "is offline" not in block.lower()
    assert "reachable: yes" not in block.lower()
