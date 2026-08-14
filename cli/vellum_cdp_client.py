#!/usr/bin/env python3
"""Bounded, dependency-free CDP client for the Vellum capture lane."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import struct
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, urlopen

from vellum_cdp import CdpEndpoint


MAX_DISCOVERY_BYTES = 64 * 1024
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_COMPUTED_STYLES = 128
DEFAULT_TIMEOUT = 15.0
ALLOWED_COMMANDS = {
    "Page.enable",
    "Page.navigate",
    "DOMSnapshot.captureSnapshot",
}


class CdpClientError(RuntimeError):
    """Raised when a bounded CDP operation cannot complete safely."""


def _loopback_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.fragment:
        raise CdpClientError("CDP navigation requires a credential-free HTTP(S) URL")
    if parsed.hostname is None:
        raise CdpClientError("CDP navigation URL has no host")
    try:
        import ipaddress
        if not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise CdpClientError("CDP navigation is loopback-only")
    except ValueError as error:
        raise CdpClientError("CDP navigation requires a numeric loopback host") from error


def _bounded_json(raw: bytes, *, label: str, limit: int) -> dict[str, Any]:
    if len(raw) > limit:
        raise CdpClientError(f"CDP {label} exceeds the bounded response size")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CdpClientError(f"CDP {label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise CdpClientError(f"CDP {label} must be an object")
    return value


class CdpClient:
    """Authenticated loopback CDP session with a narrow command surface."""

    def __init__(self, endpoint: CdpEndpoint, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        if timeout <= 0 or timeout > 120:
            raise CdpClientError("CDP timeout is outside the bounded range")
        self.endpoint = endpoint
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._next_id = 0

    def __enter__(self) -> "CdpClient":
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def discover(self) -> dict[str, Any]:
        request = Request(
            self.endpoint.url("/json/version"),
            headers={"Authorization": self.endpoint.authorization()},
        )
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return _bounded_json(response.read(MAX_DISCOVERY_BYTES + 1), label="discovery", limit=MAX_DISCOVERY_BYTES)
        except CdpClientError:
            raise
        except OSError as error:
            raise CdpClientError(f"CDP discovery failed: {error}") from error

    def connect(self) -> None:
        if self._socket is not None:
            raise CdpClientError("CDP client is already connected")
        discovery = self.discover()
        raw_ws_url = discovery.get("webSocketDebuggerUrl")
        if not isinstance(raw_ws_url, str):
            raise CdpClientError("CDP discovery omitted webSocketDebuggerUrl")
        parsed = urlsplit(raw_ws_url)
        if parsed.scheme != "ws" or not parsed.path.startswith("/devtools/"):
            raise CdpClientError("CDP websocket URL is not a browser devtools path")
        if parsed.query or parsed.fragment:
            raise CdpClientError("CDP websocket URL must not contain query or fragment data")
        sock = socket.create_connection((self.endpoint.host, self.endpoint.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {parsed.path} HTTP/1.1\r\n"
            f"Host: {self.endpoint.host}:{self.endpoint.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Authorization: {self.endpoint.authorization()}\r\n\r\n"
        ).encode("ascii")
        try:
            sock.sendall(request)
            headers = self._read_headers(sock)
            if not headers.startswith(b"HTTP/1.1 101 "):
                raise CdpClientError("CDP websocket admission did not return 101")
            expected = base64.b64encode(hashlib.sha1(
                key.encode("ascii") + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            ).digest()).decode("ascii").encode("ascii")
            if b"\r\nsec-websocket-accept: " + expected.lower() + b"\r\n" not in headers.lower():
                raise CdpClientError("CDP websocket handshake failed validation")
            self._socket = sock
        except Exception:
            sock.close()
            raise

    def close(self) -> None:
        sock, self._socket = self._socket, None
        if sock is None:
            return
        try:
            self._send_frame(sock, 0x8, b"")
        except OSError:
            pass
        finally:
            sock.close()

    def navigate(self, url: str) -> dict[str, Any]:
        _loopback_url(url)
        self.command("Page.enable")
        return self.command("Page.navigate", {"url": url})

    def capture_dom_snapshot(self, computed_styles: list[str]) -> dict[str, Any]:
        if not isinstance(computed_styles, list) or not computed_styles or len(computed_styles) > MAX_COMPUTED_STYLES:
            raise CdpClientError("computed style list is outside the bounded range")
        if not all(isinstance(name, str) and 0 < len(name) <= 128 and "\0" not in name for name in computed_styles):
            raise CdpClientError("computed style names are malformed")
        return self.command("DOMSnapshot.captureSnapshot", {"computedStyles": computed_styles})

    def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method not in ALLOWED_COMMANDS:
            raise CdpClientError(f"CDP command is not allowed: {method}")
        sock = self._socket
        if sock is None:
            raise CdpClientError("CDP client is not connected")
        self._next_id += 1
        command_id = self._next_id
        payload = json.dumps({"id": command_id, "method": method, "params": params or {}}, separators=(",", ":")).encode()
        if len(payload) > MAX_DISCOVERY_BYTES:
            raise CdpClientError("CDP command exceeds the bounded request size")
        self._send_frame(sock, 0x1, payload)
        while True:
            opcode, data = self._read_frame(sock)
            if opcode == 0x9:
                self._send_frame(sock, 0xA, data)
                continue
            if opcode == 0x8:
                raise CdpClientError("CDP websocket closed before command response")
            if opcode != 0x1:
                continue
            response = _bounded_json(data, label="command response", limit=MAX_MESSAGE_BYTES)
            if response.get("id") != command_id:
                continue
            if "error" in response:
                raise CdpClientError(f"CDP command failed: {response['error']}")
            value = response.get("result")
            return value if isinstance(value, dict) else {}

    @staticmethod
    def _read_headers(sock: socket.socket) -> bytes:
        value = bytearray()
        while b"\r\n\r\n" not in value and len(value) <= 16 * 1024:
            chunk = sock.recv(4096)
            if not chunk:
                break
            value.extend(chunk)
        if b"\r\n\r\n" not in value:
            raise CdpClientError("CDP websocket headers are missing or oversized")
        return bytes(value)

    @staticmethod
    def _send_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
        if len(payload) > MAX_MESSAGE_BYTES:
            raise CdpClientError("CDP websocket frame is oversized")
        first = 0x80 | (opcode & 0x0F)
        length = len(payload)
        if length < 126:
            header = bytes([first, 0x80 | length])
        elif length <= 0xFFFF:
            header = bytes([first, 0x80 | 126]) + struct.pack("!H", length)
        else:
            header = bytes([first, 0x80 | 127]) + struct.pack("!Q", length)
        mask = secrets.token_bytes(4)
        sock.sendall(header + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))

    @staticmethod
    def _read_frame(sock: socket.socket) -> tuple[int, bytes]:
        first, second = sock.recv(2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", sock.recv(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", sock.recv(8))[0]
        if length > MAX_MESSAGE_BYTES:
            raise CdpClientError("CDP websocket frame is oversized")
        mask = sock.recv(4) if second & 0x80 else None
        payload = bytearray()
        while len(payload) < length:
            chunk = sock.recv(min(64 * 1024, length - len(payload)))
            if not chunk:
                raise CdpClientError("CDP websocket frame ended early")
            payload.extend(chunk)
        if mask:
            payload = bytearray(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, bytes(payload)
