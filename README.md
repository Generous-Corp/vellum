# Vellum

Vellum is an experimental, audio-free application framework extracted to test
one product question: can a developer import a design, add TypeScript or
JavaScript behavior, and ship a GPU-rendered application without Chromium or an
OS WebView as the primary UI runtime?

This repository is at the independent-validation milestone. The project CLI
and repository shape are usable. An audio-free native C++ kernel, reproducible
checksummed CMake SDK artifact, and macOS CoreGraphics smoke application are
executable.
The public CLI now has an installed, deterministic DesignIR JSON import and safe
reimport backend. Native builds, Skia/Dawn GPU rendering, and packaging remain
SDK-backend capabilities and report `capability_unavailable` until implemented.

## Five-minute local-development start

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

The final command intentionally fails with structured `capability_unavailable`
output because the current SDK artifact has no application backend. It must not
pretend that a renderer or package was produced.

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
vellum import ./revision-a.source.json --source-type figma --as main
vellum reimport --source ./revision-b.source.json --as main
vellum build --target macos
vellum run --target macos
vellum test
vellum capture --scenario smoke --output artifacts/smoke.png
vellum package --target macos --output dist
```

`create` is deterministic and separates imported snapshots, normalized
DesignIR, generated UI, tokens/assets, hand-written app logic, optional native
components, platform modules, tests, and packaging configuration. The installed
backend currently implements JSON DesignIR import/reimport; native runtime
commands require additional backend capabilities. Backends are discovered through
`VELLUM_SDK_ROOT`, `VELLUM_BACKEND`, or `PATH`.

The accepted source contract, generated tree, ownership boundary, and conflict
workflow are documented in [Import and reimport](docs/cli/import-reimport.md).

Every command accepts `--json` before or after the command and emits one stable
`vellum.cli.result.v1` object. See [the CLI contract](docs/cli/contract.md).

## Build and install an immutable local SDK artifact

The builder performs a Release build, creates a relocatable CMake install tree,
normalizes archive metadata, and emits both `SHA256SUMS` and machine-readable
evidence. Building twice from the same source commit and toolchain is covered
by the integration test.

```sh
python3 scripts/build_sdk_artifact.py --output-dir dist --json
python3 scripts/verify_sdk_artifact.py \
  --archive dist/vellum-sdk-0.1.0-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS --json
./scripts/install.sh \
  --archive dist/vellum-sdk-0.1.0-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS
```

The installed CMake tree is under the chosen prefix at `lib/vellum/sdk`;
consumers can add that directory to `CMAKE_PREFIX_PATH` and use
`find_package(Vellum CONFIG REQUIRED)`.

`scripts/validate_installed_sdk.py` verifies the archive, installs to a clean
prefix, creates an app through the installed CLI, checks its project lock
against SDK metadata, imports and reimports two design revisions, and
builds/tests a sterile CMake consumer without a Vellum or Pulp checkout.

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
- The current macOS smoke proves the native lifecycle and graphics package with
  CoreGraphics. It is not evidence that the required Skia/Dawn GPU slice or
  retained view tree is complete.
