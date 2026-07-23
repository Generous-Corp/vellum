#!/usr/bin/env python3
"""Deterministic watch/build/reload supervision for ``vellum dev``."""

from __future__ import annotations

import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any, Callable
import webbrowser

from vellum_manifest import APP_MANIFEST_NAME, LOCK_NAME, load_app_manifest


TRANSCRIPT_SCHEMA = "vellum.dev.transcript.v1"
EXCLUDED_DIRECTORIES = {".git", ".vellum", "artifacts", "dist", "node_modules"}
SOURCE_DIRECTORIES = {
    "assets", "components", "design", "native", "platforms", "src", "tokens", "ui",
}
ROOT_FILES = {
    APP_MANIFEST_NAME, LOCK_NAME, "package.json", "package-lock.json", "tsconfig.json",
}
BackendInvoker = Callable[
    [str, Path, dict[str, Any], list[str]], tuple[dict[str, Any], int]
]
ResultFactory = Callable[..., dict[str, Any]]


class DevError(RuntimeError):
    def __init__(self, message: str, *, status: str, exit_code: int):
        super().__init__(message)
        self.status = status
        self.exit_code = exit_code


def source_files(root: Path) -> list[Path]:
    files = [root / name for name in sorted(ROOT_FILES) if (root / name).is_file()]
    for directory_name in sorted(SOURCE_DIRECTORIES):
        directory = root / directory_name
        if not directory.is_dir():
            continue
        files.extend(
            path for path in sorted(directory.rglob("*"))
            if path.is_file()
            and not any(
                part in EXCLUDED_DIRECTORIES for part in path.relative_to(root).parts
            )
        )
    return files


def snapshot(root: Path) -> tuple[str, dict[str, str]]:
    records: dict[str, str] = {}
    digest = hashlib.sha256()
    for path in source_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            # Editors commonly replace a file atomically between enumeration
            # and read. The next poll observes the settled replacement.
            continue
        file_digest = hashlib.sha256(content).hexdigest()
        records[relative] = file_digest
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(file_digest.encode("ascii") + b"\n")
    return digest.hexdigest(), records


def changed(previous: dict[str, str], current: dict[str, str]) -> list[str]:
    return sorted(
        name for name in set(previous) | set(current)
        if previous.get(name) != current.get(name)
    )


class Transcript:
    def __init__(self, path: Path, *, human_output: bool = False):
        self.path = path
        self.sequence = 0
        self.human_output = human_output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def append(self, event: str, **data: Any) -> None:
        self.sequence += 1
        record = {
            "schema": TRANSCRIPT_SCHEMA,
            "sequence": self.sequence,
            "event": event,
            "data": data,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )
        if self.human_output:
            detail = data.get("status") or data.get("reason") or ""
            print(
                f"[vellum dev {self.sequence}] {event}"
                + (f": {detail}" if detail else ""),
                file=sys.stderr, flush=True,
            )


class WebReloadServer:
    CLIENT = (
        "<script data-vellum-dev>"
        "const k='vellum.dev.state.v1';"
        "addEventListener('load',()=>{"
        "const s=sessionStorage.getItem(k);"
        "if(s&&globalThis.__vellum?.restoreStateJSON){"
        "sessionStorage.removeItem(k);globalThis.__vellum.restoreStateJSON(s);}});"
        "new EventSource('/__vellum_dev_events').addEventListener('reload',()=>{"
        "try{if(globalThis.__vellum?.snapshotStateJSON)"
        "sessionStorage.setItem(k,globalThis.__vellum.snapshotStateJSON());}"
        "finally{location.reload();}});</script>"
    ).encode("utf-8")

    def __init__(self, build_root: Path, port: int):
        self.build_root = build_root
        self.events: queue.Queue[str] = queue.Queue()
        controller = self

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args: object, **kwargs: object):
                super().__init__(*args, directory=str(controller.build_root), **kwargs)

            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/__vellum_dev_events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    try:
                        value = controller.events.get(timeout=25)
                        self.wfile.write(
                            f"event: reload\ndata: {value}\n\n".encode("utf-8")
                        )
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    return
                if self.path.split("?", 1)[0] in {"/", "/index.html"}:
                    content = (controller.build_root / "index.html").read_bytes()
                    content = content.replace(b"</body>", controller.CLIENT + b"</body>")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
                super().do_GET()

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/"

    def start(self, *, open_browser: bool) -> None:
        self.thread.start()
        if open_browser:
            webbrowser.open(self.url)

    def reload(self, digest: str) -> None:
        self.events.put(digest)

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()


def _failure(
    make_result: ResultFactory, status: str, message: str, root: Path,
    target: str, transcript: Path, backend: dict[str, Any],
) -> dict[str, Any]:
    return make_result(
        "dev", ok=False, status=status, message=message,
        data={
            "project_root": str(root), "target": target,
            "transcript": str(transcript), "backend": backend,
        },
        diagnostics=backend.get("diagnostics", []),
    )


def run(
    args: Any, root: Path, lock: dict[str, Any],
    sdk: tuple[Path, dict[str, Any], dict[str, Any]] | None,
    invoke_backend: BackendInvoker, make_result: ResultFactory,
    *, unavailable_exit: int,
) -> dict[str, Any]:
    target_commands = (
        sdk[1]["capabilities"].get("targets", {}).get(args.target, {}).get("commands", {})
        if sdk else {}
    )
    if target_commands.get("build") is not True or target_commands.get("run") is not True:
        raise DevError(
            f"The installed SDK does not provide the build/run dev loop for '{args.target}'.",
            status="capability_unavailable", exit_code=unavailable_exit,
        )
    transcript_path = (
        Path(args.transcript).expanduser().resolve()
        if args.transcript else root / ".vellum/state/dev-transcript.jsonl"
    )
    transcript = Transcript(transcript_path, human_output=not args.json)
    persistence = load_app_manifest(root)["capabilities"].get("persistence")
    continuity = (
        "persisted-state-v1"
        if args.target == "macos" and persistence == "state-v1" else "none"
    )
    digest, files = snapshot(root)
    transcript.append(
        "session-started", target=args.target, digest=digest,
        watched_files=sorted(files), continuity=continuity,
        adapter="verification" if args.test_mode else args.target,
    )
    built, code = invoke_backend("build", root, lock, ["--target", args.target])
    build_ok = code == 0 and bool(built.get("ok"))
    transcript.append(
        "build-finished", generation=0, ok=build_ok, status=built.get("status")
    )
    if not build_ok:
        return _failure(
            make_result, "initial_build_failed",
            "Vellum dev could not complete the initial build.",
            root, args.target, transcript_path, built,
        )

    server: WebReloadServer | None = None
    generation = 0
    reloads = 0
    try:
        if args.test_mode:
            transcript.append(
                "adapter-ready", generation=0, adapter="verification",
                detail="no-window scenario runs after each successful rebuild",
            )
        elif args.target == "web":
            build_root = Path(
                built.get("data", {}).get("root", root / ".vellum/build/web")
            )
            server = WebReloadServer(build_root, args.port)
            server.start(open_browser=not args.no_open)
            transcript.append(
                "adapter-ready", generation=0, adapter="web-page-reload",
                url=server.url, continuity="none",
            )
        else:
            launched, launch_code = invoke_backend(
                "run", root, lock, ["--target", args.target, "--no-build"]
            )
            if launch_code != 0 or not launched.get("ok"):
                transcript.append(
                    "adapter-failed", generation=0, adapter="native-restart",
                    status=launched.get("status"),
                )
                return _failure(
                    make_result, "initial_launch_failed",
                    "Vellum dev built the app but could not launch it.",
                    root, args.target, transcript_path, launched,
                )
            transcript.append(
                "adapter-ready", generation=0, adapter="native-restart",
                continuity=continuity,
            )

        deadline = time.monotonic() + args.timeout if args.timeout is not None else None
        while args.max_reloads is None or reloads < args.max_reloads:
            if deadline is not None and time.monotonic() >= deadline:
                transcript.append("session-stopped", reason="timeout", reloads=reloads)
                return make_result(
                    "dev", ok=False, status="dev_timeout",
                    message="Vellum dev reached its bounded timeout before the requested reloads.",
                    data={
                        "project_root": str(root), "target": args.target,
                        "reloads": reloads, "transcript": str(transcript_path),
                        "continuity": continuity,
                    },
                )
            time.sleep(args.poll_interval)
            current_digest, current = snapshot(root)
            if current_digest == digest:
                continue
            paths = changed(files, current)
            time.sleep(args.debounce)
            settled_digest, settled = snapshot(root)
            if settled_digest != current_digest:
                paths = changed(files, settled)
                current_digest, current = settled_digest, settled
            generation += 1
            transcript.append(
                "change-detected", generation=generation,
                digest=current_digest, paths=paths,
            )
            if LOCK_NAME in paths:
                transcript.append(
                    "session-stopped", reason="project-lock-changed",
                    reloads=reloads,
                )
                return make_result(
                    "dev", ok=False, status="dev_restart_required",
                    message="framework.lock changed; restart vellum dev to validate the new SDK pin.",
                    data={
                        "project_root": str(root), "target": args.target,
                        "reloads": reloads, "transcript": str(transcript_path),
                        "continuity": continuity,
                    },
                )
            rebuilt, rebuild_code = invoke_backend(
                "build", root, lock, ["--target", args.target]
            )
            rebuild_ok = rebuild_code == 0 and bool(rebuilt.get("ok"))
            transcript.append(
                "build-finished", generation=generation, ok=rebuild_ok,
                status=rebuilt.get("status"),
            )
            digest, files = current_digest, current
            if not rebuild_ok:
                transcript.append(
                    "reload-skipped", generation=generation, reason="build-failed"
                )
                continue

            if args.test_mode:
                adapter = "verification"
                loaded, load_code = invoke_backend(
                    "run", root, lock,
                    ["--target", args.target, "--no-build", "--no-window"],
                )
            elif server is not None:
                server.reload(current_digest)
                adapter, loaded, load_code = "web-page-reload", {"ok": True}, 0
            else:
                adapter = "native-restart"
                loaded, load_code = invoke_backend(
                    "run", root, lock,
                    ["--target", args.target, "--no-build", "--dev-reload"],
                )
            load_ok = load_code == 0 and bool(loaded.get("ok"))
            transcript.append(
                "reload-completed", generation=generation, adapter=adapter,
                ok=load_ok, status=loaded.get("status"), continuity=continuity,
            )
            if not load_ok:
                return _failure(
                    make_result,
                    "reload_verification_failed" if args.test_mode else "reload_failed",
                    "The changed source rebuilt, but the reload adapter failed.",
                    root, args.target, transcript_path, loaded,
                )
            reloads += 1

        transcript.append("session-stopped", reason="reload-limit", reloads=reloads)
        return make_result(
            "dev", ok=True, status="dev_completed",
            message=f"Vellum dev completed {reloads} verified reload(s).",
            data={
                "project_root": str(root), "project_id": lock["project"]["id"],
                "target": args.target, "reloads": reloads,
                "transcript": str(transcript_path), "continuity": continuity,
            },
        )
    except KeyboardInterrupt:
        transcript.append("session-stopped", reason="interrupt", reloads=reloads)
        return make_result(
            "dev", ok=True, status="dev_stopped", message="Vellum dev stopped.",
            data={
                "project_root": str(root), "project_id": lock["project"]["id"],
                "target": args.target, "reloads": reloads,
                "transcript": str(transcript_path), "continuity": continuity,
            },
        )
    finally:
        if server is not None:
            server.close()
