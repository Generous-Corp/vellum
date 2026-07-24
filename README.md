# Vellum

> **Status: private, experimental, 0.x.** APIs, schemas, CLI names, and the
> working name itself change without notice. Exact-pin SDK compatibility only.
> Not accepting external users.

## What this is

Vellum is an experimental, audio-free application framework extracted to test
one product question: can a developer import a design, add TypeScript or
JavaScript behavior, and ship a GPU-rendered application without Chromium or an
OS WebView as the primary UI runtime?

This repository has reached its first independent-validation milestone.
The project CLI and repository shape are usable. An audio-free native C++
kernel, deterministic DesignIR import/reimport, hardened JS/TS/JSX retained-tree
runtime, JavaScriptCore host, installable CMake SDK, and a retained scene
rendered through Skia Graphite, Dawn, and Metal are executable in-tree. A
macOS 15.0+ arm64 SDK artifact built with the pinned renderer also provides the
first installed native backend: it bundles authored TS/JS/JSX and optional imported
DesignIR, builds and runs a real `.app`, executes finite scenarios, captures a
GPU PNG, and emits an ad-hoc-signed application package using installed bytes
only. An experimental browser proof now runs browser JavaScript against the
shared C++ runtime, retained scene, and paint-command traversal compiled to
Wasm, with Canvas2D as an explicitly identified presentation shell. It is not
yet a GPU-backend or arbitrary compatibility claim. Exact-pinned SDK artifacts
may expose build/test/run-instructions/static-package for web while unsupported
targets continue to fail closed.

The history-preserving Pulp projection has been removed from the active tip.
Its authorship and exact blobs remain auditable in Git history and immutable
`provenance/` records; Vellum does not maintain a synchronized editable copy.

## What this is not

- It is not arbitrary HTML, CSS, DOM, website, or React-DOM compatibility.
- It is not an audio, MIDI, DSP, plug-in-format, or plug-in-hosting framework.
- It does not claim one renderer or WebGPU backend on every target.
- It is not a public, stable, production-supported framework.
- It does not claim smaller binaries, lower memory, or higher performance than
  Electron, Tauri, Flutter, Qt, or React Native without equivalent benchmarks.

## Quick start

The following is the exact published, immutable `v0.1.6` tagged-release flow.
Because both the repository and release are private, install and authenticate
[GitHub CLI 2.75.0 or newer](https://cli.github.com/) first (`gh auth login`,
or set `GH_TOKEN` or `GITHUB_TOKEN` for an unattended agent). This minimum
provides the immutable-release verification commands used by the installer.

Verify the supported host and bootstrap prerequisites first:

<!-- readme-exec: id=release-prerequisites profile=clean-release -->
```sh
test "$(uname -s)" = Darwin
test "$(uname -m)" = arm64
test "$(sw_vers -productVersion | awk -F. '{ print $1 }')" -ge 15
python3 -c 'import sys; assert sys.version_info >= (3, 9)'
command -v gh >/dev/null
gh --version
gh release verify-asset --help >/dev/null
gh auth status --hostname github.com
```

If Python is missing or too old, install the current Xcode Command Line Tools
with `xcode-select --install` or a current Python from python.org. The SDK
bundles its exact Node runtime and application build tools; system Node, npm,
CMake, and Ninja are not prerequisites for this release path.

The fastest authenticated path downloads the version-pinned bootstrap and lets
it acquire and verify the matching installer core and macOS 15.0+ arm64 SDK:

<!-- readme-exec: id=release-install-create-run profile=clean-release -->
```sh
bootstrap_dir="$(mktemp -d)"
gh release download v0.1.6 \
  --repo Generous-Corp/vellum \
  --pattern install.sh \
  --dir "$bootstrap_dir"
gh release verify-asset v0.1.6 "$bootstrap_dir/install.sh" \
  --repo Generous-Corp/vellum
sh "$bootstrap_dir/install.sh" --version 0.1.6
export PATH="$HOME/.local/bin:$PATH"
vellum --json doctor --require-target macos

app_dir="$(mktemp -d)/vellum-hello"
vellum create "Vellum Hello" --directory "$app_dir"
cd "$app_dir"
vellum run --no-build
```

`vellum create` scaffolds, builds, and runs the finite smoke scenario by
default, so the first application is validated before `run` launches it. To
start from a supported Pulp Figma-plugin export instead, use:

<!-- readme-exec: id=figma-imported-start manual=requires-user-supplied-export -->
```sh
vellum create "Imported App" \
  --from figma /absolute/path/to/frame.pulp.zip \
  --run
```

This is the bounded, credential-free plugin JSON/`.pulp.zip` contract documented
in [Import and reimport](docs/cli/import-reimport.md), not a `.fig` file or
arbitrary Figma API response. The first release retains the exact exported
bytes and conversion diagnostics; a Vellum-owned exporter remains an explicit
product limitation.

For the cautious bootstrap path, download the checksum manifest and both
bootstrap files together, select their exact basename entries, require exactly
one row for each bootstrap file, and verify them before executing either script:

<!-- readme-exec: id=cautious-bootstrap manual=alternative-to-quick-start -->
```sh
bootstrap_dir="$(mktemp -d)"
gh release download v0.1.6 \
  --repo Generous-Corp/vellum \
  --pattern SHA256SUMS \
  --pattern install.sh \
  --pattern install_core.py \
  --dir "$bootstrap_dir"
for asset in install.sh install_core.py SHA256SUMS
do
  gh release verify-asset v0.1.6 "$bootstrap_dir/$asset" \
    --repo Generous-Corp/vellum
done
(
  cd "$bootstrap_dir"
  awk '$2 == "install.sh" || $2 == "*install.sh"' \
    SHA256SUMS > install.sh.sha256
  awk '$2 == "install_core.py" || $2 == "*install_core.py"' \
    SHA256SUMS > install_core.py.sha256
  test "$(awk 'END { print NR }' install.sh.sha256)" -eq 1
  test "$(awk 'END { print NR }' install_core.py.sha256)" -eq 1
  cat install.sh.sha256 install_core.py.sha256 > bootstrap.sha256
  shasum -a 256 -c bootstrap.sha256
)
sh "$bootstrap_dir/install.sh" --version 0.1.6
```

Both paths verify the initial bootstrap against GitHub's immutable-release
attestation before executing it. The installer then verifies the matching
installer core, archive release digests, and release `SHA256SUMS` before it
extracts or activates SDK bytes. No private-release `curl | sh` path is
advertised.

The checkout-only development path remains available for CLI and import work:

<!-- readme-exec: id=checkout-development-install manual=unverified-development-path -->
```sh
git clone git@github.com:Generous-Corp/vellum.git
cd vellum
./scripts/install.sh --local "$PWD"
export PATH="$HOME/.local/bin:$PATH"
```

That explicitly unverified development install intentionally has no native
backend; use the tagged release or a verified local SDK artifact for the
installed native journey.

## Requirements

The private release quick start supports macOS 15.0 or newer on Apple silicon.
It requires Python 3.9 or newer and an authenticated GitHub CLI 2.75.0 or newer
with access to the private repository. The SDK bundles its exact Node runtime,
application build tools, and pinned framework dependencies. Chrome is required
only for browser scenarios. Source SDK development additionally requires the
toolchain named in the detailed commands below.

`vellum doctor` reports requirements as versioned JSON. `vellum doctor --fix`
repairs project-local SDK material that can be changed safely and gives one
actionable instruction for OS-owned or licensed prerequisites.

## What was extracted and what stayed in Pulp

Vellum owns its independently implemented retained scene model, rendering,
layout, bounded design import and reimport, scripting surface, app shell,
generic testkit/capture primitives, and application CLI.

Pulp continues to own audio, MIDI, DSP, plug-in formats, plug-in hosting, audio
widgets, audio DesignIR extensions, DAW integration, and Pulp product tooling.
The exact machine-readable authority state is
[`provenance/ownership-map.yaml`](provenance/ownership-map.yaml). Source
authority for the selected mapped framework slices is active in Vellum. Pulp
does not yet consume the Vellum SDK as a dependency; source authority and
dependency adoption are separate transitions.

## Anatomy of a generated application

`app.toml` is the developer-owned application, target, capability, and package
authority. `framework.lock` pins one exact SDK artifact. Immutable design
snapshots live under `sources/imported/`; normalized DesignIR, generated UI,
tokens, assets, and reports are inspectable and tool-owned. Hand-written
TypeScript/JavaScript lives under `src/`; optional application C++ lives under
`native/`; scenarios and capture matrices live under `tests/`.

Generated applications commit `.vellum/agent-instructions.md` and `AGENTS.md`
so agents preserve the same generated-versus-authored boundary. See
[Import and reimport](docs/cli/import-reimport.md) for the complete ownership
and conflict contract.

## Capability and platform status

This table is generated from
[`docs/status/capabilities.yaml`](docs/status/capabilities.yaml). `supported`
is reserved for a capability whose named evidence check is green on the same
commit.

<!-- docs-sync: capabilities:start -->
| Capability or target | Status | Evidence check | Honest boundary |
| --- | --- | --- | --- |
| macOS native application | experimental | `gpu-macos-arm64` | macOS 15.0+ arm64; private exact-pin SDK; ad-hoc package |
| Figma plugin export import/reimport | experimental | `product-quality`, `gpu-macos-arm64` | bounded single-root Pulp plugin JSON or `.pulp.zip`; not `.fig` or live REST |
| Browser JavaScript plus shared C++ Wasm core | partial | `gpu-macos-arm64` | Canvas2D presentation shell; no browser GPU-backend claim |
| Windows native application | planned | none | local-development CLI bootstrap only; no native product evidence |
| Linux native application | planned | none | no native product evidence |
| iOS native application | planned | none | no simulator, device, or package evidence |
| Android native application | planned | none | no emulator, device, or package evidence |
<!-- docs-sync: capabilities:end -->

## Commands

| Command | Purpose |
| --- | --- |
| `vellum create` | Scaffold `blank`, `imported-app`, or `cpp-component` and validate the exact-pinned project |
| `vellum doctor` | Inspect requirements and repair safe project-local state |
| `vellum import`, `vellum reimport` | Materialize or update a supported immutable design source |
| `vellum build`, `vellum run`, `vellum dev` | Build, launch, or watch a declared target |
| `vellum test`, `vellum capture` | Run finite scenarios and retain visual evidence |
| `vellum package` | Produce the target's current package format |

Every command accepts `--json` before or after the command and emits one stable
`vellum.cli.result.v1` object. See [the CLI contract](docs/cli/contract.md).

### Pinned macOS native SDK and first app

Download and verify the exact Skia/Dawn toolchain recorded in
[`DEPENDENCIES.md`](DEPENDENCIES.md), then configure Vellum with its extraction
root:

<!-- readme-exec: id=source-sdk-build manual=source-build-not-release-quick-start -->
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
  --skia-archive /tmp/vellum-skia-m150.zip \
  --node-binary "$(command -v node)" \
  --node-license /path/to/node-distribution/LICENSE \
  --node-provenance /path/to/node-provenance.json \
  --output-dir dist --json
./scripts/install.sh \
  --archive dist/vellum-sdk-0.1.6-darwin-arm64.tar.gz \
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

<!-- readme-exec: id=windows-development-install manual=unsupported-clean-release-host -->
```powershell
.\scripts\install.ps1 -LocalRoot $PWD
$env:Path = "$HOME\.local\bin;$env:Path"
vellum create "Vellum Hello" -d "$env:TEMP\vellum-hello"
```

### CLI journey

<!-- readme-exec: id=cli-journey manual=illustrative-requires-prepared-project -->
```sh
vellum create MyApp
cd myapp
vellum doctor --fix
vellum import ./revision-a.source.json --source-type figma --as main
vellum reimport --source ./revision-b.source.json --as main
vellum build --target macos
vellum dev --target macos
vellum run --target macos
vellum test
vellum capture --scenario smoke --output artifacts/smoke.png
vellum capture --matrix tests/capture-matrix.json --montage --output artifacts/montage.png
vellum package --target macos --output dist
```

The one-command imported path is
`vellum create "Imported App" --from figma ./frame.pulp.zip --run`; it uses
the permanent source key `main` unless `--as` supplies another key. The
installed dispatcher safely stages the normal Pulp archive shape without an
external unzip command and retains the original bytes as immutable source
evidence.

`create` is deterministic and separates imported snapshots, normalized
DesignIR, generated UI, tokens/assets, hand-written app logic, optional native
components, platform modules, tests, and packaging configuration. The macOS GPU
artifact implements this complete command lane. An artifact built without the
pinned renderer advertises only import/reimport. Backends are discovered through
`VELLUM_SDK_ROOT`, `VELLUM_BACKEND`, or `PATH`.

Generated applications use `app.toml` as the sole editable authority for app
identity, entry point, targets, capabilities, and packaging. `framework.lock`
pins the exact SDK artifact and JS package identity. `package-lock.json` is an
exact npm lock; an installed SDK validates its exact shape, projects immutable
`@vellum/ui` runtime bytes into ignored `.vellum/packages/`, and materializes
those bytes without external npm before `create` reports success.

The native authoring slice includes a controlled, versioned `TextInput`,
pointer focus, direct key/text dispatch, caret and selection state, IME
composition, synchronized accessibility text semantics, bounded semantic
`input`/`key` scenarios, and opt-in `persistence = "state-v1"` whole-app
snapshots on macOS. Its deliberately narrow limitations—including no
clipboard editing, general storage API, state migration, synchronization, or
mobile text host—are documented in
[`@vellum/ui`](packages/vellum-ui/README.md) and
[Interaction capture and montages](docs/cli/capture.md).

The accepted source contract, generated tree, ownership boundary, and conflict
workflow are documented in [Import and reimport](docs/cli/import-reimport.md).
The semantic interaction driver, offscreen capture path, and deterministic
contact-sheet format are documented in [Interaction capture and montages](docs/cli/capture.md).
The deterministic watch/build/reload supervisor, its evidence transcript, and
native/web state-continuity limits are documented in
[Development loop](docs/cli/dev.md).

Agents and automation should follow the versioned
[Vellum application-authoring skill](.agents/skills/vellum-app-authoring/SKILL.md).
Its adjacent machine-readable manifest is checked against the real CLI parser,
source-support policy, ownership boundary, and capability-failure semantics by
`python3 tools/agent_instructions/verify.py --json`; instructions cannot name a
command, flag, or import route that the checked-in product surface does not
support. SDK artifacts install the same contract under
`$VELLUM_SDK_ROOT/.agents/skills/vellum-app-authoring/`.

### Build and install an immutable local SDK artifact

The builder performs a Release build, creates a relocatable CMake install tree,
normalizes archive metadata, and emits both `SHA256SUMS` and machine-readable
evidence. Building twice from the same source commit and toolchain is covered
by the integration test.

<!-- readme-exec: id=local-sdk-artifact manual=source-build-not-release-quick-start -->
```sh
python3 scripts/build_sdk_artifact.py \
  --skia-archive /tmp/vellum-skia-m150.zip \
  --node-binary "$(command -v node)" \
  --node-license /path/to/node-distribution/LICENSE \
  --node-provenance /path/to/node-provenance.json \
  --output-dir dist --json
python3 scripts/verify_sdk_artifact.py \
  --archive dist/vellum-sdk-0.1.6-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS --json
./scripts/install.sh \
  --archive dist/vellum-sdk-0.1.6-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS
```

To compose the pinned GPU and authoring SDK into the artifact, pass the Skia
archive verified in the preceding section:

<!-- readme-exec: id=gpu-sdk-artifact manual=source-build-not-release-quick-start -->
```sh
python3 scripts/build_sdk_artifact.py \
  --skia-archive /tmp/vellum-skia-m150.zip \
  --node-binary "$(command -v node)" \
  --node-license /path/to/node-distribution/LICENSE \
  --node-provenance /path/to/node-provenance.json \
  --output-dir dist --json
```

That artifact includes the installed `Vellum::Gpu` and `Vellum::Authoring`
targets and an offline-ready `@vellum/ui` toolchain. Native CLI commands remain
unavailable unless a real `cli/vellum_native_backend.py` exists; artifact
metadata is derived from the payload and cannot claim a missing backend.

Verified installs are immutable and content-addressed under
`PREFIX/lib/vellum-installs/<version>-<target>-<archive-sha256>`.
`PREFIX/lib/vellum` is the exact active-version symlink, and
`PREFIX/bin/vellum` is the managed stable launcher. The active CMake tree is
therefore available at `PREFIX/lib/vellum/sdk`; consumers can add that
directory to `CMAKE_PREFIX_PATH` and use
`find_package(Vellum CONFIG REQUIRED)`.

Every immutable install writes an ownership receipt and
`install-manifest.json`. Verified installs record the archive SHA-256, target,
version, and source commit; local installs are explicitly unverified and carry
no fabricated hash. New projects pin this identity in `framework.lock`, so a
same-version but different SDK artifact is rejected before backend execution.

An exact reinstall verifies and reuses the content-addressed install instead of
editing it. Installation is serialized by a prefix lock; archive extraction and
self-test happen before activation; and activation rolls the launcher, active
pointer, and state back to the previously verified SDK if the transaction
fails. The uninstaller removes only files covered by verified ownership
receipts and refuses incomplete, modified, or unmanaged state.

Keep a verified `install.sh` and `install_core.py` together to inspect or remove
an installation:

<!-- readme-exec: id=installed-maintenance manual=destructive-or-post-install-maintenance -->
```sh
sh ./install.sh --verify-installed
sh ./install.sh --uninstall
```

Pass the same `--install-dir PREFIX` used during installation when it was not
the default `$HOME/.local`.

`scripts/validate_installed_sdk.py` verifies the archive, installs to a clean
prefix, creates and validates an app through the installed CLI, checks its
project lock against SDK metadata, imports and reimports two design revisions,
proves the imported design is embedded in the application bundle, exercises
build/run/test/capture/package, and builds/tests a sterile CMake consumer
without a Vellum or Pulp checkout.

The exact `v0.1.6` release is consumed without a moving `latest` pointer:

<!-- readme-exec: id=version-install-only manual=subset-of-release-quick-start -->
```sh
./scripts/install.sh --version 0.1.6
```

The release `SHA256SUMS` covers the SDK archive, `install.sh`, and
`install_core.py`. The source-controlled `scripts/INSTALLER_SHA256SUMS` covers
the two bootstrap scripts and is checked when preparing the tag. Release
publication also retains `release-trust.json`. It distinguishes unavailable
GitHub Actions build artifact attestations for this private non-Enterprise
repository from the automatic release attestation GitHub creates when an
immutable release is published. After downloading the assets, verify that
release attestation and an asset with:

<!-- readme-exec: id=release-attestation-check manual=post-download-verification-example -->
```sh
gh release verify v0.1.6 --repo Generous-Corp/vellum
gh release verify-asset v0.1.6 ./vellum-sdk-0.1.6-darwin-arm64.tar.gz \
  --repo Generous-Corp/vellum
```

The private incubation release relies on a trusted-key SSH-signed annotated Git
tag bound to the exact source commit, same-run byte repeatability, GitHub's
immutable-release attestation and asset digests, `SHA256SUMS`, and sterile
installed-SDK validation. It does not claim a GitHub Actions build artifact
attestation. GitHub documents the two distinct controls in
[Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
and [GitHub security features](https://docs.github.com/en/code-security/getting-started/github-security-features#artifact-attestations).

Checksum verification protects downloaded bytes but does not make an
unreviewed network script intrinsically safe. Review the pinned bootstrap or
use the cautious path above; no `curl | sh` command is advertised for the
private release.

## Ownership, provenance, and attribution

- No Pulp audio, plug-in, host, or product adapters belong in Vellum.
- Pulp does not currently consume this repository as an SDK dependency.
- macOS and browser/Wasm are the first proof targets; the browser proof and its
  exact non-claims are documented in
  [Browser/Wasm proof](docs/web/browser-wasm-proof.md). Other platforms should
  not be claimed before executable evidence exists.
- Import compatibility is a documented subset, not arbitrary DOM/CSS support.
- The installed macOS lane proves DesignIR materialization, JS/TS/JSX behavior,
  native GPU rendering, finite testing, capture, and `.app` packaging. It does
  not prove a browser GPU backend, browser CLI/package workflow, other
  native targets, release notarization, arbitrary web compatibility, or the
  external demonstration application.
- Ownership and provenance are described in
  [`docs/ownership.md`](docs/ownership.md),
  [`provenance/pulp-extraction.json`](provenance/pulp-extraction.json), and
  [`provenance/ownership-map.yaml`](provenance/ownership-map.yaml).
- Independent downstream proofs are pinned in
  [`provenance/downstream-consumers.v1.json`](provenance/downstream-consumers.v1.json).
  Its offline verifier checks immutable identities, repository separation, the
  evidence ladder, and the framework-first fix protocol:
  `python3 tools/provenance/verify_downstream_consumers.py`.
- README shell blocks are fail-closed classified by
  `python3 scripts/readme_exec.py --lint`. The manually dispatched clean-release
  proof retains its environment, transcript, and timings. Performance targets
  remain explicitly unratified in
  [`product/budget-ratification.v1.json`](product/budget-ratification.v1.json)
  until a reviewed clean run supplies complete evidence.

The active implementation's lineage, exclusions, third-party dependencies, and
attribution are recorded in [`docs/ownership.md`](docs/ownership.md),
[`provenance/pulp-extraction.json`](provenance/pulp-extraction.json),
[`provenance/ownership-map.yaml`](provenance/ownership-map.yaml),
[`NOTICE.md`](NOTICE.md), and [`DEPENDENCIES.md`](DEPENDENCIES.md).

## Versioning and support status

Vellum is private experimental 0.x software. Projects pin one exact framework
version, source commit, target tuple, SDK checksum, CLI API, and JS package
identity in `framework.lock`. Exact-pin source/API compatibility is the current
promise; there is no universal C++ ABI promise. Schemas and CLI names may change
between explicitly reviewed upgrades. The working name is not permanent.

## License

Vellum's repository license is [MIT](LICENSE.md). Extracted-history attribution
and third-party terms remain documented in [NOTICE.md](NOTICE.md) and
[DEPENDENCIES.md](DEPENDENCIES.md).
