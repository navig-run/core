"""Keep a Windows asyncio server listening after one client aborts before it is accepted.

The bug this closes (Python 3.12/3.13, ``asyncio.proactor_events``): when an overlapped
``accept`` completes with an error, ``BaseProactorEventLoop._start_serving`` reports
"Accept failed on a socket" and then calls ``sock.close()`` on the **listening** socket.
The commonest such error is ``WinError 64`` (ERROR_NETNAME_DELETED): a client connected and
reset before the server picked it up — a browser tab closing, a probe timing out. One
impatient client therefore stops the server from ever accepting again, while the process
keeps running, logging and answering every health signal that does not open a socket.

Measured on the operator's machine, 2026-09-30: the NAVIG gateway's HTTP listener died this
way twice in forty minutes (02:57 and again after the 03:05 restart). Every local caller —
the CLI, NAVIG OS's brain calls, the Deck — got "NAVIG is not running", while Telegram
(which arrives over the outbound Lighthouse uplink, not this socket) kept working.

The fix treats the per-connection failures as what they are — that one client left — and
re-arms ``accept`` on the same listening socket. Anything else keeps the standard library's
exact behaviour. It is applied once per process, only on Windows, and only when the method
has the signature it was written against; otherwise it does nothing rather than guess.
"""

from __future__ import annotations

import inspect
import logging
import sys

logger = logging.getLogger(__name__)

# The client went away between its connect and our accept. Nothing is wrong with the
# listening socket in any of these.
#   64    ERROR_NETNAME_DELETED      the specified network name is no longer available
#   1236  ERROR_CONNECTION_ABORTED   the network connection was aborted by the local system
#   10053 WSAECONNABORTED            software caused connection abort
#   10054 WSAECONNRESET              connection reset by peer
TRANSIENT_ACCEPT_WINERRORS = frozenset({64, 1236, 10053, 10054})

# Back off when failures repeat, so a pathological stream of them cannot spin the loop.
_MAX_BACKOFF_S = 1.0

_EXPECTED_PARAMS = (
    "self",
    "protocol_factory",
    "sock",
    "sslcontext",
    "server",
    "backlog",
    "ssl_handshake_timeout",
    "ssl_shutdown_timeout",
)

_installed = False
# The standard library's method, kept so tests (and a future uninstall) can reach it.
stdlib_start_serving = None


def is_transient_accept_error(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in TRANSIENT_ACCEPT_WINERRORS


def install() -> bool:
    """Patch the proactor's accept loop (Windows only, once). True when it is in place."""
    global _installed
    if _installed:
        return True
    if sys.platform != "win32":
        return False
    try:
        from asyncio import exceptions, proactor_events, trsock
    except ImportError:  # pragma: no cover — every supported Python has these
        return False
    base = proactor_events.BaseProactorEventLoop
    params = tuple(inspect.signature(base._start_serving).parameters)
    if params != _EXPECTED_PARAMS:
        logger.warning(
            "accept guard not installed: this Python's proactor _start_serving has %s", params
        )
        return False

    def _start_serving(
        self,
        protocol_factory,
        sock,
        sslcontext=None,
        server=None,
        backlog=100,
        ssl_handshake_timeout=None,
        ssl_shutdown_timeout=None,
    ):
        streak = [0]

        def loop(f=None):
            try:
                if f is not None:
                    conn, addr = f.result()
                    if self._debug:
                        logger.debug("%r got a new connection from %r: %r", server, addr, conn)
                    protocol = protocol_factory()
                    if sslcontext is not None:
                        self._make_ssl_transport(
                            conn,
                            protocol,
                            sslcontext,
                            server_side=True,
                            extra={"peername": addr},
                            server=server,
                            ssl_handshake_timeout=ssl_handshake_timeout,
                            ssl_shutdown_timeout=ssl_shutdown_timeout,
                        )
                    else:
                        self._make_socket_transport(
                            conn, protocol, extra={"peername": addr}, server=server
                        )
                    streak[0] = 0
                if self.is_closed():
                    return
                f = self._proactor.accept(sock)
            except OSError as exc:
                if sock.fileno() != -1 and is_transient_accept_error(exc) and not self.is_closed():
                    # One client left before we accepted it. Keep listening.
                    streak[0] += 1
                    delay = min(_MAX_BACKOFF_S, 0.005 * (2 ** min(streak[0] - 1, 8)))
                    logger.debug(
                        "accept: a client went away before it was accepted (%s); still listening",
                        exc,
                    )
                    if streak[0] == 1:
                        self.call_soon(loop)
                    else:
                        self.call_later(delay, loop)
                    return
                if sock.fileno() != -1:
                    self.call_exception_handler(
                        {
                            "message": "Accept failed on a socket",
                            "exception": exc,
                            "socket": trsock.TransportSocket(sock),
                        }
                    )
                    sock.close()
                elif self._debug:
                    logger.debug("Accept failed on socket %r", sock, exc_info=True)
            except exceptions.CancelledError:
                sock.close()
            else:
                self._accept_futures[sock.fileno()] = f
                f.add_done_callback(loop)

        self.call_soon(loop)

    global stdlib_start_serving
    stdlib_start_serving = base._start_serving
    base._start_serving = _start_serving
    _installed = True
    return True
