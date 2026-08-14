#!/usr/bin/env python3
"""Fail-closed, token-gated loopback admission for Chromium CDP.

Chromium's remote-debugging socket has no useful application-level bearer
credential.  Vellum therefore keeps the browser's CDP port private and
exposes a short-lived loopback proxy which requires an unlogged bearer token.
The proxy is deliberately narrow: it accepts only read/upgrade GET requests
to CDP discovery and WebSocket paths, and never binds a public address.
"""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import ipaddress
import secrets
import select
import socket
import socketserver
import threading
from urllib.parse import urlsplit


SCHEMA = "vellum.cdp-admission.v1"
MAX_HEADER_BYTES = 16 * 1024
DEFAULT_IDLE_TIMEOUT = 30.0
ALLOWED_DISCOVERY_PATHS = {"/json", "/json/version", "/json/list"}


class CdpAdmissionError(ValueError):
    """Raised when a CDP admission request violates the capture contract."""


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _split_request(raw: bytes) -> tuple[str, str, dict[str, str], bytes]:
    marker = raw.find(b"\r\n\r\n")
    if marker < 0 or marker + 4 > MAX_HEADER_BYTES:
        raise CdpAdmissionError("CDP request headers are missing or oversized")
    head, remainder = raw[:marker].decode("iso-8859-1"), raw[marker + 4:]
    lines = head.split("\r\n")
    if len(lines) < 2:
        raise CdpAdmissionError("malformed CDP request")
    method, target, version = lines[0].split(" ", 2)
    if method != "GET" or version not in {"HTTP/1.1", "HTTP/1.0"}:
        raise CdpAdmissionError("CDP admission accepts only GET requests")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            raise CdpAdmissionError("malformed CDP request header")
        name, value = line.split(":", 1)
        normalized = name.strip().lower()
        if not normalized or normalized in headers:
            raise CdpAdmissionError("duplicate or empty CDP request header")
        headers[normalized] = value.strip()
    return method, target, headers, remainder


def _allowed_target(target: str) -> bool:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return False
    return parsed.path in ALLOWED_DISCOVERY_PATHS or parsed.path.startswith("/devtools/")


@dataclass(frozen=True)
class CdpEndpoint:
    """The public part of a running admission proxy; the token stays separate."""

    schema: str
    host: str
    port: int
    token: str

    def authorization(self) -> str:
        return f"Bearer {self.token}"

    def url(self, path: str = "/json/version") -> str:
        if not _allowed_target(path):
            raise CdpAdmissionError("CDP endpoint path is not allowed")
        return f"http://{self.host}:{self.port}{path}"

    def public_metadata(self) -> dict[str, object]:
        # Do not expose the bearer in logs, receipts, or serialized metadata.
        return {"schema": self.schema, "host": self.host, "port": self.port}


class _ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class CdpAdmission:
    """A short-lived authenticated proxy in front of a private CDP socket."""

    _server: _ThreadingServer | None
    _thread: threading.Thread | None

    def __init__(
        self,
        upstream_port: int,
        *,
        upstream_host: str = "127.0.0.1",
        token: str | None = None,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    ) -> None:
        if not _loopback(upstream_host):
            raise CdpAdmissionError("CDP upstream must be loopback")
        if not 1 <= upstream_port <= 65535:
            raise CdpAdmissionError("CDP upstream port is invalid")
        if idle_timeout <= 0 or idle_timeout > 300:
            raise CdpAdmissionError("CDP idle timeout is outside the bounded range")
        if token is not None and (len(token) < 32 or any(c.isspace() for c in token)):
            raise CdpAdmissionError("CDP token must be a non-whitespace value of at least 32 bytes")
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self._token = token or secrets.token_urlsafe(32)
        self.idle_timeout = idle_timeout
        self._server = None
        self._thread = None

    @property
    def endpoint(self) -> CdpEndpoint:
        if self._server is None:
            raise CdpAdmissionError("CDP admission is not running")
        host, port = self._server.server_address
        return CdpEndpoint(SCHEMA, host, port, self._token)

    def chrome_arguments(self) -> list[str]:
        """Arguments that keep Chromium CDP and network access on loopback."""
        return [
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={self.upstream_port}",
            "--no-proxy-server",
            "--host-resolver-rules=MAP * ~NOTFOUND,EXCLUDE localhost,EXCLUDE 127.0.0.1",
        ]

    def __enter__(self) -> "CdpAdmission":
        if self._server is not None:
            raise CdpAdmissionError("CDP admission cannot be entered twice")
        admission = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                admission._handle(self.request)

        self._server = _ThreadingServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vellum-cdp-admission",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def _handle(self, client: socket.socket) -> None:
        client.settimeout(self.idle_timeout)
        try:
            request = bytearray()
            while b"\r\n\r\n" not in request and len(request) <= MAX_HEADER_BYTES:
                chunk = client.recv(min(4096, MAX_HEADER_BYTES + 1 - len(request)))
                if not chunk:
                    return
                request.extend(chunk)
            _method, target, headers, remainder = _split_request(bytes(request))
            supplied = headers.get("authorization", "")
            expected = f"Bearer {self._token}"
            if not hmac.compare_digest(supplied, expected):
                self._reply(client, 401, b"unauthorized")
                return
            if not _allowed_target(target):
                self._reply(client, 403, b"forbidden CDP path")
                return
            # Forward the original request verbatim; CDP WebSocket upgrades
            # rely on the Upgrade/Connection headers surviving unchanged.
            with socket.create_connection(
                (self.upstream_host, self.upstream_port), timeout=self.idle_timeout
            ) as upstream:
                upstream.settimeout(self.idle_timeout)
                upstream.sendall(bytes(request))
                self._relay(client, upstream)
        except (OSError, CdpAdmissionError):
            # A closed browser or malformed request is a normal fail-closed
            # outcome. Never echo the token or the raw request to stderr.
            return

    @staticmethod
    def _reply(client: socket.socket, status: int, body: bytes) -> None:
        reason = {401: b"Unauthorized", 403: b"Forbidden"}[status]
        client.sendall(
            b"HTTP/1.1 " + str(status).encode() + b" " + reason + b"\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + body
        )

    def _relay(self, left: socket.socket, right: socket.socket) -> None:
        sockets = [left, right]
        while True:
            readable, _writable, _exceptional = select.select(
                sockets, [], sockets, self.idle_timeout
            )
            if not readable:
                return
            for source in readable:
                data = source.recv(64 * 1024)
                if not data:
                    return
                (right if source is left else left).sendall(data)
