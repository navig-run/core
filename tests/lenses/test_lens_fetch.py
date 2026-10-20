"""Lens fetch: the address that was checked is the address that is connected to.

A DNS-rebinding resolver answers a public IP to the safety check and ``127.0.0.1`` to the
connect. The fetch must resolve each hop once and open the socket to that same IP — while
TLS still verifies the certificate for the hostname.
"""

from __future__ import annotations

import datetime as dt
import http.server
import socket
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from navig.lenses import fetch as lens_fetch
from navig.lenses.fetch import LensFetchError, fetch_text

PUBLIC = "93.184.215.14"


def _addrinfo(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))]


def test_each_hop_resolves_once_and_connects_to_the_checked_ip(monkeypatch) -> None:
    answers = iter([PUBLIC, "127.0.0.1"])  # the rebinding resolver: public, then loopback
    resolved: list[str] = []
    connected: list[tuple[str, int]] = []

    def fake_getaddrinfo(host, port, *a, **k):  # noqa: ANN001
        ip = next(answers)
        resolved.append(ip)
        return _addrinfo(ip)

    def fake_connect(address, *a, **k):  # noqa: ANN001
        connected.append(address)
        raise OSError("stop here")

    monkeypatch.setattr(lens_fetch.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(lens_fetch.socket, "create_connection", fake_connect)
    with pytest.raises(LensFetchError, match="stop here"):
        fetch_text("https://rebind.example/lens.user.js")
    assert resolved == [PUBLIC], "a second resolution is the rebinding window"
    assert connected == [(PUBLIC, 443)]


def test_any_private_answer_refuses_before_connecting(monkeypatch) -> None:
    infos = _addrinfo(PUBLIC) + _addrinfo("10.0.0.5")
    connected: list[object] = []
    monkeypatch.setattr(lens_fetch.socket, "getaddrinfo", lambda *a, **k: infos)
    monkeypatch.setattr(lens_fetch.socket, "create_connection", lambda *a, **k: connected.append(a))
    with pytest.raises(LensFetchError, match="non-public"):
        fetch_text("https://mixed.example/x.js")
    assert connected == []


# ── real TLS: the pinned socket still verifies the hostname's certificate ──────────────


def _self_signed(tmp: Path, name: str) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp / "cert.pem", tmp / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture()
def tls_server(tmp_path: Path) -> Iterator[tuple[int, Path, list[str]]]:
    pytest.importorskip("cryptography")
    cert, key = _self_signed(tmp_path, "lens.test")
    hosts: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hosts.append(self.headers.get("Host", ""))
            if self.path == "/old.user.js":
                self.send_response(302)
                self.send_header("Location", "/lens.user.js")
                self.end_headers()
                return
            body = b"// ==UserScript==\n// @name Pinned\n// ==/UserScript==\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # noqa: ANN002
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], cert, hosts
    finally:
        server.shutdown()
        server.server_close()


def _trust(monkeypatch, cert: Path) -> None:
    real = ssl.create_default_context
    monkeypatch.setattr(lens_fetch.ssl, "create_default_context", lambda: real(cafile=str(cert)))
    # The "public" answer for lens.test is the local server; the check itself is covered above.
    monkeypatch.setattr(lens_fetch, "_resolve_public", lambda host, port: "127.0.0.1")


def test_pinned_fetch_follows_a_redirect_and_sends_the_hostname(monkeypatch, tls_server) -> None:
    port, cert, hosts = tls_server
    _trust(monkeypatch, cert)
    text = fetch_text(f"https://lens.test:{port}/old.user.js")
    assert "@name Pinned" in text
    assert hosts == [f"lens.test:{port}", f"lens.test:{port}"]


def test_pinned_fetch_still_verifies_the_certificate_name(monkeypatch, tls_server) -> None:
    port, cert, _hosts = tls_server
    _trust(monkeypatch, cert)
    with pytest.raises(LensFetchError, match="(?i)certificate|hostname"):
        fetch_text(f"https://other.test:{port}/lens.user.js")
