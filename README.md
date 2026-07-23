# Vellum

> **Status: private, experimental, 0.x.** APIs, schemas, CLI names, and the
> working name may change without notice. Exact-pin SDK compatibility only.
> Not accepting external users.

Vellum is an experimental, audio-free application framework extracted to test
one product question: can a developer import a design, add TypeScript or
JavaScript behavior, and ship a GPU-rendered application without Chromium or an
OS WebView as the primary UI runtime?

This repository is being built toward the independent-validation milestone.
The project CLI and repository shape are usable. An audio-free native C++
kernel, deterministic DesignIR import/reimport, hardened JS/TS/JSX retained-tree
runtime, JavaScriptCore host, installable CMake SDK, and a retained scene
rendered through Skia Graphite, Dawn, and Metal are executable in-tree. A
macOS arm64 SDK artifact built with the pinned renderer also provides the first
installed native backend: it bundles authored TS/JS/JSX and optional imported
DesignIR, builds and runs a real `.app`, executes finite scenarios, captures a
GPU PNG, and emits an ad-hoc-signed application package using installed bytes
only. Other targets remain unavailable and fail closed.

The history-preserving Pulp projection has been removed from the active tip.
Its authorship and exact blobs remain auditable in Git history and immutable
`provenance/` records; Vellum does not maintain a synchronized editable copy.

## Five-minute authoring start

No public SDK release exists yet. Install the CLI from this checkout, create a
sterile application, and inspect exactly what is and is not ready:

```sh
git clone git@github.com:Generous-Corp/vellum.git
cd vellum
./scripts/install.sh --local "$PWD"
export PATH="$HOME/.local/bin:$PATH"

tmp_app="$(mktemp -d)/vellum-hello"
vellum create "Vellum Hello" --directory "$tmp_app"
cd "$tmp_app"
vellum doctor --fix
vellum --json build
```

The checkout-only development install intentionally has no native backend, so
the final command returns structured `capability_unavailable`. Use the pinned
artifact flow below for the installed native journey.

## Pinned macOS native SDK and first app

Download and verify the exact Skia/Dawn toolchain recorded in
[`DEPENDENCIES.md`](DEPENDENCIES.md), then configure Vellum with its extraction
root:

```sh
curl -fL \
  https://github.com/danielraffel/skia-builder/releases/download/chrome/m150/skia-build-mac-arm64-gpu-release.zip \
  -o /tmp/vellum-skia-m150.zip
printf '%s  %s\n' \
  13b0e9818c3b05db661af85cb1e2bf2ef10e30d468b81351dd90295237d17734 \
  /tmp/vellum-skia-m150.zip | shasum -a 256 -c -
cmake -S . -B build-gpu \
  -DCMAKE_BUILD_TYPE=Release \
  -DVELLUM_REQUIRE_GPU=ON \
  -DVELLUM_SKIA_ARCHIVE=/tmp/vellum-skia-m150.zip
cmake --build build-gpu --parallel
ctest --test-dir build-gpu --output-on-failure
python3 scripts/build_sdk_artifact.py \
  --skia-archive /tmp/vellum-skia-m150.zip --output-dir dist --json
./scripts/install.sh \
  --archive dist/vellum-sdk-0.1.0-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS

app_dir="$(mktemp -d)/palette-board"
vellum create "Palette Board" --directory "$app_dir"
cd "$app_dir"
vellum run --no-build
```

With this SDK, `create` scaffolds, builds, and runs the finite smoke scenario by
default; it fails if any proof step fails. `--run` may be passed directly to
`create` to launch after validation. The packaged host must report Skia Graphite
over Dawn/Metal, no fallback, semantic interaction routing, and non-blank output.

On Windows PowerShell, the equivalent local-development installer is:

```powershell
.\scripts\install.ps1 -LocalRoot $PWD
$env:Path = "$HOME\.local\bin;$env:Path"
vellum create "Vellum Hello" -d "$env:TEMP\vellum-hello"
```

## CLI journey

```sh
vellum create MyApp
cd myapp
vellum doctor --fix
npm ci
vellum import ./revision-a.source.json --source-type figma --as main
vellum reimport --source ./revision-b.source.json --as main
vellum build --target macos
vellum run --target macos
vellum test
vellum capture --scenario smoke --output artifacts/smoke.png
vellum capture --matrix tests/capture-matrix.json --montage --output artifacts/montage.png
vellum package --target macos --output dist
```

The one-command imported path is
`vellum create "Imported App" --from figma ./frame.pulp.zip --run`; it uses
the permanent source key `main` unless `--as` supplies another key.

`create` is deterministic and separates imported snapshots, normalized
DesignIR, generated UI, tokens/assets, hand-written app logic, optional native
components, platform modules, tests, and packaging configuration. The macOS GPU
artifact implements this complete command lane. An artifact built without the
pinned renderer advertises only import/reimport. Backends are discovered through
`VELLUM_SDK_ROOT`, `VELLUM_BACKEND`, or `PATH`.

Generated applications use `app.toml` as the sole editable authority for app
identity, entry point, targets, capabilities, and packaging. `framework.lock`
pins the exact SDK artifact and JS package identity. `package-lock.json` is an
exact npm lock; an installed SDK projects immutable `@vellum/ui` runtime bytes
into ignored `.vellum/packages/`, and `create` proves the lock with offline
`npm ci` before reporting success.

The accepted source contract, generated tree, ownership boundary, and conflict
workflow are documented in [Import and reimport](docs/cli/import-reimport.md).
The semantic interaction driver, offscreen capture path, and deterministic
contact-sheet format are documented in [Interaction capture and montages](docs/cli/capture.md).

Every command accepts `--json` before or after the command and emits one stable
`vellum.cli.result.v1` object. See [the CLI contract](docs/cli/contract.md).

Agents and automation should follow the versioned
[Vellum application-authoring skill](.agents/skills/vellum-app-authoring/SKILL.md).
Its adjacent machine-readable manifest is checked against the real CLI parser,
source-support policy, ownership boundary, and capability-failure semantics by
`python3 tools/agent_instructions/verify.py --json`; instructions cannot name a
command, flag, or import route that the checked-in product surface does not
support. SDK artifacts install the same contract under
`$VELLUM_SDK_ROOT/.agents/skills/vellum-app-authoring/`.

## Build and install an immutable local SDK artifact

The builder performs a Release build, creates a relocatable CMake install tree,
normalizes archive metadata, and emits both `SHA256SUMS` and machine-readable
evidence. Building twice from the same source commit and toolchain is covered
by the integration test.

```sh
python3 scripts/build_sdk_artifact.py \
  --skia-archive /tmp/vellum-skia-m150.zip --output-dir dist --json
python3 scripts/verify_sdk_artifact.py \
  --archive dist/vellum-sdk-0.1.0-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS --json
./scripts/install.sh \
  --archive dist/vellum-sdk-0.1.0-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS
```

To compose the pinned GPU and authoring SDK into the artifact, pass the Skia
archive verified in the preceding section:

```sh
python3 scripts/build_sdk_artifact.py \
  --skia-archive /tmp/vellum-skia-m150.zip \
  --output-dir dist --json
```

That artifact includes the installed `Vellum::Gpu` and `Vellum::Authoring`
targets and an offline-ready `@vellum/ui` toolchain. Native CLI commands remain
unavailable unless a real `cli/vellum_native_backend.py` exists; artifact
metadata is derived from the payload and cannot claim a missing backend.

The installed CMake tree is under the chosen prefix at `lib/vellum/sdk`;
consumers can add that directory to `CMAKE_PREFIX_PATH` and use
`find_package(Vellum CONFIG REQUIRED)`.

Every install writes `lib/vellum/install-manifest.json`. Verified installs
record the archive SHA-256, target, version, and source commit; local installs
are explicitly unverified and carry no fabricated hash. New projects pin this
identity in `framework.lock`, so a same-version but different SDK artifact is
rejected before backend execution.

`scripts/validate_installed_sdk.py` verifies the archive, installs to a clean
prefix, creates and validates an app through the installed CLI, checks its
project lock against SDK metadata, imports and reimports two design revisions,
proves the imported design is embedded in the application bundle, exercises
build/run/test/capture/package, and builds/tests a sterile CMake consumer
without a Vellum or Pulp checkout.

No hosted Vellum release exists yet. Once an exact versioned release contains
the same archive and `SHA256SUMS`, the installer can consume it without a
moving `latest` pointer:

```sh
./scripts/install.sh --version 0.1.0
```

For a cautious future network install, download the version-pinned installer,
archive, and checksum manifest separately; verify the installer source before
running it; then pass the local archive and manifest as above. Checksum
verification protects downloaded bytes but does not make an unreviewed network
script intrinsically safe. No `curl | sh` command is advertised before an
immutable, checksummed release exists.

## Current boundary

- No Pulp audio, plug-in, host, or product adapters belong in Vellum.
- Pulp does not consume this repository during independent validation.
- macOS and browser/Wasm are the first proof targets; other platforms should
  not be claimed before executable evidence exists.
- Import compatibility is a documented subset, not arbitrary DOM/CSS support.
- The installed macOS lane proves DesignIR materialization, JS/TS/JSX behavior,
  native GPU rendering, finite testing, capture, and `.app` packaging. It does
  not prove browser/Wasm, other native targets, release notarization, arbitrary
  web compatibility, or the external demonstration application.
- Ownership and provenance are described in
  [`docs/ownership.md`](docs/ownership.md),
  [`provenance/pulp-extraction.json`](provenance/pulp-extraction.json), and
  [`provenance/ownership-map.yaml`](provenance/ownership-map.yaml).
