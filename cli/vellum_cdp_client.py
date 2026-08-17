#!/usr/bin/env python3
"""Bounded, dependency-free CDP client for the Vellum capture lane."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import struct
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, urlopen

from vellum_cdp import CdpEndpoint
from vellum_interaction import InteractionPlanError, validate_interaction_plan


MAX_DISCOVERY_BYTES = 64 * 1024
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024
MAX_COMPUTED_STYLES = 128
DEFAULT_TIMEOUT = 15.0
ALLOWED_COMMANDS = {
    "DOM.enable",
    "DOM.focus",
    "DOM.getBoxModel",
    "DOM.getDocument",
    "DOM.querySelector",
    "Input.dispatchKeyEvent",
    "Input.dispatchMouseEvent",
    "Input.insertText",
    "Page.enable",
    "Page.getLayoutMetrics",
    "Page.navigate",
    "DOMSnapshot.captureSnapshot",
    "Emulation.setVirtualTimePolicy",
    "Page.captureScreenshot",
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
        port = parsed.port
        if port is not None and not 0 < port <= 65535:
            raise CdpClientError("CDP navigation port is outside the valid range")
        import ipaddress
        if not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise CdpClientError("CDP navigation is loopback-only")
    except ValueError as error:
        raise CdpClientError("CDP navigation requires a numeric loopback host") from error


def _bounded_json(raw: bytes, *, label: str, limit: int, object_required: bool = True) -> Any:
    if len(raw) > limit:
        raise CdpClientError(f"CDP {label} exceeds the bounded response size")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CdpClientError(f"CDP {label} is not valid JSON") from error
    if object_required and not isinstance(value, dict):
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
        self._events: list[dict[str, Any]] = []

    def __enter__(self) -> "CdpClient":
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def discover(self) -> dict[str, Any]:
        value = self._discover_path("/json/version")
        if not isinstance(value, dict):
            raise CdpClientError("CDP version discovery must be an object")
        return value

    def _discover_path(self, path: str) -> Any:
        request = Request(self.endpoint.url(path), headers={
            "Authorization": self.endpoint.authorization(),
        })
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return _bounded_json(
                    response.read(MAX_DISCOVERY_BYTES + 1), label="discovery",
                    limit=MAX_DISCOVERY_BYTES, object_required=path != "/json/list",
                )
        except CdpClientError:
            raise
        except OSError as error:
            raise CdpClientError(f"CDP discovery failed: {error}") from error

    def connect(self) -> None:
        if self._socket is not None:
            raise CdpClientError("CDP client is already connected")
        # /json/version identifies the browser, but its WebSocket is the
        # browser-level target and does not accept page DOM/Input commands.
        # Select the contained loopback page target from /json/list instead.
        raw_targets = self._discover_path("/json/list")
        if not isinstance(raw_targets, list):
            raise CdpClientError("CDP target discovery did not return a target list")
        candidates = [target for target in raw_targets if isinstance(target, dict)]
        page_targets = [target for target in candidates if target.get("type") == "page"]
        loopback_pages = [target for target in page_targets if isinstance(target.get("url"), str)
                          and target["url"].startswith(("http://127.0.0.1:", "http://localhost:"))]
        target = (loopback_pages or page_targets)[-1] if (loopback_pages or page_targets) else None
        if target is None:
            raise CdpClientError("CDP target discovery omitted a page target")
        raw_ws_url = target.get("webSocketDebuggerUrl")
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
        result = self.command("Page.navigate", {"url": url})
        error_text = result.get("errorText")
        if isinstance(error_text, str) and error_text:
            raise CdpClientError(f"browser navigation failed: {error_text}")
        return result

    def viewport(self) -> dict[str, int]:
        metrics = self.command("Page.getLayoutMetrics")
        visual = metrics.get("visualViewport")
        layout = metrics.get("layoutViewport")
        candidate = visual if isinstance(visual, dict) else layout
        if not isinstance(candidate, dict):
            raise CdpClientError("CDP response omitted viewport metrics")
        width = candidate.get("clientWidth", candidate.get("width"))
        height = candidate.get("clientHeight", candidate.get("height"))
        def bounded_integer(value: object) -> int | None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            if isinstance(value, float) and not value.is_integer():
                return None
            integer = int(value)
            return integer if 1 <= integer <= 16384 else None
        width_int = bounded_integer(width)
        height_int = bounded_integer(height)
        if width_int is None or height_int is None:
            raise CdpClientError("CDP viewport metrics are malformed or unbounded")
        return {"width": width_int, "height": height_int}

    def capture_dom_snapshot(self, computed_styles: list[str]) -> dict[str, Any]:
        if not isinstance(computed_styles, list) or not computed_styles or len(computed_styles) > MAX_COMPUTED_STYLES:
            raise CdpClientError("computed style list is outside the bounded range")
        if not all(isinstance(name, str) and 0 < len(name) <= 128 and "\0" not in name for name in computed_styles):
            raise CdpClientError("computed style names are malformed")
        return self.command("DOMSnapshot.captureSnapshot", {"computedStyles": computed_styles})

    def settle_idle(
        self, *, budget_ms: int = 1000, quiet_period: float = 0.25,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Advance bounded virtual time and wait for a stable DOM snapshot."""
        if not isinstance(budget_ms, int) or isinstance(budget_ms, bool) or not 1 <= budget_ms <= 10000:
            raise CdpClientError("virtual-time budget is outside the bounded range")
        if quiet_period <= 0 or quiet_period > 5 or timeout <= 0 or timeout > 120:
            raise CdpClientError("idle settling bounds are invalid")
        self.command("Page.enable")
        self.command("Emulation.setVirtualTimePolicy", {
            "policy": "pauseIfNetworkFetchesPending",
            "budget": budget_ms,
            "maxVirtualTimeTaskStarvationCount": 1000,
            "waitForNavigation": True,
        })
        deadline = time.monotonic() + timeout
        self._wait_for_event("Emulation.virtualTimeBudgetExpired", deadline)
        previous_digest: str | None = None
        stable_since: float | None = None
        snapshot: dict[str, Any] = {}
        while time.monotonic() < deadline:
            snapshot = self.capture_dom_snapshot([
                "display", "visibility", "color", "font-size",
                "background-image", "list-style-image", "content",
            ])
            digest = hashlib.sha256(
                json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            now = time.monotonic()
            if digest == previous_digest:
                if stable_since is None:
                    stable_since = now
                elif now - stable_since >= quiet_period:
                    return snapshot
            else:
                previous_digest = digest
                stable_since = now
            time.sleep(0.05)
        raise CdpClientError("browser DOM did not settle before the bounded timeout")

    def _wait_for_event(self, method: str, deadline: float) -> None:
        while time.monotonic() < deadline:
            for index, event in enumerate(self._events):
                if event.get("method") == method:
                    del self._events[index]
                    return
            self.command("Page.getLayoutMetrics")
            time.sleep(0.05)
        raise CdpClientError(f"CDP event {method} was not observed before the bounded timeout")

    def capture_screenshot(self) -> dict[str, Any]:
        """Capture one bounded PNG from the current browser surface."""
        result = self.command("Page.captureScreenshot", {
            "format": "png", "fromSurface": True, "captureBeyondViewport": False,
        })
        encoded = result.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise CdpClientError("CDP screenshot response omitted PNG data")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise CdpClientError("CDP screenshot data is not valid base64") from error
        if len(data) > MAX_SCREENSHOT_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise CdpClientError("CDP screenshot is oversized or not a PNG")
        return {"mimeType": "image/png", "data": encoded, "byteLength": len(data)}

    def wait_for_dom(self, timeout: float = 20.0) -> None:
        """Wait until the isolated page has a non-empty document tree."""
        if timeout <= 0 or timeout > 120:
            raise CdpClientError("DOM readiness timeout is outside the bounded range")
        deadline = time.monotonic() + timeout
        self.command("DOM.enable")
        while time.monotonic() < deadline:
            document = self.command("DOM.getDocument", {"depth": 0, "pierce": False})
            root = document.get("root")
            if isinstance(root, dict) and root.get("childNodeCount", 0) > 0:
                return
            time.sleep(0.05)
        raise CdpClientError("browser DOM did not become ready before the bounded timeout")

    @staticmethod
    def _node_id(value: object, label: str = "DOM node") -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise CdpClientError(f"CDP response did not provide a valid {label} id")
        return value

    def _query_node(self, target: str) -> int:
        self.command("DOM.enable")
        document = self.command("DOM.getDocument", {"depth": 0, "pierce": False})
        root = self._node_id(document.get("root", {}).get("nodeId"))
        response = self.command("DOM.querySelector", {"nodeId": root, "selector": target})
        return self._node_id(response.get("nodeId"), "matching DOM node")

    def focus_target(self, target: str) -> None:
        self.command("DOM.focus", {"nodeId": self._query_node(target)})

    def click_target(self, target: str) -> None:
        node = self._query_node(target)
        box = self.command("DOM.getBoxModel", {"nodeId": node}).get("model", {})
        content = box.get("content")
        if not isinstance(content, list) or len(content) != 8 or not all(isinstance(value, (int, float)) for value in content):
            raise CdpClientError("CDP response did not provide a bounded element box")
        x = (content[0] + content[2] + content[4] + content[6]) / 4
        y = (content[1] + content[3] + content[5] + content[7]) / 4
        if not (0 <= x <= 16384 and 0 <= y <= 16384):
            raise CdpClientError("CDP element box is outside the bounded viewport")
        self.command("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        self.command("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

    def input_target(self, target: str, value: str) -> None:
        self.focus_target(target)
        self.command("Input.insertText", {"text": value})

    def key_target(self, target: str, key: str) -> None:
        self.focus_target(target)
        self.command("Input.dispatchKeyEvent", {"type": "keyDown", "key": key})
        self.command("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})

    def execute_interaction_plan(self, plan: object) -> dict[str, Any]:
        try:
            normalized = validate_interaction_plan(plan)
        except InteractionPlanError as error:
            raise CdpClientError(str(error)) from error
        evidence: list[dict[str, Any]] = []
        for step in normalized["steps"]:
            action = step["action"]
            if action == "navigate":
                value = self.navigate(step["url"])
                self.wait_for_dom()
            elif action == "click":
                value = self.click_target(step["target"]); value = {"target": step["target"]}
            elif action == "focus":
                value = self.focus_target(step["target"]); value = {"target": step["target"]}
            elif action == "input":
                value = self.input_target(step["target"], step["value"]); value = {"target": step["target"], "valueBytes": len(step["value"].encode("utf-8"))}
            elif action == "key":
                value = self.key_target(step["target"], step["key"]); value = {"target": step["target"], "key": step["key"]}
            else:
                value = self.capture_dom_snapshot(step["computedStyles"])
            evidence.append({"action": action, "name": step.get("name"), "result": value})
        return {"schema": "vellum.browser-interaction-evidence.v1", "plan": normalized["name"], "steps": evidence}

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
                event_method = response.get("method")
                if isinstance(event_method, str):
                    if len(self._events) >= 128:
                        del self._events[0]
                    self._events.append(response)
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
