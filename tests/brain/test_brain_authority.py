"""The ownership AUTHORITY — the shared record that replaces the local file.

No live network: a scripted in-memory authority is served through
``httpx.MockTransport`` (the same semantics as the Lighthouse Durable Object,
whose own suite is ``services/lighthouse/test/ownership.test.ts``). These tests
pin the properties the binding requirement names:

* an unreachable authority makes a singleton REFUSE (77) — no local promotion;
* a stale epoch is rejected, and a replay of an old epoch is fenced;
* a takeover of a live holder needs ``force`` + the expected owner, and bumps
  the epoch exactly once;
* two concurrent promotions have exactly one winner (the authority serialises);
* a client role cannot mutate ownership, even though it holds the deck key;
* a partition is neither a promotion nor a demotion, and recovery re-joins;
* the local lease file is a CACHE: the authority owns the decision;
* the migration receipt is only "complete" when the AUTHORITY names the target,
  and the move is written into the authority record.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from navig.brain import authority, guard
from navig.brain import migrate as mig
from navig.brain import telegram_mode as tg
from navig.brain.identity import owner_id
from navig.brain.lease import (
    EXIT_OWNERSHIP_REFUSED,
    ROLE_BRAIN,
    ROLE_CLIENT,
    LeaseStore,
    OwnershipConflict,
    StaleEpoch,
    brain_settings,
)

KEY = "navig_authority_test_key_0123456789"
EDGE = "https://edge.example.test"

MACHINE_A = "brn-aaaaaaaaaaaaaaaa"
MACHINE_B = "brn-bbbbbbbbbbbbbbbb"
TARGET = "brn-outpost00000000"


# ── a scripted authority (the DO's semantics without Cloudflare) ────────────


class FakeAuthority:
    """In-memory owner record served over ``httpx.MockTransport``.

    Deliberately mirrors the Durable Object's decisions (role gate, CAS on the
    epoch, moved marker, idempotency) so these tests exercise the CLIENT and the
    WIRING; the engine itself has its own suite.
    """

    def __init__(self) -> None:
        self.record: dict[str, Any] | None = None
        self.audit: list[dict[str, Any]] = []
        self.idempotent: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self.unreachable = False
        self.status_requests = 0
        self._lock = threading.Lock()
        #: Called after a successful release, so a test can model the target
        #: claiming the Brain (the step that completes a migration).
        self.on_release: Any = None

    # ── transport ────────────────────────────────────────────────────────
    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("simulated outage", request=request)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {KEY}":
            return httpx.Response(
                401, json={"ok": False, "code": "unauthorized", "error": "bad key"}
            )
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        with self._lock:
            self.requests.append(dict(body))
            if body.get("op") == "status":
                self.status_requests += 1
            return self._handle(body)

    def _ok(self, body: dict[str, Any], status: int = 200) -> httpx.Response:
        return httpx.Response(status, json={"ok": True, **body})

    def _err(self, status: int, code: str, error: str) -> httpx.Response:
        return httpx.Response(status, json={"ok": False, "code": code, "error": error})

    # ── semantics (a compact mirror of ownership_core.ts) ───────────────
    def _handle(self, body: dict[str, Any]) -> httpx.Response:
        op = str(body.get("op") or "status")
        now = time.time()
        if op == "status":
            return self._ok(
                {
                    "authority": "lighthouse-ownership",
                    "server_time": now,
                    "record": self.record,
                    "status": self._status_of(str(body.get("owner_id") or ""), now),
                }
            )
        oid = str(body.get("owner_id") or "").strip()
        role = str(body.get("role") or "all-in-one")
        if not oid:
            return self._err(400, "owner_id_required", "owner_id is required")
        if role not in ("brain", "all-in-one"):
            return self._err(
                403,
                "role_forbidden",
                f"this install is a {role} — a {role} may read ownership status but can "
                "never own the Brain or change its owner.",
            )
        rid = str(body.get("request_id") or "")
        if rid and rid in self.idempotent:
            return self._ok({**self.idempotent[rid], "idempotent": True})

        if op == "acquire":
            result = self._acquire(body, oid, role, now)
        elif op == "renew":
            result = self._renew(body, oid, now)
        elif op == "release":
            result = self._release(body, oid, now)
        elif op == "takeover":
            result = self._takeover(body, oid, role, now)
        elif op == "mark_moved":
            result = self._mark_moved(body, oid, now)
        elif op == "clear_moved":
            result = self._clear_moved(body, oid, now)
        else:
            return self._err(400, "unknown_op", f"unknown op {op}")
        if result.status_code == 200 and rid:
            self.idempotent[rid] = dict(result.json())
        return result

    def _status_of(self, viewer: str, now: float) -> dict[str, Any]:
        rec = self.record
        if rec is None:
            state = "free"
        elif rec.get("released_at") is not None:
            state = "moved" if rec.get("moved_marker") else "released"
        elif now > float(rec.get("heartbeat_at") or 0) + float(rec.get("ttl") or 300):
            state = "expired"
        else:
            state = "held"
        ours = bool(rec) and rec.get("owner_id") == viewer and rec.get("released_at") is None
        return {
            "state": state,
            "owner_id": rec.get("owner_id", "") if rec else "",
            "owner_hostname": rec.get("hostname", "") if rec else "",
            "owner_role": rec.get("role", "") if rec else "",
            "epoch": rec.get("epoch", 0) if rec else 0,
            "is_owner": ours,
            "can_claim": state in ("free", "released") or ours,
            "can_takeover": bool(rec)
            and rec.get("released_at") is None
            and rec.get("owner_id") != viewer
            and state == "expired",
            "summary": f"epoch {rec.get('epoch') if rec else 0} {state}",
        }

    def _claim(self, oid: str, role: str, ttl: int, now: float, reason: str) -> dict[str, Any]:
        record = {
            "schema": 1,
            "owner_id": oid,
            "role": role,
            "epoch": 1 if self.record is None else int(self.record["epoch"]) + 1,
            "ttl": ttl,
            "acquired_at": now,
            "heartbeat_at": now,
            "updated_at": now,
            "hostname": "test-host",
            "reason": reason,
            "takeovers": [] if self.record is None else list(self.record.get("takeovers") or []),
            "moved_marker": None,
            "released_at": None,
        }
        self.record = record
        return record

    def _acquire(self, body: dict[str, Any], oid: str, role: str, now: float) -> httpx.Response:
        ttl = int(body.get("ttl") or 300)
        rec = self.record
        if rec is None:
            return self._ok({"record": self._claim(oid, role, ttl, now, "first claim"), "epoch": 1})
        moved = rec.get("moved_marker")
        if isinstance(moved, dict) and moved.get("from_owner") == oid:
            return self._err(409, "moved", f"this Brain moved to {moved.get('to')}")
        if rec.get("released_at") is not None:
            record = self._claim(oid, role, ttl, now, "claimed a released record")
            record["cleared_moved"] = bool(moved)
            return self._ok({"record": record, "epoch": record["epoch"], "cleared_moved": bool(moved)})
        if rec.get("owner_id") == oid:
            rec["heartbeat_at"] = now
            rec["updated_at"] = now
            rec["ttl"] = ttl
            return self._ok({"record": rec, "epoch": rec["epoch"], "renewed": True})
        if now > float(rec["heartbeat_at"]) + float(rec["ttl"]):
            if body.get("auto_promote") is True:
                return self._takeover(
                    {**body, "reason": "auto-promoted"}, oid, role, now
                )
            return self._err(409, "expired", "the record has EXPIRED; auto_promote=false")
        return self._err(409, "held", f"the Brain is owned by {rec['owner_id']}; navig brain takeover")

    def _renew(self, body: dict[str, Any], oid: str, now: float) -> httpx.Response:
        rec = self.record
        if rec is None:
            return self._err(404, "no_record", "no ownership record")
        if body.get("epoch") is not None and int(body["epoch"]) != int(rec["epoch"]):
            return self._err(409, "stale_epoch", f"stale epoch {body['epoch']}: authority at epoch {rec['epoch']}")
        if rec.get("released_at") is not None:
            return self._err(409, "released", "the record is released")
        if rec.get("owner_id") != oid:
            return self._err(409, "not_owner", f"{rec['owner_id']} owns the Brain, not {oid}")
        rec["heartbeat_at"] = now
        rec["updated_at"] = now
        return self._ok({"record": rec, "epoch": rec["epoch"]})

    def _release(self, body: dict[str, Any], oid: str, now: float) -> httpx.Response:
        rec = self.record
        if rec is None:
            return self._err(404, "no_record", "no ownership record")
        if body.get("epoch") is not None and int(body["epoch"]) != int(rec["epoch"]):
            return self._err(409, "stale_epoch", f"stale epoch {body['epoch']} (authority at {rec['epoch']})")
        if rec.get("owner_id") != oid:
            return self._err(409, "not_owner", f"{rec['owner_id']} owns the record, not {oid}")
        if rec.get("released_at") is None:
            rec["released_at"] = now
            rec["updated_at"] = now
            rec["reason"] = str(body.get("reason") or "released")
        response = self._ok({"record": rec, "epoch": rec["epoch"]})
        if self.on_release is not None:
            self.on_release()
        return response

    def _takeover(self, body: dict[str, Any], oid: str, role: str, now: float) -> httpx.Response:
        reason = str(body.get("reason") or "").strip()
        if not reason:
            return self._err(400, "reason_required", "a takeover must state why: --reason <why>")
        rec = self.record
        ttl = int(body.get("ttl") or 300)
        if rec is None:
            return self._ok({"record": self._claim(oid, role, ttl, now, f"takeover: {reason}"), "epoch": 1})
        if rec.get("owner_id") == oid and rec.get("released_at") is None:
            rec["heartbeat_at"] = now
            return self._ok({"record": rec, "epoch": rec["epoch"], "renewed": True})
        live = now <= float(rec["heartbeat_at"]) + float(rec["ttl"]) and rec.get("released_at") is None
        from_owner = str(body.get("from_owner") or "")
        if live:
            if body.get("force") is not True:
                return self._err(409, "live_requires_force", f"--force --from {rec['owner_id']} is required")
            if from_owner != rec.get("owner_id"):
                return self._err(409, "wrong_from", f"expected --from {rec['owner_id']}, got {from_owner or 'nothing'}")
        elif from_owner and from_owner != rec.get("owner_id"):
            return self._err(409, "wrong_from", f"the record belongs to {rec['owner_id']}, not {from_owner}")
        audit = {
            "at": now,
            "op": "takeover",
            "from_owner": rec.get("owner_id"),
            "from_epoch": rec["epoch"],
            "to_owner": oid,
            "to_epoch": int(rec["epoch"]) + 1,
            "reason": reason,
            "forced": live,
            "expired": not live,
        }
        rec.update(
            {
                "owner_id": oid,
                "role": role,
                "epoch": int(rec["epoch"]) + 1,
                "ttl": ttl,
                "acquired_at": now,
                "heartbeat_at": now,
                "updated_at": now,
                "hostname": "test-host",
                "reason": reason,
                "released_at": None,
                "takeovers": [*(rec.get("takeovers") or []), audit][-50:],
            }
        )
        return self._ok({"record": rec, "epoch": rec["epoch"]})

    def _mark_moved(self, body: dict[str, Any], oid: str, now: float) -> httpx.Response:
        rec = self.record
        if rec is None:
            return self._err(404, "no_record", "no ownership record")
        wanted = body.get("moved_marker") or {}
        if not wanted.get("to"):
            return self._err(400, "moved_to_required", "moved_marker.to is required")
        reason = str(body.get("reason") or "").strip()
        if not reason:
            return self._err(400, "reason_required", "a move must state why")
        if body.get("epoch") is not None and int(body["epoch"]) != int(rec["epoch"]):
            return self._err(409, "stale_epoch", f"stale epoch {body['epoch']} (authority at {rec['epoch']})")
        if rec.get("owner_id") != oid:
            return self._err(409, "not_owner", f"{rec['owner_id']} owns the record, not {oid}")
        marker = {
            "to": str(wanted["to"]),
            "migration": str(wanted.get("migration") or ""),
            "reason": reason,
            "at": now,
            "from_owner": oid,
        }
        rec["moved_marker"] = marker
        rec["released_at"] = now
        rec["updated_at"] = now
        return self._ok({"record": rec, "epoch": rec["epoch"], "moved_marker": marker})

    def _clear_moved(self, body: dict[str, Any], oid: str, now: float) -> httpx.Response:
        rec = self.record
        if rec is None:
            return self._err(404, "no_record", "no ownership record")
        marker = rec.get("moved_marker")
        if not marker:
            return self._ok({"record": rec, "epoch": rec["epoch"], "already_clear": True})
        if marker.get("from_owner") != oid:
            return self._err(403, "not_moved_owner", f"only {marker.get('from_owner')} may clear the marker")
        rec["moved_marker"] = None
        rec["updated_at"] = now
        return self._ok({"record": rec, "epoch": rec["epoch"], "cleared": True})

    # ── convenience for tests ───────────────────────────────────────────
    def own(self, oid: str, *, epoch: int = 1, role: str = ROLE_BRAIN, ttl: int = 300) -> dict:
        now = time.time()
        self.record = {
            "schema": 1,
            "owner_id": oid,
            "role": role,
            "epoch": epoch,
            "ttl": ttl,
            "acquired_at": now,
            "heartbeat_at": now,
            "updated_at": now,
            "hostname": "other-host",
            "reason": "seeded by the test",
            "takeovers": [],
            "moved_marker": None,
            "released_at": None,
        }
        return self.record


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text(
        f"deck:\n  api_key: {KEY}\nbrain:\n  role: all-in-one\n", encoding="utf-8"
    )
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", "all-in-one")
    monkeypatch.setenv("NAVIG_BRAIN_AUTHORITY_KEY", KEY)
    monkeypatch.delenv("NAVIG_BRAIN_AUTHORITY_URL", raising=False)
    monkeypatch.delenv("NAVIG_BRAIN_AUTHORITY_REQUIRE", raising=False)
    monkeypatch.delenv("NAVIG_BRAIN_AUTO_PROMOTE", raising=False)
    guard.reset_process_lease()
    yield
    guard.reset_process_lease()


@pytest.fixture()
def fake() -> FakeAuthority:
    return FakeAuthority()


@pytest.fixture()
def client(fake: FakeAuthority):
    made = authority.AuthorityClient(
        EDGE,
        KEY,
        timeout=1.0,
        retries=0,
        transport=httpx.MockTransport(fake.handler),
    )
    yield made
    made.close()


@pytest.fixture()
def remote(client):
    """Every guard/CLI call in the test sees this authority as configured."""
    with authority.override(client):
        yield client


# ── the authority IS the record ─────────────────────────────────────────────


def test_a_singleton_acquires_through_the_authority_and_the_local_file_is_a_cache(
    remote, fake: FakeAuthority, tmp_path: Path
) -> None:
    lease = guard.require_ownership(service="gateway")
    assert lease is not None
    assert lease.owner_id == owner_id()
    assert lease.epoch == 1
    assert fake.record is not None and fake.record["owner_id"] == owner_id()
    assert guard.current_epoch() == 1

    # The authoritative answer was mirrored; the local LEASE file was never written,
    # because in remote mode it is not the record.
    cache = json.loads(authority.authority_cache_path().read_text(encoding="utf-8"))
    assert cache["record"]["owner_id"] == owner_id()
    assert cache["record"]["epoch"] == 1
    assert not (tmp_path / "cfg" / "brain" / "lease.json").exists()


def test_the_status_document_names_the_authority(remote, fake: FakeAuthority) -> None:
    guard.require_ownership(service="daemon")
    status = authority.lease_store().status()
    assert status["authority"]["mode"] == "remote"
    assert status["authority"]["url"].endswith("/api/ownership")
    assert status["authority"]["reachable"] is True
    assert status["epoch"] == 1
    assert status["you"]["is_owner"] is True


# ── fail closed: the property the binding requirement names ────────────────


def test_an_unreachable_authority_refuses_the_singleton_with_77(
    remote, fake: FakeAuthority, tmp_path: Path
) -> None:
    fake.unreachable = True
    with pytest.raises(guard.OwnershipRefused) as excinfo:
        guard.require_ownership(service="gateway")
    decision = excinfo.value.decision
    assert decision.allowed is False
    assert decision.exit_code == EXIT_OWNERSHIP_REFUSED == 77
    assert decision.authority == "remote"
    assert "authority" in decision.message.lower()
    assert "refuses to start" in decision.message
    # No self-promotion, no silent local acquire: the local record was not touched.
    assert not (tmp_path / "cfg" / "brain" / "lease.json").exists()
    assert guard.current_epoch() is None


def test_an_unreachable_authority_is_reported_as_cached_status_not_as_truth(
    remote, fake: FakeAuthority
) -> None:
    guard.require_ownership(service="daemon")
    fake.unreachable = True
    status = authority.lease_store().status()
    assert status["authority"]["reachable"] is False
    assert status["authority"]["stale"] is True
    assert status["state"].startswith("cached-")
    assert "CACHED" in status["summary"]
    assert status["can_claim"] is False  # a cached view may never licence a claim
    assert status["can_takeover"] is False


def test_a_partition_then_recovery_rejoins_the_same_epoch(
    remote, fake: FakeAuthority
) -> None:
    first = guard.require_ownership(service="daemon")
    assert first is not None
    guard.reset_process_lease()

    fake.unreachable = True
    with pytest.raises(guard.OwnershipRefused):
        guard.require_ownership(service="gateway")

    # Recovery: the record was never promoted, and this machine re-joins it.
    fake.unreachable = False
    again = guard.require_ownership(service="gateway")
    assert again is not None
    assert again.epoch == first.epoch == 1
    assert again.owner_id == first.owner_id
    assert fake.record is not None and fake.record["epoch"] == 1


def test_authority_required_without_configuration_refuses(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NAVIG_BRAIN_AUTHORITY_REQUIRE", "true")
    with pytest.raises(guard.OwnershipRefused) as excinfo:
        guard.require_ownership(service="daemon")
    decision = excinfo.value.decision
    assert decision.exit_code == 77
    assert decision.authority == "unconfigured"
    assert "authority_required" in decision.message
    assert not (tmp_path / "cfg" / "brain" / "lease.json").exists()


# ── fencing: stale epochs and replays ─────────────────────────────────────


def test_a_stale_epoch_renew_is_rejected_and_fences_the_writers(
    remote, fake: FakeAuthority
) -> None:
    guard.require_ownership(service="gateway")
    epoch = guard.current_epoch()
    assert epoch == 1
    # Another machine takes the Brain over (the authority's own CAS).
    fake.own(MACHINE_B, epoch=2)

    with pytest.raises(StaleEpoch):
        guard.refresh()
    assert guard.write_allowed(who="scheduler") is False
    with pytest.raises(StaleEpoch):
        guard.assert_write_allowed(who="scheduler")


def test_a_replay_of_an_old_epoch_cannot_release_or_renew(remote, fake: FakeAuthority) -> None:
    fake.own(MACHINE_A, epoch=1)
    authority.authority_takeover(
        oid=MACHINE_B, role=ROLE_BRAIN, reason="moved", force=True, from_owner=MACHINE_A
    )
    assert fake.record is not None and fake.record["epoch"] == 2

    with pytest.raises(StaleEpoch):
        authority.authority_renew(oid=MACHINE_A, epoch=1)
    with pytest.raises(StaleEpoch):
        authority.authority_release(oid=MACHINE_A, epoch=1, reason="replay")
    assert fake.record["owner_id"] == MACHINE_B
    assert fake.record["released_at"] is None


# ── takeover: explicit, audited, epoch +1 ─────────────────────────────────


def test_a_takeover_of_a_live_holder_needs_force_and_the_expected_owner(
    remote, fake: FakeAuthority
) -> None:
    fake.own(MACHINE_A, epoch=1)

    with pytest.raises(OwnershipConflict):
        authority.authority_takeover(oid=MACHINE_B, role=ROLE_BRAIN, reason="moving")

    with pytest.raises(OwnershipConflict):
        authority.authority_takeover(
            oid=MACHINE_B, role=ROLE_BRAIN, reason="moving", force=True, from_owner="brn-wrong"
        )

    taken = authority.authority_takeover(
        oid=MACHINE_B, role=ROLE_BRAIN, reason="moving", force=True, from_owner=MACHINE_A
    )
    assert taken.owner_id == MACHINE_B
    assert taken.epoch == 2
    assert taken.takeovers[-1]["from_owner"] == MACHINE_A
    assert taken.takeovers[-1]["forced"] is True
    assert taken.takeovers[-1]["reason"] == "moving"


def test_an_expired_record_is_not_promoted_without_the_explicit_flag(
    remote, fake: FakeAuthority, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.own(MACHINE_A, epoch=1, ttl=30)
    fake.record["heartbeat_at"] = time.time() - 600  # type: ignore[index]

    with pytest.raises(OwnershipConflict) as excinfo:
        authority.authority_acquire(oid=MACHINE_B, role=ROLE_BRAIN)
    assert "auto_promote" in str(excinfo.value)
    assert fake.record["owner_id"] == MACHINE_A

    monkeypatch.setenv("NAVIG_BRAIN_AUTO_PROMOTE", "true")
    promoted = authority.authority_acquire(oid=MACHINE_B, role=ROLE_BRAIN)
    assert promoted.owner_id == MACHINE_B
    assert promoted.epoch == 2
    assert "auto-promoted" in promoted.takeovers[-1]["reason"]


# ── concurrency: one winner, decided by the authority ─────────────────────


def test_concurrent_promotions_produce_exactly_one_winner(
    remote, fake: FakeAuthority, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.own(MACHINE_A, epoch=1, ttl=30)
    fake.record["heartbeat_at"] = time.time() - 600  # type: ignore[index]
    monkeypatch.setenv("NAVIG_BRAIN_AUTO_PROMOTE", "true")

    winners: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def promote(oid: str) -> None:
        barrier.wait()
        try:
            lease = authority.authority_acquire(oid=oid, role=ROLE_BRAIN)
            winners.append(lease.owner_id)
        except Exception as exc:  # noqa: BLE001 — a loser must fail loudly
            errors.append(exc)

    threads = [
        threading.Thread(target=promote, args=(MACHINE_B,)),
        threading.Thread(target=promote, args=("brn-cccccccccccccccc",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a promotion thread hung on the authority"

    assert fake.record is not None
    assert fake.record["epoch"] == 2
    assert len(set(winners)) == 1, winners
    assert all(isinstance(e, OwnershipConflict) for e in errors)


# ── authorization: the key is the identity, the role still matters ────────


def test_a_wrong_key_cannot_even_read(fake: FakeAuthority) -> None:
    made = authority.AuthorityClient(
        EDGE, "not-the-key", timeout=1.0, retries=0, transport=httpx.MockTransport(fake.handler)
    )
    try:
        with pytest.raises(authority.AuthorityRefused) as excinfo:
            made.status()
    finally:
        made.close()
    assert "401" in str(excinfo.value)


def test_a_client_role_cannot_mutate_ownership_even_with_the_key(
    remote, fake: FakeAuthority
) -> None:
    with pytest.raises(authority.AuthorityRefused) as excinfo:
        authority.authority_acquire(oid=MACHINE_A, role=ROLE_CLIENT)
    assert "client" in str(excinfo.value)
    assert fake.record is None  # nothing was written

    # …but it can read the status (that is the client's view).
    fake.own(MACHINE_A, epoch=3)
    payload = authority.authority_status(oid=MACHINE_B, role=ROLE_CLIENT)
    assert payload["record"]["owner_id"] == MACHINE_A
    assert payload["status"]["is_owner"] is False


def test_a_client_install_never_starts_a_singleton(monkeypatch, remote) -> None:
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", "client")
    with pytest.raises(guard.OwnershipRefused) as excinfo:
        guard.require_ownership(service="gateway")
    assert excinfo.value.decision.exit_code == 77
    assert "client" in excinfo.value.decision.message


# ── Telegram: only the authority's current epoch sets the webhook ─────────


def test_only_the_authoritys_current_owner_may_set_the_webhook(
    remote, fake: FakeAuthority
) -> None:
    fake.own(MACHINE_A, epoch=3)

    with pytest.raises(tg.TelegramModeRefused) as wrong:
        tg.authorize_delivery_mode(
            tg.MODE_WEBHOOK, role=ROLE_BRAIN, owner=MACHINE_B, epoch=3
        )
    assert "AUTHORITY" in str(wrong.value)
    assert MACHINE_A in str(wrong.value)

    with pytest.raises(tg.TelegramModeRefused) as stale:
        tg.authorize_delivery_mode(
            tg.MODE_WEBHOOK, role=ROLE_BRAIN, owner=MACHINE_A, epoch=2
        )
    assert "Stale epoch 2" in str(stale.value)

    # The owner at the current epoch is allowed to (re)register its webhook.
    tg.authorize_delivery_mode(tg.MODE_WEBHOOK, role=ROLE_BRAIN, owner=MACHINE_A, epoch=3)


def test_a_client_install_is_refused_delivery_by_the_authority(remote, fake: FakeAuthority) -> None:
    fake.own(MACHINE_A, epoch=1)
    with pytest.raises(tg.TelegramModeRefused) as excinfo:
        tg.authorize_delivery_mode(tg.MODE_WEBHOOK, role=ROLE_CLIENT, owner=MACHINE_B)
    assert "client" in str(excinfo.value)


class _TelegramAPI:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, method: str, params: dict) -> Any:
        self.calls.append(method)
        if method == "getWebhookInfo":
            return {"url": "", "pending_update_count": 0}
        if method == "getUpdates":
            return []
        return True


def test_a_transition_refuses_before_touching_telegram_when_the_authority_is_unreachable(
    remote, fake: FakeAuthority
) -> None:
    import asyncio

    fake.own(MACHINE_A, epoch=1)
    fake.unreachable = True
    api = _TelegramAPI()
    with pytest.raises(tg.TelegramModeRefused) as excinfo:
        asyncio.run(
            tg.transition(
                tg.MODE_POLLING, api=api, owner=MACHINE_A, role=ROLE_BRAIN, epoch=1
            )
        )
    assert "AUTHORITY" in str(excinfo.value)
    assert api.calls == [], api.calls  # deleteWebhook never ran


def test_a_stale_epoch_process_cannot_switch_telegram(remote, fake: FakeAuthority) -> None:
    import asyncio

    fake.own(MACHINE_A, epoch=4)
    api = _TelegramAPI()
    with pytest.raises(tg.TelegramModeRefused):
        asyncio.run(
            tg.transition(
                tg.MODE_POLLING, api=api, owner=MACHINE_A, role=ROLE_BRAIN, epoch=3
            )
        )
    assert api.calls == []

    result = asyncio.run(
        tg.transition(tg.MODE_POLLING, api=api, owner=MACHINE_A, role=ROLE_BRAIN, epoch=4)
    )
    assert result.ok is True
    assert api.calls[0] == "deleteWebhook"  # the order the incident requires


# ── migration: the authority's epoch is the evidence ──────────────────────


class _FakeRemote:
    def __init__(self, tmp_path: Path) -> None:
        self._tmp = tmp_path
        self.calls: list[str] = []

    def discover(self) -> dict[str, Any]:
        self.calls.append("discover")
        return {"host": "outpost", "reachable": True, "platform": "linux", "has_config": True}

    def inspect(self) -> dict[str, Any]:
        self.calls.append("inspect")
        return {
            "role": "brain",
            "owner_id": TARGET,
            "you": {"id": TARGET, "is_owner": True},
            "state": "held",
            "epoch": 2,
            "existing_lease": {"state": "free", "owner_id": ""},
            "telegram_mode": "off",
            "scheduler_jobs": 0,
            "deck_key_fingerprint": "",
        }

    def backup(self, destination: Path) -> dict[str, Any]:
        self.calls.append("backup")
        destination.mkdir(parents=True, exist_ok=True)
        bundle = destination / "brain.tar.gz.enc"
        bundle.write_bytes(b"x" * 64)
        import hashlib

        return {
            "path": str(bundle),
            "bytes": 64,
            "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "items": 1,
        }

    def transfer(self, bundle: Path) -> dict[str, Any]:
        self.calls.append("transfer")
        import hashlib

        return {"transferred": True, "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest()}

    def validate(self, receipt) -> dict[str, Any]:
        self.calls.append("validate")
        return {"ok": True, "reconciled": ["hosts"], "unreconciled": [], "item_counts": {}}

    def cut_over(self, receipt) -> dict[str, Any]:
        self.calls.append("cutover")
        return {"started_target": True}

    def verify(self) -> dict[str, dict[str, Any]]:
        self.calls.append("verify")
        return {
            "telegram": {"verified": True, "applicable": True, "detail": "polling"},
            "scheduler": {"verified": True, "applicable": True, "detail": "3 jobs"},
            "mail_watcher": {"verified": False, "applicable": False, "detail": "off"},
            "api": {"verified": True, "applicable": True, "detail": "port 8789"},
        }

    def rollback(self, receipt) -> dict[str, Any]:
        self.calls.append("rollback")
        return {"target_stopped": True}


def test_cutover_writes_the_moved_marker_into_the_authority(
    remote, fake: FakeAuthority, tmp_path: Path
) -> None:
    fake.own(owner_id(), epoch=1)
    runner = mig.MigrationRunner(
        _FakeRemote(tmp_path),
        store=mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations"),
    )
    # Completion still refuses (the target has not claimed it), but the move was
    # written into the SHARED record at cutover — that is what this test pins.
    with pytest.raises(mig.MigrationBlocked):
        runner.run(target_host="outpost")
    receipt = runner.store.latest()
    assert receipt is not None
    assert receipt.stage_state("cutover") == "done"
    assert fake.record is not None
    marker = fake.record["moved_marker"]
    assert marker is not None
    assert marker["to"] == "outpost"
    assert marker["migration"] == receipt.id
    assert marker["from_owner"] == owner_id()
    assert receipt.authority["moved_marker"]["to"] == "outpost"
    assert receipt.rollback["authority_moved"] is True


def test_completion_is_refused_until_the_authority_names_the_target(
    remote, fake: FakeAuthority, tmp_path: Path
) -> None:
    fake.own(owner_id(), epoch=1)
    runner = mig.MigrationRunner(
        _FakeRemote(tmp_path),
        store=mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations"),
    )
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        runner.run(target_host="outpost")
    assert "authority" in str(excinfo.value).lower()
    receipt = runner.store.latest()
    assert receipt is not None
    assert receipt.state == "blocked"
    assert receipt.stage_state("complete") == "failed"


def test_completion_passes_when_the_authority_records_the_target_as_owner(
    remote, fake: FakeAuthority, tmp_path: Path
) -> None:
    fake.own(owner_id(), epoch=1)

    def target_arrives() -> None:
        # The real sequence: the source releases, then the target claims, and the
        # authority moves the epoch and names the target.
        fake.own(TARGET, epoch=2)

    fake.on_release = target_arrives
    runner = mig.MigrationRunner(
        _FakeRemote(tmp_path),
        store=mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations"),
    )
    receipt = runner.run(target_host="outpost")
    assert receipt.state == "complete", receipt.blockers
    assert receipt.authority["verified"] is True
    assert receipt.authority["owner_id"] == TARGET
    assert receipt.authority["epoch"] == 2
    evidence = (receipt.stage("complete") or {}).get("evidence") or {}
    assert evidence["authority"]["owner_id"] == TARGET


def test_a_migration_refuses_to_start_when_the_authority_says_another_owner_holds_it(
    remote, fake: FakeAuthority, tmp_path: Path
) -> None:
    fake.own("brn-thirdmachine00", epoch=7)
    runner = mig.MigrationRunner(
        _FakeRemote(tmp_path),
        store=mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations"),
    )
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        runner.run(target_host="outpost")
    assert "AUTHORITY" in str(excinfo.value)
    assert "thirdmachine00" in str(excinfo.value)


def test_a_migration_refuses_when_the_authority_is_unreachable(
    remote, fake: FakeAuthority, tmp_path: Path
) -> None:
    fake.own(owner_id(), epoch=1)
    fake.unreachable = True
    runner = mig.MigrationRunner(
        _FakeRemote(tmp_path),
        store=mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations"),
    )
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        runner.run(target_host="outpost")
    assert "could not be consulted" in str(excinfo.value)
    receipt = runner.store.latest()
    assert receipt is not None
    assert receipt.stage_state("inspect") == "failed"


# ── the CLI reads the authority ───────────────────────────────────────────


def test_brain_status_and_claim_go_through_the_authority(remote, fake: FakeAuthority) -> None:
    from typer.testing import CliRunner

    from navig.commands.brain import brain_app

    runner = CliRunner()
    claimed = runner.invoke(brain_app, ["claim", "--reason", "from the CLI", "--json"])
    assert claimed.exit_code == 0, claimed.output
    payload = json.loads(claimed.stdout)
    assert payload["ok"] is True
    assert payload["lease"]["epoch"] == 1
    assert fake.record is not None and fake.record["owner_id"] == owner_id()

    status = json.loads(runner.invoke(brain_app, ["status", "--json"]).stdout)
    assert status["brain"]["authority"]["mode"] == "remote"
    assert status["brain"]["epoch"] == 1


# ── backward compatibility: no authority, local mode ─────────────────────


def test_without_an_authority_the_local_record_is_used_and_says_so(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("NAVIG_BRAIN_AUTHORITY_KEY", raising=False)
    assert authority.client_or_none() is None
    lease = guard.require_ownership(service="gateway")
    assert lease is not None and lease.epoch == 1
    assert (tmp_path / "cfg" / "brain" / "lease.json").exists()
    status = LeaseStore().status()
    assert status["authority"]["mode"] == "local"
    assert status["authority"]["reachable"] is None


def test_a_corrupt_cache_is_not_an_authority(
    remote, fake: FakeAuthority, tmp_path: Path
) -> None:
    cache = tmp_path / "cfg" / "brain" / "authority-cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("not json", encoding="utf-8")
    guard.require_ownership(service="daemon")
    # A fresh authoritative answer replaced the garbage; status is not confused.
    status = authority.lease_store().status()
    assert status["authority"]["reachable"] is True
    assert status["state"] == "held"


def test_the_authority_url_falls_back_to_the_lighthouse_edge(
    monkeypatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "cfg"
    (cfg / "config.yaml").write_text(
        f"deck:\n  api_key: {KEY}\ncloud:\n  lighthouse_url: https://edge.example.test/\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("NAVIG_BRAIN_AUTHORITY_KEY", raising=False)
    settings = brain_settings()
    assert settings["authority_url"] == "https://edge.example.test"
    assert authority.base_url() == "https://edge.example.test"
    assert authority.client_or_none() is not None


def test_the_shipped_worker_bundle_actually_routes_the_authority_path() -> None:
    """The client's route must exist in the artifact users DEPLOY, not just in src.

    `core/navig/cloud/lighthouse_worker/worker.js` is the committed bundle the pure
    Python deployer uploads. If the route string (or the DO class/binding) drifts,
    every install would answer "authority unreachable" and refuse to start — a
    fail-closed outage that no unit test of the Python client would notice.
    """
    from navig.cloud import lighthouse_deploy

    bundle = lighthouse_deploy.bundle_path()
    assert bundle.is_file()
    text = bundle.read_text(encoding="utf-8", errors="replace")
    assert authority.AUTHORITY_PATH in text
    assert "OwnershipRecord" in text
    assert "OWNERSHIP" in text
