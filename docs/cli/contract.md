# CLI contract

The CLI is the stable authoring front door; rendering and compilation are
provided by a separately installed SDK backend. This keeps project creation and
automation usable while unavailable capabilities fail honestly.

## Project lock

Every command except `create`, `doctor`, `--help`, and `--version` searches from
the requested path or current directory toward the filesystem root for
`vellum.lock.json`. Version 1 pins:

- schema `vellum.project-lock.v1`;
- a deterministic 24-character project identity;
- the exact framework version plus a separately versioned CLI protocol API;
- the source template and template version.

An unsupported schema, malformed identity, CLI API mismatch, or installed SDK
artifact mismatch fails before a backend is invoked. The framework pin is
passed to the backend, which must use
the matching SDK. Separating CLI API compatibility from the framework pin lets
the CLI gain backward-compatible fixes without making every existing project
unopenable. The lock is application-owned and should be committed.

## JSON result

`--json` emits exactly one compact object:

```json
{
  "schema": "vellum.cli.result.v1",
  "cli_version": "0.1.0-dev",
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
| `create` | yes | none |
| `doctor [--fix]` | yes | reports backend availability |
| `import` / `reimport` | no | implemented by the installed `@vellum/design-ir` backend |
| `build` / `run` | no | unavailable; future native backend |
| `test` / `capture` | no | unavailable; future native backend |
| `package` | no | unavailable; future native backend |

Installed SDK metadata carries a boolean capability for every backend command.
The CLI rejects a false capability before dispatch, so the existence of the
import backend never implies that native build, run, capture, or packaging is
available.

`doctor --fix` only creates safe project-local cache and state directories. It
does not silently install system packages or modify shell profiles.
