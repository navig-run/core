"""
MeshDiscovery — UDP multicast peer announcement for LAN-local Flux mesh.

Self-asked questions answered here:
  Q: Why port 5354 instead of 5353?
  A: 5353 is reserved by mDNS/Bonjour on most OSes. 5354 is free on all platforms.

  Q: How does Windows handle multicast sockets?
  A: Identically to Linux for the socket options used here (IP_ADD_MEMBERSHIP,
     SO_REUSEADDR). Tested on Windows 10+. No admin rights required.

  Q: What if a HELLO packet is lost?
  A: HEARTBEAT every 30s provides eventual consistency. Peers gossip back on
     receipt of any HELLO, so discovery converges within ~1 heartbeat cycle.

  Q: Could a malicious LAN device inject fake peer records?
  A: In Phase 1 (LAN scope), we accept all packets from the local subnet.
     Phase 2 adds BLAKE2b HMAC with a per-mesh shared secret.

Multicast group: 224.0.0.251 (same as mDNS, different port — standard practice)
Port: 5354
Packet: JSON ≤ 512 bytes (single UDP datagram, guaranteed delivery within LAN MTU)
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import time

from navig.debug_logger import get_debug_logger
from navig.mesh.auth import attach_hmac, verify_payload
from navig.mesh.registry import NodeRecord, NodeRegistry

logger = get_debug_logger()

MCAST_GROUP = "224.0.0.251"
MCAST_PORT = 5354
HEARTBEAT_INTERVAL = 15  # seconds — was 30; two misses = degraded at 45 s threshold
PROBE_INTERVAL = 20  # seconds between active HTTP /health pings per peer
PROBE_TIMEOUT_S = 3.0  # seconds before a probe is counted as failed
MAX_PACKET_SIZE = 512  # bytes — single datagram fits all fields

# Election message type constants — imported by navig.mesh.election
ELECT_PROPOSE = "elect_propose"  # candidate announces itself
ELECT_PROMOTE = "elect_promote"  # winner claims the leader role
ELECT_YIELD = "elect_yield"  # current leader gracefully steps down
ELECT_ACK = "elect_ack"  # peer acknowledges yield / takeover offer


def _build_packet(
    registry: NodeRegistry,
    ptype: str,
    seq: int = 0,
    secret: bytes | None = None,
    extra: dict | None = None,
) -> bytes:
    """Serialize a discovery packet from the local self record.

    When *secret* is provided the packet is BLAKE2b-signed: an ``hmac`` field
    carrying a 64-char hex MAC is added before encoding.
    """
    rec = registry.self_record
    payload = {
        "v": 1,
        "type": ptype,  # "hello" | "heartbeat" | "goodbye"
        "seq": seq,  # monotonic per-sender counter for loss detection
        "node_id": rec.node_id,
        "hostname": rec.hostname,
        "os": rec.os,
        "gateway_url": rec.gateway_url,
        "capabilities": rec.capabilities,
        "formation": rec.formation,
        "load": rec.load,
        "version": rec.version,
        "role": getattr(rec, "role", "standby"),
        "epoch": getattr(rec, "epoch", 0),
        "ts": int(time.time()),
    }
    if extra:
        payload.update(extra)
    if secret:
        payload = attach_hmac(payload, secret)
    data = json.dumps(payload, separators=(",", ":")).encode()
    if len(data) > MAX_PACKET_SIZE:
        logger.warning(
            "[mesh.discovery] Packet too large (%d bytes) — truncating capabilities",
            len(data),
        )
        payload["capabilities"] = payload["capabilities"][:4]
        if secret:
            payload = attach_hmac(payload, secret)
        data = json.dumps(payload, separators=(",", ":")).encode()
    return data


def _parse_packet(
    data: bytes,
    secret: bytes | None = None,
) -> NodeRecord | None:
    """Parse incoming UDP packet into a NodeRecord. Returns None on any error.

    When *secret* is provided the packet **must** carry a valid BLAKE2b HMAC;
    packets without one or with a wrong MAC are rejected (return ``None``).
    When *secret* is ``None`` (Phase 1 / no-auth mode) all packets are
    accepted regardless of whether they carry an ``hmac`` field.
    """
    try:
        d = json.loads(data.decode())
        if d.get("v") != 1:
            return None
        if secret is not None:
            if not verify_payload(d, secret):
                logger.debug("[mesh.discovery] Dropped packet — bad or missing HMAC")
                return None
        return NodeRecord(
            node_id=d["node_id"],
            hostname=d["hostname"],
            os=d["os"],
            gateway_url=d["gateway_url"],
            capabilities=d.get("capabilities", []),
            formation=d.get("formation", ""),
            load=float(d.get("load", 0.0)),
            version=d.get("version", "unknown"),
            role=d.get("role", "standby"),
            epoch=d.get("epoch", 0),
            last_seen=time.time(),
            is_self=False,
        )
    except Exception as e:
        logger.debug("[mesh.discovery] Bad packet: %s", e)
        return None


def _create_sender_socket() -> socket.socket:
    """UDP socket for sending multicast packets."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.setsockopt(
        socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1
    )  # receive own packets in dev
    return sock


def _create_receiver_socket() -> socket.socket:
    """UDP socket for receiving multicast packets."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # Linux only
    except AttributeError:
        pass  # attribute absent; skip
    # Binding to INADDR_ANY (0.0.0.0) is required for UDP multicast reception;
    # restricting to a single interface would prevent receiving multicast packets.
    sock.bind(("0.0.0.0", MCAST_PORT))  # noqa: S104
    mreq = struct.pack("4sL", socket.inet_aton(MCAST_GROUP), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.setblocking(False)
    return sock


class MeshDiscovery:
    """
    Async UDP multicast broadcaster + listener.

    Lifecycle:
        await discovery.start()   # called once during gateway start
        await discovery.stop()    # called on gateway shutdown

    Resilience model:
      • UDP heartbeats every 15 s ensure convergence within two missed beats (45 s).
      • An independent active-probe loop HTTP-GETs /health on every known peer
        every 20 s to detect failures before the heartbeat timer expires.
      • Per-peer consecutive_failures counter feeds the circuit-breaker in
        NodeRecord; the router consults composite_score which penalises
        degraded / circuit-open peers without fully excluding them.
      • Sequence numbers on multicast packets allow the receiver to detect
        packet loss and log it without blocking normal operation.
    """

    def __init__(self, registry: NodeRegistry, secret: bytes | None = None):
        """Initialise MeshDiscovery.

        Args:
            registry: The local NodeRegistry that tracks self + peers.
            secret:   Optional BLAKE2b shared secret.  When provided all
                      outgoing packets are HMAC-signed and incoming packets
                      without a valid MAC are silently dropped.  Pass
                      ``None`` (default) to run in unauthenticated Phase 1
                      mode.  Use :func:`navig.mesh.auth.load_secret` to
                      resolve the secret from config / env before passing it.
        """
        self._registry = registry
        self._secret: bytes | None = secret
        self._sender: socket.socket | None = None
        self._receiver: socket.socket | None = None
        self._listen_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._probe_task: asyncio.Task | None = None
        self._running = False
        self._seq: int = 0  # monotonic per-node packet sequence counter
        self._peer_seqs: dict = {}  # node_id -> last seen seq (loss detection)
        self._election_callback = None
        if self._secret:
            logger.info("[mesh.discovery] BLAKE2b HMAC authentication enabled")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            self._sender = _create_sender_socket()
            self._receiver = _create_receiver_socket()
        except Exception as e:
            logger.warning("[mesh.discovery] Socket init failed — mesh disabled: %s", e)
            self._running = False
            return

        logger.info("[mesh.discovery] Starting on %s:%s", MCAST_GROUP, MCAST_PORT)
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._probe_task = asyncio.create_task(self._probe_loop())

        # Announce self immediately
        await self._send("hello")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await self._send("goodbye")

        for task in (self._listen_task, self._heartbeat_task, self._probe_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass  # task cancelled; expected during shutdown

        for sock in (self._sender, self._receiver):
            if sock:
                try:
                    sock.close()
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

        logger.info("[mesh.discovery] Stopped")

    # ───────────────────────── Send ────────────────────────────────────

    async def _send(self, ptype: str) -> None:
        if not self._sender:
            return
        try:
            self._seq += 1
            data = _build_packet(
                self._registry, ptype, seq=self._seq, secret=self._secret
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self._sender.sendto(data, (MCAST_GROUP, MCAST_PORT)),  # type: ignore[union-attr]
            )
            logger.debug(
                "[mesh.discovery] Sent %s seq=%d (%d bytes)",
                ptype,
                self._seq,
                len(data),
            )
        except Exception as e:
            logger.warning("[mesh.discovery] Send error: %s", e)

    def set_election_callback(self, callback) -> None:
        """Register the callback for election-related packets."""
        self._election_callback = callback

    async def send_election_packet(self, ptype: str, extra: dict | None = None) -> None:
        """Send a multicast election packet with optional extra payload."""
        if not self._sender:
            return
        try:
            self._seq += 1
            data = _build_packet(
                self._registry, ptype, seq=self._seq, secret=self._secret, extra=extra
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self._sender.sendto(data, (MCAST_GROUP, MCAST_PORT)),  # type: ignore[union-attr]
            )
            logger.debug(
                "[mesh.discovery] Sent %s seq=%d (%d bytes)",
                ptype,
                self._seq,
                len(data),
            )
        except Exception as e:
            logger.warning("[mesh.discovery] Election send error: %s", e)

    async def _heartbeat_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self._send("heartbeat")
        except asyncio.CancelledError:
            pass  # task cancelled; expected during shutdown

    async def _probe_loop(self) -> None:
        """
        Active health probe  —  HTTP GET each known peer’s /health endpoint.

        Benefits over passive heartbeat:
          • Detects failures within PROBE_INTERVAL (20 s) instead of waiting
            for three missed heartbeats (45 s).
          • Measures RTT for use in composite_score routing weights.
          • Directly exercises the HTTP stack, so a node whose multicast stack
            died but whose gateway is still responding will stay ‘online’.
          • Feeds the circuit-breaker: 3 consecutive failures → circuit open.
        """
        # Stagger the first probe so it doesn’t race the initial HELLO
        await asyncio.sleep(PROBE_INTERVAL // 2)
        try:
            while self._running:
                peers = self._registry.get_peers()
                for peer in peers:
                    if not self._running:
                        break
                    await self._probe_peer(peer)
                await asyncio.sleep(PROBE_INTERVAL)
        except asyncio.CancelledError:
            pass  # task cancelled; expected during shutdown

    async def _probe_peer(self, peer) -> None:
        """HTTP GET /health on one peer; update registry with result."""
        url = f"{peer.gateway_url.rstrip('/')}/health"
        t0 = time.monotonic()
        try:
            import aiohttp

            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_S),
                ) as resp,
            ):
                rtt_ms = (time.monotonic() - t0) * 1000
                if resp.status == 200:
                    self._registry.record_probe_success(peer.node_id, rtt_ms)
                    logger.debug("[mesh.probe] %s OK rtt=%.0fms", peer.node_id, rtt_ms)
                else:
                    self._registry.record_probe_failure(peer.node_id)
                    logger.debug("[mesh.probe] %s HTTP %s", peer.node_id, resp.status)
        except ImportError:
            pass  # aiohttp not installed — skip probing silently
        except Exception as e:
            self._registry.record_probe_failure(peer.node_id)
            logger.debug("[mesh.probe] %s failed: %s", peer.node_id, e)

    # ──────────────────────────── Receive ────────────────────────────

    async def _listen_loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self._running:
                try:
                    data, addr = await loop.run_in_executor(None, self._recv_once)
                    await self._handle_packet(data, addr[0])
                except TimeoutError:
                    continue
                except Exception as e:
                    if self._running:
                        logger.debug("[mesh.discovery] Recv error: %s", e)
        except asyncio.CancelledError:
            pass  # task cancelled; expected during shutdown

    def _recv_once(self):
        """Blocking recv with 1s timeout — called in executor."""
        self._receiver.settimeout(1.0)
        return self._receiver.recvfrom(MAX_PACKET_SIZE)

    async def _handle_packet(self, data: bytes, src_ip: str) -> None:
        record = _parse_packet(data, self._secret)
        if record is None:
            return

        # Ignore our own packets (identified by node_id)
        if record.node_id == self._registry.self_record.node_id:
            return

        raw = json.loads(data)
        ptype = raw.get("type", "")
        incoming_seq = raw.get("seq", 0)

        # Sequence-number loss detection (informational only in Phase 1)
        if incoming_seq and ptype != "goodbye":
            prev_seq = self._peer_seqs.get(record.node_id, 0)
            if prev_seq and incoming_seq > prev_seq + 1:
                lost = incoming_seq - prev_seq - 1
                logger.debug(
                    "[mesh.discovery] Packet loss detected from %s: "
                    "%d packet(s) missing (last=%d current=%d)",
                    record.node_id,
                    lost,
                    prev_seq,
                    incoming_seq,
                )
            self._peer_seqs[record.node_id] = incoming_seq

        if ptype == "goodbye":
            self._registry.remove_peer(record.node_id)
            self._peer_seqs.pop(record.node_id, None)
            logger.info("[mesh.discovery] Peer left: %s", record.node_id)
            return

        # hello or heartbeat → upsert; reset circuit breaker on any live contact
        existing = next(
            (r for r in self._registry.get_peers() if r.node_id == record.node_id),
            None,
        )
        if existing:
            record.consecutive_failures = 0  # live packet → clear circuit
        self._registry.upsert_peer(record)
        logger.info(
            "[mesh.discovery] Peer %s: %s @ %s (%s)",
            ptype,
            record.node_id,
            record.gateway_url,
            record.health,
        )

        # Reply with our own HELLO so the new peer knows about us immediately
        if ptype == "hello":
            await self._send("hello")

        # Route election packets to the election manager
        if ptype in (ELECT_PROPOSE, ELECT_PROMOTE, ELECT_YIELD, ELECT_ACK):
            if self._election_callback:
                self._election_callback(ptype, record, raw)
