# CLI contract

The CLI is the stable authoring front door; rendering and compilation are
provided by a separately installed SDK backend. This keeps project creation and
automation usable while unavailable capabilities fail honestly.

## Project lock

Every command except `create`, `doctor`, `--help`, and `--version` searches from
the requested path or current directory toward the filesystem root for
`framework.lock`. Version 1 pins:

- schema `vellum.project-lock.v1`;
- a deterministic 24-character project identity;
- the exact framework version and installed artifact identity (verification
  state, SHA-256, target, and source commit), plus a separately versioned CLI
  protocol API;
- the exact `@vellum/ui` package identity, which must agree with the committed
  npm lock;
- the source template and template version.

An unsupported schema, malformed identity, CLI API mismatch, or installed SDK
artifact mismatch fails before a backend is invoked. The framework pin is
passed to the backend, which must use
the matching SDK. Separating CLI API compatibility from the framework pin lets
the CLI gain backward-compatible fixes without making every existing project
unopenable. The lock is tool-owned and should be committed. `app.toml`,
`package.json`, and `package-lock.json` are developer-owned within their
validated roles. Projects
made from a local source install are explicitly marked unverified with no SHA
or commit; verified installs pin the archive SHA exactly.

## JSON result

`--json` emits exactly one compact object:

```json
{
  "schema": "vellum.cli.result.v1",
  "cli_version": "0.1.0",
  "command": "build",
  "ok": false,
  "status": "capability_unavailable",
  "message": "'build' needs the Vellum SDK backend, which is not installed in this extraction milestone.",
  "data": {},
  "diagnostics": []
}
```

Fields are always present. Scripts should branch on `ok` and `status`, not
human prose. Exit codes are: `0` success, `2` usage, `3` project/lock error, `4`
unavailable capability, and `5` backend/protocol failure.

## Backend protocol

The CLI discovers an executable in this order:

1. `VELLUM_BACKEND`;
2. `$VELLUM_SDK_ROOT/bin/vellum-backend`;
3. `vellum-backend` on `PATH`.

It invokes:

```text
vellum-backend COMMAND --project ABSOLUTE_ROOT --json \
  --framework-version EXACT_VERSION --cli-api API [command options]
```

The framework/API arguments are a required compatibility handshake. The
installed dispatcher checks them against `metadata.json`, then routes
`import`/`reimport` to `vellum-import-backend` and reserves
`vellum-native-backend` for native application commands. Those executables never
replace one another.

The selected backend must write one JSON object with `ok`, `status`, `message`, optional
`data`, and optional `diagnostics`; it must use a nonzero exit status on
failure. The front end nests that response under `data.backend`, preserving the
stable outer schema.

## Command availability

| Command | CLI-only today | Backend capability |
|---|---:|---|
| `create [--no-verify] [--run]` | yes | with a native SDK, builds and tests by default; `--run` launches |
| `doctor [--fix]` | yes | reports backend availability |
| `import` / `reimport` | no | implemented by the installed `@vellum/design-ir` backend |
| `build` / `run` | no | macOS 15.0+ arm64 GPU artifact builds/launches a real `.app`; finite `run --self-test` is available |
| `test` / `capture` | no | macOS 15.0+ arm64 GPU artifact executes bounded scenarios, captures PNGs, and composes bounded capture matrices/montages |
| `package` | no | macOS 15.0+ arm64 GPU artifact creates an ad-hoc-signed `.app` |

Installed SDK metadata carries a boolean capability for every backend command.
The CLI rejects a false capability before dispatch, so the existence of the
import backend never implies that native build, run, capture, or packaging is
available.

The native backend response uses the exact `vellum.backend.result.v1` schema.
It reads only the installed host, libraries, UI bundler, and the application
project. At this milestone it accepts exactly the `macos` target; unsupported
targets and missing payloads are errors, never successful no-ops.

`[capabilities].persistence` accepts only `"none"` or the explicit
`"state-v1"` whole-runtime snapshot lane. The macOS package records that exact
choice in its Info.plist; the host does not infer persistence from use of
`useState` or `createApp`.

An application may declare app-owned custom C++ paint components through the
versioned manifest and ABI described in
[`custom-components.md`](custom-components.md). They are compiled into the app,
not into the installed SDK, and cannot include Vellum renderer internals.

`doctor --fix` creates safe project-local cache/state directories and projects
the exact locked UI package from the installed SDK into ignored `.vellum/`
state. It does not silently install system packages or modify shell profiles.
