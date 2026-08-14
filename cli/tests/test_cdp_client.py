from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import socket
import socketserver
import struct
import sys
import tempfile
import threading
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "cli"))
from vellum_cdp import CdpAdmission
from vellum_cdp_client import CdpClient, CdpClientError
sys.path.pop(0)


def _read_headers(connection: socket.socket) -> bytes:
    value = bytearray()
    while b"\r\n\r\n" not in value:
        chunk = connection.recv(4096)
        if not chunk:
            return bytes(value)
        value.extend(chunk)
    return bytes(value)


def _read_client_frame(connection: socket.socket) -> bytes:
    first, second = connection.recv(2)
    del first
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", connection.recv(2))[0]
    mask = connection.recv(4)
    payload = connection.recv(length)
    return bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))


def _send_server_frame(connection: socket.socket, payload: bytes) -> None:
    connection.sendall(bytes([0x81, len(payload)]) + payload)


class _FakeCdpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        headers = _read_headers(self.request)
        first_line = headers.split(b"\r\n", 1)[0]
        target = first_line.split(b" ", 2)[1].decode()
        if target == "/json/version":
            body = json.dumps({
                "Browser": "Chrome/151",
                "webSocketDebuggerUrl": (
                    "ws://127.0.0.1:0/devtools/browser/test"
                ),
            }, separators=(",", ":")).encode()
            self.request.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            return
        key = next(
            line.split(b":", 1)[1].strip()
            for line in headers.split(b"\r\n")
            if line.lower().startswith(b"sec-websocket-key:")
        )
        accept = base64.b64encode(hashlib.sha1(
            key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        ).digest())
        self.request.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n"
        )
        while True:
            try:
                command = json.loads(_read_client_frame(self.request))
            except (OSError, ValueError):
                return
            method = command["method"]
            if method == "Page.navigate":
                result = {"frameId": "frame-1"}
            elif method == "DOMSnapshot.captureSnapshot":
                result = {"documents": [{"computedStyles": ["color"]}]}
            else:
                result = {}
            _send_server_frame(self.request, json.dumps({"id": command["id"], "result": result}).encode())


class CdpClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temporary.name) / "upstream.sock"
        self.server = socketserver.UnixStreamServer(str(self.socket_path), _FakeCdpHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.socket_path.unlink(missing_ok=True)
        self.temporary.cleanup()

    def test_authenticated_discovery_navigation_and_snapshot(self) -> None:
        with CdpAdmission(str(self.socket_path)) as admission:
            client = CdpClient(admission.endpoint)
            discovery = client.discover()
            self.assertEqual(discovery["Browser"], "Chrome/151")
            client.connect()
            try:
                self.assertEqual(client.navigate("http://127.0.0.1:8000/"), {"frameId": "frame-1"})
                snapshot = client.capture_dom_snapshot(["color"])
                self.assertIn("documents", snapshot)
            finally:
                client.close()

    def test_wait_for_dom_is_bounded_and_retries_until_document_is_ready(self) -> None:
        client = object.__new__(CdpClient)
        responses = iter(({}, {"root": {"childNodeCount": 1}}))
        calls: list[str] = []

        def command(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
            del params
            calls.append(method)
            if method == "DOM.getDocument":
                return next(responses)
            return {}

        client.command = command  # type: ignore[method-assign]
        client.wait_for_dom(timeout=1)
        self.assertEqual(calls, ["DOM.enable", "DOM.getDocument", "DOM.getDocument"])

    def test_wait_for_dom_rejects_unbounded_timeout(self) -> None:
        client = object.__new__(CdpClient)
        with self.assertRaises(CdpClientError):
            client.wait_for_dom(timeout=121)

    def test_navigation_rejects_browser_error_result(self) -> None:
        client = object.__new__(CdpClient)
        client.command = lambda method, params=None: (
            {"errorText": "net::ERR_CONNECTION_REFUSED"}
            if method == "Page.navigate" else {}
        )  # type: ignore[method-assign]
        with self.assertRaisesRegex(CdpClientError, "navigation failed"):
            client.navigate("http://127.0.0.1:8000/")

    def test_viewport_requires_bounded_integer_metrics(self) -> None:
        client = object.__new__(CdpClient)
        client.command = lambda method, params=None: {
            "visualViewport": {"clientWidth": 1280, "clientHeight": 720},
        }  # type: ignore[method-assign]
        self.assertEqual(client.viewport(), {"width": 1280, "height": 720})

    def test_screenshot_rejects_non_png_data(self) -> None:
        client = object.__new__(CdpClient)
        client.command = lambda method, params=None: {"data": "bm90LXB u".replace(" ", "")}  # type: ignore[method-assign]
        with self.assertRaisesRegex(CdpClientError, "not a PNG"):
            client.capture_screenshot()

    def test_settle_idle_uses_virtual_time_and_returns_stable_snapshot(self) -> None:
        client = object.__new__(CdpClient)
        calls: list[str] = []

        def command(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
            del params
            calls.append(method)
            if method == "DOMSnapshot.captureSnapshot":
                return {"documents": [{"nodes": {"nodeName": ["HTML"]}}]}
            return {}

        client.command = command  # type: ignore[method-assign]
        snapshot = client.settle_idle(quiet_period=0.05, timeout=1)
        self.assertIn("documents", snapshot)
        self.assertIn("Emulation.setVirtualTimePolicy", calls)

    def test_client_rejects_public_navigation_and_unbounded_styles(self) -> None:
        with CdpAdmission(str(self.socket_path)) as admission:
            client = CdpClient(admission.endpoint)
            with self.assertRaises(CdpClientError):
                client.navigate("https://example.com/")
            with self.assertRaises(CdpClientError):
                client.capture_dom_snapshot([])
            with self.assertRaises(CdpClientError):
                client.capture_dom_snapshot(["color"] * 129)

    def test_client_rejects_unapproved_commands_and_timeout(self) -> None:
        with CdpAdmission(str(self.socket_path)) as admission:
            client = CdpClient(admission.endpoint)
            with self.assertRaises(CdpClientError):
                client.command("Runtime.evaluate")
            with self.assertRaises(CdpClientError):
                CdpClient(admission.endpoint, timeout=121)


if __name__ == "__main__":
    unittest.main()
