# Development loop

`vellum dev` is the installed-SDK authoring loop:

```sh
vellum dev --target macos
vellum dev --target web
```

It performs an initial build, watches portable application inputs, waits for an
edit to settle, rebuilds, and reloads only after a successful build. The
watched set is deterministic: root manifests and locks plus files under
`assets/`, `components/`, `design/`, `native/`, `platforms/`, `src/`,
`tokens/`, and `ui/`. Build output, caches, artifacts, `node_modules`, and
version-control state are excluded.

Each session replaces `.vellum/state/dev-transcript.jsonl` (or the path passed
to `--transcript`) with `vellum.dev.transcript.v1` records. Records use a
monotonic sequence number rather than wall-clock timestamps. They include the
source digest, changed paths, build outcome, adapter outcome, state-continuity
mode, and stop reason. `--json` still prints exactly one
`vellum.cli.result.v1` object when the session ends; JSONL is the streaming
evidence surface.

## Reload adapters and state

The macOS adapter quits the application by bundle identifier after a
successful rebuild and launches the newly staged `.app`. When
`[capabilities].persistence = "state-v1"` is declared, continuity is the
existing bounded whole-app snapshot restored by the native host. Without that
explicit capability, a restart resets in-memory state. Vellum does not claim
to preserve arbitrary JavaScript heap objects across reload.

The web adapter serves the build from a loopback-only port, injects an
EventSource client into the served HTML without modifying packaged output, and
reloads the existing page after a successful rebuild. Web reload currently
resets in-memory application state. `--no-open` starts the server without
opening the system browser.

Build failures are recorded and leave the last successfully loaded app
running. A later edit is built normally; there is no hidden fallback bundle.
Changing `framework.lock` stops with `dev_restart_required`; the supervisor
never continues with a stale in-memory SDK identity.
Source-map diagnostics can attach to build and adapter events without changing
the transcript schema.

## Bounded automation proof

CI uses the same watcher and build path:

```sh
vellum dev --target macos --test-mode \
  --max-reloads 1 --timeout 30 \
  --transcript artifacts/dev-loop.jsonl --json
```

`--test-mode` requires both a reload limit and timeout. Instead of opening a
window, it executes the target's finite no-window scenario after each
successful changed-source build. Success proves that an observed edit caused a
new build and that new output reached the runtime adapter. These bounded flags
are an automation surface, not the normal interactive workflow.

Current limitations are explicit: polling is used rather than a platform file
notification API; native reload is process restart rather than live module
replacement; browser reload is full-page reload; and mobile adapters are not
implemented.
