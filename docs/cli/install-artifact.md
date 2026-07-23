# SDK artifact and installer contract

The repository builds a reproducible local artifact, and the installer supports
local development, verified local archive, and an exact-version private release
mode. The commands below describe the exact `v0.1.0` tagged-release contract;
they do not claim the tag or release has already been published.

## Private tagged-release installation

The Vellum repository and release are private. The application SDK requires
macOS 15.0 or newer on arm64 plus Python 3.9 or newer. Install
[GitHub CLI 2.75.0 or newer](https://cli.github.com/) and authenticate with
`gh auth login`, or provide `GH_TOKEN`/`GITHUB_TOKEN` for unattended use. The
minimum version provides the immutable-release verification commands required
by the installer. The fastest authenticated path is:

Verified archive and release installation is currently exposed only through
`install.sh` on the supported macOS target. `install.ps1` supports explicit
`-LocalRoot` development installs only; its archive and release parameters fail
closed before reading, downloading, extracting, or installing anything. This
prevents PowerShell from becoming a second artifact-verification authority.

```sh
bootstrap_dir="$(mktemp -d)"
gh release download v0.1.0 \
  --repo Generous-Corp/vellum \
  --pattern install.sh \
  --dir "$bootstrap_dir"
sh "$bootstrap_dir/install.sh" --version 0.1.0
export PATH="$HOME/.local/bin:$PATH"

app_dir="$(mktemp -d)/vellum-hello"
vellum create "Vellum Hello" --directory "$app_dir"
cd "$app_dir"
vellum run --no-build
```

The initial bootstrap in that convenience path is trusted because it was
selected and transferred through the authenticated GitHub CLI. The bootstrap
then downloads `SHA256SUMS`, `install_core.py`, and the exact target archive,
uses `gh release verify-asset` on all three, checks the installer-core and
archive hashes again against `SHA256SUMS`, and only then executes the core.

For a cautious bootstrap, download the release manifest and both scripts,
extract only their exact basename entries, require exactly one row for each
bootstrap file, and verify them before execution:

```sh
bootstrap_dir="$(mktemp -d)"
gh release download v0.1.0 \
  --repo Generous-Corp/vellum \
  --pattern SHA256SUMS \
  --pattern install.sh \
  --pattern install_core.py \
  --dir "$bootstrap_dir"
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
sh "$bootstrap_dir/install.sh" --version 0.1.0
```

Do not replace either flow with `curl | sh`: anonymous `curl` does not provide
the private GitHub authentication used by this release contract, and piping a
network script executes it before it can be reviewed or checksummed.

The gzip-compressed tar has this root layout:

```text
vellum_cli.py
vellum_backend.py
vellum_manifest.py
vellum_png.py
templates/basic/...
metadata.json
sdk/include/...
sdk/lib/cmake/Vellum/...
sdk/lib/libvellum-*.a
sdk/bin/vellum-app-host             # pinned-Skia mode
sdk/lib/libvellum-authoring.dylib   # pinned-Skia mode
sdk/lib/libvellum-gpu.dylib         # pinned-Skia mode
design-ir/bin/vellum-backend.js
design-ir/src/...
design-ir/schema/...
design-ir/LICENSE.md
ui/package.json                     # pinned-Skia mode
ui/scripts/build-project.mjs        # pinned-Skia mode
ui/node_modules/esbuild/...         # exact package-lock versions
ui/node_modules/typescript/...
node/bin/node                       # exact SDK-local Node executable
node/LICENSE                        # complete Node distribution license
node/provenance.json                # exact version/source/hash tuple
vellum_native_backend.py            # pinned-Skia mode
```

`metadata.json` uses `vellum.sdk-artifact.v1`, records framework and CLI
compatibility, inventories every payload file by SHA-256, and states capability
claims. Claims are derived from the files and installed CMake targets, then
independently recomputed by the verifier. The default artifact claims the
authoring CLI, deterministic DesignIR import/reimport, and CMake SDK while
recording GPU rendering and native application commands as unavailable.

GPU artifacts claim `custom_components` (native only) when the installed
`Vellum::ComponentAbi` target, its versioned C header, the native host, and the
complete native backend are all present. The verifier derives that claim again
from archive bytes.

Pass the verified pinned Skia archive explicitly to compose the installed
`Vellum::Gpu` and `Vellum::Authoring` targets plus `@vellum/ui` and its exact
esbuild/TypeScript dependencies:

```sh
python3 scripts/build_sdk_artifact.py \
  --skia-archive /tmp/vellum-skia-m150.zip \
  --node-binary "$(command -v node)" \
  --node-license /path/to/node-distribution/LICENSE \
  --node-provenance /path/to/node-provenance.json \
  --output-dir dist --json
```

`node-provenance.json` uses `vellum.node-runtime-provenance.v1` and contains
exactly `schema`, `name` (`Node.js`), `version`, SDK `target`, HTTPS
`source_url`, `distribution_sha256`, `binary_sha256`, `license_file`
(`LICENSE`), and `license_sha256`. Construction rejects a runtime whose probed
version or bytes do not match this record. Verification independently requires
the executable, license, and record as one indivisible payload and recomputes
the executable and license hashes.

Create the record from a verified Node distribution before composing the SDK:

```sh
python3 scripts/create_node_provenance.py \
  --node-binary /path/to/node/bin/node \
  --node-license /path/to/node/LICENSE \
  --target darwin-arm64 \
  --source-url https://nodejs.org/dist/v22.16.0/node-v22.16.0-darwin-arm64.tar.gz \
  --distribution-sha256 <sha256-from-the-signed-node-release-list> \
  --output /tmp/node-provenance.json
```

`build`, `run`, `test`, `capture`, and `package` become available only when the
installed app host, GPU/authoring libraries, UI compiler and locked dependencies,
and `cli/vellum_native_backend.py` all exist. Missing any one of those payloads
keeps every native command claim false.

The installer creates three distinct executable roles under
`$VELLUM_SDK_ROOT/bin`: `vellum-backend` is the stable dispatcher,
`vellum-import-backend` runs the packaged DesignIR backend, and
`vellum-native-backend` is installed only when its source implementation was
packaged. Import and native command implementations therefore cannot shadow one
another. Application-capable artifacts carry their exact Node.js 20+ runtime,
license, and provenance; verified installation does not require system Node.
Local development installs remain an explicit system-Node fallback. The GPU
artifact contains its locked esbuild platform binary and TypeScript compiler;
application builds do not resolve framework packages from the network. Project
creation validates the committed npm lock's exact shape and materializes the
exact runtime package directly from SDK bytes without external npm.

The verified installer stores SDKs immutably at
`PREFIX/lib/vellum-installs/<version>-<target>-<archive-sha256>`. It caches the
verified archive by SHA-256 at `PREFIX/lib/vellum-cache`, then exposes exactly
one active SDK through the `PREFIX/lib/vellum` symlink and managed
`PREFIX/bin/vellum` launcher. The active SDK's `install-manifest.json` records
the exact archive basename, SHA-256, target, framework version, and source
commit. `vellum create` copies that identity into `framework.lock`, and later
commands reject a different installed artifact even when its framework version
matches.

Each immutable SDK has a complete file/directory ownership receipt. A prefix
lock serializes installation, verification, and removal. The installer safely
extracts into staging, verifies the metadata inventory, runs `vellum --version`
against staged bytes, and only then atomically activates the new install. If
activation fails, it restores the prior active pointer, launcher, and installer
state. Reinstalling the exact artifact verifies and reuses the existing
content-addressed SDK; it never edits that SDK in place. Corrupt cache entries,
modified installed bytes, incomplete activation state, symlink escapes, and
unmanaged launchers fail closed.

Keep a verified `install.sh` and `install_core.py` adjacent for lifecycle
commands:

```sh
sh ./install.sh --verify-installed
sh ./install.sh --uninstall
```

Use `--install-dir PREFIX` on installation and lifecycle commands to select a
prefix other than `$HOME/.local`. `--verify-installed` checks the active
pointer, ownership receipt, installed hashes and modes, managed launcher,
installer state, and CLI self-test. `--uninstall` first performs the same
verification, then removes only receipt-owned SDK files, owned archive-cache
entries, the managed launcher, active pointer, and installer state. It refuses
modified or unmanaged content and leaves unrelated prefix files untouched.

A `--local` install is a separate, explicitly unverified development mode. It
records `verified: false` with null archive, hash, and commit fields and never
manufactures release identity. It is not installed into, or mixed with, the
transactional immutable layout.

The release `SHA256SUMS` contains exactly one basename entry each for the SDK
archive, `install.sh`, and `install_core.py`. The installer requires exactly one
entry for every file it verifies and aborts before extraction or core execution
when an entry is missing, duplicated, malformed, or mismatched. The
source-controlled `scripts/INSTALLER_SHA256SUMS` covers `install.sh` and
`install_core.py` and is checked as part of tag preparation. Release mode
requires an exact version, derives one archive basename from OS/architecture,
and rejects `latest`.

Build and validate the artifact:

```sh
python3 scripts/build_sdk_artifact.py --output-dir dist --json
python3 scripts/verify_sdk_artifact.py \
  --archive dist/vellum-sdk-0.1.0-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS --json
python3 scripts/validate_installed_sdk.py \
  --archive dist/vellum-sdk-0.1.0-darwin-arm64.tar.gz \
  --checksums dist/SHA256SUMS \
  --output dist/installed-validation.json --json
```

The normal builder refuses a dirty source tree so `source_commit` identifies
the packaged bytes. Its explicit `--allow-dirty` mode exists for local testing
and records `source_tree_clean=false` in metadata and evidence.

The last command produces `vellum.installed-sdk-validation.v1` evidence for
checksum verification, clean-prefix installation, CMake relocation, sterile
consumer configure/build/test, installed-CLI project creation, and exact
project-lock compatibility, including the exact artifact SHA. It also runs
installed CLI import and reimport from
two fixture revisions, verifies that the accepted active revision advances,
embeds the materialized imported design in a real `.app`, and exercises the
installed build/run/test/capture/package journey.

The tagged-release workflow publishes a draft only after reproducibility,
artifact verification, and installed-SDK validation pass. It uploads the SDK,
the two installer scripts, `SHA256SUMS`, and retained evidence; creates build
trust evidence for the private repository's available controls; then publishes
the immutable, non-`latest` private release and verifies it.

After authenticated download, independently inspect GitHub's release
verification and asset digest:

```sh
gh release verify v0.1.0 --repo Generous-Corp/vellum
gh release verify-asset v0.1.0 ./vellum-sdk-0.1.0-darwin-arm64.tar.gz \
  --repo Generous-Corp/vellum
```

`release-trust.json` records the exact tag and source commit, the trusted-key
signed-tag/same-run-repeatability/digest/checksum/sterile-install controls, and
the explicit attestation status. GitHub Actions build artifact attestations are
unavailable for private repositories without GitHub Enterprise Cloud, so this
incubation release does not claim one. The separately generated immutable
release attestation is required and verified. See
[Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
and [GitHub security features](https://docs.github.com/en/code-security/getting-started/github-security-features#artifact-attestations).

These are release gates, not claims that `v0.1.0` exists before the tag workflow
has completed. The local artifact flow exercises the same archive verification
and transactional installation logic but cannot claim hosted release
immutability or GitHub asset digests.
