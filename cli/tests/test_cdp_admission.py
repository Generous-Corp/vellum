from __future__ import annotations

import http.client
from pathlib import Path
import socket
import socketserver
import tempfile
import sys
import threading
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "cli"))
from vellum_cdp import CdpAdmission, CdpAdmissionError
sys.path.pop(0)


class _Upstream(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        request = self.rfile.readline()
        while self.rfile.readline() not in (b"\r\n", b""):
            pass
        if request.startswith(b"GET /json/version"):
            body = b'{"Browser":"Chrome/151"}'
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
        else:
            self.wfile.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")


class CdpAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temporary.name) / "upstream.sock"
        self.upstream = socketserver.UnixStreamServer(str(self.socket_path), _Handler)
        self.upstream.daemon_threads = True
        self.thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.upstream.shutdown()
        self.upstream.server_close()
        self.thread.join(2)
        self.socket_path.unlink(missing_ok=True)
        self.temporary.cleanup()

    def request(self, admission: CdpAdmission, *, token: str | None, path: str) -> tuple[int, bytes]:
        endpoint = admission.endpoint
        connection = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=2)
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_loopback_proxy_requires_token_and_forwards_discovery(self) -> None:
        with CdpAdmission(str(self.socket_path)) as admission:
            endpoint = admission.endpoint
            self.assertEqual(endpoint.schema, "vellum.cdp-admission.v1")
            self.assertNotIn(endpoint.token, str(endpoint.public_metadata()))
            self.assertEqual(self.request(admission, token=None, path="/json/version")[0], 401)
            status, body = self.request(
                admission, token=endpoint.token, path="/json/version"
            )
            self.assertEqual((status, body), (200, b'{"Browser":"Chrome/151"}'))

    def test_proxy_rejects_non_cdp_paths_before_upstream(self) -> None:
        with CdpAdmission(str(self.socket_path)) as admission:
            status, _body = self.request(
                admission, token=admission.endpoint.token, path="/../secret"
            )
            self.assertEqual(status, 403)

    def test_upstream_and_timeout_inputs_are_bounded(self) -> None:
        with self.assertRaises(CdpAdmissionError):
            CdpAdmission("relative.sock")
        with self.assertRaises(CdpAdmissionError):
            CdpAdmission(str(self.socket_path), idle_timeout=301)
        with self.assertRaises(CdpAdmissionError):
            CdpAdmission(str(self.socket_path), token="short")

    def test_chrome_arguments_pin_network_and_cdp_to_loopback(self) -> None:
        admission = CdpAdmission(str(self.socket_path))
        arguments = admission.chrome_arguments()
        self.assertIn(f"--remote-debugging-socket-name={self.socket_path}", arguments)
        self.assertFalse(any("remote-debugging-port" in argument for argument in arguments))
        self.assertIn("--no-proxy-server", arguments)
        self.assertTrue(any(argument.startswith("--host-resolver-rules=MAP * ~NOTFOUND")
                            for argument in arguments))

    def test_desktop_chrome_uses_ephemeral_loopback_port(self) -> None:
        arguments = CdpAdmission.chrome_launch_arguments()
        self.assertIn("--remote-debugging-port=0", arguments)
        self.assertIn("--remote-debugging-address=127.0.0.1", arguments)
        self.assertNotIn("--remote-debugging-socket-name", " ".join(arguments))


if __name__ == "__main__":
    unittest.main()
